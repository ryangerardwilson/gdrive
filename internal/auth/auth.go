package auth

import (
	"bufio"
	"context"
	"encoding/json"
	"fmt"
	"net"
	"net/http"
	"os"
	"os/exec"
	"strings"
	"time"

	"github.com/ryangerardwilson/gdrive/internal/config"
	"github.com/ryangerardwilson/gdrive/internal/paths"
	"golang.org/x/oauth2"
	"golang.org/x/oauth2/google"
	"google.golang.org/api/drive/v3"
	"google.golang.org/api/option"
)

const localServerTimeout = 60 * time.Second

type storedToken struct {
	AccessToken  string    `json:"access_token,omitempty"`
	Token        string    `json:"token,omitempty"`
	TokenType    string    `json:"token_type,omitempty"`
	RefreshToken string    `json:"refresh_token,omitempty"`
	Expiry       time.Time `json:"expiry,omitempty"`
}

func AuthorizeAccount(clientSecretFile string, stdin *os.File, stdout, stderr *os.File) (*oauth2.Token, string, error) {
	if err := paths.EnsureDirs(); err != nil {
		return nil, "", err
	}
	data, err := os.ReadFile(paths.ExpandHome(clientSecretFile))
	if err != nil {
		return nil, "", fmt.Errorf("oauth authorization failed: %w", err)
	}
	oauthConfig, err := google.ConfigFromJSON(data, drive.DriveScope)
	if err != nil {
		return nil, "", fmt.Errorf("oauth authorization failed: %w", err)
	}
	token, err := completeOAuth(oauthConfig, stdin, stdout, stderr)
	if err != nil {
		return nil, "", err
	}
	email, err := LookupEmail(context.Background(), oauthConfig.Client(context.Background(), token))
	if err != nil {
		return nil, "", err
	}
	if email == "" {
		return nil, "", fmt.Errorf("drive profile lookup returned no email address")
	}
	if err := WriteToken(paths.TokenFileForEmail(email), token); err != nil {
		return nil, "", err
	}
	return token, email, nil
}

func HTTPClient(ctx context.Context, account *config.Account) (*http.Client, error) {
	if account.Email == "" {
		return nil, fmt.Errorf("preset %s is missing email; re-run `gdrive auth <client_secret_path>`", account.Preset)
	}
	secret, err := config.RequireClientSecret(account)
	if err != nil {
		return nil, err
	}
	data, err := os.ReadFile(paths.ExpandHome(secret))
	if err != nil {
		return nil, err
	}
	oauthConfig, err := google.ConfigFromJSON(data, drive.DriveScope)
	if err != nil {
		return nil, err
	}
	tokenPath := paths.TokenFileForEmail(account.Email)
	token, err := ReadToken(tokenPath)
	if err != nil {
		return nil, err
	}
	if !token.Valid() && token.RefreshToken == "" {
		token, _, err = AuthorizeAccount(secret, os.Stdin, os.Stdout, os.Stderr)
		if err != nil {
			return nil, err
		}
	}
	source := oauthConfig.TokenSource(ctx, token)
	refreshed, err := source.Token()
	if err != nil {
		return nil, fmt.Errorf("oauth refresh failed: %w", err)
	}
	if refreshed.AccessToken != token.AccessToken || refreshed.RefreshToken != token.RefreshToken || !refreshed.Expiry.Equal(token.Expiry) {
		_ = WriteToken(tokenPath, refreshed)
	}
	return oauth2.NewClient(ctx, source), nil
}

func LookupEmail(ctx context.Context, client *http.Client) (string, error) {
	service, err := drive.NewService(ctx, option.WithHTTPClient(client))
	if err != nil {
		return "", fmt.Errorf("drive profile lookup failed after oauth: %w", err)
	}
	about, err := service.About.Get().Fields("user(emailAddress)").Do()
	if err != nil {
		return "", fmt.Errorf("drive profile lookup failed after oauth: %w", err)
	}
	return config.NormalizeEmail(about.User.EmailAddress), nil
}

func ReadToken(path string) (*oauth2.Token, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	var raw storedToken
	if err := json.Unmarshal(data, &raw); err != nil {
		return nil, err
	}
	access := raw.AccessToken
	if access == "" {
		access = raw.Token
	}
	tokenType := raw.TokenType
	if tokenType == "" {
		tokenType = "Bearer"
	}
	return &oauth2.Token{
		AccessToken:  access,
		TokenType:    tokenType,
		RefreshToken: raw.RefreshToken,
		Expiry:       raw.Expiry,
	}, nil
}

func WriteToken(path string, token *oauth2.Token) error {
	raw := storedToken{
		AccessToken:  token.AccessToken,
		Token:        token.AccessToken,
		TokenType:    token.TokenType,
		RefreshToken: token.RefreshToken,
		Expiry:       token.Expiry,
	}
	data, err := json.MarshalIndent(raw, "", "  ")
	if err != nil {
		return err
	}
	data = append(data, '\n')
	return os.WriteFile(path, data, 0o600)
}

func completeOAuth(oauthConfig *oauth2.Config, stdin *os.File, stdout, stderr *os.File) (*oauth2.Token, error) {
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		return nil, fmt.Errorf("oauth local server failed: %w", err)
	}
	defer listener.Close()
	redirectURL := "http://" + listener.Addr().String() + "/"
	oauthConfig.RedirectURL = redirectURL
	state := fmt.Sprintf("gdrive-%d", time.Now().UnixNano())
	authURL := oauthConfig.AuthCodeURL(state, oauth2.AccessTypeOffline, oauth2.ApprovalForce)
	fmt.Fprintf(stdout, "Please visit this URL to authorize this application:\n%s\n\n", authURL)
	_ = exec.Command("xdg-open", authURL).Start()

	codeCh := make(chan string, 1)
	errCh := make(chan error, 1)
	server := &http.Server{
		Handler: http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			if r.URL.Query().Get("state") != state {
				errCh <- fmt.Errorf("oauth callback state mismatch")
				http.Error(w, "state mismatch", http.StatusBadRequest)
				return
			}
			code := r.URL.Query().Get("code")
			if code == "" {
				errCh <- fmt.Errorf("oauth callback URL did not contain a code")
				http.Error(w, "missing code", http.StatusBadRequest)
				return
			}
			fmt.Fprintln(w, "gdrive authorization complete. Return to the terminal.")
			codeCh <- code
		}),
	}
	go func() { _ = server.Serve(listener) }()
	defer server.Shutdown(context.Background())

	select {
	case code := <-codeCh:
		return oauthConfig.Exchange(context.Background(), code)
	case err := <-errCh:
		return nil, err
	case <-time.After(localServerTimeout):
		fmt.Fprintln(stderr, "No browser callback was received. Paste the callback URL or code below.")
		code, err := promptForCode(stdin, stdout)
		if err != nil {
			return nil, err
		}
		return oauthConfig.Exchange(context.Background(), code)
	}
}

func promptForCode(stdin *os.File, stdout *os.File) (string, error) {
	fmt.Fprint(stdout, "Paste the full localhost callback URL from the browser address bar, or just the code value: ")
	scanner := bufio.NewScanner(stdin)
	if !scanner.Scan() {
		return "", fmt.Errorf("oauth callback was empty")
	}
	value := strings.Trim(strings.TrimSpace(scanner.Text()), "'\"`;")
	if value == "" {
		return "", fmt.Errorf("oauth callback was empty")
	}
	if strings.HasPrefix(value, "http://") || strings.HasPrefix(value, "https://") {
		req, err := http.NewRequest("GET", value, nil)
		if err != nil {
			return "", err
		}
		code := req.URL.Query().Get("code")
		if code == "" {
			return "", fmt.Errorf("oauth callback URL did not contain a code")
		}
		return code, nil
	}
	return value, nil
}

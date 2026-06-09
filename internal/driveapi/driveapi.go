package driveapi

import (
	"context"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sort"
	"strings"

	"github.com/ryangerardwilson/gdrive/internal/auth"
	"github.com/ryangerardwilson/gdrive/internal/config"
	"google.golang.org/api/drive/v3"
	"google.golang.org/api/googleapi"
	"google.golang.org/api/option"
)

const FolderMime = "application/vnd.google-apps.folder"

var ExportMimeTypes = map[string]struct {
	Mime   string
	Suffix string
}{
	"application/vnd.google-apps.document":     {"application/vnd.openxmlformats-officedocument.wordprocessingml.document", ".docx"},
	"application/vnd.google-apps.spreadsheet":  {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ".xlsx"},
	"application/vnd.google-apps.presentation": {"application/vnd.openxmlformats-officedocument.presentationml.presentation", ".pptx"},
	"application/vnd.google-apps.drawing":      {"image/png", ".png"},
}

type RemoteEntry struct {
	ID       string
	RelPath  string
	Name     string
	ParentID string
	MimeType string
}

func (e RemoteEntry) IsDir() bool {
	return e.MimeType == FolderMime
}

type NavEntry struct {
	ID       string
	Name     string
	MimeType string
	ParentID string
}

func (e NavEntry) IsDir() bool {
	return e.MimeType == FolderMime
}

type Client interface {
	EnsureDrivePath(drivePath string) (string, error)
	ListChildren(parentID string) ([]NavEntry, error)
	GetEntry(fileID string) (NavEntry, error)
	FindChild(parentID, name, mimeType string) (*NavEntry, error)
	CreateFolder(parentID, name string) (string, error)
	FindAvailableName(parentID, name string) (string, error)
	DownloadEntry(entry NavEntry, targetPath string) (string, error)
	ListTree(rootID string) (map[string]RemoteEntry, error)
	UploadFile(parentID, name, filePath string) (string, error)
	UpdateFile(fileID, filePath string) error
	MoveEntry(fileID, newParentID, newName, oldParentID string) error
	RenameEntry(fileID, newName string) error
	DeleteEntry(fileID string) error
}

type DriveClient struct {
	service *drive.Service
}

func New(ctx context.Context, account *config.Account) (*DriveClient, error) {
	httpClient, err := auth.HTTPClient(ctx, account)
	if err != nil {
		return nil, err
	}
	service, err := drive.NewService(ctx, option.WithHTTPClient(httpClient))
	if err != nil {
		return nil, err
	}
	return &DriveClient{service: service}, nil
}

func (c *DriveClient) EnsureDrivePath(drivePath string) (string, error) {
	parentID := "root"
	for _, segment := range strings.Split(drivePath, "/") {
		segment = strings.TrimSpace(segment)
		if segment == "" {
			continue
		}
		existing, err := c.FindChild(parentID, segment, FolderMime)
		if err != nil {
			return "", err
		}
		if existing != nil {
			parentID = existing.ID
			continue
		}
		created, err := c.CreateFolder(parentID, segment)
		if err != nil {
			return "", err
		}
		parentID = created
	}
	return parentID, nil
}

func (c *DriveClient) ListChildren(parentID string) ([]NavEntry, error) {
	entries := []NavEntry{}
	pageToken := ""
	for {
		call := c.service.Files.List().
			Q(fmt.Sprintf("'%s' in parents and trashed = false", escapeQuery(parentID))).
			Fields("nextPageToken,files(id,name,mimeType,parents)").
			PageSize(1000).
			OrderBy("folder,name_natural").
			SupportsAllDrives(false)
		if pageToken != "" {
			call.PageToken(pageToken)
		}
		response, err := call.Do()
		if err != nil {
			return nil, apiError(err)
		}
		for _, item := range response.Files {
			parent := parentID
			if len(item.Parents) > 0 {
				parent = item.Parents[0]
			}
			entries = append(entries, NavEntry{ID: item.Id, Name: item.Name, MimeType: item.MimeType, ParentID: parent})
		}
		if response.NextPageToken == "" {
			break
		}
		pageToken = response.NextPageToken
	}
	sort.SliceStable(entries, func(i, j int) bool {
		if entries[i].IsDir() != entries[j].IsDir() {
			return entries[i].IsDir()
		}
		left := strings.ToLower(entries[i].Name)
		right := strings.ToLower(entries[j].Name)
		if left != right {
			return left < right
		}
		return entries[i].Name < entries[j].Name
	})
	return entries, nil
}

func (c *DriveClient) GetEntry(fileID string) (NavEntry, error) {
	item, err := c.service.Files.Get(fileID).Fields("id,name,mimeType,parents").SupportsAllDrives(false).Do()
	if err != nil {
		return NavEntry{}, apiError(err)
	}
	parent := ""
	if len(item.Parents) > 0 {
		parent = item.Parents[0]
	}
	return NavEntry{ID: item.Id, Name: item.Name, MimeType: item.MimeType, ParentID: parent}, nil
}

func (c *DriveClient) FindChild(parentID, name, mimeType string) (*NavEntry, error) {
	query := []string{
		fmt.Sprintf("'%s' in parents", escapeQuery(parentID)),
		fmt.Sprintf("name = '%s'", escapeQuery(name)),
		"trashed = false",
	}
	if mimeType != "" {
		query = append(query, fmt.Sprintf("mimeType = '%s'", escapeQuery(mimeType)))
	}
	response, err := c.service.Files.List().
		Q(strings.Join(query, " and ")).
		Fields("files(id,name,mimeType,parents)").
		PageSize(10).
		SupportsAllDrives(false).
		Do()
	if err != nil {
		return nil, apiError(err)
	}
	if len(response.Files) == 0 {
		return nil, nil
	}
	item := response.Files[0]
	parent := parentID
	if len(item.Parents) > 0 {
		parent = item.Parents[0]
	}
	return &NavEntry{ID: item.Id, Name: item.Name, MimeType: item.MimeType, ParentID: parent}, nil
}

func (c *DriveClient) CreateFolder(parentID, name string) (string, error) {
	item := &drive.File{Name: name, MimeType: FolderMime, Parents: []string{parentID}}
	result, err := c.service.Files.Create(item).Fields("id").SupportsAllDrives(false).Do()
	if err != nil {
		return "", apiError(err)
	}
	return result.Id, nil
}

func (c *DriveClient) FindAvailableName(parentID, name string) (string, error) {
	existing, err := c.FindChild(parentID, name, "")
	if err != nil {
		return "", err
	}
	if existing == nil {
		return name, nil
	}
	ext := filepath.Ext(name)
	base := strings.TrimSuffix(name, ext)
	for idx := 1; idx < 10000; idx++ {
		candidate := fmt.Sprintf("%s-%d%s", base, idx, ext)
		existing, err := c.FindChild(parentID, candidate, "")
		if err != nil {
			return "", err
		}
		if existing == nil {
			return candidate, nil
		}
	}
	return "", fmt.Errorf("could not allocate name for %s", name)
}

func (c *DriveClient) DownloadEntry(entry NavEntry, targetPath string) (string, error) {
	targetPath = downloadTargetPath(entry, targetPath)
	if err := os.MkdirAll(filepath.Dir(targetPath), 0o755); err != nil {
		return "", err
	}
	out, err := os.Create(targetPath)
	if err != nil {
		return "", err
	}
	defer out.Close()
	var response *httpResponse
	if export, ok := ExportMimeTypes[entry.MimeType]; ok {
		resp, err := c.service.Files.Export(entry.ID, export.Mime).Download()
		if err != nil {
			return "", apiError(err)
		}
		response = &httpResponse{Body: resp.Body}
	} else {
		if strings.HasPrefix(entry.MimeType, "application/vnd.google-apps.") {
			return "", fmt.Errorf("download not supported for Google file type `%s`", entry.MimeType)
		}
		resp, err := c.service.Files.Get(entry.ID).Download()
		if err != nil {
			return "", apiError(err)
		}
		response = &httpResponse{Body: resp.Body}
	}
	defer response.Body.Close()
	if _, err := io.Copy(out, response.Body); err != nil {
		return "", err
	}
	return targetPath, nil
}

func (c *DriveClient) ListTree(rootID string) (map[string]RemoteEntry, error) {
	result := map[string]RemoteEntry{}
	queue := []struct {
		Base     string
		ParentID string
	}{{Base: "", ParentID: rootID}}
	for len(queue) > 0 {
		item := queue[0]
		queue = queue[1:]
		children, err := c.ListChildren(item.ParentID)
		if err != nil {
			return nil, err
		}
		for _, child := range children {
			rel := strings.Trim(strings.TrimPrefix(item.Base+"/"+child.Name, "/"), "/")
			entry := RemoteEntry{ID: child.ID, RelPath: rel, Name: child.Name, ParentID: item.ParentID, MimeType: child.MimeType}
			result[rel] = entry
			if entry.IsDir() {
				queue = append(queue, struct {
					Base     string
					ParentID string
				}{Base: rel, ParentID: entry.ID})
			}
		}
	}
	return result, nil
}

func (c *DriveClient) UploadFile(parentID, name, filePath string) (string, error) {
	file, err := os.Open(filePath)
	if err != nil {
		return "", err
	}
	defer file.Close()
	item := &drive.File{Name: name, Parents: []string{parentID}}
	result, err := c.service.Files.Create(item).Media(file, googleapi.ChunkSize(8*1024*1024)).Fields("id").SupportsAllDrives(false).Do()
	if err != nil {
		return "", apiError(err)
	}
	return result.Id, nil
}

func (c *DriveClient) UpdateFile(fileID, filePath string) error {
	file, err := os.Open(filePath)
	if err != nil {
		return err
	}
	defer file.Close()
	_, err = c.service.Files.Update(fileID, &drive.File{}).Media(file, googleapi.ChunkSize(8*1024*1024)).Fields("id").SupportsAllDrives(false).Do()
	return apiError(err)
}

func (c *DriveClient) MoveEntry(fileID, newParentID, newName, oldParentID string) error {
	_, err := c.service.Files.Update(fileID, &drive.File{Name: newName}).
		AddParents(newParentID).
		RemoveParents(oldParentID).
		Fields("id,parents").
		SupportsAllDrives(false).
		Do()
	return apiError(err)
}

func (c *DriveClient) RenameEntry(fileID, newName string) error {
	_, err := c.service.Files.Update(fileID, &drive.File{Name: newName}).Fields("id").SupportsAllDrives(false).Do()
	return apiError(err)
}

func (c *DriveClient) DeleteEntry(fileID string) error {
	return apiError(c.service.Files.Delete(fileID).SupportsAllDrives(false).Do())
}

func downloadTargetPath(entry NavEntry, targetPath string) string {
	export, ok := ExportMimeTypes[entry.MimeType]
	if !ok {
		return targetPath
	}
	if strings.EqualFold(filepath.Ext(targetPath), export.Suffix) {
		return targetPath
	}
	ext := filepath.Ext(targetPath)
	if ext != "" {
		return strings.TrimSuffix(targetPath, ext) + export.Suffix
	}
	return targetPath + export.Suffix
}

func escapeQuery(value string) string {
	value = strings.ReplaceAll(value, "\\", "\\\\")
	return strings.ReplaceAll(value, "'", "\\'")
}

func apiError(err error) error {
	if err == nil {
		return nil
	}
	if googleErr, ok := err.(*googleapi.Error); ok {
		if googleErr.Code == 403 && strings.Contains(googleErr.Body, "accessNotConfigured") {
			return fmt.Errorf("google drive api is disabled for this oauth project. enable Drive API in Google Cloud Console for this client id, wait a few minutes, then retry")
		}
		return fmt.Errorf("google drive api error (%d): %s", googleErr.Code, googleErr.Body)
	}
	return err
}

type httpResponse struct {
	Body io.ReadCloser
}

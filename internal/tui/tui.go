package tui

import (
	"fmt"
	"path/filepath"
	"strings"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"
	"github.com/ryangerardwilson/gdrive/internal/driveapi"
	"github.com/ryangerardwilson/gdrive/internal/transfer"
)

type Mode string

const (
	ModeBrowse Mode = "browse"
	ModeUpload Mode = "upload"
)

type Result struct {
	UploadSummary *transfer.UploadSummary
	UploadTarget  string
}

type model struct {
	client      driveapi.Client
	mode        Mode
	downloadDir string
	uploadPaths []string
	parentStack []stackItem
	parentID    string
	pathLabel   string
	entries     []driveapi.NavEntry
	cursor      int
	status      string
	result      Result
	err         error
	width       int
	height      int
}

type stackItem struct {
	ID    string
	Label string
}

type loadMsg struct {
	parentID string
	entries  []driveapi.NavEntry
	err      error
}

type actionMsg struct {
	status string
	result Result
	err    error
	quit   bool
}

var (
	titleStyle  = lipgloss.NewStyle().Bold(true)
	activeStyle = lipgloss.NewStyle().Bold(true).Foreground(lipgloss.Color("10"))
	mutedStyle  = lipgloss.NewStyle().Foreground(lipgloss.Color("8"))
	errorStyle  = lipgloss.NewStyle().Foreground(lipgloss.Color("9"))
)

func Browse(client driveapi.Client, downloadDir string) (int, error) {
	_, err := run(client, ModeBrowse, downloadDir, nil)
	if err != nil {
		return 1, err
	}
	return 0, nil
}

func Upload(client driveapi.Client, downloadDir string, uploadPaths []string) (Result, int, error) {
	result, err := run(client, ModeUpload, downloadDir, uploadPaths)
	if err != nil {
		return result, 1, err
	}
	return result, 0, nil
}

func run(client driveapi.Client, mode Mode, downloadDir string, uploadPaths []string) (Result, error) {
	m := model{client: client, mode: mode, downloadDir: downloadDir, uploadPaths: uploadPaths, parentID: "root", pathLabel: "My Drive"}
	program := tea.NewProgram(m, tea.WithAltScreen())
	final, err := program.Run()
	if err != nil {
		return Result{}, err
	}
	if m, ok := final.(model); ok {
		return m.result, m.err
	}
	return Result{}, nil
}

func (m model) Init() tea.Cmd {
	return m.load("root")
}

func (m model) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	switch message := msg.(type) {
	case tea.WindowSizeMsg:
		m.width = message.Width
		m.height = message.Height
	case loadMsg:
		if message.err != nil {
			m.err = message.err
			m.status = message.err.Error()
			return m, nil
		}
		m.parentID = message.parentID
		m.entries = message.entries
		m.cursor = 0
	case actionMsg:
		if message.err != nil {
			m.status = message.err.Error()
			return m, nil
		}
		m.status = message.status
		m.result = message.result
		if message.quit {
			return m, tea.Quit
		}
	case tea.KeyMsg:
		switch message.String() {
		case "ctrl+c", "q", "esc":
			return m, tea.Quit
		case "j", "down":
			m.cursor++
		case "k", "up":
			m.cursor--
		case "g", "home":
			m.cursor = 0
		case "G", "end":
			m.cursor = len(m.entries) - 1
		case "h", "left", "backspace":
			if len(m.parentStack) > 0 {
				last := m.parentStack[len(m.parentStack)-1]
				m.parentStack = m.parentStack[:len(m.parentStack)-1]
				m.pathLabel = last.Label
				return m, m.load(last.ID)
			}
		case "l", "right", "enter":
			if len(m.entries) == 0 {
				break
			}
			entry := m.entries[m.clampedCursor()]
			if entry.IsDir() {
				if m.mode == ModeUpload && message.String() == "enter" {
					return m, m.upload(entry)
				}
				m.parentStack = append(m.parentStack, stackItem{ID: m.parentID, Label: m.pathLabel})
				m.pathLabel = strings.Trim(m.pathLabel+"/"+entry.Name, "/")
				return m, m.load(entry.ID)
			}
			if m.mode == ModeUpload {
				return m, m.uploadToParent(entry)
			}
			return m, m.download(entry)
		}
	}
	if len(m.entries) == 0 {
		m.cursor = 0
	} else if m.cursor < 0 {
		m.cursor = 0
	} else if m.cursor >= len(m.entries) {
		m.cursor = len(m.entries) - 1
	}
	return m, nil
}

func (m model) View() string {
	var b strings.Builder
	modeLabel := "browse"
	if m.mode == ModeUpload {
		modeLabel = "upload"
	}
	fmt.Fprintf(&b, "%s\n", titleStyle.Render(fmt.Sprintf("gdrive %s - %s", modeLabel, m.pathLabel)))
	fmt.Fprintf(&b, "%s\n\n", mutedStyle.Render("j/k move  l enter  h up  enter download/upload  q quit"))
	if len(m.entries) == 0 {
		b.WriteString("No files.\n")
	} else {
		visible := m.entries
		for idx, entry := range visible {
			prefix := "  "
			style := lipgloss.NewStyle()
			if idx == m.cursor {
				prefix = "> "
				style = activeStyle
			}
			icon := "file"
			if entry.IsDir() {
				icon = "dir "
			}
			line := fmt.Sprintf("%s%s  %s", prefix, icon, entry.Name)
			b.WriteString(style.Render(truncate(line, m.width)))
			b.WriteByte('\n')
		}
	}
	if m.status != "" {
		style := mutedStyle
		if m.err != nil || strings.Contains(strings.ToLower(m.status), "error") {
			style = errorStyle
		}
		fmt.Fprintf(&b, "\n%s\n", style.Render(m.status))
	}
	return b.String()
}

func (m model) load(parentID string) tea.Cmd {
	return func() tea.Msg {
		entries, err := m.client.ListChildren(parentID)
		return loadMsg{parentID: parentID, entries: entries, err: err}
	}
}

func (m model) download(entry driveapi.NavEntry) tea.Cmd {
	return func() tea.Msg {
		target := filepath.Join(m.downloadDir, entry.Name)
		if entry.IsDir() {
			downloaded, err := transfer.DownloadDirectoryAsZip(m.client, entry, target)
			if err != nil {
				return actionMsg{err: err}
			}
			return actionMsg{status: "downloaded " + downloaded}
		}
		downloaded, err := m.client.DownloadEntry(entry, target)
		if err != nil {
			return actionMsg{err: err}
		}
		return actionMsg{status: "downloaded " + downloaded}
	}
}

func (m model) upload(entry driveapi.NavEntry) tea.Cmd {
	return func() tea.Msg {
		summary, err := transfer.UploadLocalPaths(m.client, entry.ID, m.uploadPaths)
		if err != nil {
			return actionMsg{err: err}
		}
		return actionMsg{status: "uploaded", result: Result{UploadSummary: &summary, UploadTarget: m.pathLabel + "/" + entry.Name}, quit: true}
	}
}

func (m model) uploadToParent(entry driveapi.NavEntry) tea.Cmd {
	return func() tea.Msg {
		summary, err := transfer.UploadLocalPaths(m.client, entry.ParentID, m.uploadPaths)
		if err != nil {
			return actionMsg{err: err}
		}
		return actionMsg{status: "uploaded", result: Result{UploadSummary: &summary, UploadTarget: m.pathLabel}, quit: true}
	}
}

func (m model) clampedCursor() int {
	if len(m.entries) == 0 {
		return 0
	}
	if m.cursor < 0 {
		return 0
	}
	if m.cursor >= len(m.entries) {
		return len(m.entries) - 1
	}
	return m.cursor
}

func truncate(value string, width int) string {
	if width <= 4 || len(value) <= width {
		return value
	}
	return value[:width-3] + "..."
}

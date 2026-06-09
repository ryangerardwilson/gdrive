package syncer

import (
	"os"
	"path/filepath"
	"testing"

	"github.com/ryangerardwilson/gdrive/internal/config"
	"github.com/ryangerardwilson/gdrive/internal/driveapi"
)

type fakeDrive struct {
	deleted  []string
	uploaded []string
	updated  []string
}

func (f *fakeDrive) EnsureDrivePath(path string) (string, error) { return "root-id", nil }
func (f *fakeDrive) ListChildren(parentID string) ([]driveapi.NavEntry, error) {
	return nil, nil
}
func (f *fakeDrive) GetEntry(fileID string) (driveapi.NavEntry, error) {
	return driveapi.NavEntry{}, nil
}
func (f *fakeDrive) FindChild(parentID, name, mimeType string) (*driveapi.NavEntry, error) {
	return nil, nil
}
func (f *fakeDrive) CreateFolder(parentID, name string) (string, error) {
	f.uploaded = append(f.uploaded, "dir:"+name)
	return "created-" + name, nil
}
func (f *fakeDrive) FindAvailableName(parentID, name string) (string, error) { return name, nil }
func (f *fakeDrive) DownloadEntry(entry driveapi.NavEntry, targetPath string) (string, error) {
	if err := os.MkdirAll(filepath.Dir(targetPath), 0o755); err != nil {
		return "", err
	}
	return targetPath, os.WriteFile(targetPath, []byte("downloaded "+entry.Name), 0o644)
}
func (f *fakeDrive) ListTree(rootID string) (map[string]driveapi.RemoteEntry, error) {
	return map[string]driveapi.RemoteEntry{
		"Album":          {ID: "dir-1", RelPath: "Album", Name: "Album", ParentID: "root-id", MimeType: driveapi.FolderMime},
		"Album/song.mp3": {ID: "file-1", RelPath: "Album/song.mp3", Name: "song.mp3", ParentID: "dir-1", MimeType: "audio/mpeg"},
		"book.pdf":       {ID: "file-2", RelPath: "book.pdf", Name: "book.pdf", ParentID: "root-id", MimeType: "application/pdf"},
	}, nil
}
func (f *fakeDrive) UploadFile(parentID, name, filePath string) (string, error) {
	f.uploaded = append(f.uploaded, "file:"+name)
	return "uploaded-" + name, nil
}
func (f *fakeDrive) UpdateFile(fileID, filePath string) error {
	f.updated = append(f.updated, fileID)
	return nil
}
func (f *fakeDrive) MoveEntry(fileID, newParentID, newName, oldParentID string) error {
	f.updated = append(f.updated, "move:"+fileID+":"+newName)
	return nil
}
func (f *fakeDrive) RenameEntry(fileID, newName string) error {
	f.updated = append(f.updated, "rename:"+fileID+":"+newName)
	return nil
}
func (f *fakeDrive) DeleteEntry(fileID string) error {
	f.deleted = append(f.deleted, fileID)
	return nil
}

func TestParentRelPath(t *testing.T) {
	if ParentRelPath("a/b/c.txt") != "a/b" {
		t.Fatal("unexpected parent")
	}
	if ParentRelPath("file.txt") != "" {
		t.Fatal("unexpected root parent")
	}
}

func TestBuildRenameMap(t *testing.T) {
	old := map[string]StateEntry{"old.txt": {RelPath: "old.txt", Kind: "file", DriveID: "id-1", Size: 5, SHA1: "abc"}}
	newEntries := map[string]LocalEntry{"new.txt": {RelPath: "new.txt", Kind: "file", Size: 5, SHA1: "abc"}}
	got := BuildRenameMap(old, newEntries)
	if got["old.txt"] != "new.txt" {
		t.Fatalf("rename map = %#v", got)
	}
}

func TestRestoreSeedsStateWithoutDeleting(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("XDG_DATA_HOME", filepath.Join(home, "data"))
	root := filepath.Join(home, "Music")
	reg := config.Registration{ID: "1", LocalDir: root, DrivePath: "Music", Enabled: true}
	drive := &fakeDrive{}
	summary, err := RestoreRegistrationFromRemote("1", &reg, drive, "Backups")
	if err != nil {
		t.Fatal(err)
	}
	state, err := LoadState("1", "1")
	if err != nil {
		t.Fatal(err)
	}
	if summary.Downloaded != 2 || summary.DirsCreated != 1 || len(drive.deleted) != 0 {
		t.Fatalf("summary=%#v deleted=%#v", summary, drive.deleted)
	}
	if _, ok := state["Album/song.mp3"]; !ok {
		t.Fatalf("state missing song: %#v", state)
	}
}

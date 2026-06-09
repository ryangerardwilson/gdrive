package transfer

import (
	"archive/zip"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sort"

	"github.com/ryangerardwilson/gdrive/internal/driveapi"
	"github.com/ryangerardwilson/gdrive/internal/paths"
)

type UploadSummary struct {
	FilesUploaded int
	DirsCreated   int
}

func NormalizeUploadPaths(values []string) ([]string, error) {
	if len(values) == 0 {
		return nil, fmt.Errorf("usage: gdrive <preset> upload <file_path> <file_path> ...")
	}
	result := []string{}
	for _, value := range values {
		path := paths.ExpandHome(value)
		info, err := os.Stat(path)
		if err != nil {
			return nil, fmt.Errorf("missing local path: %s", path)
		}
		if !info.IsDir() && !info.Mode().IsRegular() {
			return nil, fmt.Errorf("unsupported local path: %s", path)
		}
		abs, err := filepath.Abs(path)
		if err != nil {
			return nil, err
		}
		result = append(result, abs)
	}
	return result, nil
}

func UploadLocalPaths(client driveapi.Client, parentID string, localPaths []string) (UploadSummary, error) {
	summary := UploadSummary{}
	for _, path := range localPaths {
		if err := uploadLocalPath(client, parentID, path, &summary); err != nil {
			return summary, err
		}
	}
	return summary, nil
}

func DownloadDirectoryAsZip(client driveapi.Client, entry driveapi.NavEntry, targetPath string) (string, error) {
	tmp, err := os.MkdirTemp("", "gdrive-dir-zip-*")
	if err != nil {
		return "", err
	}
	defer os.RemoveAll(tmp)
	localRoot := filepath.Join(tmp, entry.Name)
	if err := os.MkdirAll(localRoot, 0o755); err != nil {
		return "", err
	}
	remoteEntries, err := client.ListTree(entry.ID)
	if err != nil {
		return "", err
	}
	keys := make([]string, 0, len(remoteEntries))
	for key := range remoteEntries {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	for _, relpath := range keys {
		remote := remoteEntries[relpath]
		localPath := filepath.Join(localRoot, filepath.FromSlash(remote.RelPath))
		if remote.IsDir() {
			if err := os.MkdirAll(localPath, 0o755); err != nil {
				return "", err
			}
			continue
		}
		if _, err := client.DownloadEntry(driveapi.NavEntry{ID: remote.ID, Name: remote.Name, MimeType: remote.MimeType, ParentID: remote.ParentID}, localPath); err != nil {
			return "", err
		}
	}
	return zipDirectory(localRoot, targetPath)
}

func uploadLocalPath(client driveapi.Client, parentID, localPath string, summary *UploadSummary) error {
	name, err := client.FindAvailableName(parentID, filepath.Base(localPath))
	if err != nil {
		return err
	}
	info, err := os.Stat(localPath)
	if err != nil {
		return err
	}
	if info.IsDir() {
		folderID, err := client.CreateFolder(parentID, name)
		if err != nil {
			return err
		}
		summary.DirsCreated++
		children, err := os.ReadDir(localPath)
		if err != nil {
			return err
		}
		sort.SliceStable(children, func(i, j int) bool {
			if children[i].IsDir() != children[j].IsDir() {
				return children[i].IsDir()
			}
			return children[i].Name() < children[j].Name()
		})
		for _, child := range children {
			if err := uploadLocalPath(client, folderID, filepath.Join(localPath, child.Name()), summary); err != nil {
				return err
			}
		}
		return nil
	}
	if _, err := client.UploadFile(parentID, name, localPath); err != nil {
		return err
	}
	summary.FilesUploaded++
	return nil
}

func zipDirectory(root, targetPath string) (string, error) {
	if filepath.Ext(targetPath) != ".zip" {
		targetPath += ".zip"
	}
	if err := os.MkdirAll(filepath.Dir(targetPath), 0o755); err != nil {
		return "", err
	}
	out, err := os.Create(targetPath)
	if err != nil {
		return "", err
	}
	defer out.Close()
	writer := zip.NewWriter(out)
	defer writer.Close()
	return targetPath, filepath.WalkDir(root, func(path string, d os.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if path == root {
			return nil
		}
		rel, err := filepath.Rel(filepath.Dir(root), path)
		if err != nil {
			return err
		}
		rel = filepath.ToSlash(rel)
		if d.IsDir() {
			_, err := writer.Create(rel + "/")
			return err
		}
		info, err := d.Info()
		if err != nil {
			return err
		}
		header, err := zip.FileInfoHeader(info)
		if err != nil {
			return err
		}
		header.Name = rel
		header.Method = zip.Deflate
		part, err := writer.CreateHeader(header)
		if err != nil {
			return err
		}
		file, err := os.Open(path)
		if err != nil {
			return err
		}
		defer file.Close()
		_, err = io.Copy(part, file)
		return err
	})
}

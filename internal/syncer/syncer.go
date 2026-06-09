package syncer

import (
	"crypto/sha1"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sort"
	"strings"

	"github.com/ryangerardwilson/gdrive/internal/config"
	"github.com/ryangerardwilson/gdrive/internal/driveapi"
	"github.com/ryangerardwilson/gdrive/internal/paths"
)

type LocalEntry struct {
	RelPath string `json:"relpath"`
	Kind    string `json:"kind"`
	Size    int64  `json:"size"`
	MTimeNS int64  `json:"mtime_ns"`
	SHA1    string `json:"sha1,omitempty"`
}

type StateEntry struct {
	RelPath       string `json:"relpath"`
	Kind          string `json:"kind"`
	DriveID       string `json:"drive_id"`
	ParentRelPath string `json:"parent_relpath"`
	Size          int64  `json:"size"`
	MTimeNS       int64  `json:"mtime_ns"`
	SHA1          string `json:"sha1,omitempty"`
}

type SyncSummary struct {
	Created int
	Updated int
	Moved   int
	Deleted int
}

type RestoreSummary struct {
	Downloaded      int
	DirsCreated     int
	SkippedExisting int
	StateEntries    int
}

type statePayload struct {
	Entries []StateEntry `json:"entries"`
}

func StateFile(preset, regID string) string {
	_ = paths.EnsureDirs()
	path := filepath.Join(paths.StateDir(), preset+"-"+regID+".json")
	legacy := filepath.Join(paths.StateDir(), regID+".json")
	if preset == "1" {
		if _, err := os.Stat(path); os.IsNotExist(err) {
			if _, legacyErr := os.Stat(legacy); legacyErr == nil {
				_ = os.Rename(legacy, path)
			}
		}
	}
	return path
}

func LoadState(preset, regID string) (map[string]StateEntry, error) {
	path := StateFile(preset, regID)
	if _, err := os.Stat(path); os.IsNotExist(err) {
		return map[string]StateEntry{}, nil
	}
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	payload := statePayload{}
	if err := json.Unmarshal(data, &payload); err != nil {
		return nil, err
	}
	result := map[string]StateEntry{}
	for _, entry := range payload.Entries {
		result[entry.RelPath] = entry
	}
	return result, nil
}

func SaveState(preset, regID string, entries map[string]StateEntry) error {
	path := StateFile(preset, regID)
	keys := sortedMapKeys(entries)
	payload := statePayload{Entries: make([]StateEntry, 0, len(keys))}
	for _, key := range keys {
		payload.Entries = append(payload.Entries, entries[key])
	}
	data, err := json.MarshalIndent(payload, "", "  ")
	if err != nil {
		return err
	}
	data = append(data, '\n')
	return os.WriteFile(path, data, 0o600)
}

func DeleteState(preset, regID string) error {
	path := StateFile(preset, regID)
	if _, err := os.Stat(path); os.IsNotExist(err) {
		return nil
	}
	return os.Remove(path)
}

func SHA1File(path string) (string, error) {
	file, err := os.Open(path)
	if err != nil {
		return "", err
	}
	defer file.Close()
	digest := sha1.New()
	if _, err := io.Copy(digest, file); err != nil {
		return "", err
	}
	return hex.EncodeToString(digest.Sum(nil)), nil
}

func ParentRelPath(relpath string) string {
	if !strings.Contains(relpath, "/") {
		return ""
	}
	return relpath[:strings.LastIndex(relpath, "/")]
}

func ScanLocalTree(root string, previous map[string]StateEntry) (map[string]LocalEntry, error) {
	result := map[string]LocalEntry{}
	err := filepath.WalkDir(root, func(path string, d os.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if path == root {
			return nil
		}
		info, err := d.Info()
		if err != nil {
			return err
		}
		if info.Mode()&os.ModeSymlink != 0 {
			if d.IsDir() {
				return filepath.SkipDir
			}
			return nil
		}
		rel, err := filepath.Rel(root, path)
		if err != nil {
			return err
		}
		rel = filepath.ToSlash(rel)
		if d.IsDir() {
			result[rel] = LocalEntry{RelPath: rel, Kind: "dir", MTimeNS: info.ModTime().UnixNano()}
			return nil
		}
		old := previous[rel]
		hash := ""
		if old.Kind == "file" && old.Size == info.Size() && old.MTimeNS == info.ModTime().UnixNano() {
			hash = old.SHA1
		}
		if hash == "" {
			var err error
			hash, err = SHA1File(path)
			if err != nil {
				return err
			}
		}
		result[rel] = LocalEntry{RelPath: rel, Kind: "file", Size: info.Size(), MTimeNS: info.ModTime().UnixNano(), SHA1: hash}
		return nil
	})
	return result, err
}

func BuildRenameMap(oldEntries map[string]StateEntry, newEntries map[string]LocalEntry) map[string]string {
	type sig struct {
		Size int64
		SHA1 string
	}
	bySig := map[sig][]LocalEntry{}
	for path, entry := range newEntries {
		if _, ok := oldEntries[path]; ok || entry.Kind != "file" {
			continue
		}
		key := sig{Size: entry.Size, SHA1: entry.SHA1}
		bySig[key] = append(bySig[key], entry)
	}
	renameMap := map[string]string{}
	for path, entry := range oldEntries {
		if _, ok := newEntries[path]; ok || entry.Kind != "file" {
			continue
		}
		key := sig{Size: entry.Size, SHA1: entry.SHA1}
		bucket := bySig[key]
		if len(bucket) == 0 {
			continue
		}
		renameMap[path] = bucket[0].RelPath
		bySig[key] = bucket[1:]
	}
	return renameMap
}

func SyncRegistration(preset string, registration *config.Registration, drive driveapi.Client, backupRootName string) (SyncSummary, error) {
	localRoot := registration.LocalDir
	info, err := os.Stat(localRoot)
	if err != nil || !info.IsDir() {
		return SyncSummary{}, fmt.Errorf("missing local dir: %s", localRoot)
	}
	previous, err := LoadState(preset, registration.ID)
	if err != nil {
		return SyncSummary{}, err
	}
	localEntries, err := ScanLocalTree(localRoot, previous)
	if err != nil {
		return SyncSummary{}, err
	}
	remoteRootID := registration.RemoteRootID
	if remoteRootID == "" {
		remoteRootID, err = drive.EnsureDrivePath(backupRootName + "/" + registration.DrivePath)
		if err != nil {
			return SyncSummary{}, err
		}
		registration.RemoteRootID = remoteRootID
	}
	remoteEntries, err := drive.ListTree(remoteRootID)
	if err != nil {
		return SyncSummary{}, err
	}
	summary := SyncSummary{}
	dirIDs := map[string]string{"": remoteRootID}
	for relpath, entry := range remoteEntries {
		if entry.IsDir() {
			dirIDs[relpath] = entry.ID
		}
	}
	for _, relpath := range sortPathsDeep(filterLocalKind(localEntries, "dir"), false) {
		if existing, ok := remoteEntries[relpath]; ok && existing.IsDir() {
			dirIDs[relpath] = existing.ID
			continue
		}
		parentID := dirIDs[ParentRelPath(relpath)]
		driveID, err := drive.CreateFolder(parentID, filepath.Base(relpath))
		if err != nil {
			return SyncSummary{}, err
		}
		dirIDs[relpath] = driveID
		summary.Created++
	}

	renameMap := BuildRenameMap(previous, localEntries)
	currentState := map[string]StateEntry{}
	removedFilePaths := map[string]bool{}
	for path, entry := range previous {
		if entry.Kind == "file" {
			if _, exists := localEntries[path]; !exists {
				if _, renamed := renameMap[path]; !renamed {
					removedFilePaths[path] = true
				}
			}
		}
	}
	for oldPath, newPath := range renameMap {
		oldState := previous[oldPath]
		localEntry := localEntries[newPath]
		parentID := dirIDs[ParentRelPath(newPath)]
		oldParentID := remoteRootID
		if oldState.ParentRelPath != "" {
			if id := dirIDs[oldState.ParentRelPath]; id != "" {
				oldParentID = id
			} else if parentState, ok := previous[oldState.ParentRelPath]; ok {
				oldParentID = parentState.DriveID
			}
		}
		if err := drive.MoveEntry(oldState.DriveID, parentID, filepath.Base(newPath), oldParentID); err != nil {
			return SyncSummary{}, err
		}
		summary.Moved++
		currentState[newPath] = StateEntry{RelPath: newPath, Kind: "file", DriveID: oldState.DriveID, ParentRelPath: ParentRelPath(newPath), Size: localEntry.Size, MTimeNS: localEntry.MTimeNS, SHA1: localEntry.SHA1}
	}

	for _, relpath := range sortPathsDeep(filterLocalKind(localEntries, "file"), false) {
		if _, ok := currentState[relpath]; ok {
			continue
		}
		localEntry := localEntries[relpath]
		existingRemote, hasExisting := remoteEntries[relpath]
		previousEntry, hadPrevious := previous[relpath]
		parentID := dirIDs[ParentRelPath(relpath)]
		if hasExisting && existingRemote.IsDir() {
			if err := drive.DeleteEntry(existingRemote.ID); err != nil {
				return SyncSummary{}, err
			}
			summary.Deleted++
			hasExisting = false
		}
		driveID := ""
		if hadPrevious && previousEntry.Kind == "file" {
			if hasExisting && !existingRemote.IsDir() {
				driveID = existingRemote.ID
			}
			if driveID == "" {
				var err error
				driveID, err = drive.UploadFile(parentID, filepath.Base(relpath), filepath.Join(localRoot, filepath.FromSlash(relpath)))
				if err != nil {
					return SyncSummary{}, err
				}
				summary.Created++
			} else if driveID != previousEntry.DriveID || localEntry.Size != previousEntry.Size || localEntry.SHA1 != previousEntry.SHA1 {
				if err := drive.UpdateFile(driveID, filepath.Join(localRoot, filepath.FromSlash(relpath))); err != nil {
					return SyncSummary{}, err
				}
				summary.Updated++
			}
			if previousEntry.ParentRelPath != ParentRelPath(relpath) {
				oldParentID := remoteRootID
				if previousEntry.ParentRelPath != "" {
					oldParentID = dirIDs[previousEntry.ParentRelPath]
				}
				if err := drive.MoveEntry(driveID, parentID, filepath.Base(relpath), oldParentID); err != nil {
					return SyncSummary{}, err
				}
				summary.Moved++
			} else if filepath.Base(relpath) != filepath.Base(previousEntry.RelPath) {
				if err := drive.RenameEntry(driveID, filepath.Base(relpath)); err != nil {
					return SyncSummary{}, err
				}
				summary.Moved++
			}
		} else if hasExisting && !existingRemote.IsDir() {
			driveID = existingRemote.ID
			if err := drive.UpdateFile(driveID, filepath.Join(localRoot, filepath.FromSlash(relpath))); err != nil {
				return SyncSummary{}, err
			}
			summary.Updated++
		} else {
			var err error
			driveID, err = drive.UploadFile(parentID, filepath.Base(relpath), filepath.Join(localRoot, filepath.FromSlash(relpath)))
			if err != nil {
				return SyncSummary{}, err
			}
			summary.Created++
		}
		currentState[relpath] = StateEntry{RelPath: relpath, Kind: "file", DriveID: driveID, ParentRelPath: ParentRelPath(relpath), Size: localEntry.Size, MTimeNS: localEntry.MTimeNS, SHA1: localEntry.SHA1}
	}

	for _, relpath := range sortPathsDeep(mapKeys(removedFilePaths), true) {
		if err := drive.DeleteEntry(previous[relpath].DriveID); err != nil {
			return SyncSummary{}, err
		}
		summary.Deleted++
	}
	desiredPaths := map[string]bool{}
	for path := range localEntries {
		desiredPaths[path] = true
	}
	remoteExtras := []string{}
	for path := range remoteEntries {
		if !desiredPaths[path] {
			if _, existedBefore := previous[path]; !existedBefore {
				remoteExtras = append(remoteExtras, path)
			}
		}
	}
	for _, relpath := range sortPathsDeep(remoteExtras, true) {
		if err := drive.DeleteEntry(remoteEntries[relpath].ID); err != nil {
			return SyncSummary{}, err
		}
		summary.Deleted++
	}
	for _, relpath := range sortPathsDeep(filterLocalKind(localEntries, "dir"), false) {
		localEntry := localEntries[relpath]
		currentState[relpath] = StateEntry{RelPath: relpath, Kind: "dir", DriveID: dirIDs[relpath], ParentRelPath: ParentRelPath(relpath), MTimeNS: localEntry.MTimeNS}
	}
	removedDirs := []string{}
	for path, entry := range previous {
		if entry.Kind == "dir" {
			if _, exists := localEntries[path]; !exists {
				removedDirs = append(removedDirs, path)
			}
		}
	}
	for _, relpath := range sortPathsDeep(removedDirs, true) {
		if err := drive.DeleteEntry(previous[relpath].DriveID); err != nil {
			return SyncSummary{}, err
		}
		summary.Deleted++
	}
	if err := SaveState(preset, registration.ID, currentState); err != nil {
		return SyncSummary{}, err
	}
	return summary, nil
}

func RestoreRegistrationFromRemote(preset string, registration *config.Registration, drive driveapi.Client, backupRootName string) (RestoreSummary, error) {
	localRoot := registration.LocalDir
	if err := os.MkdirAll(localRoot, 0o755); err != nil {
		return RestoreSummary{}, err
	}
	remoteRootID := registration.RemoteRootID
	var err error
	if remoteRootID == "" {
		remoteRootID, err = drive.EnsureDrivePath(backupRootName + "/" + registration.DrivePath)
		if err != nil {
			return RestoreSummary{}, err
		}
		registration.RemoteRootID = remoteRootID
	}
	remoteEntries, err := drive.ListTree(remoteRootID)
	if err != nil {
		return RestoreSummary{}, err
	}
	summary := RestoreSummary{}
	currentState := map[string]StateEntry{}
	for _, relpath := range sortPathsDeep(filterRemoteKind(remoteEntries, true), false) {
		target := filepath.Join(localRoot, filepath.FromSlash(relpath))
		if _, err := os.Stat(target); os.IsNotExist(err) {
			if err := os.MkdirAll(target, 0o755); err != nil {
				return RestoreSummary{}, err
			}
			summary.DirsCreated++
		}
		info, err := os.Stat(target)
		if err != nil {
			return RestoreSummary{}, err
		}
		if !info.IsDir() {
			return RestoreSummary{}, fmt.Errorf("cannot restore directory over file: %s", target)
		}
		currentState[relpath] = StateEntry{RelPath: relpath, Kind: "dir", DriveID: remoteEntries[relpath].ID, ParentRelPath: ParentRelPath(relpath), MTimeNS: info.ModTime().UnixNano()}
	}
	for _, relpath := range sortPathsDeep(filterRemoteKind(remoteEntries, false), false) {
		entry := remoteEntries[relpath]
		target := filepath.Join(localRoot, filepath.FromSlash(relpath))
		if info, err := os.Stat(target); err == nil {
			if info.IsDir() {
				return RestoreSummary{}, fmt.Errorf("cannot restore file over directory: %s", target)
			}
			summary.SkippedExisting++
			continue
		}
		downloaded, err := drive.DownloadEntry(driveapi.NavEntry{ID: entry.ID, Name: entry.Name, MimeType: entry.MimeType, ParentID: entry.ParentID}, target)
		if err != nil {
			return RestoreSummary{}, err
		}
		rel, err := filepath.Rel(localRoot, downloaded)
		if err != nil || strings.HasPrefix(rel, "..") {
			return RestoreSummary{}, fmt.Errorf("download escaped local root: %s", downloaded)
		}
		rel = filepath.ToSlash(rel)
		info, err := os.Stat(downloaded)
		if err != nil {
			return RestoreSummary{}, err
		}
		hash, err := SHA1File(downloaded)
		if err != nil {
			return RestoreSummary{}, err
		}
		currentState[rel] = StateEntry{RelPath: rel, Kind: "file", DriveID: entry.ID, ParentRelPath: ParentRelPath(rel), Size: info.Size(), MTimeNS: info.ModTime().UnixNano(), SHA1: hash}
		summary.Downloaded++
	}
	summary.StateEntries = len(currentState)
	if err := SaveState(preset, registration.ID, currentState); err != nil {
		return RestoreSummary{}, err
	}
	return summary, nil
}

func filterLocalKind(entries map[string]LocalEntry, kind string) []string {
	paths := []string{}
	for path, entry := range entries {
		if entry.Kind == kind {
			paths = append(paths, path)
		}
	}
	return paths
}

func filterRemoteKind(entries map[string]driveapi.RemoteEntry, dirs bool) []string {
	paths := []string{}
	for path, entry := range entries {
		if entry.IsDir() == dirs {
			paths = append(paths, path)
		}
	}
	return paths
}

func sortPathsDeep(paths []string, reverse bool) []string {
	out := append([]string(nil), paths...)
	sort.SliceStable(out, func(i, j int) bool {
		leftDepth := strings.Count(out[i], "/")
		rightDepth := strings.Count(out[j], "/")
		if leftDepth != rightDepth {
			if reverse {
				return leftDepth > rightDepth
			}
			return leftDepth < rightDepth
		}
		if reverse {
			return out[i] > out[j]
		}
		return out[i] < out[j]
	})
	return out
}

func sortedMapKeys(entries map[string]StateEntry) []string {
	keys := make([]string, 0, len(entries))
	for key := range entries {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	return keys
}

func mapKeys(values map[string]bool) []string {
	keys := make([]string, 0, len(values))
	for key := range values {
		keys = append(keys, key)
	}
	return keys
}

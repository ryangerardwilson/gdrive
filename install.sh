#!/usr/bin/env bash
set -euo pipefail

APP=gdrive
REPO="ryangerardwilson/gdrive"
APP_HOME="$HOME/.${APP}"
INSTALL_DIR="$APP_HOME/bin"
APP_DIR="$APP_HOME/app"
FILENAME="${APP}-linux-x64.tar.gz"

MUTED='\033[0;2m'
RED='\033[0;31m'
NC='\033[0m'

usage() {
  cat <<EOF
${APP^^} Installer

Usage: install.sh [options]

Options:
  -h                         Show this help and exit
  -v [<version>]             Install a specific release (e.g., 0.1.0 or v0.1.0)
                             Without an argument, print the latest release version and exit
  -u                         Reinstall the latest release if it is newer (upgrade)
  -b, --binary <path>        Install from a local binary bundle
      --no-modify-path       Skip editing shell rc files
EOF
}

info() { echo -e "${MUTED}$1${NC}"; }
die() { echo -e "${RED}$1${NC}" >&2; exit 1; }

requested_version=${VERSION:-}
binary_path=""
no_modify_path=false
show_latest=false
upgrade=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    -v)
      if [[ -n "${2:-}" && "${2:0:1}" != "-" ]]; then
        requested_version="$2"
        shift 2
      else
        show_latest=true
        shift
      fi
      ;;
    -u|--upgrade)
      upgrade=true
      shift
      ;;
    -b|--binary)
      [[ -n "${2:-}" ]] || die "--binary requires a path"
      binary_path="$2"
      shift 2
      ;;
    --no-modify-path)
      no_modify_path=true
      shift
      ;;
    --version)
      info "--version is deprecated. Use -v instead."
      if [[ -n "${2:-}" && "${2:0:1}" != "-" ]]; then
        requested_version="$2"
        shift 2
      else
        show_latest=true
        shift
      fi
      ;;
    --help)
      info "--help is deprecated. Use -h instead."
      usage
      exit 0
      ;;
    *)
      info "Unknown option $1"
      shift
      ;;
  esac
done

_latest_version=""
latest_version_from_redirect() {
  command -v curl >/dev/null 2>&1 || die "'curl' is required"
  curl -fsSL -o /dev/null -w '%{url_effective}' "https://github.com/${REPO}/releases/latest" \
    | sed -n 's#.*/releases/tag/v\([^/?#[:space:]]*\).*#\1#p'
}
get_latest_version() {
  if [[ -z "${_latest_version}" ]]; then
    if command -v gh >/dev/null 2>&1; then
      _latest_version=$(gh release view --repo "${REPO}" --json tagName --jq '.tagName' 2>/dev/null || true)
      _latest_version="${_latest_version#v}"
    fi
    if [[ -z "${_latest_version}" ]]; then
      _latest_version="$(latest_version_from_redirect || true)"
    fi
    [[ -n "${_latest_version}" ]] || die "Unable to determine latest release"
  fi
  printf '%s\n' "${_latest_version}"
}

if $show_latest; then
  [[ "$upgrade" == false && -z "$binary_path" && -z "$requested_version" ]] || \
    die "-v (no arg) cannot be combined with other options"
  get_latest_version
  exit 0
fi

if $upgrade; then
  [[ -z "$binary_path" ]] || die "-u cannot be used with -b/--binary"
  [[ -z "$requested_version" ]] || die "-u cannot be combined with -v"
  latest=$(get_latest_version)
  if command -v "$APP" >/dev/null 2>&1; then
    installed=$($APP -v 2>/dev/null || true)
    installed="${installed#v}"
    if [[ -n "$installed" && "$installed" == "$latest" ]]; then
      info "${APP} ${latest} already installed"
      exit 0
    fi
  fi
  requested_version="$latest"
fi

mkdir -p "$INSTALL_DIR"

if [[ -n "$binary_path" ]]; then
  [[ -f "$binary_path" ]] || die "Binary not found: $binary_path"
  info "Installing ${APP^^} from local binary"
  mkdir -p "$APP_DIR"
  cp "$binary_path" "$INSTALL_DIR/$APP"
  chmod 755 "$INSTALL_DIR/$APP"
  installed_label="local"
else
  raw_os=$(uname -s)
  arch=$(uname -m)
  [[ "$raw_os" == "Linux" ]] || die "Unsupported OS: $raw_os"
  [[ "$arch" == "x86_64" ]] || die "Unsupported arch: $arch"
  command -v curl >/dev/null 2>&1 || die "'curl' is required"
  command -v tar >/dev/null 2>&1 || die "'tar' is required"

  if [[ -z "$requested_version" ]]; then
    version_label=$(get_latest_version)
    url="https://github.com/${REPO}/releases/latest/download/${FILENAME}"
  else
    requested_version="${requested_version#v}"
    version_label="$requested_version"
    url="https://github.com/${REPO}/releases/download/v${requested_version}/${FILENAME}"
    http_status=$(curl -sI -o /dev/null -w "%{http_code}" \
      "https://github.com/${REPO}/releases/tag/v${requested_version}")
    [[ "$http_status" != "404" ]] || die "Release v${requested_version} not found"
  fi

  if command -v "$APP" >/dev/null 2>&1 && [[ "$version_label" != "latest" ]]; then
    installed=$($APP -v 2>/dev/null || true)
    installed="${installed#v}"
    if [[ -n "$installed" && "$installed" == "$version_label" ]]; then
      info "${APP} ${version_label} already installed"
      exit 0
    fi
  fi

  info "Installing ${APP^^} version ${version_label}"
  tmp_dir=$(mktemp -d "${TMPDIR:-/tmp}/${APP}.XXXXXX")
  curl -# -L -o "$tmp_dir/$FILENAME" "$url"
  tar -xzf "$tmp_dir/$FILENAME" -C "$tmp_dir"
  [[ -f "$tmp_dir/${APP}/${APP}" ]] || die "Archive missing ${APP}/${APP}"
  rm -rf "$APP_DIR"
  mkdir -p "$APP_DIR"
  mv "$tmp_dir/${APP}" "$APP_DIR"
  rm -rf "$tmp_dir"

  cat > "$INSTALL_DIR/$APP" <<EOF
#!/usr/bin/env bash
set -euo pipefail
"${HOME}/.${APP}/app/${APP}/${APP}" "\$@"
EOF
  chmod 755 "$INSTALL_DIR/$APP"
  installed_label="$version_label"
fi

maybe_add_path() {
  local command=$1
  local rc_files=()
  local shell_name
  shell_name=$(basename "${SHELL:-bash}")
  case "$shell_name" in
    zsh) rc_files=("$HOME/.zshrc" "$HOME/.zshenv" "$HOME/.config/zsh/.zshrc") ;;
    bash) rc_files=("$HOME/.bashrc" "$HOME/.bash_profile" "$HOME/.profile") ;;
    fish) rc_files=("$HOME/.config/fish/config.fish") ;;
    *) rc_files=("$HOME/.profile") ;;
  esac
  for rc in "${rc_files[@]}"; do
    [[ -w "$rc" ]] || continue
    if grep -Fq "$command" "$rc" 2>/dev/null; then
      return
    fi
    printf '\n# %s\n%s\n' "${APP^^}" "$command" >> "$rc"
    info "Added ${APP^^} to PATH in $rc"
    return
  done
  info "Add to PATH manually: $command"
}

if ! $no_modify_path && [[ ":$PATH:" != *":$INSTALL_DIR:"* ]]; then
  if [[ $(basename "${SHELL:-bash}") == "fish" ]]; then
    maybe_add_path "fish_add_path $INSTALL_DIR"
  else
    maybe_add_path "export PATH=$INSTALL_DIR:\$PATH"
  fi
fi

info "Installed ${APP^^} (${installed_label:-unknown}) to $INSTALL_DIR/$APP"
info "Run: ${APP} -h"

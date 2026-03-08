#!/usr/bin/env bash
set -euo pipefail

APP=gdrive
REPO="ryangerardwilson/gdrive"
APP_HOME="$HOME/.${APP}"
INSTALL_DIR="$APP_HOME/bin"
APP_DIR="$APP_HOME/app"

usage() {
  cat <<USAGE
${APP} Installer

Usage: install.sh [options]

Options:
  -h, --help              Display this help message
  -v, --version <version> Install a specific version
  -b, --binary <path>     Install from a local binary instead of downloading
      --no-modify-path    Don't modify shell config files
USAGE
}

requested_version=${VERSION:-}
binary_path=""
no_modify_path=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    -v|--version)
      [[ -n "${2:-}" ]] || { echo "Error: --version requires an argument" >&2; exit 1; }
      requested_version="$2"
      shift 2
      ;;
    -b|--binary)
      [[ -n "${2:-}" ]] || { echo "Error: --binary requires a path" >&2; exit 1; }
      binary_path="$2"
      shift 2
      ;;
    --no-modify-path)
      no_modify_path=true
      shift
      ;;
    *)
      echo "Warning: Unknown option '$1'" >&2
      shift
      ;;
  esac
done

mkdir -p "$INSTALL_DIR"
mkdir -p "$APP_DIR"

if [[ -n "$binary_path" ]]; then
  [[ -f "$binary_path" ]] || { echo "Binary not found: $binary_path" >&2; exit 1; }
  mkdir -p "$APP_DIR/${APP}"
  cp "$binary_path" "$APP_DIR/${APP}/${APP}"
  chmod 755 "$APP_DIR/${APP}/${APP}"
  specific_version="local"
else
  command -v curl >/dev/null 2>&1 || { echo "'curl' is required but not installed." >&2; exit 1; }
  command -v tar >/dev/null 2>&1 || { echo "'tar' is required but not installed." >&2; exit 1; }

  raw_os=$(uname -s)
  arch=$(uname -m)
  [[ "$raw_os" == "Linux" ]] || { echo "Unsupported OS: $raw_os" >&2; exit 1; }
  [[ "$arch" == "x86_64" ]] || { echo "Unsupported arch: $arch" >&2; exit 1; }

  if [[ -z "$requested_version" ]]; then
    release_url_prefix="https://github.com/${REPO}/releases/latest/download"
    specific_version="$(curl -fsSL "https://api.github.com/repos/${REPO}/releases/latest" | sed -n 's/.*"tag_name": *"v\([^\"]*\)".*/\1/p' || true)"
    [[ -n "$specific_version" ]] || specific_version="latest"
  else
    requested_version="${requested_version#v}"
    release_url_prefix="https://github.com/${REPO}/releases/download/v${requested_version}"
    specific_version="$requested_version"
  fi

  if command -v "${APP}" >/dev/null 2>&1; then
    installed_version=$("${APP}" -v 2>/dev/null || true)
    if [[ -n "$installed_version" && "$installed_version" == "$specific_version" ]]; then
      echo "${APP} version ${specific_version} already installed"
      exit 0
    fi
  fi

  tmp_dir="${TMPDIR:-/tmp}/${APP}_install_$$"
  archive_path="${tmp_dir}/${APP}-linux-x64.tar.gz"
  mkdir -p "$tmp_dir"
  curl -fL "${release_url_prefix}/${APP}-linux-x64.tar.gz" -o "$archive_path"
  tar -xzf "$archive_path" -C "$tmp_dir"

  rm -rf "$APP_DIR"
  mkdir -p "$APP_DIR/${APP}"
  cp -a "$tmp_dir/${APP}/." "$APP_DIR/${APP}/"
  chmod 755 "$APP_DIR/${APP}/${APP}"
  rm -rf "$tmp_dir"
fi

cat > "${INSTALL_DIR}/${APP}" <<SHIM
#!/usr/bin/env bash
set -euo pipefail
"\${HOME}/.${APP}/app/${APP}/${APP}" "\$@"
SHIM
chmod 755 "${INSTALL_DIR}/${APP}"

add_to_path() {
  local config_file=$1
  local command=$2
  if grep -Fxq "$command" "$config_file" 2>/dev/null; then
    return
  fi
  if [[ -w "$config_file" || ! -e "$config_file" ]]; then
    {
      echo ""
      echo "# ${APP}"
      echo "$command"
    } >> "$config_file"
  else
    echo "Add this to your shell config:" >&2
    echo "$command" >&2
  fi
}

if [[ "$no_modify_path" != true ]]; then
  path_line='export PATH="$HOME/.gdrive/bin:$PATH"'
  [[ -f "$HOME/.bashrc" || ! -e "$HOME/.bashrc" ]] && add_to_path "$HOME/.bashrc" "$path_line"
  [[ -f "$HOME/.zshrc" || ! -e "$HOME/.zshrc" ]] && add_to_path "$HOME/.zshrc" "$path_line"
fi

echo "installed ${APP} ${specific_version}"
echo "binary: $HOME/.gdrive/bin/${APP}"

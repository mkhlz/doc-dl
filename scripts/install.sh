#!/bin/sh
set -eu

repository="${DOC_DL_REPOSITORY:-mkhlz/doc-dl}"
system="$(uname -s)"
architecture="$(uname -m)"

case "$system:$architecture" in
    Linux:x86_64|Linux:amd64)
        asset="doc-dl-linux-x64.tar.gz"
        ;;
    Darwin:x86_64)
        asset="doc-dl-macos-x64.tar.gz"
        ;;
    Darwin:arm64|Darwin:aarch64)
        asset="doc-dl-macos-arm64.tar.gz"
        ;;
    *)
        echo "No portable doc-dl release is available for $system $architecture" >&2
        exit 1
        ;;
esac

release_root="https://github.com/$repository/releases/latest/download"
temporary_root="$(mktemp -d)"
archive_path="$temporary_root/$asset"
checksum_path="$temporary_root/SHA256SUMS"
install_root="${XDG_DATA_HOME:-$HOME/.local/share}/doc-dl"
binary_root="$HOME/.local/bin"
backup_root=""

cleanup() {
    rm -rf "$temporary_root"
}
trap cleanup EXIT HUP INT TERM

echo "Downloading $asset..."
curl -fL "$release_root/$asset" -o "$archive_path"
curl -fL "$release_root/SHA256SUMS" -o "$checksum_path"

expected="$(awk -v name="$asset" '$2 == name { print $1; exit }' "$checksum_path")"
if [ -z "$expected" ]; then
    echo "SHA256SUMS does not contain $asset" >&2
    exit 1
fi
if command -v sha256sum >/dev/null 2>&1; then
    actual="$(sha256sum "$archive_path" | awk '{print $1}')"
else
    actual="$(shasum -a 256 "$archive_path" | awk '{print $1}')"
fi
if [ "$actual" != "$expected" ]; then
    echo "Checksum verification failed for $asset" >&2
    exit 1
fi

tar -xzf "$archive_path" -C "$temporary_root"
if [ ! -x "$temporary_root/doc-dl/doc-dl" ]; then
    echo "The release archive does not contain an executable doc-dl" >&2
    exit 1
fi

mkdir -p "$(dirname "$install_root")" "$binary_root"
if [ -e "$install_root" ]; then
    backup_root="$install_root.previous.$$"
    mv "$install_root" "$backup_root"
fi
if ! mv "$temporary_root/doc-dl" "$install_root"; then
    if [ -n "$backup_root" ] && [ -e "$backup_root" ] && [ ! -e "$install_root" ]; then
        mv "$backup_root" "$install_root"
    fi
    exit 1
fi
if [ -n "$backup_root" ] && [ -e "$backup_root" ]; then
    rm -rf "$backup_root"
fi

ln -sf "$install_root/doc-dl" "$binary_root/doc-dl"
echo "Installed doc-dl in $install_root"
"$binary_root/doc-dl" version
case ":$PATH:" in
    *":$binary_root:"*) ;;
    *) echo "Add $binary_root to PATH, then run: doc-dl \"https://example.com/document.pdf\"" ;;
esac

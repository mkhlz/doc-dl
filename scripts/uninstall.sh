#!/bin/sh
set -eu

purge_data=false
if [ "$#" -gt 1 ] || { [ "$#" -eq 1 ] && [ "$1" != "--purge-data" ]; }; then
    echo "Usage: uninstall.sh [--purge-data]" >&2
    exit 2
fi
if [ "$#" -eq 1 ]; then
    purge_data=true
fi

install_root="${XDG_DATA_HOME:-$HOME/.local/share}/doc-dl"
binary_root="$HOME/.local/bin"
link_path="$binary_root/doc-dl"
default_state_root="${XDG_STATE_HOME:-$HOME/.local/state}/doc-dl"

remove_expected_directory() {
    path="$1"
    case "$path" in
        */doc-dl) ;;
        *)
            echo "Refusing to remove an unexpected directory: $path" >&2
            exit 1
            ;;
    esac
    if [ -e "$path" ]; then
        rm -rf "$path"
        return 0
    fi
    return 1
}

if [ -L "$link_path" ]; then
    link_target="$(readlink "$link_path")"
    if [ "$link_target" = "$install_root/doc-dl" ]; then
        rm -f "$link_path"
    else
        echo "Leaving $link_path because it does not point to this doc-dl installation." >&2
    fi
fi

if remove_expected_directory "$install_root"; then
    echo "Uninstalled doc-dl from $install_root"
else
    echo "doc-dl is not installed in $install_root"
fi

if [ "$purge_data" = true ]; then
    if remove_expected_directory "$default_state_root"; then
        echo "Removed doc-dl state data from $default_state_root"
    fi
    if [ -n "${DOC_DL_STATE_DIR:-}" ]; then
        echo "DOC_DL_STATE_DIR is set. Its custom directory was not removed." >&2
    fi
fi

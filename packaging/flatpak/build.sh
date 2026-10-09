#!/bin/bash
# Builds Catalyst Lab Flatpak from this checkout (including uncommitted changes) with flatpak-builder.
# GNOME runtime and SDK are installed from Flathub when missing. Dependencies (bubblewrap, squashfs-tools, Python
# packages) are downloaded as sources pinned in dependencies/ and built into the Flatpak.
#
# Usage: packaging/flatpak/build.sh [--install] [--bundle] [--clean]
#   --install  installs the built app for current user
#   --bundle   creates dist/CatalystLab.flatpak, which can be installed on other computers
#   --clean    removes build directory and download cache first
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
HERE="$ROOT/packaging/flatpak"
WORK="$ROOT/.build-flatpak"
DIST="$ROOT/dist"
APP_ID=com.damiandudycz.CatalystLab
MANIFEST="$HERE/$APP_ID.json"

INSTALL=0
BUNDLE=0
for argument in "$@"; do
    case "$argument" in
        --install) INSTALL=1 ;;
        --bundle) BUNDLE=1 ;;
        --clean) rm -rf "$WORK" ;;
        *) echo "Unknown option: $argument"; exit 1 ;;
    esac
done

step() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }

for tool in flatpak flatpak-builder; do
    command -v "$tool" > /dev/null || { echo "$tool is required, install it with package manager of your system"; exit 1; }
done

step "Building $APP_ID"
flatpak remote-add --user --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo
options=(--user --install-deps-from=flathub --force-clean --ccache --state-dir="$WORK/state" --repo="$WORK/repo")
[ $INSTALL = 1 ] && options+=(--install)
flatpak-builder "${options[@]}" "$WORK/build" "$MANIFEST"

if [ $BUNDLE = 1 ]; then
    step "Bundle"
    mkdir -p "$DIST"
    flatpak build-bundle --runtime-repo=https://dl.flathub.org/repo/flathub.flatpakrepo "$WORK/repo" "$DIST/CatalystLab.flatpak" "$APP_ID"
    echo "$DIST/CatalystLab.flatpak ($(du -sh "$DIST/CatalystLab.flatpak" | cut -f1))"
fi

[ $INSTALL = 1 ] && echo "Run with: flatpak run $APP_ID"
exit 0

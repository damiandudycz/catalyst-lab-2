#!/bin/bash
set -e

# Install project as host application

BUILD_DIR="builddir"

if [ ! -d "$BUILD_DIR" ]; then
    meson setup "$BUILD_DIR"
fi

ninja -C "$BUILD_DIR"
sudo ninja -C "$BUILD_DIR" install

rm -rf "$BUILD_DIR"

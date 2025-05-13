#!/bin/bash
set -e  # Exit on first error

BUILD_DIR="builddir"

# Configure if not already configured
if [ ! -d "$BUILD_DIR" ]; then
    meson setup "$BUILD_DIR"
fi

# Build and install
ninja -C "$BUILD_DIR"
sudo ninja -C "$BUILD_DIR" install
rm -rf "$BUILD_DIR"

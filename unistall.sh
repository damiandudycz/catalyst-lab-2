#!/bin/bash

echo "Uninstalling CatalystLab..."

# Executable
sudo rm -v /usr/local/bin/catalystlab

# Desktop entry and app metadata
sudo rm -v /usr/local/share/applications/com.damiandudycz.CatalystLab.desktop
sudo rm -v /usr/local/share/metainfo/com.damiandudycz.CatalystLab.metainfo.xml

# Schema and recompile
sudo rm -v /usr/local/share/glib-2.0/schemas/com.damiandudycz.CatalystLab.gschema.xml
sudo glib-compile-schemas /usr/local/share/glib-2.0/schemas

# Icons (symbolic and scalable)
sudo rm -v /usr/local/share/icons/hicolor/scalable/apps/com.damiandudycz.CatalystLab.svg
sudo rm -v /usr/local/share/icons/hicolor/symbolic/apps/*.svg

# Application files
sudo rm -rv /usr/local/share/catalystlab

# Update system caches
sudo gtk4-update-icon-cache -q -t -f /usr/local/share/icons/hicolor
sudo update-desktop-database -q /usr/local/share/applications

echo "CatalystLab has been successfully uninstalled."

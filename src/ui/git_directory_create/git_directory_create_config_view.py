from __future__ import annotations
from gi.repository import Gtk, GLib, Gio, Adw
from enum import Enum
from .multistage_process import MultiStageProcessState
from .releng_installation import RelengInstallation
from .releng_directory import RelengDirectory
from .releng_manager import RelengManager
import os

@Gtk.Template(resource_path='/com/damiandudycz/CatalystLab/ui/git_directory_create/git_directory_create_config_view.ui')
class GitDirectoryCreateConfigView(Gtk.Box):
    __gtype_name__ = "GitDirectoryCreateConfigView"

    source_group = Gtk.Template.Child()
    options_group = Gtk.Template.Child()
    source_toggle_group_container = Gtk.Template.Child()
    directory_name_row = Gtk.Template.Child()
    directory_local_directory_row = Gtk.Template.Child()
    directory_url_row = Gtk.Template.Child()
    name_used_label = Gtk.Template.Child()

    def __init__(self):
        super().__init__()
        self.selected_source = GitDirectorySource.GIT_REPOSITORY
        self.selected_local_directory: Gio.File | None = None
        self._update_source_rows()
        self.check_filename_is_free()
        self.check_source_is_configured()
        self.connect("map", self.on_map)

    def on_map(self, widget):
        self.setup_source_toggles()

    def setup_source_toggles(self):
        toggle_group = Adw.ToggleGroup()
        toggle_group.add_css_class("round")
        toggle_group.add_css_class("caption")
        for source in GitDirectorySource:
            toggle = Adw.Toggle(label=source.name())
            toggle_group.add(toggle)
            if source == self.selected_source:
                toggle_group.set_active(source.value)
        def on_toggle_source_clicked(group, pspec):
            index = group.get_active()
            self.directory_url_row.set_text("")
            self.selected_local_directory = None
            self.directory_local_directory_row.set_subtitle("(Select directory)")
            self.selected_source = list(GitDirectorySource)[index]
            self._update_source_rows()
            self.check_source_is_configured()
        toggle_group.connect("notify::active", on_toggle_source_clicked)
        self.source_toggle_group_container.append(toggle_group)

    def _update_source_rows(self):
        self.directory_local_directory_row.set_visible(self.selected_source == GitDirectorySource.LOCAL_DIRECTORY)
        self.directory_url_row.set_visible(self.selected_source == GitDirectorySource.GIT_REPOSITORY)

    def check_filename_is_free(self) -> bool:
        self.filename_is_free = RelengManager.shared().is_name_available(name=self.directory_name_row.get_text())
        self.setup_back_next_buttons()
        self.name_used_label.set_visible(not self.filename_is_free)
        return self.filename_is_free

    def check_source_is_configured(self) -> bool:
        match self.selected_source:
            case GitDirectorySource.GIT_REPOSITORY:
                self.source_is_configured = self.directory_url_row.get_text()
            case GitDirectorySource.LOCAL_DIRECTORY:
                self.source_is_configured = self.selected_local_directory
            case GitDirectorySource.CREATE_NEW_PORTAGE_OVERLAY:
                self.source_is_configured = True
        self.setup_back_next_buttons()
        return self.source_is_configured

    @Gtk.Template.Callback()
    def on_directory_name_activate(self, sender):
        self.check_filename_is_free()
        self.get_root().set_focus(None)

    @Gtk.Template.Callback()
    def on_directory_name_changed(self, sender):
        self.check_filename_is_free()

    @Gtk.Template.Callback()
    def on_directory_url_activate(self, sender):
        self.get_root().set_focus(None)
        self.directory_name_row.set_text(self.directory_url_row.get_text().rstrip('/').split('/')[-1])
        self.check_source_is_configured()

    @Gtk.Template.Callback()
    def _on_select_local_directory_clicked(self, sender):
        def on_folder_selected(dialog, result):
            try:
                self.selected_local_directory = dialog.select_folder_finish(result)
                self.directory_name_row.set_text(self.selected_local_directory.get_basename())
                self.directory_local_directory_row.set_subtitle(self.selected_local_directory.get_path())
                self.check_source_is_configured()
                print("Selected folder:", self.selected_local_directory.get_path())
            except GLib.Error as e:
                print("Folder selection canceled or failed:", e)
        file_dialog = Gtk.FileDialog.new()
        file_dialog.set_title("Select a directory")
        file_dialog.select_folder(
            getattr(self, '_window', None) or self.get_root(),
            None,
            on_folder_selected
        )


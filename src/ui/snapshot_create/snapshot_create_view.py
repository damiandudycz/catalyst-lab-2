from __future__ import annotations
from gi.repository import Gtk, GLib, Gio
from gi.repository import Adw
from urllib.parse import ParseResult
from dataclasses import dataclass
from typing import ClassVar
from enum import Enum, auto
from abc import ABC, abstractmethod
import os, re, time
from datetime import datetime
from .architecture import Architecture
from .event_bus import EventBus
from .root_helper_client import RootHelperClient, AuthorizationKeeper
from .multistage_process import MultiStageProcessState
from .toolset import Toolset, ToolsetEvents
from .repository import Repository
from .environments_section import ToolsetRow
from .toolset_application import ToolsetApplication
from .snapshot_installation import SnapshotInstallation

@Gtk.Template(resource_path='/com/damiandudycz/CatalystLab/ui/snapshot_create/snapshot_create_view.ui')
class SnapshotCreateView(Gtk.Box):
    __gtype_name__ = "SnapshotCreateView"

    # Main views:
    setup_view = Gtk.Template.Child()
    fetch_view = Gtk.Template.Child()
    # Setup view elements:
    carousel = Gtk.Template.Child()
    welcome_page = Gtk.Template.Child()
    source_page = Gtk.Template.Child()
    config_page = Gtk.Template.Child()
    toolsets_list = Gtk.Template.Child()
    config_toolset_view = Gtk.Template.Child()
    config_file_view = Gtk.Template.Child()
    snapshot_name_row = Gtk.Template.Child()
    back_button = Gtk.Template.Child()
    next_button = Gtk.Template.Child()

    def __init__(self, installation_in_progress: SnapshotInstallation | None = None, content_navigation_view: Adw.NavigationView | None = None):
        super().__init__()
        self.selected_file: GLocalFile | None = None
        self.selected_toolset: Toolset | None = None
        self.installation_in_progress = installation_in_progress
        self.content_navigation_view = content_navigation_view
        self.carousel.connect('page-changed', self.on_page_changed)
        self._set_current_stage(self.installation_in_progress.status if self.installation_in_progress else MultiStageProcessState.SETUP)
        self.fetch_view.set_multistage_process(self.installation_in_progress)
        if installation_in_progress is None or installation_in_progress.status == MultiStageProcessState.SETUP:
            self._fill_toolsets_rows(Repository.TOOLSETS.value)

    def on_page_changed(self, carousel, pspec):
        self.current_page = int(carousel.get_position())
        self.setup_back_next_buttons()

    def setup_back_next_buttons(self, _ = None):
        is_first_page = self.current_page == 0
        is_second_page = self.current_page == 0
        is_last_page = self.current_page == 2
        is_stage_selected = self.selected_toolset is not None
        is_file_selected = self.selected_file is not None
        if_source_selected = is_stage_selected or is_file_selected
        self.back_button.set_sensitive(not is_first_page)
        self.back_button.set_opacity(0.0 if is_first_page else 1.0)
        self.next_button.set_sensitive(if_source_selected and is_last_page and (is_file_selected or not self.selected_toolset.is_reserved))
        self.next_button.set_opacity(0.0 if not is_last_page else 1.0)
        self.next_button.set_label("Save toolset" if is_file_selected else "Create snapshot")
        if hasattr(self, 'reserved_label'):
            self.reserved_label.set_visible(self.selected_toolset and self.selected_toolset.is_reserved and not self.selected_file)

    @Gtk.Template.Callback()
    def on_fetch_with_catalyst_pressed(self, _):
        self.selected_file = None
        self.config_toolset_view.set_visible(True)
        self.config_file_view.set_visible(False)
        self.carousel.scroll_to(self.carousel.get_nth_page(self.current_page + 1), True)

    @Gtk.Template.Callback()
    def on_select_file_pressed(self, _):
        def on_file_open_response(file_dialog, result):
            try:
                self.selected_file = file_dialog.open_finish(result)
                file_name = self.selected_file.get_basename()
                file_name_without_ext = file_name.rsplit('.', 1)[0]
                self.snapshot_name_row.set_text(file_name_without_ext)
                self.config_toolset_view.set_visible(False)
                self.config_file_view.set_visible(True)
                self.carousel.scroll_to(self.carousel.get_nth_page(self.current_page + 1), True)
            except GLib.Error as e:
                print("File open canceled or failed:", e)
        def create_file_filter():
            file_filter = Gtk.FileFilter()
            file_filter.set_name("Squashfs files (*.sqfs)")
            file_filter.add_pattern("*.sqfs")
            return file_filter
        def create_filter_list():
            store = Gio.ListStore.new(Gtk.FileFilter)
            store.append(create_file_filter())
            return store
        file_dialog = Gtk.FileDialog.new()
        file_dialog.set_title("Select a .sqfs file")
        filters = create_filter_list()
        file_dialog.set_filters(filters)
        file_dialog.open(self._window, None, on_file_open_response)

    @Gtk.Template.Callback()
    def on_back_pressed(self, _):
        is_first_page = self.current_page == 0
        if not is_first_page:
            self.carousel.scroll_to(self.carousel.get_nth_page(self.current_page - 1), True)

    @Gtk.Template.Callback()
    def on_next_pressed(self, _):
        is_last_page = self.current_page == 2
        if not is_last_page:
            self.carousel.scroll_to(self.carousel.get_nth_page(self.current_page + 1), True)
        else:
            RootHelperClient.shared().authorize_and_run(callback=self._start_installation)

    def _start_installation(self, authorization_keeper: AuthorizationKeeper):
        if not authorization_keeper:
            return
        self.installation_in_progress = SnapshotInstallation(
            toolset=self.selected_toolset if self.selected_file is None else None,
            file=self.selected_file,
            custom_filename=self.snapshot_name_row.get_text() + ".sqfs"
        )
        self.installation_in_progress.start(authorization_keeper=authorization_keeper)
        self.fetch_view.set_multistage_process(self.installation_in_progress)
        self._set_current_stage(self.installation_in_progress.status)

    @Gtk.Template.Callback()
    def on_start_row_activated(self, _):
        self.carousel.scroll_to(self.source_page, True)

    @Gtk.Template.Callback()
    def on_finish_pressed(self, _):
        if hasattr(self, "_window"):
            self._window.close()
        elif hasattr(self, "content_navigation_view"):
            self.content_navigation_view.pop()

    def _fill_toolsets_rows(self, result: list[Toolset]):
        self.selected_toolset = None
        sorted_toolsets = sorted(result, key=lambda toolset: toolset.metadata.get("date_updated", 0), reverse=True)
        valid_toolsets = [
            toolset for toolset in sorted_toolsets
            if toolset.get_app_install(ToolsetApplication.CATALYST) is not None
        ]
        # Monitor valid toolsets for is_reserved changes
        for toolset in valid_toolsets:
            print(f"Monitor {toolset}")
            toolset.event_bus.subscribe(
                ToolsetEvents.IS_RESERVED_CHANGED,
                self.setup_back_next_buttons
            )
        self.selected_toolset = next(
            (toolset for toolset in valid_toolsets if not toolset.is_reserved),
            valid_toolsets[0] if valid_toolsets else None
        )
        if not valid_toolsets:
            error_label = Gtk.Label(label=f"You need to create a toolset with Catalyst installed. Go to Environments section to create such toolset.")
            error_label.set_wrap(True)
            error_label.set_halign(Gtk.Align.CENTER)
            error_label.set_margin_top(12)
            error_label.set_margin_bottom(12)
            error_label.set_margin_start(24)
            error_label.set_margin_end(24)
            error_label.add_css_class("dimmed")
            self.toolsets_list.add(error_label)
        self.reserved_label = Gtk.Label(label="This toolset is currently in use.")
        self.reserved_label.set_wrap(True)
        self.reserved_label.set_halign(Gtk.Align.CENTER)
        self.reserved_label.set_margin_top(12)
        self.reserved_label.set_margin_bottom(12)
        self.reserved_label.set_margin_start(24)
        self.reserved_label.set_margin_end(24)
        self.reserved_label.add_css_class("dimmed")
        self.reserved_label.set_visible(self.selected_toolset and self.selected_toolset.is_reserved and not self.selected_file)
        self.toolsets_list.add(self.reserved_label)
        toolsets_check_buttons_group = []
        for toolset in sorted_toolsets:
            row = ToolsetRow(toolset=toolset)
            check_button = Gtk.CheckButton()
            check_button.set_active(toolset == self.selected_toolset)
            if toolsets_check_buttons_group:
                check_button.set_group(toolsets_check_buttons_group[0])
            check_button.connect("toggled", self._on_toolset_selected, toolset)
            toolsets_check_buttons_group.append(check_button)
            row.add_prefix(check_button)
            row.set_activatable_widget(check_button)
            row.set_sensitive(toolset.get_app_install(ToolsetApplication.CATALYST) is not None)
            self.toolsets_list.add(row)

    def _on_toolset_selected(self, button: Gtk.CheckButton, toolset: Toolset):
        """Callback for when a row's checkbox is toggled."""
        if button.get_active():
            self.selected_toolset = toolset
        else:
            # Deselect if unchecked
            if self.selected_toolset == toolset:
                self.selected_toolset = None
        self.setup_back_next_buttons()

    def _set_current_stage(self, stage: MultiStageProcessState):
        # Setup views visibility:
        self.setup_view.set_visible(stage == MultiStageProcessState.SETUP)
        self.fetch_view.set_visible(stage != MultiStageProcessState.SETUP)


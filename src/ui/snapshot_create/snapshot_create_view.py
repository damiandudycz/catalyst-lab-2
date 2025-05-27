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
from .toolset import Toolset
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
    toolset_page = Gtk.Template.Child()
    config_page = Gtk.Template.Child()
    toolsets_list = Gtk.Template.Child()
    back_button = Gtk.Template.Child()
    next_button = Gtk.Template.Child()

    def __init__(self, installation_in_progress: SnapshotInstallation | None = None, content_navigation_view: Adw.NavigationView | None = None):
        super().__init__()
        self.installation_in_progress = installation_in_progress
        self.content_navigation_view = content_navigation_view
        self.selected_toolset: Toolset | None = None
        self.carousel.connect('page-changed', self.on_page_changed)
        self._set_current_stage(self.installation_in_progress.status if self.installation_in_progress else MultiStageProcessState.SETUP)
        self.fetch_view.set_multistage_process(self.installation_in_progress)
        if installation_in_progress is None or installation_in_progress.status == MultiStageProcessState.SETUP:
            self._update_toolsets_result(Repository.TOOLSETS.value)

    def on_page_changed(self, carousel, pspec):
        self.current_page = int(carousel.get_position())
        self.setup_back_next_buttons()

    def setup_back_next_buttons(self):
        is_first_page = self.current_page == 0
        is_last_page = self.current_page == 2
        is_stage_selected = self.selected_toolset is not None
        self.back_button.set_sensitive(not is_first_page)
        self.back_button.set_opacity(0.0 if is_first_page else 1.0)
        self.next_button.set_sensitive(not is_first_page and is_stage_selected)
        self.next_button.set_opacity(0.0 if is_first_page or not is_stage_selected else 1.0)
        self.next_button.set_label("Create toolset" if is_last_page else "Next")

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
            toolset=self.selected_toolset
        )
        self.installation_in_progress.start(authorization_keeper=authorization_keeper)
        self.fetch_view.set_multistage_process(self.installation_in_progress)
        self._set_current_stage(self.installation_in_progress.status)

    @Gtk.Template.Callback()
    def on_start_row_activated(self, _):
        self.carousel.scroll_to(self.toolset_page, True)

    @Gtk.Template.Callback()
    def on_finish_pressed(self, _):
        if hasattr(self, "_window"):
            self._window.close()
        elif hasattr(self, "content_navigation_view"):
            self.content_navigation_view.pop()

    def _update_toolsets_result(self, result: list[Toolset]):
        self.selected_toolset = None
        if hasattr(self, "_toolset_rows"):
            for row in self._toolset_rows:
                self.toolsets_list.remove(row)
        # Refresh list of results
        if not result:
            error_label = Gtk.Label(label=f"No toolsets available")
            error_label.set_wrap(True)
            error_label.set_halign(Gtk.Align.START)
            self.toolsets_list.add(error_label)
            self._toolset_rows = [error_label]
        else:
            # TODO: Place newest first.
            sorted_toolsets = result
            self.selected_toolset = next(
                (toolset for toolset in sorted_toolsets
                 if toolset.get_installed_app_version(ToolsetApplication.CATALYST) is not None),
                None
            )
            self._toolset_rows = []
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
                row.set_sensitive(toolset.get_installed_app_version(ToolsetApplication.CATALYST) is not None)
                self.toolsets_list.add(row)
                self._toolset_rows.append(row)

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


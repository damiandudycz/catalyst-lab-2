from __future__ import annotations
from gi.repository import Gtk, GLib
from gi.repository import Adw
from urllib.parse import ParseResult
from dataclasses import dataclass
from typing import ClassVar
from enum import Enum, auto
from abc import ABC, abstractmethod
import os, re, time
from datetime import datetime
from .toolset_env_builder import ToolsetEnvBuilder
from .architecture import Architecture
from .event_bus import EventBus
from .toolset import (
    ToolsetInstallation,
    ToolsetInstallationStage,
    ToolsetApplication,
    ToolsetInstallationStep,
    ToolsetInstallationStepState,
    ToolsetInstallationStepEvent
)

@Gtk.Template(resource_path='/com/damiandudycz/CatalystLab/ui/toolset_create/toolset_create_view.ui')
class ToolsetCreateView(Gtk.Box):
    __gtype_name__ = "ToolsetCreateView"

    setup_view = Gtk.Template.Child()
    install_view = Gtk.Template.Child()
    carousel = Gtk.Template.Child()
    allow_binpkgs_checkbox = Gtk.Template.Child()
    configuration_page = Gtk.Template.Child()
    tools_page = Gtk.Template.Child()
    stages_list = Gtk.Template.Child()
    installation_steps_list = Gtk.Template.Child()
    tools_list = Gtk.Template.Child()
    back_button = Gtk.Template.Child()
    next_button = Gtk.Template.Child()

    def __init__(self, installation_in_progress: ToolsetInstallation | None = None):
        super().__init__()
        self.installation_in_progress = installation_in_progress
        self.selected_stage: ParseResult | None = None
        self.architecture = Architecture.HOST
        self.allow_binpkgs = True
        self.carousel.connect('page-changed', self.on_page_changed)
        self.tools_selection: Dict[ToolsetApplication, bool] = {app: True for app in ToolsetApplication.ALL}
        self.allow_binpkgs_checkbox.set_active(self.allow_binpkgs)
        self._load_applications_rows()
        self._set_current_stage(ToolsetInstallationStage.SETUP if installation_in_progress is None else ToolsetInstallationStage.INSTALL)
        if installation_in_progress and installation_in_progress.status != ToolsetInstallationStage.SETUP:
            self._update_installation_steps(steps=installation_in_progress.steps)
        else:
            ToolsetEnvBuilder.get_stage3_urls(architecture=self.architecture, completion_handler=self._update_stages_result)

    def on_page_changed(self, carousel, pspec):
        self.current_page = int(carousel.get_position())
        self.setup_back_next_buttons()

    def setup_back_next_buttons(self):
        is_first_page = self.current_page == 0
        is_last_page = self.current_page == 2
        is_stage_selected = self.selected_stage is not None
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
            self._start_installation()

    @Gtk.Template.Callback()
    def on_allow_binpkgs_toggled(self, checkbox):
        self.allow_binpkgs = checkbox.get_active()

    def _start_installation(self):
        selected_apps = [app for app, selected in self.tools_selection.items() if selected]
        self.installation_in_progress = ToolsetInstallation(
            stage_url=self.selected_stage,
            allow_binpkgs=self.allow_binpkgs,
            selected_apps=selected_apps
        )
        self._update_installation_steps(self.installation_in_progress.steps)
        self._set_current_stage(ToolsetInstallationStage.INSTALL)
        self.installation_in_progress.start()

    @Gtk.Template.Callback()
    def on_start_row_activated(self, _):
        self.carousel.scroll_to(self.configuration_page, True)

    @Gtk.Template.Callback()
    def on_cancel_pressed(self, _):
        pass

    def _update_stages_result(self, result: list[ParseResult] | Exception):
        self.selected_stage = None
        if hasattr(self, "_stage_rows"):
            for row in self._stage_rows:
                self.stages_list.remove(row)
        # Refresh list of results
        if isinstance(result, Exception):
            error_label = Gtk.Label(label=f"Error: {str(result)}")
            self.stages_list.add(error_label)
            self._stage_rows = [error_label]
        else:
            if result:
                # Place recommended first.
                sorted_stages = sorted(result, key=lambda stage: not ToolsetCreateView._is_recommended_stage(
                    os.path.basename(stage.geturl()), self.architecture
                ))
                self.selected_stage = sorted_stages[0]
            self._stage_rows = []
            check_buttons_group = []
            for stage in sorted_stages:
                filename = os.path.basename(stage.geturl())
                row = Adw.ActionRow(title=filename)
                check_button = Gtk.CheckButton()
                check_button.set_active(stage == self.selected_stage)
                if check_buttons_group:
                    check_button.set_group(check_buttons_group[0])
                if ToolsetCreateView._is_recommended_stage(filename, self.architecture):
                    # Mark first entry as recommended
                    label = Gtk.Label(label="Recommended")
                    label.add_css_class("dim-label")
                    label.add_css_class("caption")
                    row.add_suffix(label)
                check_button.connect("toggled", self._on_stage_selected, stage)
                check_buttons_group.append(check_button)
                row.add_prefix(check_button)
                row.set_activatable_widget(check_button)
                self.stages_list.add(row)
                self._stage_rows.append(row)

    def _on_stage_selected(self, button: Gtk.CheckButton, stage: ParseResult):
        """Callback for when a row's checkbox is toggled."""
        if button.get_active():
            self.selected_stage = stage
        else:
            # Deselect if unchecked
            if self.selected_stage == stage:
                self.selected_stage = None
        self.setup_back_next_buttons()

    def _on_tool_selected(self, button: Gtk.CheckButton, app: ToolsetApplication):
        """Callback for when a row's checkbox is toggled."""
        self.tools_selection[app] = button.get_active()
        self.setup_back_next_buttons()

    @staticmethod
    def _is_recommended_stage(filename: str, architecture: Architecture) -> bool:
        escaped_arch = re.escape(architecture.value)
        pattern = rf'^stage3-{escaped_arch}-openrc-(\d{{8}}T\d{{6}}Z)\.tar\.xz$'
        match = re.match(pattern, filename)
        if not match:
            return False
        date_str = match.group(1)
        try:
            datetime.strptime(date_str, "%Y%m%dT%H%M%SZ")
            return True
        except ValueError:
            return False

    def _load_applications_rows(self):
        for app in ToolsetApplication.ALL:
            row = Adw.ActionRow(title=app.name)
            row.set_subtitle(app.description)
            check_button = Gtk.CheckButton()
            check_button.set_active(self.tools_selection.get(app))
            if app.is_recommended or app.is_highly_recommended:
                # Mark first entry as recommended
                label = Gtk.Label(label="Recommended" if not app.is_highly_recommended else "Highly recommended")
                label.add_css_class("dim-label")
                label.add_css_class("caption")
                row.add_suffix(label)
            check_button.connect("toggled", self._on_tool_selected, app)
            row.add_prefix(check_button)
            row.set_activatable_widget(check_button)
            self.tools_list.add(row)

    def _set_current_stage(self, stage: ToolsetInstallationStage):
        self.current_stage = stage
        # Setup views visibility:
        self.setup_view.set_visible(stage == ToolsetInstallationStage.SETUP)
        self.install_view.set_visible(stage == ToolsetInstallationStage.INSTALL)

    def _update_installation_steps(self, steps: list[ToolsetInstallationStep]):
        if hasattr(self, "_installation_rows"):
            for row in self._installation_rows:
                self.installation_steps_list.remove(row)
        self._installation_rows = []
        check_buttons_group = []
        for step in steps:
            row = ToolsetInstallationStepRow(step=step, owner=self)
            self.installation_steps_list.add(row)
            self._installation_rows.append(row)

    def _scroll_to_tool_row(self, row: ToolsetInstallationStepRow):
        def _scroll():
            scrolled_window = self.installation_steps_list.get_ancestor(Gtk.ScrolledWindow)
            if not scrolled_window:
                return False
            vadjustment = scrolled_window.get_vadjustment()
            _, y = row.translate_coordinates(self.installation_steps_list, 0, 0)
            row_height = row.get_allocated_height()
            visible_height = vadjustment.get_page_size()
            center_y = y + row_height / 2 - visible_height / 2
            max_value = vadjustment.get_upper() - vadjustment.get_page_size()
            scroll_to = max(0, min(center_y, max_value))
            vadjustment.set_value(scroll_to)
            return False
        GLib.idle_add(_scroll)

class ToolsetInstallationStepRow(Adw.ActionRow):

    def __init__(self, step: ToolsetInstallationStep, owner: ToolsetCreateView):
        super().__init__(title=step.name)
        self.step = step
        self.owner = owner
        label = Gtk.Label(label=step.description)
        label.add_css_class("dim-label")
        label.add_css_class("caption")
        self.add_suffix(label)
        self.set_sensitive(step.state != ToolsetInstallationStepState.SCHEDULED)
        step.event_bus.subscribe(
            ToolsetInstallationStepEvent.STATE_CHANGED,
            self._step_state_changed
        )

    def _step_state_changed(self, state: ToolsetInstallationStepState):
        self.set_sensitive(state != ToolsetInstallationStepState.SCHEDULED)
        self.owner._scroll_to_tool_row(self)


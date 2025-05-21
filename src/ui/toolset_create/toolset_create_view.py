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
from .root_helper_client import RootHelperClient
from .toolset import (
    ToolsetInstallation,
    ToolsetInstallationStage,
    ToolsetInstallationEvent,
    ToolsetApplication,
    ToolsetApplicationVersion,
    ToolsetApplicationSelection,
    ToolsetInstallationStep,
    ToolsetInstallationStepState,
    ToolsetInstallationStepEvent
)

@Gtk.Template(resource_path='/com/damiandudycz/CatalystLab/ui/toolset_create/toolset_create_view.ui')
class ToolsetCreateView(Gtk.Box):
    __gtype_name__ = "ToolsetCreateView"

    # Main views:
    setup_view = Gtk.Template.Child()
    install_view = Gtk.Template.Child()
    # Setup view elements:
    carousel = Gtk.Template.Child()
    welcome_page = Gtk.Template.Child()
    configuration_page = Gtk.Template.Child()
    tools_page = Gtk.Template.Child()
    stages_list = Gtk.Template.Child()
    tools_list = Gtk.Template.Child()
    allow_binpkgs_checkbox = Gtk.Template.Child()
    back_button = Gtk.Template.Child()
    next_button = Gtk.Template.Child()
    # Install view elements:
    installation_steps_list = Gtk.Template.Child()
    cancel_button = Gtk.Template.Child()
    finish_button = Gtk.Template.Child()

    def __init__(self, installation_in_progress: ToolsetInstallation | None = None, content_navigation_view: Adw.NavigationView | None = None):
        super().__init__()
        self.installation_in_progress = installation_in_progress
        self.content_navigation_view = content_navigation_view
        self.selected_stage: ParseResult | None = None
        self.architecture = Architecture.HOST
        self.allow_binpkgs = True
        self.carousel.connect('page-changed', self.on_page_changed)
        self.tools_selection: Dict[ToolsetApplication, bool] = {app: True for app in ToolsetApplication.ALL}
        self.tools_selection_versions: Dict[ToolsetApplication, ToolsetApplicationSelection] = {app: app.versions[0] for app in ToolsetApplication.ALL}
        self.allow_binpkgs_checkbox.set_active(self.allow_binpkgs)
        self._load_applications_rows()
        self._set_current_stage(self.installation_in_progress.status if self.installation_in_progress else ToolsetInstallationStage.SETUP)
        if installation_in_progress and installation_in_progress.status != ToolsetInstallationStage.SETUP:
            self._update_installation_steps(steps=installation_in_progress.steps)
            self.bind_installation_events(self.installation_in_progress)
        else:
            ToolsetEnvBuilder.get_stage3_urls(architecture=self.architecture, completion_handler=self._update_stages_result)

    def bind_installation_events(self, installation_in_progress: ToolsetInstallation):
        installation_in_progress.event_bus.subscribe(
            ToolsetInstallationEvent.STATE_CHANGED,
            self._set_current_stage
        )

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
            RootHelperClient.shared().authorize_and_run(callback=self._start_installation)

    @Gtk.Template.Callback()
    def on_allow_binpkgs_toggled(self, checkbox):
        self.allow_binpkgs = checkbox.get_active()

    def _start_installation(self, authorized: bool):
        if not authorized:
            return
        apps_selection = [
            ToolsetApplicationSelection(
                app=app,
                version=self.tools_selection_versions[app],
                selected=self.tools_selection[app] and all(self.tools_selection[dep] for dep in getattr(app, "dependencies", ()))
            )
            for app, _ in self.tools_selection.items()
        ]
        self.installation_in_progress = ToolsetInstallation(
            stage_url=self.selected_stage,
            allow_binpkgs=self.allow_binpkgs,
            apps_selection=apps_selection
        )
        self._update_installation_steps(self.installation_in_progress.steps)
        self._set_current_stage(self.installation_in_progress.status)
        self.bind_installation_events(self.installation_in_progress)
        self.installation_in_progress.start()

    @Gtk.Template.Callback()
    def on_start_row_activated(self, _):
        self.carousel.scroll_to(self.configuration_page, True)

    @Gtk.Template.Callback()
    def on_cancel_pressed(self, _):
        self.installation_in_progress.cancel()

    @Gtk.Template.Callback()
    def on_finish_pressed(self, _):
        if hasattr(self, "_window"):
            self._window.close()
        elif hasattr(self, "content_navigation_view"):
            self.content_navigation_view.pop()
        self.installation_in_progress.clean_from_started_installations()

    def _update_stages_result(self, result: list[ParseResult] | Exception):
        self.selected_stage = None
        if hasattr(self, "_stage_rows"):
            for row in self._stage_rows:
                self.stages_list.remove(row)
        # Refresh list of results
        if isinstance(result, Exception):
            error_label = Gtk.Label(label=f"Error: {str(result)}")
            error_label.set_wrap(True)
            error_label.set_halign(Gtk.Align.START)
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
            tools_check_buttons_group = []
            for stage in sorted_stages:
                filename = os.path.basename(stage.geturl())
                row = Adw.ActionRow(title=filename)
                check_button = Gtk.CheckButton()
                check_button.set_active(stage == self.selected_stage)
                if tools_check_buttons_group:
                    check_button.set_group(tools_check_buttons_group[0])
                if ToolsetCreateView._is_recommended_stage(filename, self.architecture):
                    # Mark first entry as recommended
                    label = Gtk.Label(label="Recommended")
                    label.add_css_class("dim-label")
                    label.add_css_class("caption")
                    row.add_suffix(label)
                check_button.connect("toggled", self._on_stage_selected, stage)
                tools_check_buttons_group.append(check_button)
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
        self._update_dependencies()

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
        self.tools_rows = {}
        self.tools_check_buttons = {}
        for app in ToolsetApplication.ALL:
            if app.auto_select:
                continue # Don't show automatic dependencies
            row = Adw.ExpanderRow(title=app.name)
            row.set_subtitle(app.description)
            check_button = Gtk.CheckButton()
            check_button.set_active(self.tools_selection.get(app))
            check_button.connect("toggled", self._on_tool_selected, app)

            label = Gtk.Label(label=self.tools_selection_versions[app].name)
            label.add_css_class("dim-label")
            label.add_css_class("caption")
            row.add_suffix(label)

            for version in app.versions:
                version_row = Adw.ActionRow(title=version.name)
                version_row.set_activatable(True)
                # Connect signal to update label when row is activated
                def on_version_selected(version_row, version=version):
                    self.tools_selection[version_row.app] = True
                    self.tools_selection_versions[version_row.app] = version
                    version_row.assoiciated_label.set_text(version.name)
                    version_row.check_button.set_active(self.tools_selection.get(version_row.app))
                    version_row.app_row.set_expanded(False)
                version_row.assoiciated_label = label
                version_row.app_row = row
                version_row.app = app
                version_row.check_button = check_button
                version_row.connect("activated", on_version_selected)
                row.add_row(version_row)

            row.add_prefix(check_button)
            self.tools_list.add(row)
            self.tools_rows[app] = row
            self.tools_check_buttons[app] = check_button
        # After building all rows, update sensitivity based on current state
        self._update_dependencies()

    def _update_dependencies(self):
        for app, row in self.tools_rows.items():
            # Create the comma-separated string of unmet dependencies
            unmet_dependencies = ", ".join([
                dep.name for dep in getattr(app, "dependencies", ())
                if not (self.tools_check_buttons.get(dep, Gtk.CheckButton()).get_active() or dep.auto_select)
            ])
            if unmet_dependencies:
                 row.set_title(f"{app.name} (requires: {unmet_dependencies})")
            else:
                row.set_title(app.name)
            # Check if all dependencies are satisfied (i.e., no unmet dependencies)
            dependencies_satisfied = not unmet_dependencies
            is_sensitive = dependencies_satisfied
            row.set_sensitive(is_sensitive)
            if not is_sensitive:
                row.set_expanded(False)
            self.tools_check_buttons[app].set_sensitive(is_sensitive)

    def _set_current_stage(self, stage: ToolsetInstallationStage):
        self.current_stage = stage
        # Setup views visibility:
        self.setup_view.set_visible(stage == ToolsetInstallationStage.SETUP)
        self.install_view.set_visible(stage != ToolsetInstallationStage.SETUP)
        self.cancel_button.set_visible(stage == ToolsetInstallationStage.INSTALL)
        self.finish_button.set_visible(stage != ToolsetInstallationStage.INSTALL)
        # Add label with summary for completion states:
        def display_status(text: str, style: str | None):
            label = Gtk.Label(label=text)
            label.set_margin_top(12)
            label.set_margin_bottom(12)
            label.set_margin_start(24)
            label.set_margin_end(24)
            label.add_css_class("heading")
            if style:
                label.add_css_class(style)
            self.installation_steps_list.add(label)
            self._scroll_to_installation_steps_bottom()

        match stage:
            case ToolsetInstallationStage.COMPLETED:
                display_status(text="Installation completed successfully.", style="success")
            case ToolsetInstallationStage.FAILED:
                display_status(text="Installation failed.", style="error")

    def _update_installation_steps(self, steps: list[ToolsetInstallationStep]):
        if hasattr(self, "_installation_rows"):
            for row in self._installation_rows:
                self.installation_steps_list.remove(row)
        self._installation_rows = []
        tools_check_buttons_group = []
        running_stage_row = None
        for step in steps:
            row = ToolsetInstallationStepRow(step=step, owner=self)
            self.installation_steps_list.add(row)
            self._installation_rows.append(row)
            if step.state == ToolsetInstallationStepState.IN_PROGRESS:
                running_stage_row = row
        if running_stage_row:
            GLib.idle_add(self._scroll_to_installation_step_row, running_stage_row)

    def _scroll_to_installation_step_row(self, row: ToolsetInstallationStepRow):
        def _scroll(widget):
            scrolled_window = self.installation_steps_list.get_ancestor(Gtk.ScrolledWindow)
            vadjustment = scrolled_window.get_vadjustment()
            _, y = row.translate_coordinates(self.installation_steps_list, 0, 0)
            row_height = row.get_allocated_height()
            visible_height = vadjustment.get_page_size()
            center_y = y + row_height / 2 - visible_height / 2
            max_value = vadjustment.get_upper() - vadjustment.get_page_size()
            scroll_to = max(0, min(center_y, max_value))
            vadjustment.set_value(scroll_to)
        GLib.idle_add(_scroll, row)

    def _scroll_to_installation_steps_bottom(self):
        def _scroll():
            scrolled_window = self.installation_steps_list.get_ancestor(Gtk.ScrolledWindow)
            vadjustment = scrolled_window.get_vadjustment()
            bottom = vadjustment.get_upper() - vadjustment.get_page_size()
            vadjustment.set_value(bottom)
        GLib.timeout_add(100, _scroll)

class ToolsetInstallationStepRow(Adw.ActionRow):

    def __init__(self, step: ToolsetInstallationStep, owner: ToolsetCreateView):
        super().__init__(title=step.name, subtitle=step.description)
        self.step = step
        self.owner = owner
        self.progress_label = Gtk.Label()
        self.progress_label.add_css_class("dim-label")
        self.progress_label.add_css_class("caption")
        self._update_status_label()
        self.add_suffix(self.progress_label)
        self.set_sensitive(step.state != ToolsetInstallationStepState.SCHEDULED)
        self._set_status_icon(state=step.state)
        step.event_bus.subscribe(
            ToolsetInstallationStepEvent.STATE_CHANGED,
            self._step_state_changed
        )
        step.event_bus.subscribe(
            ToolsetInstallationStepEvent.PROGRESS_CHANGED,
            self._step_progress_changed
        )

    def _step_progress_changed(self, progress: float | None):
        self._update_status_label()

    def _step_state_changed(self, state: ToolsetInstallationStepState):
        self.set_sensitive(state != ToolsetInstallationStepState.SCHEDULED)
        self._set_status_icon(state=state)
        self.owner._scroll_to_installation_step_row(self)
        self._update_status_label()

    def _update_status_label(self):
        self.progress_label.set_label(
            "" if self.step.state == ToolsetInstallationStepState.SCHEDULED else ("..." if self.step.progress is None else f"{int(self.step.progress * 100)}%")
        )

    def _set_status_icon(self, state: ToolsetInstallationStepState):
        if not hasattr(self, "status_icon"):
            self.status_icon = Gtk.Image()
            self.status_icon.set_pixel_size(24)
            self.add_prefix(self.status_icon)
        icon_name = {
            ToolsetInstallationStepState.SCHEDULED: "square-alt-arrow-right-svgrepo-com-symbolic",
            ToolsetInstallationStepState.IN_PROGRESS: "menu-dots-square-svgrepo-com-symbolic",
            ToolsetInstallationStepState.FAILED: "error-box-svgrepo-com-symbolic",
            ToolsetInstallationStepState.COMPLETED: "check-square-svgrepo-com-symbolic"
        }.get(state)
        styles = {
            ToolsetInstallationStepState.SCHEDULED: "dimmed",
            ToolsetInstallationStepState.IN_PROGRESS: "",
            ToolsetInstallationStepState.FAILED: "error",
            ToolsetInstallationStepState.COMPLETED: "success"
        }
        style = styles.get(state)
        self.status_icon.set_from_icon_name(icon_name)
        for css_class in styles.values():
            if css_class:
                self.status_icon.remove_css_class(css_class)
        if style:
            self.status_icon.add_css_class(style)

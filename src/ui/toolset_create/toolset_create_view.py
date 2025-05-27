from __future__ import annotations
import os, re, time
from gi.repository import Gtk, GLib, Gio
from gi.repository import Adw
from urllib.parse import ParseResult
from dataclasses import dataclass
from typing import ClassVar
from enum import Enum, auto
from datetime import datetime
from .toolset_env_builder import ToolsetEnvBuilder
from .architecture import Architecture
from .event_bus import EventBus
from .root_helper_client import RootHelperClient, AuthorizationKeeper
from .multistage_process import MultiStageProcessState
from .toolset_installation import ToolsetInstallation
from .toolset_application import ToolsetApplication, ToolsetApplicationVersion, ToolsetApplicationSelection

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

    def __init__(self, installation_in_progress: ToolsetInstallation | None = None, content_navigation_view: Adw.NavigationView | None = None):
        super().__init__()
        self.installation_in_progress = installation_in_progress
        self.content_navigation_view = content_navigation_view
        self.selected_stage: ParseResult | None = None
        self.architecture = Architecture.HOST
        self.allow_binpkgs = True
        self.carousel.connect('page-changed', self.on_page_changed)
        self.tools_selection: Dict[ToolsetApplication, bool] = {app: not app.auto_select for app in ToolsetApplication.ALL}
        self.tools_selection_versions: Dict[ToolsetApplication, ToolsetApplicationSelection] = {app: app.versions[0] for app in ToolsetApplication.ALL}
        self.tools_selection_patches: Dict[ToolsetApplication, list[GLocalFile]] = {app: [] for app in ToolsetApplication.ALL}
        self.allow_binpkgs_checkbox.set_active(self.allow_binpkgs)
        self._load_applications_rows()
        self._set_current_stage(self.installation_in_progress.status if self.installation_in_progress else MultiStageProcessState.SETUP)
        self.install_view.set_multistage_process(self.installation_in_progress)
        if installation_in_progress is None or installation_in_progress.status == MultiStageProcessState.SETUP:
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
            RootHelperClient.shared().authorize_and_run(callback=self._start_installation)

    @Gtk.Template.Callback()
    def on_allow_binpkgs_toggled(self, checkbox):
        self.allow_binpkgs = checkbox.get_active()

    def _start_installation(self, authorization_keeper: AuthorizationKeeper):
        if not authorization_keeper:
            return
        apps_selection = [
            ToolsetApplicationSelection(
                app=app,
                version=self.tools_selection_versions[app],
                selected=self.tools_selection[app],
                patches=self.tools_selection_patches[app]
            )
            for app, _ in self.tools_selection.items()
        ]
        self.installation_in_progress = ToolsetInstallation(
            stage_url=self.selected_stage,
            allow_binpkgs=self.allow_binpkgs,
            apps_selection=apps_selection
        )
        self.installation_in_progress.start(authorization_keeper=authorization_keeper)
        self.install_view.set_multistage_process(self.installation_in_progress)
        self._set_current_stage(self.installation_in_progress.status)

    @Gtk.Template.Callback()
    def on_start_row_activated(self, _):
        self.carousel.scroll_to(self.configuration_page, True)

    @Gtk.Template.Callback()
    def on_finish_pressed(self, _):
        if hasattr(self, "_window"):
            self._window.close()
        elif hasattr(self, "content_navigation_view"):
            self.content_navigation_view.pop()
        self.installation_in_progress.clean_from_started_processes()

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
            stages_check_buttons_group = []
            for stage in sorted_stages:
                filename = os.path.basename(stage.geturl())
                row = Adw.ActionRow(title=filename)
                check_button = Gtk.CheckButton()
                check_button.set_active(stage == self.selected_stage)
                if stages_check_buttons_group:
                    check_button.set_group(stages_check_buttons_group[0])
                if ToolsetCreateView._is_recommended_stage(filename, self.architecture):
                    # Mark first entry as recommended
                    label = Gtk.Label(label="Recommended")
                    label.add_css_class("dim-label")
                    label.add_css_class("caption")
                    row.add_suffix(label)
                check_button.connect("toggled", self._on_stage_selected, stage)
                stages_check_buttons_group.append(check_button)
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

    def _on_tool_selected(self, button: Gtk.CheckButton, app: ToolsetApplication, row: Adw.ExpanderRow):
        """Callback for when a row's checkbox is toggled."""
        self.tools_selection[app] = button.get_active()
        self.setup_back_next_buttons()
        self._update_dependencies()
        row.set_enable_expansion(self.tools_selection[app])
        row.set_expanded(False)

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

    def _update_application_info_label(self, row: Adw.ExpanderRow, app):
        fragments = [self.tools_selection_versions[app].name]
        if self.tools_selection_patches[app]:
            fragments.append("Patched")
        row.info_label.set_label(", ".join(fragments))

    def _load_applications_rows(self):
        self.tools_rows = {}
        for app in ToolsetApplication.ALL:
            if app.auto_select:
                continue # Don't show automatic dependencies
            row = Adw.ExpanderRow(title=app.name)
            row.set_subtitle(app.description)
            row.set_enable_expansion(self.tools_selection[app])

            check_button = Gtk.CheckButton()
            check_button.set_active(self.tools_selection[app])
            check_button.connect("toggled", self._on_tool_selected, app, row)
            row.check_button = check_button
            row.add_prefix(check_button)

            label = Gtk.Label()
            label.add_css_class("dim-label")
            label.add_css_class("caption")
            row.info_label = label
            row.add_suffix(label)

            versions_row = Adw.ActionRow()
            versions_row.set_activatable(False)

            toggle_group = Adw.ToggleGroup()
            toggle_group.add_css_class("round")
            toggle_group.add_css_class("caption")
            for version in app.versions:
                toggle = Adw.Toggle(label=version.name)
                toggle_group.add(toggle)
            def on_toggle_clicked(group, pspec, app, app_row):
                index = group.get_active()
                self.tools_selection_versions[app] = app.versions[index]
                self._update_application_info_label(row=app_row, app=app)
            toggle_group.connect("notify::active", on_toggle_clicked, app, row)

            wrapper = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER)
            wrapper.append(toggle_group)
            versions_row.add_prefix(wrapper)
            row.add_row(versions_row)

            add_patch_button = Gtk.Button()
            add_patch_button_content = Adw.ButtonContent(label="Add patch", icon_name="copy-svgrepo-com-symbolic")
            add_patch_button.set_child(add_patch_button_content)
            add_patch_button.get_style_context().add_class("flat")
            add_patch_button.get_style_context().add_class("caption")
            add_patch_button.connect("clicked", self._on_add_patch_clicked, app, row)
            wrapper = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER)
            wrapper.append(add_patch_button)
            versions_row.add_suffix(wrapper)

            self._update_application_info_label(row=row, app=app)
            self.tools_list.add(row)
            self.tools_rows[app] = row
        # After building all rows, update sensitivity based on current state
        self._update_dependencies()

    def _on_add_patch_clicked(self, add_patch_row, app, app_row):
        def delete_patch_pressed(remove_button, app, app_row, patch_row):
            self.tools_selection_patches[app].remove(remove_button.file)
            app_row.remove(patch_row)
            self._update_application_info_label(row=app_row, app=app)
        def on_file_open_response(file_dialog, result):
            try:
                file = file_dialog.open_finish(result)
                self.tools_selection_patches[app].append(file)
                patch_row = Adw.ActionRow(title=file.get_basename(), subtitle="Patch file", icon_name="copy-svgrepo-com-symbolic")
                remove_button = Gtk.Button()
                remove_button_content = Adw.ButtonContent(label="Remove", icon_name="error-box-svgrepo-com-symbolic")
                remove_button.set_child(remove_button_content)
                remove_button.get_style_context().add_class("destructive-action")
                remove_button.get_style_context().add_class("flat")
                remove_button.get_style_context().add_class("caption")
                remove_button.connect("clicked", delete_patch_pressed, app, app_row, patch_row)
                remove_button.file = file
                wrapper = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER)
                wrapper.append(remove_button)
                patch_row.add_suffix(wrapper)
                app_row.add_row(patch_row)
                self._update_application_info_label(row=app_row, app=app)
            except GLib.Error as e:
                print("File open canceled or failed:", e)
        def create_patch_file_filter():
            file_filter = Gtk.FileFilter()
            file_filter.set_name("Patch files (*.patch)")
            file_filter.add_pattern("*.patch")
            return file_filter
        def create_filter_list():
            store = Gio.ListStore.new(Gtk.FileFilter)
            store.append(create_patch_file_filter())
            return store
        file_dialog = Gtk.FileDialog.new()
        file_dialog.set_title("Select a .patch file")
        filters = create_filter_list()
        file_dialog.set_filters(filters)
        file_dialog.open(self._window, None, on_file_open_response)

    def _update_dependencies(self):
        for app, row in self.tools_rows.items():
            # Create the comma-separated string of unmet dependencies
            unmet_dependencies = ", ".join([
                dep.name for dep in getattr(app, "dependencies", ())
                if not (self.tools_selection[dep] or dep.auto_select)
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
            self.tools_rows[app].check_button.set_sensitive(is_sensitive)

    def _set_current_stage(self, stage: MultiStageProcessState):
        # Setup views visibility:
        self.setup_view.set_visible(stage == MultiStageProcessState.SETUP)
        self.install_view.set_visible(stage != MultiStageProcessState.SETUP)
        self.install_view._set_current_stage(stage=stage)


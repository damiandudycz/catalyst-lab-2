from gi.repository import Gtk, Adw, Gio, GLib
from .toolset import Toolset, ToolsetEvents
from .toolset_application import ToolsetApplication, ToolsetApplicationSelection
from .helper_functions import get_file_size_string
import uuid
import os
from datetime import datetime
from urllib.parse import urlparse
from .root_helper_client import RootHelperClient, AuthorizationKeeper
from .repository import Repository
from .toolset_update import ToolsetUpdate
from .multistage_process import MultiStageProcess, MultiStageProcessEvent, MultiStageProcessState
from .multistage_process_execution_view import MultistageProcessExecutionView

@Gtk.Template(resource_path='/com/damiandudycz/CatalystLab/ui/toolset_details/toolset_details_view.ui')
class ToolsetDetailsView(Gtk.Box):
    __gtype_name__ = "ToolsetDetailsView"

    toolset_name_row = Gtk.Template.Child()
    applications_group = Gtk.Template.Child()
    status_file_row = Gtk.Template.Child()
    status_size_row = Gtk.Template.Child()
    toolset_date_created_row = Gtk.Template.Child()
    toolset_date_updated_row = Gtk.Template.Child()
    toolset_source_row = Gtk.Template.Child()
    status_state_row = Gtk.Template.Child()
    status_bindings_row = Gtk.Template.Child()
    status_update_row = Gtk.Template.Child()
    status_update_progress_label = Gtk.Template.Child()
    tag_free = Gtk.Template.Child()
    tag_store_changes = Gtk.Template.Child()
    tag_spawned = Gtk.Template.Child()
    tag_in_use = Gtk.Template.Child()
    tag_updating = Gtk.Template.Child()
    tag_is_reserved = Gtk.Template.Child()
    tag_update_succeded = Gtk.Template.Child()
    tag_update_failed = Gtk.Template.Child()
    action_button_spawn = Gtk.Template.Child()
    action_button_unspawn = Gtk.Template.Child()
    action_button_chroot = Gtk.Template.Child()
    action_button_update = Gtk.Template.Child()
    action_button_delete = Gtk.Template.Child()
    allow_binpkgs_checkbox = Gtk.Template.Child()
    applications_actions_container = Gtk.Template.Child()
    applications_button_cancel = Gtk.Template.Child()
    applications_button_apply = Gtk.Template.Child()

    # --------------------------------------------------------------------------
    # Lifecycle:

    def __init__(self, toolset: Toolset, content_navigation_view: Adw.NavigationView | None = None):
        super().__init__()
        self.toolset = toolset
        self.content_navigation_view = content_navigation_view

        self.apps_changed = False
        self.update_in_progress: ToolsetUpdate | None = None
        self.tools_selection: Dict[ToolsetApplication, bool] = {app: False for app in ToolsetApplication.ALL}
        self.tools_selection_versions: Dict[ToolsetApplication, ToolsetApplicationSelection] = {app: app.versions[0] for app in ToolsetApplication.ALL}
        self.tools_selection_patches: Dict[ToolsetApplication, list[Gio.File | str]] = {app: [] for app in ToolsetApplication.ALL}

        self.setup_toolset_details()
        self.load_initial_applications_selection()
        self.load_applications()
        self.load_update_state()
        self.load_bindings()
        self.setup_status()
        self.connect("map", self.on_map)

        toolset.event_bus.subscribe(ToolsetEvents.SPAWNED_CHANGED, self.load_bindings)
        toolset.event_bus.subscribe(ToolsetEvents.SPAWNED_CHANGED, self.setup_status)
        toolset.event_bus.subscribe(ToolsetEvents.IN_USE_CHANGED, self.setup_status)
        toolset.event_bus.subscribe(ToolsetEvents.IS_RESERVED_CHANGED, self.setup_toolset_details)
        toolset.event_bus.subscribe(ToolsetEvents.IS_RESERVED_CHANGED, self.setup_status)
        MultiStageProcess.event_bus.subscribe(MultiStageProcessEvent.STARTED_PROCESSES_CHANGED, self.toolsets_updates_updated)

    def on_map(self, widget):
        # Disables toolset_name_row auto focus on start
        self.get_root().set_focus(None)

    # --------------------------------------------------------------------------
    # Main details:

    def setup_toolset_details(self, _ = None):
        """Displays main details of the toolset."""
        self.toolset_name_row.set_text(self.toolset.name)
        self.status_file_row.set_subtitle(self.toolset.squashfs_file)
        self.status_size_row.set_subtitle(get_file_size_string(self.toolset.squashfs_file) or "unknown")
        source = self.toolset.metadata.get('source')
        timestamp_date_created = self.toolset.metadata.get('date_created')
        timestamp_date_updated = self.toolset.metadata.get('date_updated')
        allow_binpkgs = self.toolset.metadata.get('allow_binpkgs', False)
        date_created = datetime.fromtimestamp(timestamp_date_created) if isinstance(timestamp_date_created, int) else None
        date_updated = datetime.fromtimestamp(timestamp_date_updated) if isinstance(timestamp_date_updated, int) else None
        source_url = urlparse(source).path if source else None
        filename = os.path.basename(source_url) if source_url else None
        # Display source, date_created, date_updateds
        self.toolset_date_created_row.set_subtitle(date_created.strftime("%Y-%d-%m %H:%M") if date_created else "unknown")
        self.toolset_date_updated_row.set_subtitle(date_updated.strftime("%Y-%d-%m %H:%M") if date_created else "unknown")
        self.toolset_source_row.set_subtitle(filename or "unknown")
        self.allow_binpkgs_checkbox.set_active(allow_binpkgs)

    def setup_status(self, _ = None):
        """Updates controls visibility and sensitivity for current status."""
        self.tag_free.set_visible(not self.toolset.spawned and not self.toolset.in_use and not self.toolset.is_reserved)
        self.tag_store_changes.set_visible(self.toolset.spawned and self.toolset.store_changes)
        self.tag_is_reserved.set_visible(self.toolset.is_reserved)
        self.tag_in_use.set_visible(self.toolset.in_use)
        self.tag_spawned.set_visible(self.toolset.spawned)
        self.tag_updating.set_visible(self.update_in_progress and self.update_in_progress.status == MultiStageProcessState.IN_PROGRESS)
        self.tag_update_succeded.set_visible(self.update_in_progress and self.update_in_progress.status == MultiStageProcessState.COMPLETED)
        self.tag_update_failed.set_visible(self.update_in_progress and self.update_in_progress.status == MultiStageProcessState.FAILED)
        self.action_button_spawn.set_sensitive(not self.toolset.spawned and not self.toolset.in_use and not self.toolset.is_reserved)
        self.action_button_spawn.set_visible(not self.toolset.spawned)
        self.action_button_unspawn.set_sensitive(self.toolset.spawned and not self.toolset.in_use and not self.toolset.is_reserved)
        self.action_button_unspawn.set_visible(self.toolset.spawned)
        self.action_button_chroot.set_sensitive(not self.toolset.in_use and not self.toolset.is_reserved)
        self.action_button_update.set_sensitive(not self.toolset.in_use and not self.toolset.is_reserved)
        self.action_button_delete.set_sensitive(not self.toolset.spawned and not self.toolset.in_use and not self.toolset.is_reserved)
        self.status_bindings_row.set_visible(self.toolset.spawned)
        self.status_update_row.set_visible(self.update_in_progress)
        self.status_update_row.set_subtitle(
            "" if not self.update_in_progress else
            "Update in progress" if self.update_in_progress.status == MultiStageProcessState.IN_PROGRESS else
            "Update completed" if self.update_in_progress.status == MultiStageProcessState.COMPLETED else
            "Update failed" if self.update_in_progress.status == MultiStageProcessState.FAILED else
            ""
        )
        self.status_update_progress_label.set_visible(self.update_in_progress and self.update_in_progress.status == MultiStageProcessState.IN_PROGRESS)
        self.applications_button_apply.set_sensitive(
            not self.toolset.in_use and not self.toolset.is_reserved and
            (not self.update_in_progress or self.update_in_progress.status == MultiStageProcessState.COMPLETED or self.update_in_progress.status == MultiStageProcessState.FAILED)
        )
        self.applications_actions_container.set_visible(self.apps_changed)

    def load_bindings(self, _ = None):
        """Loads toolset bindings rows."""
        if hasattr(self, "_binding_rows"):
            for row in self._binding_rows:
                self.status_bindings_row.remove(row)
        self.status_bindings_row.set_expanded(False)
        self._binding_rows = []
        if self.toolset.current_bindings:
            for binding in self.toolset.current_bindings:
                row = Adw.ActionRow(title=binding.mount_path)
                access_str = "RW" if binding.store_changes else "RO"
                source_str = f"(Toolset){binding.toolset_path} ({access_str})" if binding.toolset_path else f"(Host){binding.host_path} ({access_str})" if binding.host_path else "(Temp)"
                source_label = Gtk.Label(label=source_str)
                source_label.get_style_context().add_class("dimmed")
                row.add_suffix(source_label)
                self.status_bindings_row.add_row(row)
                self._binding_rows.append(row)

    def load_update_state(self, started_processes: list[ToolsetUpdate] | None = None):
        if started_processes is None:
            started_processes = MultiStageProcess.get_started_processes_by_class(ToolsetUpdate)
        if self.update_in_progress is not None :
            self.update_in_progress.event_bus.unsubscribe(MultiStageProcessEvent.STATE_CHANGED, self)
        self.update_in_progress = next((process for process in started_processes if process.toolset == self.toolset), None)
        if self.update_in_progress:
            self.update_in_progress.event_bus.subscribe(
                MultiStageProcessEvent.STATE_CHANGED,
                self.toolsets_update_process_state_changed,
                self
            )
            self.status_update_progress_label.set_label(f"{int(self.update_in_progress.progress * 100)}%")
            self.update_in_progress.event_bus.subscribe(
                MultiStageProcessEvent.PROGRESS_CHANGED,
                self.toolsets_update_process_progress_changed,
                self
            )

    def toolsets_updates_updated(self, process_class: type[MultiStageProcess], started_processes: list[MultiStageProcess]):
        if issubclass(process_class, ToolsetUpdate):
            self.load_update_state(started_processes=started_processes)
            self.setup_status()

    def toolsets_update_process_state_changed(self, state: MultiStageProcessState):
        self.setup_status()

    def toolsets_update_process_progress_changed(self, progress: float):
        self.status_update_progress_label.set_label(f"{int(progress * 100)}%")

    @Gtk.Template.Callback()
    def status_update_row_clicked(self, sender):
        update_view = MultistageProcessExecutionView()
        update_view.set_multistage_process(multistage_process=self.update_in_progress)
        self.content_navigation_view.push_view(update_view, title="Updating toolset")

    # --------------------------------------------------------------------------
    # Changing toolset name:

    @Gtk.Template.Callback()
    def on_toolset_name_activate(self, sender):
        self.toolset.name = self.toolset_name_row.get_text()
        Repository.TOOLSETS.save()
        self.get_root().set_focus(None)

    # --------------------------------------------------------------------------
    # Apps selection:

    def load_initial_applications_selection(self):
        """Load initial selection of apps details."""
        self.tools_selection.clear()
        self.tools_selection_versions.clear()
        self.tools_selection_patches.clear()
        for app in ToolsetApplication.ALL:
            app_install = self.toolset.get_app_install(app=app)
            self.tools_selection[app] = app_install is not None
            self.tools_selection_versions[app] = app_install.variant if app_install else app.versions[0]
            self.tools_selection_patches[app] = app_install.patches[:] if app_install else []

    def load_applications(self):
        """Prepares applications rows."""
        if hasattr(self, "_apps_rows"):
            for row in self._apps_rows:
                self.applications_group.remove(row)
        self._apps_rows = []
        for app in ToolsetApplication.ALL:
            app_install = self.toolset.get_app_install(app=app)
            if app.auto_select:
                continue
            if self.tools_selection[app]:
                subtitle = f"{app_install.variant.name}: {app_install.version}, Patched" if app_install.patches else f"{app_install.variant.name}: {app_install.version}"
            else:
                subtitle = "Not installed"
            row = Adw.ExpanderRow(title=app.name, subtitle=subtitle)
            row.set_enable_expansion(self.tools_selection[app])

            check_button = Gtk.CheckButton()
            check_button.set_active(self.tools_selection[app])
            check_button.connect("toggled", self._on_tool_selected, app, row)
            row.check_button = check_button
            row.add_prefix(check_button)

            versions_row = Adw.ActionRow()
            versions_row.set_activatable(False)

            toggle_group = Adw.ToggleGroup()
            toggle_group.add_css_class("round")
            toggle_group.add_css_class("caption")
            for version in app.versions:
                toggle = Adw.Toggle(label=version.name)
                toggle_group.add(toggle)
            if app_install: # Activate selected app variant toggle
                for i, version in enumerate(app.versions):
                    if version == app_install.variant:
                        toggle_group.set_active(i)
                        break
            def on_toggle_clicked(group, pspec, app, row):
                index = group.get_active()
                self.tools_selection_versions[app] = app.versions[index]
                self.apps_changed = True
                self.setup_status()
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

            for patch_filename in self.tools_selection_patches[app]:
                self._add_patch_file_row(app, row, patch_filename)

            self.applications_group.add(row)
            self._apps_rows.append(row)

    def _add_patch_file_row(self, app, app_row, file: Gio.File | str):
        if isinstance(file, Gio.File):
            patch_row = Adw.ActionRow(title=file.get_basename(), subtitle="Patch file", icon_name="copy-svgrepo-com-symbolic")
        else:
            patch_row = Adw.ActionRow(title=file, subtitle="Patch file", icon_name="copy-svgrepo-com-symbolic")
        remove_button = Gtk.Button()
        remove_button_content = Adw.ButtonContent(label="Remove", icon_name="error-box-svgrepo-com-symbolic")
        remove_button.set_child(remove_button_content)
        remove_button.get_style_context().add_class("destructive-action")
        remove_button.get_style_context().add_class("flat")
        remove_button.get_style_context().add_class("caption")
        remove_button.connect("clicked", self._delete_patch_pressed, app, app_row, patch_row)
        remove_button.file = file
        wrapper = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER)
        wrapper.append(remove_button)
        patch_row.add_suffix(wrapper)
        app_row.add_row(patch_row)

    def _on_tool_selected(self, button: Gtk.CheckButton, app: ToolsetApplication, row: Adw.ExpanderRow):
        """Application checkbox toggled."""
        self.tools_selection[app] = button.get_active()
        row.set_enable_expansion(self.tools_selection[app])
        row.set_expanded(False)
        self.apps_changed = True
        self.setup_status()

    def _delete_patch_pressed(self, remove_button, app, app_row, patch_row):
        self.tools_selection_patches[app].remove(remove_button.file)
        app_row.remove(patch_row)
        self.apps_changed = True
        self.setup_status()

    def _on_add_patch_clicked(self, add_patch_row, app, app_row):
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
                remove_button.connect("clicked", self._delete_patch_pressed, app, app_row, patch_row)
                remove_button.file = file
                wrapper = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER)
                wrapper.append(remove_button)
                patch_row.add_suffix(wrapper)
                app_row.add_row(patch_row)
                self.apps_changed = True
                self.setup_status()
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
        file_dialog.open(getattr(self, '_window', None) or self.get_root(), None, on_file_open_response)

    @Gtk.Template.Callback()
    def on_allow_binpkgs_toggled(self, checkbox):
        self.toolset.metadata['allow_binpkgs'] = checkbox.get_active()
        Repository.TOOLSETS.save()

    @Gtk.Template.Callback()
    def applications_button_cancel_clicked(self, sender):
        self.load_initial_applications_selection()
        self.load_applications()
        self.apps_changed = False
        self.setup_status()

    @Gtk.Template.Callback()
    def applications_button_apply_clicked(self, sender):
        def update(authorization_keeper: AuthorizationKeeper):
            if authorization_keeper:
                apps_selection = [
                    ToolsetApplicationSelection(
                        app=app,
                        version=self.tools_selection_versions[app],
                        selected=self.tools_selection[app],
                        patches=self.tools_selection_patches[app]
                    )
                    for app, _ in self.tools_selection.items()
                ]
            self.start_update(authorization_keeper=authorization_keeper, apps_selection=apps_selection)
        RootHelperClient.shared().authorize_and_run(callback=update)

    def start_update(self, authorization_keeper: AuthorizationKeeper, apps_selection: list[ToolsetApplicationSelection] | None = None):
        update = ToolsetUpdate(toolset=self.toolset, apps_selection=apps_selection)
        update.start(authorization_keeper=authorization_keeper)
        self.show_update(update=update)

    def show_update(self, update: ToolsetUpdate):
        update_view = MultistageProcessExecutionView()
        update_view.set_multistage_process(multistage_process=update)
        self.content_navigation_view.push_view(update_view, title="Updating toolset")

    # --------------------------------------------------------------------------
    # Toolset actions:

    @Gtk.Template.Callback()
    def action_button_spawn_clicked(self, sender):
        def spawn(authorization_keeper: AuthorizationKeeper):
            if authorization_keeper:
                try:
                    self.toolset.reserve()
                    self.toolset.spawn()
                    self.toolset.analyze()
                except Exception as e:
                    print(e)
                finally:
                    self.toolset.release()
        RootHelperClient.shared().authorize_and_run(callback=spawn)

    @Gtk.Template.Callback()
    def action_button_unspawn_clicked(self, sender):
        def unspawn(authorization_keeper: AuthorizationKeeper):
            if authorization_keeper:
                try:
                    self.toolset.reserve()
                    self.toolset.unspawn()
                except Exception as e:
                    print(e)
                finally:
                    self.toolset.release()
        RootHelperClient.shared().authorize_and_run(callback=unspawn)

    @Gtk.Template.Callback()
    def action_button_chroot_clicked(self, sender):
        pass

    @Gtk.Template.Callback()
    def action_button_update_clicked(self, sender):
        def update(authorization_keeper: AuthorizationKeeper):
            if authorization_keeper:
                self.start_update(authorization_keeper=authorization_keeper)
        RootHelperClient.shared().authorize_and_run(callback=update)

    @Gtk.Template.Callback()
    def action_button_delete_clicked(self, sender):
        os.remove(self.toolset.squashfs_file)
        Repository.TOOLSETS.value.remove(self.toolset)
        if hasattr(self, "_window"):
            self._window.close()
        elif hasattr(self, "content_navigation_view"):
            self.content_navigation_view.pop()


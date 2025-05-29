from gi.repository import Gtk, Adw
from .toolset import Toolset, ToolsetEvents
from .toolset_application import ToolsetApplication
from .helper_functions import get_file_size_string
import uuid
import os
from datetime import datetime
from urllib.parse import urlparse
from .root_helper_client import RootHelperClient, AuthorizationKeeper
from .repository import Repository
from .toolset_update import ToolsetUpdate
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
    tag_free = Gtk.Template.Child()
    tag_store_changes = Gtk.Template.Child()
    tag_spawned = Gtk.Template.Child()
    tag_in_use = Gtk.Template.Child()
    tag_is_reserved = Gtk.Template.Child()
    action_button_spawn = Gtk.Template.Child()
    action_button_unspawn = Gtk.Template.Child()
    action_button_chroot = Gtk.Template.Child()
    action_button_update = Gtk.Template.Child()
    action_button_delete = Gtk.Template.Child()
    applications_settings_allow_binpkgs_row = Gtk.Template.Child()

    def __init__(self, toolset: Toolset, content_navigation_view: Adw.NavigationView | None = None):
        super().__init__()
        self.toolset = toolset
        self.content_navigation_view = content_navigation_view
        self.setup_toolset_details()
        self.load_applications()
        self.load_bindings()
        self.setup_status()
        toolset.event_bus.subscribe(ToolsetEvents.SPAWNED_CHANGED, self.load_bindings)
        toolset.event_bus.subscribe(ToolsetEvents.SPAWNED_CHANGED, self.setup_status)
        toolset.event_bus.subscribe(ToolsetEvents.IN_USE_CHANGED, self.setup_status)
        toolset.event_bus.subscribe(ToolsetEvents.IS_RESERVED_CHANGED, self.setup_toolset_details)
        toolset.event_bus.subscribe(ToolsetEvents.IS_RESERVED_CHANGED, self.setup_status)
        self.connect("map", self.on_map)

    @Gtk.Template.Callback()
    def on_toolset_name_activate(self, sender):
        print(f"Save {self.toolset_name_row.get_text()}")
        self.toolset.name = self.toolset_name_row.get_text()
        Repository.TOOLSETS.save()
        self.get_root().set_focus(None)

    def on_map(self, widget):
        # Disables toolset_name_row auto focus on start
        self.get_root().set_focus(None)

    def setup_toolset_details(self, _ = None):
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
        self.applications_settings_allow_binpkgs_row.set_subtitle("Yes" if allow_binpkgs else "No")

    def load_applications(self):
        if hasattr(self, "_apps_rows"):
            for row in self._apps_rows:
                self.applications_group.remove(row)
        self._apps_rows = []
        for app in ToolsetApplication.ALL:
            if app.auto_select:
                continue
            app_install = self.toolset.get_app_install(app=app)
            if app_install:
                subtitle = f"{app_install.variant.name}: {app_install.version}, Patched" if app_install.patched else f"{app_install.variant.name}: {app_install.version}"
            else:
                subtitle = "Not installed"
            app_row = Adw.ActionRow(title=app.name, subtitle=subtitle)
            self.applications_group.add(app_row)
            self._apps_rows.append(app_row)

    def setup_status(self, _ = None):
        self.tag_free.set_visible(not self.toolset.spawned and not self.toolset.in_use and not self.toolset.is_reserved)
        self.tag_store_changes.set_visible(self.toolset.spawned and self.toolset.store_changes)
        self.tag_is_reserved.set_visible(self.toolset.is_reserved)
        self.tag_in_use.set_visible(self.toolset.in_use)
        self.tag_spawned.set_visible(self.toolset.spawned)
        self.action_button_spawn.set_sensitive(not self.toolset.spawned and not self.toolset.in_use and not self.toolset.is_reserved)
        self.action_button_spawn.set_visible(not self.toolset.spawned)
        self.action_button_unspawn.set_sensitive(self.toolset.spawned and not self.toolset.in_use and not self.toolset.is_reserved)
        self.action_button_unspawn.set_visible(self.toolset.spawned)
        self.action_button_chroot.set_sensitive(not self.toolset.in_use and not self.toolset.is_reserved)
        self.action_button_update.set_sensitive(not self.toolset.in_use and not self.toolset.is_reserved)
        self.action_button_delete.set_sensitive(not self.toolset.spawned and not self.toolset.in_use and not self.toolset.is_reserved)
        self.status_bindings_row.set_visible(self.toolset.spawned)

    def load_bindings(self, _ = None):
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
                update = ToolsetUpdate(toolset=self.toolset)
                update.start(authorization_keeper=authorization_keeper)
                update_view = MultistageProcessExecutionView()
                update_view.set_multistage_process(multistage_process=update)
                self.content_navigation_view.push_view(update_view, title="Updating toolset")
        RootHelperClient.shared().authorize_and_run(callback=update)

    @Gtk.Template.Callback()
    def action_button_delete_clicked(self, sender):
        os.remove(self.toolset.squashfs_file)
        Repository.TOOLSETS.value.remove(self.toolset)
        if hasattr(self, "_window"):
            self._window.close()
        elif hasattr(self, "content_navigation_view"):
            self.content_navigation_view.pop()


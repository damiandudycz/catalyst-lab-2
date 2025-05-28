from gi.repository import Gtk, Adw
from .toolset import Toolset, ToolsetEvents
from .toolset_application import ToolsetApplication
from .helper_functions import get_file_size_string
import uuid
import os
from datetime import datetime
from urllib.parse import urlparse
from .root_helper_client import RootHelperClient, AuthorizationKeeper

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
    tag_free = Gtk.Template.Child()
    tag_spawned = Gtk.Template.Child()
    tag_in_use = Gtk.Template.Child()
    tag_is_reserved = Gtk.Template.Child()
    action_button_spawn = Gtk.Template.Child()
    action_button_unspawn = Gtk.Template.Child()
    action_button_chroot = Gtk.Template.Child()
    action_button_update = Gtk.Template.Child()
    action_button_delete = Gtk.Template.Child()

    def __init__(self, toolset: Toolset, content_navigation_view: Adw.NavigationView | None = None):
        super().__init__()
        self.toolset = toolset
        self.content_navigation_view = content_navigation_view
        self.setup_toolset_details()
        self.load_applications()
        self.setup_status()
        toolset.event_bus.subscribe(ToolsetEvents.SPAWNED_CHANGED, self.setup_status)
        toolset.event_bus.subscribe(ToolsetEvents.IN_USE_CHANGED, self.setup_status)
        toolset.event_bus.subscribe(ToolsetEvents.IS_RESERVED_CHANGED, self.setup_status)
        self.connect("map", self.on_map)

    def on_map(self, widget):
        # Disables toolset_name_row auto focus on start
        self.get_root().set_focus(None)

    def setup_toolset_details(self):
        self.toolset_name_row.set_text(self.toolset.name)
        self.status_file_row.set_subtitle(self.toolset.squashfs_file)
        self.status_size_row.set_subtitle(get_file_size_string(self.toolset.squashfs_file) or "unknown")
        source = self.toolset.metadata.get('source')
        timestamp_date_created = self.toolset.metadata.get('date_created')
        timestamp_date_updated = self.toolset.metadata.get('date_updated')
        date_created = datetime.fromtimestamp(timestamp_date_created) if isinstance(timestamp_date_created, int) else None
        date_updated = datetime.fromtimestamp(timestamp_date_updated) if isinstance(timestamp_date_updated, int) else None
        source_url = urlparse(source).path if source else None
        filename = os.path.basename(source_url) if source_url else None
        # Display source, date_created, date_updateds
        self.toolset_date_created_row.set_subtitle(date_created.strftime("%Y-%d-%m %H:%M") if date_created else "unknown")
        self.toolset_date_updated_row.set_subtitle(date_updated.strftime("%Y-%d-%m %H:%M") if date_created else "unknown")
        self.toolset_source_row.set_subtitle(filename or "unknown")

    def load_applications(self):
        for app in ToolsetApplication.ALL:
            app_metadata = self.toolset.metadata.get(app.package)
            if app_metadata:
                version = app_metadata.get('version')
                version_id = app_metadata.get('version_id')
                version_id_uuid = uuid.UUID(version_id)
                version_variant = next((version for version in app.versions if version.id == version_id_uuid), None)
                app_row = Adw.ActionRow(title=app.name, subtitle=f"{version_variant.name}: {version}")
                self.applications_group.add(app_row)

    def setup_status(self, _ = None):
        self.tag_free.set_visible(not self.toolset.spawned and not self.toolset.in_use and not self.toolset.is_reserved)
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

    @Gtk.Template.Callback()
    def action_button_spawn_clicked(self, sender):
        def spawn(authorization_keeper: AuthorizationKeeper):
            if authorization_keeper:
                self.toolset.reserve()
                self.toolset.spawn()
                self.toolset.release()
        RootHelperClient.shared().authorize_and_run(callback=spawn)

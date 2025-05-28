from gi.repository import Gtk, Adw
from .toolset import Toolset, ToolsetApplication
from .helper_functions import get_file_size_string
import uuid
import os
import datetime

@Gtk.Template(resource_path='/com/damiandudycz/CatalystLab/ui/toolset_details/toolset_details_view.ui')
class ToolsetDetailsView(Gtk.Box):
    __gtype_name__ = "ToolsetDetailsView"

    toolset_name_row = Gtk.Template.Child()
    applications_group = Gtk.Template.Child()
    status_file_row = Gtk.Template.Child()
    status_size_row = Gtk.Template.Child()

    def __init__(self, toolset: Toolset, content_navigation_view: Adw.NavigationView | None = None):
        super().__init__()
        self.toolset = toolset
        self.content_navigation_view = content_navigation_view
        self.setup_toolset_details()
        self.load_applications()
        self.connect("map", self.on_map)

    def on_map(self, widget):
        # Disables toolset_name_row auto focus on start
        self.get_root().set_focus(None)

    def setup_toolset_details(self):
        self.toolset_name_row.set_text(self.toolset.name)
        self.status_file_row.set_subtitle(self.toolset.squashfs_file)
        self.status_size_row.set_subtitle(get_file_size_string(self.toolset.squashfs_file) or "unknown")
        source_url = self.toolset.metadata.get('source')
        timestamp_date_created = self.toolset.metadata.get('date_created')
        timestamp_date_updated = self.toolset.metadata.get('date_updated')
        date_created = datetime.fromtimestamp(timestamp_date_created) if isinstance(timestamp_date_created, int) else None
        date_updated = datetime.fromtimestamp(timestamp_date_updated) if isinstance(timestamp_date_updated, int) else None
        # Display source, date_created, date_updateds

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


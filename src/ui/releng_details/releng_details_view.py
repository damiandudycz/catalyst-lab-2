from gi.repository import Gtk, Adw, Gio, GLib
from .releng_directory import RelengDirectory, RelengDirectoryEvent, RelengDirectoryStatus
from .releng_manager import RelengManager
from .repository import Repository
import threading, os

@Gtk.Template(resource_path='/com/damiandudycz/CatalystLab/ui/releng_details/releng_details_view.ui')
class RelengDetailsView(Gtk.Box):
    __gtype_name__ = "RelengDetailsView"

    directory_name_row = Gtk.Template.Child()
    status_directory_branch_name_row = Gtk.Template.Child()
    status_directory_date_updated_row = Gtk.Template.Child()
    status_directory_path_row = Gtk.Template.Child()
    status_logs_row = Gtk.Template.Child()
    tag_unknown = Gtk.Template.Child()
    tag_unchanged = Gtk.Template.Child()
    tag_changed = Gtk.Template.Child()
    action_button_save_changes = Gtk.Template.Child()
    action_button_update = Gtk.Template.Child()
    action_button_delete = Gtk.Template.Child()

    def __init__(self, releng_directory: RelengDirectory, content_navigation_view: Adw.NavigationView | None = None):
        super().__init__()
        self.releng_directory = releng_directory
        self.content_navigation_view = content_navigation_view
        self.setup_releng_directory_details()
        self.setup_releng_directory_logs()
        self.connect("map", self.on_map)
        self.setup_status()
        releng_directory.event_bus.subscribe(RelengDirectoryEvent.STATUS_CHANGED, self.setup_releng_directory_details)
        releng_directory.event_bus.subscribe(RelengDirectoryEvent.LOGS_CHANGED, self.setup_releng_directory_logs)

    def on_map(self, widget):
        # Disables toolset_name_row auto focus on start
        self.get_root().set_focus(None)

    def setup_releng_directory_details(self, event_data = None):
        """Displays main details of the releng directory."""
        self.directory_name_row.set_text(self.releng_directory.name)
        last_commit_date = self.releng_directory.last_commit_date
        self.status_directory_branch_name_row.set_subtitle(self.releng_directory.branch_name)
        self.status_directory_date_updated_row.set_subtitle(last_commit_date.strftime("%Y-%d-%m %H:%M") if last_commit_date else "unknown")
        self.status_directory_path_row.set_subtitle(self.releng_directory.directory_path())
        self.setup_status()
        if event_data is None:
            self.releng_directory.update_status()
            self.releng_directory.update_logs()

    def setup_releng_directory_logs(self, event_data = None):
        if hasattr(self, "_log_rows"):
            for row in self._log_rows:
                self.status_logs_row.remove(row)
        self.status_logs_row.set_expanded(False)
        self._log_rows = []
        max_logs = 10
        if self.releng_directory.logs:
            i = 1
            for log in self.releng_directory.logs:
                print(log)
                print("------------------")
                message = log.get("message")
                author = log.get("author")
                date = log.get("date")
                row = Adw.ActionRow(title=message, subtitle=f"{date.strftime("%Y-%d-%m %H:%M")}, {author}")
                self.status_logs_row.add_row(row)
                self._log_rows.append(row)
                i += 1
                if i > max_logs:
                    break

    def setup_status(self, _ = None):
        """Updates controls visibility and sensitivity for current status."""
        self.tag_unknown.set_visible(self.releng_directory.status == RelengDirectoryStatus.UNKNOWN)
        self.tag_unchanged.set_visible(self.releng_directory.status == RelengDirectoryStatus.UNCHANGED)
        self.tag_changed.set_visible(self.releng_directory.status == RelengDirectoryStatus.CHANGED)
        self.action_button_save_changes.set_sensitive(self.releng_directory.status == RelengDirectoryStatus.CHANGED)
        self.action_button_update.set_sensitive(self.releng_directory.status == RelengDirectoryStatus.UNCHANGED)

    @Gtk.Template.Callback()
    def action_button_save_changes_clicked(self, sender):
        pass

    @Gtk.Template.Callback()
    def action_button_update_clicked(self, sender):
        pass

    @Gtk.Template.Callback()
    def action_button_delete_clicked(self, sender):
        RelengManager.shared().remove_releng_directory(releng_directory=self.releng_directory)
        if hasattr(self, "_window"):
            self._window.close()
        elif hasattr(self, "content_navigation_view"):
            self.content_navigation_view.pop()


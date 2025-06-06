from gi.repository import Gtk, Adw, Gio, GLib
from .releng_directory import RelengDirectory
from .git_directory import GitDirectoryEvent, GitDirectoryStatus
from .releng_manager import RelengManager
from .repository import Repository
from .releng_update import RelengUpdate
import threading, os
from .multistage_process import MultiStageProcess, MultiStageProcessEvent, MultiStageProcessState
from .multistage_process_execution_view import MultistageProcessExecutionView
from .event_bus import SharedEvent

@Gtk.Template(resource_path='/com/damiandudycz/CatalystLab/ui/releng_details/releng_details_view.ui')
class RelengDetailsView(Gtk.Box):
    __gtype_name__ = "RelengDetailsView"

    directory_name_row = Gtk.Template.Child()
    name_used_row = Gtk.Template.Child()
    status_directory_branch_name_row = Gtk.Template.Child()
    status_directory_date_updated_row = Gtk.Template.Child()
    status_directory_path_row = Gtk.Template.Child()
    status_logs_row = Gtk.Template.Child()
    tag_unknown = Gtk.Template.Child()
    tag_unchanged = Gtk.Template.Child()
    tag_changed = Gtk.Template.Child()
    tag_updating = Gtk.Template.Child()
    tag_update_succeded = Gtk.Template.Child()
    tag_update_failed = Gtk.Template.Child()
    tag_update_available = Gtk.Template.Child()
    action_button_save_changes = Gtk.Template.Child()
    action_button_update = Gtk.Template.Child()
    action_button_delete = Gtk.Template.Child()
    status_update_row = Gtk.Template.Child()
    status_update_progress_label = Gtk.Template.Child()

    def __init__(self, releng_directory: RelengDirectory, content_navigation_view: Adw.NavigationView | None = None):
        super().__init__()
        self.releng_directory = releng_directory
        self.update_in_progress: RelengUpdate | None = None
        self.content_navigation_view = content_navigation_view
        self._changes_action_group = Gio.SimpleActionGroup()
        self._add_changes_action("save_changes", self.save_changes)
        self._add_changes_action("discard_changes", self.discard_changes)
        self.insert_action_group("changes", self._changes_action_group)
        self.setup_releng_directory_details()
        self.setup_releng_directory_logs()
        self.load_update_state()
        self.setup_status()
        self.connect("map", self.on_map)
        releng_directory.event_bus.subscribe(SharedEvent.STATE_UPDATED, self.setup_releng_directory_details)
        releng_directory.event_bus.subscribe(GitDirectoryEvent.LOGS_CHANGED, self.setup_releng_directory_logs)
        MultiStageProcess.event_bus.subscribe(MultiStageProcessEvent.STARTED_PROCESSES_CHANGED, self.releng_directories_updates_updated)

    def on_map(self, widget):
        # Disables directory_name_row auto focus on start
        self.get_root().set_focus(None)

    def setup_releng_directory_details(self, object = None):
        if object is not None and object != self.releng_directory:
            return
        """Displays main details of the releng directory."""
        self.directory_name_row.set_text(self.releng_directory.name)
        last_commit_date = self.releng_directory.last_commit_date
        self.status_directory_branch_name_row.set_subtitle(self.releng_directory.branch_name)
        self.status_directory_date_updated_row.set_subtitle(last_commit_date.strftime("%Y-%d-%m %H:%M") if last_commit_date else "unknown")
        self.status_directory_path_row.set_subtitle(self.releng_directory.directory_path())
        self.setup_status()
        if object is None:
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
        self.tag_unknown.set_visible(self.releng_directory.status == GitDirectoryStatus.UNKNOWN)
        self.tag_unchanged.set_visible(self.releng_directory.status == GitDirectoryStatus.UNCHANGED)
        self.tag_update_available.set_visible(self.releng_directory.has_remote_changes)
        self.tag_updating.set_visible(
            self.update_in_progress
            and self.update_in_progress.status == MultiStageProcessState.IN_PROGRESS
        )
        self.tag_update_succeded.set_visible(
            self.update_in_progress
            and self.update_in_progress.status == MultiStageProcessState.COMPLETED
        )
        self.tag_update_failed.set_visible(
            self.update_in_progress
            and self.update_in_progress.status == MultiStageProcessState.FAILED
        )
        self.status_update_row.set_visible(self.update_in_progress)
        self.status_update_row.set_subtitle(
            "" if not self.update_in_progress
            else "Update in progress" if self.update_in_progress.status == MultiStageProcessState.IN_PROGRESS
            else "Update completed" if self.update_in_progress.status == MultiStageProcessState.COMPLETED
            else "Update failed" if self.update_in_progress.status == MultiStageProcessState.FAILED
            else ""
        )
        if self.releng_directory.has_remote_changes:
            self.action_button_update.get_style_context().add_class("suggested-action")
        else:
            self.action_button_update.get_style_context().remove_class("suggested-action")
        self.status_update_progress_label.set_visible(
            self.update_in_progress
            and self.update_in_progress.status == MultiStageProcessState.IN_PROGRESS
        )
        self.tag_changed.set_visible(self.releng_directory.status == GitDirectoryStatus.CHANGED)
        self.action_button_save_changes.set_sensitive(
            self.releng_directory.status == GitDirectoryStatus.CHANGED
            and (
                not self.update_in_progress
                or self.update_in_progress.status == MultiStageProcessState.COMPLETED
                or self.update_in_progress.status == MultiStageProcessState.FAILED
            )
        )
        self.action_button_update.set_sensitive(
            self.releng_directory.status == GitDirectoryStatus.UNCHANGED
            and (
                not self.update_in_progress
                or self.update_in_progress.status == MultiStageProcessState.COMPLETED
                or self.update_in_progress.status == MultiStageProcessState.FAILED
            )
        )

    def load_update_state(self, started_processes: list[RelengUpdate] | None = None):
        if started_processes is None:
            started_processes = MultiStageProcess.get_started_processes_by_class(RelengUpdate)
        if self.update_in_progress is not None :
            self.update_in_progress.event_bus.unsubscribe(MultiStageProcessEvent.STATE_CHANGED, self)
        self.update_in_progress = next((process for process in started_processes if process.directory == self.releng_directory), None)
        if self.update_in_progress:
            self.update_in_progress.event_bus.subscribe(
                MultiStageProcessEvent.STATE_CHANGED,
                self.releng_directories_update_process_state_changed,
                self
            )
            self.status_update_progress_label.set_label(f"{int(self.update_in_progress.progress * 100)}%")
            self.update_in_progress.event_bus.subscribe(
                MultiStageProcessEvent.PROGRESS_CHANGED,
                self.releng_directories_update_process_progress_changed,
                self
            )

    def releng_directories_updates_updated(self, process_class: type[MultiStageProcess], started_processes: list[MultiStageProcess]):
        if issubclass(process_class, RelengUpdate):
            self.load_update_state(started_processes=started_processes)
            self.setup_status()

    def releng_directories_update_process_state_changed(self, state: MultiStageProcessState):
        self.setup_status()

    def releng_directories_update_process_progress_changed(self, progress: float):
        self.status_update_progress_label.set_label(f"{int(progress * 100)}%")

    @Gtk.Template.Callback()
    def on_directory_name_activate(self, sender):
        new_name = self.directory_name_row.get_text()
        if new_name == self.releng_directory.name:
            self.get_root().set_focus(None)
            return
        is_name_available = RelengManager.shared().is_name_available(name=new_name)
        try:
            if not is_name_available:
                raise RuntimeError(f"Releng directory name {new_name} is not available")
            RelengManager.shared().rename_directory(directory=self.releng_directory, name=new_name)
            self.get_root().set_focus(None)
            self.setup_releng_directory_details()
        except Exception as e:
            print(f"Error renaming releng directory: {e}")
            self.directory_name_row.add_css_class("error")
            self.directory_name_row.grab_focus()

    @Gtk.Template.Callback()
    def on_directory_name_changed(self, sender):
        is_name_available = RelengManager.shared().is_name_available(name=self.directory_name_row.get_text()) or self.directory_name_row.get_text() == self.releng_directory.name
        self.name_used_row.set_visible(not is_name_available)
        self.directory_name_row.remove_css_class("error")

    @Gtk.Template.Callback()
    def status_update_row_clicked(self, sender):
        self.show_update(update=self.update_in_progress)

    @Gtk.Template.Callback()
    def action_button_save_changes_clicked(self, sender):
        self.releng_directory.commit_changes()

    def save_changes(self, action, param):
        self.releng_directory.commit_changes()

    def discard_changes(self, action, param):
        self.releng_directory.discard_changes()

    @Gtk.Template.Callback()
    def action_button_update_clicked(self, sender):
        self.start_update()

    @Gtk.Template.Callback()
    def action_button_delete_clicked(self, sender):
        RelengManager.shared().remove_directory(directory=self.releng_directory)
        if hasattr(self, "_window"):
            self._window.close()
        elif hasattr(self, "content_navigation_view"):
            self.content_navigation_view.pop()

    def _add_changes_action(self, name, callback) -> Gio.SimpleAction:
        action = Gio.SimpleAction.new(name, None)
        action.connect("activate", callback)
        self._changes_action_group.add_action(action)
        return action

    def start_update(self):
        if self.update_in_progress:
            self.update_in_progress.clean_from_started_processes()
        update = RelengUpdate(directory=self.releng_directory)
        update.start()
        self.show_update(update=update)

    def show_update(self, update: RelengUpdate):
        update_view = MultistageProcessExecutionView()
        update_view.set_multistage_process(multistage_process=update)
        self.content_navigation_view.push_view(update_view, title="Updating Releng")

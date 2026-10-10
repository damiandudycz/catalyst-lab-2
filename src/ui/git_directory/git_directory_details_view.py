from __future__ import annotations
from gi.repository import Gtk, GLib, Gio, Adw, GObject
from .git_directory import GitDirectoryEvent, GitDirectoryStatus
from .git_manager import GitManager
from .git_update import GitUpdate
import threading, os
from .multistage_process import MultiStageProcess, MultiStageProcessEvent, MultiStageProcessState
from .multistage_process_execution_view import MultistageProcessExecutionView
from .event_bus import SharedEvent

# Import additional classes so that they can be parsed for self.manager_class and self.update_class_name:
from .releng_manager import RelengManager
from .releng_update import RelengUpdate
from .overlay_manager import OverlayManager
from .overlay_update import OverlayUpdate
from .project_manager import ProjectManager
from .project_update import ProjectUpdate

@Gtk.Template(resource_path='/com/damiandudycz/CatalystLab/ui/git_directory/git_directory_details_view.ui')
class GitDirectoryDetailsView(Gtk.Box):
    __gtype_name__ = "GitDirectoryDetailsView"

    directory_name_row = Gtk.Template.Child()
    name_used_row = Gtk.Template.Child()
    status_directory_url_row = Gtk.Template.Child()
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

    # Use these properties if using GitDirectoryDetailsView in .ui file.
    # If created in code, pass manager_class and update_class in initializer.
    manager_class_name = GObject.Property(type=str, default=None)
    update_class_name = GObject.Property(type=str, default=None)

    def __init__(self, git_directory: GitDirectory | None = None, manager_class: Type[GitManager] | None = None, update_class: Type[GitUpdate] | None = None):
        super().__init__()
        self.update_in_progress: GitUpdate | None = None
        self.git_directory = git_directory
        self.manager_class = manager_class
        self.update_class = update_class
        # Actions of Save changes menu are inserted before view is added to window. Inserted later (when realized),
        # menu items don't find them and stay disabled.
        self._changes_action_group = Gio.SimpleActionGroup()
        self._add_changes_action("save_changes", self.save_changes)
        self._add_changes_action("discard_changes", self.discard_changes)
        self.insert_action_group("changes", self._changes_action_group)
        self.connect("realize", self.on_realize)

    def setup(self, git_directory: GitDirectory, content_navigation_view: Adw.NavigationView | None = None):
        """Call this after init, before view appears."""
        self.git_directory = git_directory
        self.content_navigation_view = content_navigation_view

    def on_realize(self, widget):
        self.get_root().set_focus(None)
        if self.manager_class_name and self.manager_class is None:
            self.manager_class = globals().get(self.manager_class_name)
        if self.update_class_name and self.update_class is None:
            self.update_class = globals().get(self.update_class_name)
        self.setup_git_directory_details()
        self.setup_git_directory_logs()
        self.load_update_state()
        self.setup_status()
        self.git_directory.event_bus.subscribe(SharedEvent.STATE_UPDATED, self.setup_git_directory_details)
        self.git_directory.event_bus.subscribe(GitDirectoryEvent.LOGS_CHANGED, self.setup_git_directory_logs)
        MultiStageProcess.event_bus.subscribe(MultiStageProcessEvent.STARTED_PROCESSES_CHANGED, self.git_directories_updates_updated)

    def setup_git_directory_details(self, object = None):
        if object is not None and object != self.git_directory:
            return
        """Displays main details of the git directory."""
        self.directory_name_row.set_text(self.git_directory.name)
        last_commit_date = self.git_directory.last_commit_date
        self.status_directory_url_row.set_subtitle(self.git_directory.remote_url or "(local)")
        self.status_directory_branch_name_row.set_subtitle(self.git_directory.branch_name or "(none)")
        self.status_directory_date_updated_row.set_subtitle(last_commit_date.strftime("%Y-%d-%m %H:%M") if last_commit_date else "unknown")
        self.status_directory_path_row.set_subtitle(self.git_directory.directory_path())
        self.setup_status()
        if object is None:
            self.git_directory.update_status()
            self.git_directory.update_logs()

    def setup_git_directory_logs(self, event_data = None):
        if hasattr(self, "_log_rows"):
            for row in self._log_rows:
                self.status_logs_row.remove(row)
        self.status_logs_row.set_expanded(False)
        self._log_rows = []
        max_logs = 10
        if self.git_directory.logs:
            i = 1
            for log in self.git_directory.logs:
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
        self.tag_unknown.set_visible(self.git_directory.status in (GitDirectoryStatus.UNKNOWN, GitDirectoryStatus.ERROR))
        self.tag_unknown.set_label("Error" if self.git_directory.status == GitDirectoryStatus.ERROR else "Unknown")
        self.tag_unchanged.set_visible(self.git_directory.status == GitDirectoryStatus.UNCHANGED)
        self.tag_update_available.set_visible(self.git_directory.has_remote_changes)
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
        if self.git_directory.has_remote_changes:
            self.action_button_update.get_style_context().add_class("suggested-action")
        else:
            self.action_button_update.get_style_context().remove_class("suggested-action")
        self.status_update_progress_label.set_visible(
            self.update_in_progress
            and self.update_in_progress.status == MultiStageProcessState.IN_PROGRESS
        )
        self.tag_changed.set_visible(self.git_directory.status in (GitDirectoryStatus.CHANGED, GitDirectoryStatus.CONFLICTED))
        self.tag_changed.set_label("Conflicts" if self.git_directory.status == GitDirectoryStatus.CONFLICTED else "Changed")
        self.action_button_save_changes.set_sensitive(
            self.git_directory.status == GitDirectoryStatus.CHANGED
            and (
                not self.update_in_progress
                or self.update_in_progress.status == MultiStageProcessState.COMPLETED
                or self.update_in_progress.status == MultiStageProcessState.FAILED
            )
        )
        self.action_button_update.set_sensitive(
            self.git_directory.status == GitDirectoryStatus.UNCHANGED
            and (
                not self.update_in_progress
                or self.update_in_progress.status == MultiStageProcessState.COMPLETED
                or self.update_in_progress.status == MultiStageProcessState.FAILED
            )
            and self.git_directory.remote_url is not None
        )

    def load_update_state(self, started_processes: list[GitUpdate] | None = None):
        if started_processes is None:
            started_processes = MultiStageProcess.get_started_processes_by_class(GitUpdate)
        if self.update_in_progress is not None :
            self.update_in_progress.event_bus.unsubscribe(MultiStageProcessEvent.STATE_CHANGED, self)
        self.update_in_progress = next((process for process in started_processes if process.directory == self.git_directory), None)
        if self.update_in_progress:
            self.update_in_progress.event_bus.subscribe(
                MultiStageProcessEvent.STATE_CHANGED,
                self.git_directories_update_process_state_changed,
                self
            )
            self.status_update_progress_label.set_label(f"{int(self.update_in_progress.progress * 100)}%")
            self.update_in_progress.event_bus.subscribe(
                MultiStageProcessEvent.PROGRESS_CHANGED,
                self.git_directories_update_process_progress_changed,
                self
            )

    def git_directories_updates_updated(self, process_class: type[MultiStageProcess], started_processes: list[MultiStageProcess]):
        if issubclass(process_class, GitUpdate):
            self.load_update_state(started_processes=started_processes)
            self.setup_status()

    def git_directories_update_process_state_changed(self, state: MultiStageProcessState):
        self.setup_status()

    def git_directories_update_process_progress_changed(self, progress: float):
        self.status_update_progress_label.set_label(f"{int(progress * 100)}%")

    @Gtk.Template.Callback()
    def on_directory_name_activate(self, sender):
        new_name = self.directory_name_row.get_text()
        if new_name == self.git_directory.name:
            self.get_root().set_focus(None)
            return
        is_name_available = self.manager_class.shared().is_name_available(name=new_name)
        try:
            if not is_name_available:
                raise RuntimeError(f"Git directory name {new_name} is not available")
            self.manager_class.shared().rename_directory(directory=self.git_directory, name=new_name)
            self.get_root().set_focus(None)
            self.setup_git_directory_details()
        except Exception as e:
            print(f"Error renaming git directory: {e}")
            self.directory_name_row.add_css_class("error")
            self.directory_name_row.grab_focus()

    @Gtk.Template.Callback()
    def on_directory_name_changed(self, sender):
        is_name_available = self.manager_class.shared().is_name_available(name=self.directory_name_row.get_text()) or self.directory_name_row.get_text() == self.git_directory.name
        self.name_used_row.set_visible(not is_name_available)
        self.directory_name_row.remove_css_class("error")

    @Gtk.Template.Callback()
    def status_update_row_clicked(self, sender):
        self.show_update(update=self.update_in_progress)

    @Gtk.Template.Callback()
    def action_button_save_changes_clicked(self, sender):
        self.ask_commit_message()

    def save_changes(self, action, param):
        self.ask_commit_message()

    def ask_commit_message(self):
        """Dialog with commit message of saved changes."""
        entry = Gtk.Entry(placeholder_text=self.git_directory.DEFAULT_COMMIT_MESSAGE, activates_default=True)
        dialog = Adw.AlertDialog(heading="Save changes", body="Describe the changes, they are committed to Git repository of this directory.",
                                 extra_child=entry)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("save", "Save")
        dialog.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("save")
        dialog.set_close_response("cancel")
        def on_response(dialog, response):
            if response == "save":
                self.git_directory.commit_changes(message=entry.get_text())
        dialog.connect("response", on_response)
        dialog.present(self.get_root())
        entry.grab_focus()

    def discard_changes(self, action, param):
        self.git_directory.discard_changes()

    @Gtk.Template.Callback()
    def action_button_update_clicked(self, sender):
        self.start_update()

    @Gtk.Template.Callback()
    def action_button_delete_clicked(self, sender):
        root = self.get_root()
        # Binary packages folder of deleted project, when no other project uses it.
        get_packages_directory = getattr(self.git_directory, "get_packages_directory", None)
        packages_directory = get_packages_directory() if get_packages_directory else None
        self.manager_class.shared().remove_directory(directory=self.git_directory)
        if packages_directory is not None and not packages_directory.projects:
            ask_delete_unused_packages_directory(root, packages_directory)
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
        update = self.update_class(directory=self.git_directory)
        update.start()
        self.show_update(update=update)

    def show_update(self, update: GitUpdate):
        update_view = MultistageProcessExecutionView()
        update_view.set_multistage_process(multistage_process=update)
        self.content_navigation_view.push_view(update_view, title="Updating Git repository")

def ask_delete_unused_packages_directory(root, packages_directory):
    """Binary packages folder isn't used by any project anymore (its last project was deleted): it's kept by default,
    as packages can be used by other projects later."""
    from .packages_directory import delete_packages_directory
    dialog = Adw.AlertDialog(
        heading=f"Delete binary packages folder {packages_directory.name}?",
        body="No other project uses it. Packages in it can be reused by projects created later.")
    dialog.add_response("keep", "Keep")
    dialog.add_response("delete", "Delete")
    dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
    dialog.set_default_response("keep")
    dialog.set_close_response("keep")
    dialog.connect("response", lambda _, response: delete_packages_directory(packages_directory) if response == "delete" else None)
    dialog.present(root)

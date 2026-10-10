from gi.repository import Gtk, Adw, GLib, Pango
from .git_directory import GitDirectoryEvent
from .project_manager import ProjectManager
from .project_directory import ProjectDirectory, ProjectConfiguration
from .toolset_application import ToolsetApplication
from .toolset import ToolsetEvents
from .event_bus import SharedEvent
from .repository import Repository
from .item_select_view import ItemSelectionViewEvent
from .project_stage_create_view import ProjectStageCreateView
from .app_events import app_event_bus, AppEvents
from .stages_tree_view import StagesTreeView, TreeNode, StageNodeStatus
from .project_stage_details_view import ProjectStageDetailsView
from .project_build_view import ProjectBuildView
from .project_build_process import ProjectBuild, ProjectBuildStepBuildStage, running_project_build
from .project_builds_view import ProjectBuildsView
from .multistage_process import MultiStageProcess, MultiStageProcessState, MultiStageProcessEvent, MultiStageProcessStageEvent, MultiStageProcessStageState
from .architecture import Architecture
from .cl_toggle_group import CLToggle, CLToggleGroup
from .project_template_update import (
    TemplateState, repository_origin, has_unsaved_changes, own_commits_count, template_update_available, project_overlays
)
from .project_update_view import ProjectUpdateView
import threading

@Gtk.Template(resource_path='/com/damiandudycz/CatalystLab/ui/project/project_details_view.ui')
class ProjectDetailsView(Gtk.Box):
    __gtype_name__ = "ProjectDetailsView"

    stack = Gtk.Template.Child()
    stages_tree_view = Gtk.Template.Child()
    build_row = Gtk.Template.Child()
    build_progress_row = Gtk.Template.Child()
    directory_details_view = Gtk.Template.Child()
    toolset_selection_view = Gtk.Template.Child()
    releng_selection_view = Gtk.Template.Child()
    snapshot_selection_view = Gtk.Template.Child()
    arch_selection_view = Gtk.Template.Child()

    def __init__(self, project_directory: ProjectDirectory, content_navigation_view: Adw.NavigationView | None = None):
        super().__init__()
        self.project_directory = project_directory
        self.content_navigation_view = content_navigation_view
        self.apps_requirements = [ToolsetApplication.CATALYST]
        self.directory_details_view.setup(git_directory=project_directory, content_navigation_view=self.content_navigation_view)
        self.get_configuration()
        self.monitor_stages_changes()
        self.monitor_information_changes()
        self.monitor_configuration_changes()
        self.stages_tree_view.set_root_nodes(project_directory.stages_tree())
        self._setup_stages_view_mode()
        self._setup_source_banner()
        # Result of last build is seen, project is not marked anymore in projects list.
        project_directory.mark_build_result_seen()
        self._observed_builds: set[int] = set() # Ids of builds whose changes are observed.
        self._observed_toolset = None # Toolset (and its machine) whose state allows building.
        self._build_refresh_scheduled = False
        MultiStageProcess.event_bus.subscribe(MultiStageProcessEvent.STARTED_PROCESSES_CHANGED, self._on_build_changed)
        self._refresh_build_state()

    def get_configuration(self):
        self.toolset_selection_view.select(self.project_directory.get_toolset())
        self.releng_selection_view.select(self.project_directory.get_releng_directory())
        self.snapshot_selection_view.select(self.project_directory.get_snapshot())
        self.arch_selection_view.select(self.project_directory.get_architecture())
        self.arch_selection_view.set_static_list(sorted(Architecture, key=lambda arch: arch.name))

    def configuration_item_changed(self, container):
        match container:
            case self.toolset_selection_view:
                self.project_directory.initialize_metadata().toolset_id = (
                    self.toolset_selection_view.selected_item.uuid
                    if self.toolset_selection_view.selected_item else None
                )
            case self.releng_selection_view:
                self.project_directory.initialize_metadata().releng_directory_id = (
                    self.releng_selection_view.selected_item.id
                    if self.releng_selection_view.selected_item else None
                )
            case self.snapshot_selection_view:
                self.project_directory.initialize_metadata().snapshot_id = (
                    self.snapshot_selection_view.selected_item.filename
                    if self.snapshot_selection_view.selected_item else None
                )
            case self.arch_selection_view:
                self.project_directory.initialize_metadata().architecture = (
                    self.arch_selection_view.selected_item
                    if self.arch_selection_view.selected_item else None
                )
        Repository.ProjectDirectory.save()
        self._refresh_build_state() # Toolset could change.

    def _update_name(self, name: str):
        self._page.set_title(name)

    def _update_stages(self, data):
        self.stages_tree_view.set_root_nodes(self.project_directory.stages_tree())

    # Running build
    # --------------------------------------------------------------------------

    def _refresh_build_state(self):
        """Shows states of stages in running build of this project and button to its progress."""
        if self.get_mapped():
            # Result of build finished while project is displayed is seen.
            self.project_directory.mark_build_result_seen()
        running_builds = _running_builds()
        for build in running_builds:
            if id(build) not in self._observed_builds:
                self._observed_builds.add(id(build))
                build.event_bus.subscribe(MultiStageProcessEvent.STATE_CHANGED, self._on_build_changed)
                for step in build.stages:
                    step.event_bus.subscribe(MultiStageProcessStageEvent.STATE_CHANGED, self._on_build_changed)
                    step.event_bus.subscribe(MultiStageProcessStageEvent.PROGRESS_CHANGED, self._on_build_changed)
        running_build = running_project_build(self.project_directory)
        # Only one build can run at a time, also for different projects. Toolset (and its virtual machine) can't be
        # used by other operation then.
        toolset = self.project_directory.get_toolset()
        self._observe_toolset(toolset)
        busy_reason = toolset.busy_reason if toolset else "Select toolset in project configuration"
        self.build_row.set_visible(not running_builds)
        self.build_row.set_sensitive(not running_builds and busy_reason is None)
        self.build_row.set_title(GLib.markup_escape_text(busy_reason) if busy_reason else "Create build")
        self.build_row.set_tooltip_text(busy_reason)
        self.build_progress_row.set_visible(running_build is not None)
        statuses = {}
        if running_build is not None:
            for step in running_build.stages:
                if isinstance(step, ProjectBuildStepBuildStage):
                    statuses[step.stage.id] = _build_step_status(step)
        self.stages_tree_view.set_statuses(statuses)

    def _observe_toolset(self, toolset):
        if toolset is None or toolset is self._observed_toolset:
            return
        self._observed_toolset = toolset
        for event in ToolsetEvents:
            toolset.event_bus.subscribe(event, self._on_build_changed)
        if machine := toolset.machine:
            machine.event_bus.subscribe(SharedEvent.STATE_UPDATED, self._on_build_changed)

    def _on_build_changed(self, *args):
        # Several changes can come at once, refresh once.
        if self._build_refresh_scheduled:
            return
        self._build_refresh_scheduled = True
        def refresh():
            self._build_refresh_scheduled = False
            self._refresh_build_state()
            return False
        GLib.idle_add(refresh)

    def _setup_source_banner(self):
        """Template or Git repository project was created from, own changes of project, and button updating project
        (blue when there are updates)."""
        self.source_banner = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8, margin_start=24,
                                     margin_end=24, margin_top=6, margin_bottom=6, visible=False)
        self.source_label = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
        self.source_label.add_css_class("caption")
        self.source_label.add_css_class("dimmed")
        self.source_badges = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4, hexpand=True, valign=Gtk.Align.CENTER)
        self.source_button = Gtk.Button(valign=Gtk.Align.CENTER)
        self.source_button.add_css_class("caption")
        self.source_button.add_css_class("small-button")
        self.source_button.connect("clicked", self._on_update_source_clicked)
        for widget in (self.source_label, self.source_badges, self.source_button):
            self.source_banner.append(widget)
        self.prepend(self.source_banner)
        self._updates_available = None # Checked in background when project is opened.
        self.project_directory.event_bus.subscribe(SharedEvent.STATE_UPDATED, self._update_source_banner)
        self._update_source_banner()
        self._check_updates()

    def _check_updates(self):
        """Template is checked for changes when project is opened (repositories are fetched with Git status)."""
        path = self.project_directory.directory_path()
        if TemplateState.load(path) is None:
            return
        def check():
            available = template_update_available(path)
            def show():
                self._updates_available = available
                self._update_source_banner()
                return False
            GLib.idle_add(show)
        threading.Thread(target=check, daemon=True).start()

    def _update_source_banner(self, *args):
        path = self.project_directory.directory_path()
        self._source = None
        if state := TemplateState.load(path):
            self._source = "template"
            title, button = "Generated from template", "Update template"
            details = f"{state.template_name}\n{state.repository_url}" + (f" ({state.repository_path})" if state.repository_path else "")
            updates = self._updates_available
        elif origin := repository_origin(path):
            self._source = "repository"
            title, button = "Cloned from repository", "Update from repository"
            details = origin.url + (f" ({origin.branch})" if origin.branch else "")
            updates = self.project_directory.has_remote_changes
        self.source_banner.set_visible(self._source is not None)
        if self._source is None:
            return
        self.source_label.set_label(title)
        self.source_label.set_tooltip_text(details)
        # Own changes of project, not in template or repository:
        while badge := self.source_badges.get_first_child():
            self.source_badges.remove(badge)
        unsaved = has_unsaved_changes(path)
        badges = []
        if commits := own_commits_count(path):
            badges.append((f"{commits} own commit{'s' if commits != 1 else ''}",
                           f"Commits of project that are not in {self._source}, they are kept when updating", "notice"))
        if unsaved:
            badges.append(("Not saved changes", "Save or discard changes before updating", None))
        for text, tooltip, css_class in badges:
            badge = Gtk.Label(label=text, tooltip_text=tooltip)
            badge.add_css_class("tag-label")
            badge.add_css_class("caption")
            if css_class:
                badge.add_css_class(css_class)
            self.source_badges.append(badge)
        # Update button, blue when there are updates, disabled with not saved changes:
        self.source_button.set_label(button)
        self.source_button.set_sensitive(not unsaved)
        if updates:
            self.source_button.add_css_class("suggested-action")
        else:
            self.source_button.remove_css_class("suggested-action")
        self.source_button.set_tooltip_text(
            "Save or discard changes first" if unsaved
            else f"There are changes in {self._source}" if updates
            else f"No changes in {self._source} found" if updates is False
            else f"Check {self._source} for changes")

    def _on_update_source_clicked(self, button):
        if self._source is None:
            return
        title = "Update template" if self._source == "template" else "Update from repository"
        source = self._source
        overlays = project_overlays(self.project_directory)
        def present(update_overlays: bool):
            view = ProjectUpdateView(self.project_directory, source, overlays=overlays if update_overlays else [])
            app_event_bus.emit(AppEvents.PRESENT_VIEW, view, title, 640, 560)
        if not overlays:
            present(False)
            return
        # Overlays used by project can be updated too.
        dialog = Adw.AlertDialog(heading=title, body=f"Latest version of {source} is downloaded and compared with project.")
        check_button = Gtk.CheckButton(label="Also update overlays used by project", active=True)
        names = Gtk.Label(label=", ".join(overlay.name for overlay in overlays), xalign=0, wrap=True, margin_start=28)
        names.add_css_class("caption")
        names.add_css_class("dimmed")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.append(check_button)
        box.append(names)
        dialog.set_extra_child(box)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("update", "Update")
        dialog.set_response_appearance("update", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("update")
        dialog.set_close_response("cancel")
        dialog.connect("response", lambda _, response: present(check_button.get_active()) if response == "update" else None)
        dialog.present(self.get_root())

    def _setup_stages_view_mode(self):
        """Compact stages (short names, expanded while hovered) or full ones, switched in header bar while stages are
        shown."""
        self.stages_view_mode_toggle = CLToggleGroup(valign=Gtk.Align.CENTER)
        self.stages_view_mode_toggle.add_css_class("round")
        self.stages_view_mode_toggle.add_css_class("caption")
        self.stages_view_mode_toggle.add(CLToggle(label="Compact"))
        self.stages_view_mode_toggle.add(CLToggle(label="Full"))
        self.stages_view_mode_toggle.set_active(1 if self.stages_tree_view.expand_all else 0)
        self.stages_view_mode_toggle.connect("notify::active", lambda group, _: self.stages_tree_view.set_expand_all(group.get_active() == 1))
        self.stack.connect("notify::visible-child-name", lambda *args: self._update_stages_view_mode_visibility())
        self._update_stages_view_mode_visibility()

    def _update_stages_view_mode_visibility(self):
        self.stages_view_mode_toggle.set_visible(self.stack.get_visible_child_name() == "stages")

    def header_bar_end_widgets(self) -> list[Gtk.Widget]:
        return [self.stages_view_mode_toggle]

    def monitor_stages_changes(self):
        self.project_directory.event_bus.subscribe(
            GitDirectoryEvent.CONTENT_CHANGED,
            self._update_stages
        )

    def monitor_information_changes(self):
        self.project_directory.event_bus.subscribe(
            GitDirectoryEvent.NAME_CHANGED,
            self._update_name
        )

    def monitor_configuration_changes(self):
        """Reacts to changes in configuration lists, saves new metadata"""
        for view in [
            self.toolset_selection_view,
            self.releng_selection_view,
            self.snapshot_selection_view,
            self.arch_selection_view
        ]:
            view.event_bus.subscribe(
                ItemSelectionViewEvent.ITEM_CHANGED,
                self.configuration_item_changed
            )

    @Gtk.Template.Callback()
    def is_item_selectable(self, sender, item) -> bool:
        match sender:
            case self.toolset_selection_view:
                return all(item.get_app_install(app) is not None for app in self.apps_requirements)
            case self.releng_selection_view:
                return True
            case self.snapshot_selection_view:
                return True
            case self.arch_selection_view:
                return True
        return False

    @Gtk.Template.Callback()
    def is_item_usable(self, sender, item) -> bool:
        match sender:
            case self.toolset_selection_view:
                return True
            case self.releng_selection_view:
                return True
            case self.snapshot_selection_view:
                return True
            case self.arch_selection_view:
                return True
        return False

    @Gtk.Template.Callback()
    def setup_items_monitoring(self, sender, items):
        match sender:
            case self.toolset_selection_view:
                if hasattr(self, 'monitored_items_toolset'):
                    for item in self.monitored_items_toolset:
                        item.event_bus.unsubscribe(ToolsetEvents.IS_RESERVED_CHANGED, self)
                self.monitored_items_toolset = items
                for item in items:
                    item.event_bus.subscribe(
                        ToolsetEvents.IS_RESERVED_CHANGED,
                        self.toolset_selection_view.refresh_items_state,
                        self
                    )
            case self.releng_selection_view:
                pass
            case self.snapshot_selection_view:
                pass

    @Gtk.Template.Callback()
    def on_add_stage_activated(self, sender):
        if (
            self.project_directory.get_toolset() is None
            or self.project_directory.get_releng_directory() is None
            or self.project_directory.get_snapshot() is None
        ):
            print("Missing configuration")
            self.show_alert(message="Please setup toolset, releng directory and snapshot first.")
            return
        app_event_bus.emit(AppEvents.PRESENT_VIEW, ProjectStageCreateView(project_directory=self.project_directory), "New Stage", 640, 480)

    @Gtk.Template.Callback()
    def on_build_activated(self, sender):
        if (
            self.project_directory.get_toolset() is None
            or self.project_directory.get_releng_directory() is None
            or self.project_directory.get_snapshot() is None
        ):
            self.show_alert(message="Please setup toolset, releng directory and snapshot first.")
            return
        if _running_builds():
            self._refresh_build_state()
            return
        app_event_bus.emit(AppEvents.PRESENT_VIEW, ProjectBuildView(project_directory=self.project_directory), "Build stages", 640, 480)

    @Gtk.Template.Callback()
    def on_build_progress_activated(self, sender):
        if running_build := running_project_build(self.project_directory):
            app_event_bus.emit(AppEvents.PRESENT_VIEW, ProjectBuildView(project_directory=self.project_directory, installation_in_progress=running_build), "Build stages", 640, 480)
        else:
            self._refresh_build_state()

    @Gtk.Template.Callback()
    def on_builds_activated(self, sender):
        view = ProjectBuildsView(project_directory=self.project_directory, content_navigation_view=self.content_navigation_view)
        self.content_navigation_view.push_view(view, title=f"{self.project_directory.name} builds")

    @Gtk.Template.Callback()
    def on_stage_selected(self, sender, stage):
        if (
            self.project_directory.get_toolset() is None
            or self.project_directory.get_releng_directory() is None
            or self.project_directory.get_snapshot() is None
        ):
            print("Missing configuration")
            self.show_alert(message="Please setup toolset, releng directory and snapshot first.")
            return
        view = ProjectStageDetailsView(project_directory=self.project_directory, stage=stage, content_navigation_view=self.content_navigation_view)
        self.content_navigation_view.push_view(view, title=stage.name)

    def show_alert(self, message):
        dialog = Gtk.MessageDialog(
            transient_for=self.get_root(),
            modal=True,
            buttons=Gtk.ButtonsType.CLOSE,
            text=message
        )
        dialog.connect("response", lambda d, r: d.destroy())
        dialog.show()

def _running_builds() -> list[ProjectBuild]:
    """Builds in progress, of all projects."""
    return [
        build for build in MultiStageProcess.get_started_processes_by_class(ProjectBuild)
        if build.status == MultiStageProcessState.IN_PROGRESS
    ]

def _build_step_status(step: ProjectBuildStepBuildStage) -> StageNodeStatus:
    match step.state:
        case MultiStageProcessStageState.IN_PROGRESS:
            # Progress of built packages.
            return StageNodeStatus(title=f"Building {int(step.progress * 100)}%" if step.progress is not None else "Building", in_progress=True)
        case MultiStageProcessStageState.COMPLETED:
            return StageNodeStatus(title="Built", icon_name="check-square-svgrepo-com-symbolic", css_class="success")
        case MultiStageProcessStageState.FAILED if step.build is None:
            # Failed without starting, because stage it depends on failed.
            return StageNodeStatus(title="Skipped", icon_name="square-svgrepo-com-symbolic", css_class="dimmed")
        case MultiStageProcessStageState.FAILED:
            return StageNodeStatus(title="Failed", icon_name="error-box-svgrepo-com-symbolic", css_class="error")
    return StageNodeStatus(title="Scheduled", icon_name="clock-square-svgrepo-com-symbolic", css_class="dimmed")

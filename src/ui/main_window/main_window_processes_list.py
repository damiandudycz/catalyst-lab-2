import weakref
from gi.repository import Gtk, GLib, Pango
from .app_events import AppEvents, app_event_bus
from .multistage_process import (MultiStageProcess, MultiStageProcessEvent, MultiStageProcessState, MultiStageProcessStage,
                                 MultiStageProcessStageEvent, MultiStageProcessStageState)

# ------------------------------------------------------------------------------
# Installers and setups (installations, builds, deploys...) running or failed, listed at the bottom of side menu.
# Clicking one shows it, like its row in its section.

def present_process(process: MultiStageProcess):
    """Shows view of process (installer it was started from, or its steps)."""
    from .snapshot_installation import SnapshotInstallation
    from .toolset_installation import ToolsetInstallation
    from .build_machine_installation import BuildMachineInstallation
    from .releng_installation import RelengInstallation
    from .overlay_installation import OverlayInstallation
    from .project_installation import ProjectInstallation
    from .project_stage_installation import ProjectStageInstallation
    from .project_build_process import ProjectBuild
    from .deploy_installation import DeployInstallation
    match process:
        case SnapshotInstallation():
            from .snapshot_create_view import SnapshotCreateView
            view, title, height = SnapshotCreateView(installation_in_progress=process), "New snapshot", 480
        case ToolsetInstallation():
            from .toolset_create_view import ToolsetCreateView
            view, title, height = ToolsetCreateView(installation_in_progress=process), "New toolset", 480
        case BuildMachineInstallation():
            from .build_machine_create_view import BuildMachineCreateView
            view, title, height = BuildMachineCreateView(installation_in_progress=process), "New virtual machine", 480
        case RelengInstallation():
            from .releng_create_view import RelengCreateView
            view, title, height = RelengCreateView(installation_in_progress=process), "New releng directory", 480
        case OverlayInstallation():
            from .overlay_create_view import OverlayCreateView
            view, title, height = OverlayCreateView(installation_in_progress=process), "New portage overlay", 480
        case ProjectInstallation():
            from .project_create_view import ProjectCreateView
            view, title, height = ProjectCreateView(installation_in_progress=process), "New Project", 480
        case ProjectStageInstallation():
            from .project_stage_create_view import ProjectStageCreateView
            view, title, height = ProjectStageCreateView(project_directory=process.project_directory, installation_in_progress=process), "New Stage", 480
        case ProjectBuild():
            from .project_build_view import ProjectBuildView
            view, title, height = ProjectBuildView(project_directory=process.project_directory, installation_in_progress=process), "Build stages", 480
        case DeployInstallation():
            from .deploy_create_view import DeployCreateView
            view, title, height = DeployCreateView(installation_in_progress=process), "Deploy build", 560
        case _:
            # Other processes (eg. toolset update) show their steps.
            from .multistage_process_execution_view import MultistageProcessExecutionView
            view = MultistageProcessExecutionView()
            view.set_multistage_process(multistage_process=process)
            title, height = process.title, 480
    app_event_bus.emit(AppEvents.PRESENT_VIEW, view, title, 640, height)

def _listed(process: MultiStageProcess) -> bool:
    return process.status in (MultiStageProcessState.IN_PROGRESS, MultiStageProcessState.FAILED)

def _entries(process: MultiStageProcess) -> list:
    """Rows of process: its running steps, or process itself when it failed or no step is running (between steps)."""
    if process.status == MultiStageProcessState.IN_PROGRESS:
        steps = [step for step in process.stages if step.state == MultiStageProcessStageState.IN_PROGRESS]
        if steps:
            return [(process, step) for step in steps]
    return [(process, None)]

class ProcessesList(Gtk.Box):
    """Running steps of processes and failed processes, hidden when there are none."""

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, visible=False)
        self.append(Gtk.Separator())
        header = Gtk.Label(label="Operations", xalign=0, margin_start=18, margin_top=8)
        header.add_css_class("caption-heading")
        header.add_css_class("dimmed")
        self.append(header)
        self.list_box = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.list_box.add_css_class("navigation-sidebar")
        self.list_box.connect("row-activated", lambda list_box, row: present_process(row.process))
        self.append(self.list_box)
        self._rows: dict = {} # (process, step) -> row.
        self._observed = weakref.WeakSet()
        self._update_scheduled = False
        MultiStageProcess.event_bus.subscribe(MultiStageProcessEvent.STARTED_PROCESSES_CHANGED, self._schedule_update)
        self._schedule_update()

    def _schedule_update(self, *args):
        # Events come from threads of processes, and several at once: list is updated once, in main thread.
        if not self._update_scheduled:
            self._update_scheduled = True
            GLib.idle_add(self._update)

    def _observe(self, event_bus, owner, *events):
        if owner not in self._observed:
            self._observed.add(owner)
            for event in events:
                event_bus.subscribe(event, self._schedule_update)

    def _update(self):
        self._update_scheduled = False
        processes = list(MultiStageProcess.started_processes)
        for process in processes:
            self._observe(process.event_bus, process, MultiStageProcessEvent.STATE_CHANGED, MultiStageProcessEvent.PROGRESS_CHANGED)
            for step in process.stages:
                self._observe(step.event_bus, step, MultiStageProcessStageEvent.STATE_CHANGED)
        entries = [entry for process in processes if _listed(process) for entry in _entries(process)]
        for entry in list(self._rows):
            if entry not in entries:
                self.list_box.remove(self._rows.pop(entry))
        for index, entry in enumerate(entries):
            if entry not in self._rows:
                self._rows[entry] = ProcessRow(*entry)
                self.list_box.insert(self._rows[entry], index)
            self._rows[entry].update()
        self.set_visible(bool(entries))
        return False

class ProcessRow(Gtk.ListBoxRow):
    """Looks like rows of installation steps: status icon, name with state, and progress. Row of running step shows
    step with name of its process, row of process shows its state."""

    def __init__(self, process: MultiStageProcess, step: MultiStageProcessStage | None = None):
        super().__init__(tooltip_text=process.title)
        self.process = process
        self.step = step
        box = Gtk.Box(spacing=10, margin_start=6, margin_end=6, margin_top=2, margin_bottom=2)
        # Status icon like in rows of installation steps.
        self.status_icon = Gtk.Image(valign=Gtk.Align.CENTER)
        box.append(self.status_icon)
        labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, valign=Gtk.Align.CENTER)
        try:
            self.process_name = process.name()
        except Exception:
            self.process_name = process.title
        labels.append(Gtk.Label(label=step.name if step else self.process_name, xalign=0, ellipsize=Pango.EllipsizeMode.END))
        self.subtitle = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
        self.subtitle.add_css_class("caption")
        self.subtitle.add_css_class("dim-label")
        labels.append(self.subtitle)
        box.append(labels)
        self.progress_label = Gtk.Label(valign=Gtk.Align.CENTER)
        self.progress_label.add_css_class("caption")
        self.progress_label.add_css_class("dim-label")
        box.append(self.progress_label)
        self.set_child(box)

    def update(self):
        failed = self.process.status == MultiStageProcessState.FAILED
        stopped = failed and getattr(self.process, "cancelled", False)
        progress = None
        if stopped:
            icon_name, style, state = "stop-square-symbolic", "dimmed", "Stopped"
        elif failed:
            icon_name, style, state = "error-box-svgrepo-com-symbolic", "error", "Failed"
        elif self.step is not None:
            icon_name, style, state = "menu-dots-square-svgrepo-com-symbolic", None, self.process_name
            progress = self.step.progress
        else:
            icon_name, style, state = "menu-dots-square-svgrepo-com-symbolic", None, "In progress"
            progress = self.process.progress
        self.status_icon.set_from_icon_name(icon_name)
        for css_class in ("dimmed", "error"):
            self.status_icon.remove_css_class(css_class)
        if style:
            self.status_icon.add_css_class(style)
        self.subtitle.set_label(state)
        self.progress_label.set_label(f"{int(progress * 100)}%" if progress is not None else "")
        self.progress_label.set_visible(progress is not None)

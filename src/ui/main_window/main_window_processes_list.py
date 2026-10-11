import weakref
from gi.repository import Gtk, GLib, Pango
from .app_events import AppEvents, app_event_bus
from .multistage_process import MultiStageProcess, MultiStageProcessEvent, MultiStageProcessState
from .status_indicator import StatusIndicator, StatusIndicatorState, status_values

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

def _process_icon(process: MultiStageProcess) -> str:
    name = type(process).__name__
    for prefix, icon in (("Snapshot", "video-frame-svgrepo-com-symbolic"), ("Toolset", "toolbox-symbolic"),
                         ("BuildMachine", "toolbox-symbolic"), ("Releng", "book-minimalistic-svgrepo-com-symbolic"),
                         ("Overlay", "layers-minimalistic-svgrepo-com-symbolic"), ("ProjectBuild", "box-minimalistic-svgrepo-com-symbolic"),
                         ("Project", "notes-minimalistic-svgrepo-com-symbolic"), ("Deploy", "deploy-symbolic")):
        if name.startswith(prefix):
            return icon
    return "sledgehammer-svgrepo-com-symbolic"

def _listed(process: MultiStageProcess) -> bool:
    return process.status in (MultiStageProcessState.IN_PROGRESS, MultiStageProcessState.FAILED)

class ProcessesList(Gtk.Box):
    """Running and failed processes, hidden when there are none."""

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
        self._rows: dict = {} # Process -> row.
        self._observed = weakref.WeakSet()
        self._update_scheduled = False
        MultiStageProcess.event_bus.subscribe(MultiStageProcessEvent.STARTED_PROCESSES_CHANGED, self._schedule_update)
        self._schedule_update()

    def _schedule_update(self, *args):
        # Events come from threads of processes, and several at once: list is updated once, in main thread.
        if not self._update_scheduled:
            self._update_scheduled = True
            GLib.idle_add(self._update)

    def _update(self):
        self._update_scheduled = False
        processes = list(MultiStageProcess.started_processes)
        for process in processes:
            if process not in self._observed:
                self._observed.add(process)
                process.event_bus.subscribe(MultiStageProcessEvent.STATE_CHANGED, self._schedule_update)
                process.event_bus.subscribe(MultiStageProcessEvent.PROGRESS_CHANGED, self._schedule_update)
        listed = [process for process in processes if _listed(process)]
        for process in list(self._rows):
            if process not in listed:
                self.list_box.remove(self._rows.pop(process))
        for process in listed:
            if process not in self._rows:
                self._rows[process] = ProcessRow(process)
                self.list_box.append(self._rows[process])
            self._rows[process].update()
        self.set_visible(bool(listed))
        return False

class ProcessRow(Gtk.ListBoxRow):

    def __init__(self, process: MultiStageProcess):
        super().__init__(tooltip_text=process.title)
        self.process = process
        box = Gtk.Box(spacing=10, margin_start=6, margin_end=6)
        box.append(Gtk.Image(icon_name=_process_icon(process)))
        labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        try:
            name = process.name()
        except Exception:
            name = process.title
        labels.append(Gtk.Label(label=name, xalign=0, ellipsize=Pango.EllipsizeMode.END))
        self.subtitle = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
        self.subtitle.add_css_class("caption")
        self.subtitle.add_css_class("dimmed")
        labels.append(self.subtitle)
        box.append(labels)
        self.indicator = StatusIndicator()
        self.indicator.set_valign(Gtk.Align.CENTER)
        box.append(self.indicator)
        self.set_child(box)

    def update(self):
        if self.process.status == MultiStageProcessState.FAILED:
            result = "Stopped" if getattr(self.process, "cancelled", False) else "Failed"
            self.subtitle.set_label(result)
            self.indicator.set_values(status_values(StatusIndicatorState.ERROR))
        else:
            self.subtitle.set_label(f"{int(self.process.progress * 100)}%")
            self.indicator.set_values(status_values(StatusIndicatorState.LOADED, blinking=True))

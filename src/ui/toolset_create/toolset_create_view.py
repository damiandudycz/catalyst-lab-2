from __future__ import annotations
from gi.repository import Gtk, GLib
from gi.repository import Adw
from .toolset_env_builder import ToolsetEnvBuilder
from urllib.parse import ParseResult
from .architecture import Architecture
import os, re
from datetime import datetime
from dataclasses import dataclass
from typing import ClassVar
from enum import Enum, auto
from abc import ABC, abstractmethod
from .event_bus import EventBus
from typing import final
import time

@dataclass(frozen=True)
class ToolsetApplication:
    ALL: ClassVar[list[ToolsetApplication]] = []
    name: str
    description: str
    is_recommended: bool
    is_highly_recommended: bool
    def __post_init__(self):
        # Automatically add new instances to ToolsetApplication.ALL
        ToolsetApplication.ALL.append(self)

ToolsetApplication.CATALYST = ToolsetApplication(name="Catalyst", description="Required to build Gentoo stages", is_recommended=True, is_highly_recommended=True)
ToolsetApplication.QEMU = ToolsetApplication(name="Qemu", description="Allows building stages for different architectures", is_recommended=True, is_highly_recommended=False)

class ToolsetCreateViewStage(Enum):
    SETUP = auto()
    INSTALL = auto()
    COMPLETED = auto()
    FAILED = auto()

class ToolsetInstallationStepState(Enum):
    SCHEDULED = auto()
    IN_PROGRESS = auto()
    COMPLETED = auto()
    FAILED = auto()

@final
class ToolsetInstallationStepEvent(Enum):
    STATE_CHANGED = auto()

class ToolsetInstallationStep(ABC):
    def __init__(self, name: str, description: str, installer: ToolsetCreateView):
        self.state = ToolsetInstallationStepState.SCHEDULED
        self.name = name
        self.description = description
        self.installer = installer
        self.event_bus: EventBus[ToolsetInstallationStepEvent] = EventBus[ToolsetInstallationStepEvent]()
    @abstractmethod
    def start(self):
        self._update_state(ToolsetInstallationStepState.IN_PROGRESS)
    def complete(self, state: ToolsetInstallationStepState):
        """Call this when step finishes"""
        self._update_state(state=state)
        # If state was success continue installation
        if state == ToolsetInstallationStepState.COMPLETED:
            GLib.idle_add(self.installer._continue_installation)
        else:
            pass # TODO: Show installation failed screen and cleanup created objects.
    def _update_state(self, state: ToolsetInstallationStepState):
        self.state = state
        self.event_bus.emit(ToolsetInstallationStepEvent.STATE_CHANGED, state)

class ToolsetInstallationStepDownload(ToolsetInstallationStep):
    def __init__(self, url: ParseResult, installer: ToolsetCreateView):
        super().__init__(name="Download stage tarball", description="Downloading Gentoo stage tarball", installer=installer)
        self.url = url
    def start(self):
        super().start()
        print(f"Downloading {self.url} ...")
        time.sleep(3)
        self.complete(ToolsetInstallationStepState.COMPLETED)

class ToolsetInstallationStepExtract(ToolsetInstallationStep):
    def __init__(self, installer: ToolsetCreateView):
        super().__init__(name="Extract stage tarball", description="Extracts Gentoo stage tarball to work directory", installer=installer)
    def start(self):
        super().start()
        print(f"Extracting ...")
        time.sleep(3)
        self.complete(ToolsetInstallationStepState.COMPLETED)

class ToolsetInstallationStepSpawn(ToolsetInstallationStep):
    def __init__(self, installer: ToolsetCreateView):
        super().__init__(name="Spawn environment", description="Prepares Gentoo environment for work", installer=installer)
    def start(self):
        super().start()
        print(f"Spawning ...")
        time.sleep(3)
        self.complete(ToolsetInstallationStepState.COMPLETED)

class ToolsetInstallationStepUpdatePortage(ToolsetInstallationStep):
    def __init__(self, installer: ToolsetCreateView):
        super().__init__(name="Update portage", description="Synchronizes portage tree", installer=installer)
    def start(self):
        super().start()
        print(f"Syncing ...")
        time.sleep(3)
        self.complete(ToolsetInstallationStepState.COMPLETED)

class ToolsetInstallationStepInstallApp(ToolsetInstallationStep):
    def __init__(self, app: ToolsetApplication, installer: ToolsetCreateView):
        super().__init__(name=f"Install {app.name}", description=f"Configures and emerges {app.name}", installer=installer)
        self.app = app
    def start(self):
        super().start()
        print(f"Installing {self.app} ...")
        time.sleep(3)
        self.complete(ToolsetInstallationStepState.COMPLETED)

class ToolsetInstallationStepVerify(ToolsetInstallationStep):
    def __init__(self, installer: ToolsetCreateView):
        super().__init__(name="Verify stage", description="Checks if toolset works correctly", installer=installer)
    def start(self):
        super().start()
        print(f"Verifying ...")
        time.sleep(3)
        self.complete(ToolsetInstallationStepState.COMPLETED)

class ToolsetInstallationStepCompress(ToolsetInstallationStep):
    def __init__(self, installer: ToolsetCreateView):
        super().__init__(name="Compress", description="Compresses toolset into .squashfs file", installer=installer)
    def start(self):
        super().start()
        print(f"Compressing ...")
        time.sleep(3)
        self.complete(ToolsetInstallationStepState.COMPLETED)

class ToolsetInstallationStepCleanup(ToolsetInstallationStep):
    def __init__(self, installer: ToolsetCreateView):
        super().__init__(name="Cleanup", description="Unspawns toolset and cleans up", installer=installer)
    def start(self):
        super().start()
        print(f"Cleaning ...")
        time.sleep(3)
        self.complete(ToolsetInstallationStepState.COMPLETED)

@Gtk.Template(resource_path='/com/damiandudycz/CatalystLab/ui/toolset_create/toolset_create_view.ui')
class ToolsetCreateView(Gtk.Box):
    __gtype_name__ = "ToolsetCreateView"

    setup_view = Gtk.Template.Child()
    install_view = Gtk.Template.Child()
    carousel = Gtk.Template.Child()
    configuration_page = Gtk.Template.Child()
    tools_page = Gtk.Template.Child()
    stages_list = Gtk.Template.Child()
    installation_steps_list = Gtk.Template.Child()
    tools_list = Gtk.Template.Child()
    back_button = Gtk.Template.Child()
    next_button = Gtk.Template.Child()

    def __init__(self):
        super().__init__()
        self.selected_stage: ParseResult | None = None
        self.architecture = Architecture.HOST
        self.carousel.connect('page-changed', self.on_page_changed)
        self.tools_selection: Dict[ToolsetApplication, bool] = {app: True for app in ToolsetApplication.ALL}
        self._load_applications_rows()
        self._set_current_stage(ToolsetCreateViewStage.SETUP)
        ToolsetEnvBuilder.get_stage3_urls(architecture=self.architecture, completion_handler=self._update_stages_result)

    def on_page_changed(self, carousel, pspec):
        self.current_page = int(carousel.get_position())
        self.setup_back_next_buttons()

    def setup_back_next_buttons(self):
        is_first_page = self.current_page == 0
        is_last_page = self.current_page == 2
        is_stage_selected = self.selected_stage is not None
        self.back_button.set_sensitive(not is_first_page)
        self.back_button.set_opacity(0.0 if is_first_page else 1.0)
        self.next_button.set_sensitive(not is_first_page and is_stage_selected)
        self.next_button.set_opacity(0.0 if is_first_page or not is_stage_selected else 1.0)
        self.next_button.set_label("Create toolset" if is_last_page else "Next")

    @Gtk.Template.Callback()
    def on_back_pressed(self, _):
        is_first_page = self.current_page == 0
        if not is_first_page:
            self.carousel.scroll_to(self.carousel.get_nth_page(self.current_page - 1), True)

    @Gtk.Template.Callback()
    def on_next_pressed(self, _):
        is_last_page = self.current_page == 2
        if not is_last_page:
            self.carousel.scroll_to(self.carousel.get_nth_page(self.current_page + 1), True)
        else:
            self._start_installation()

    def _start_installation(self):
        self._set_current_stage(ToolsetCreateViewStage.INSTALL)
        # Collect steps to perform
        steps: list[ToolsetInstallationStep] = []
        steps.append(ToolsetInstallationStepDownload(url=self.selected_stage, installer=self))
        steps.append(ToolsetInstallationStepExtract(installer=self))
        steps.append(ToolsetInstallationStepSpawn(installer=self))
        steps.append(ToolsetInstallationStepUpdatePortage(installer=self))
        for app, selected in filter(lambda item: item[1], self.tools_selection.items()):
            steps.append(ToolsetInstallationStepInstallApp(app=app, installer=self))
        steps.append(ToolsetInstallationStepVerify(installer=self))
        steps.append(ToolsetInstallationStepCleanup(installer=self))
        steps.append(ToolsetInstallationStepCompress(installer=self))
        self._update_installation_steps(steps=steps)
        self._installation_steps = steps
        # Begin installation
        self._continue_installation()

    def _continue_installation(self):
        """Get next step from the list that was not finished and starts it."""
        next_step = next(
            (step for step in self._installation_steps if step.state == ToolsetInstallationStepState.SCHEDULED),
            None
        )
        if next_step:
            next_step.start() # TODO: In completion handler call self._continue_installation
        else:
            pass # All steps done, show success. Create new Toolset entry.

    @Gtk.Template.Callback()
    def on_start_row_activated(self, _):
        self.carousel.scroll_to(self.configuration_page, True)

    @Gtk.Template.Callback()
    def on_cancel_pressed(self, _):
        pass

    def _update_stages_result(self, result: list[ParseResult] | Exception):
        self.selected_stage = None
        if hasattr(self, "_stage_rows"):
            for row in self._stage_rows:
                self.stages_list.remove(row)
        # Refresh list of results
        if isinstance(result, Exception):
            error_label = Gtk.Label(label=f"Error: {str(result)}")
            self.stages_list.add(error_label)
            self._stage_rows = [error_label]
        else:
            if result:
                # Place recommended first.
                sorted_stages = sorted(result, key=lambda stage: not ToolsetCreateView._is_recommended_stage(
                    os.path.basename(stage.geturl()), self.architecture
                ))
                self.selected_stage = sorted_stages[0]
            self._stage_rows = []
            check_buttons_group = []
            for stage in sorted_stages:
                filename = os.path.basename(stage.geturl())
                row = Adw.ActionRow(title=filename)
                check_button = Gtk.CheckButton()
                check_button.set_active(stage == self.selected_stage)
                if check_buttons_group:
                    check_button.set_group(check_buttons_group[0])
                if ToolsetCreateView._is_recommended_stage(filename, self.architecture):
                    # Mark first entry as recommended
                    label = Gtk.Label(label="Recommended")
                    label.add_css_class("dim-label")
                    label.add_css_class("caption")
                    row.add_suffix(label)
                check_button.connect("toggled", self._on_stage_selected, stage)
                check_buttons_group.append(check_button)
                row.add_prefix(check_button)
                row.set_activatable_widget(check_button)
                self.stages_list.add(row)
                self._stage_rows.append(row)

    def _on_stage_selected(self, button: Gtk.CheckButton, stage: ParseResult):
        """Callback for when a row's checkbox is toggled."""
        if button.get_active():
            self.selected_stage = stage
        else:
            # Deselect if unchecked
            if self.selected_stage == stage:
                self.selected_stage = None
        self.setup_back_next_buttons()

    def _on_tool_selected(self, button: Gtk.CheckButton, app: ToolsetApplication):
        """Callback for when a row's checkbox is toggled."""
        self.tools_selection[app] = button.get_active()
        self.setup_back_next_buttons()

    @staticmethod
    def _is_recommended_stage(filename: str, architecture: Architecture) -> bool:
        escaped_arch = re.escape(architecture.value)
        pattern = rf'^stage3-{escaped_arch}-openrc-(\d{{8}}T\d{{6}}Z)\.tar\.xz$'
        match = re.match(pattern, filename)
        if not match:
            return False
        date_str = match.group(1)
        try:
            datetime.strptime(date_str, "%Y%m%dT%H%M%SZ")
            return True
        except ValueError:
            return False

    def _load_applications_rows(self):
        for app in ToolsetApplication.ALL:
            row = Adw.ActionRow(title=app.name)
            row.set_subtitle(app.description)
            check_button = Gtk.CheckButton()
            check_button.set_active(self.tools_selection.get(app))
            if app.is_recommended or app.is_highly_recommended:
                # Mark first entry as recommended
                label = Gtk.Label(label="Recommended" if not app.is_highly_recommended else "Highly recommended")
                label.add_css_class("dim-label")
                label.add_css_class("caption")
                row.add_suffix(label)
            check_button.connect("toggled", self._on_tool_selected, app)
            row.add_prefix(check_button)
            row.set_activatable_widget(check_button)
            self.tools_list.add(row)

    def _set_current_stage(self, stage: ToolsetCreateViewStage):
        self.current_stage = stage
        # Setup views visibility:
        self.setup_view.set_visible(stage == ToolsetCreateViewStage.SETUP)
        self.install_view.set_visible(stage == ToolsetCreateViewStage.INSTALL)

    def _update_installation_steps(self, steps: list[ToolsetInstallationStep]):
        if hasattr(self, "_installation_rows"):
            for row in self._installation_rows:
                self.installation_steps_list.remove(row)
        self._installation_rows = []
        check_buttons_group = []
        for step in steps:
            row = ToolsetInstallationStepRow(step=step)
            self.installation_steps_list.add(row)
            self._installation_rows.append(row)

class ToolsetInstallationStepRow(Adw.ActionRow):
    def __init__(self, step: ToolsetInstallationStep):
        super().__init__(title=step.name)
        self.step = step
        label = Gtk.Label(label=step.description)
        label.add_css_class("dim-label")
        label.add_css_class("caption")
        self.add_suffix(label)
        self.set_sensitive(step.state != ToolsetInstallationStepState.SCHEDULED)
        step.event_bus.subscribe(
            ToolsetInstallationStepEvent.STATE_CHANGED,
            self._step_state_changed
        )

    def _step_state_changed(self, state: ToolsetInstallationStepState):
        self.set_sensitive(state != ToolsetInstallationStepState.SCHEDULED)


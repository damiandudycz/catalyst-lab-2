from __future__ import annotations
import os, re, threading
from gi.repository import Gtk, Gdk, Adw, GLib, Pango
from .qemu_run import QemuRun, QemuRunEvent, QemuBuildKind, qemu_support, stage_image_ready, stage_image_directory, stage_image_script
from .vnc_view import VncView
from .cl_toggle_group import CLToggle, CLToggleGroup
from .usage_graph import UsageGraph, USAGE_INTERVAL_SECONDS
from .multistage_process import MultiStageProcess, MultiStageProcessStage, MultiStageProcessStageState, MultiStageProcessEvent, MultiStageProcessState
from .repository import Repository

# ------------------------------------------------------------------------------
# Build running in QEMU: screen of machine (VNC), its serial console and QEMU log, with power controls. Stage gets
# disk image made first (once, kept in build folder).

_ANSI = re.compile(r"\x1b\[[0-9;?=<>:]*[A-Za-z]|\x1b[()][A-Za-z0-9]|\x1b[=>78]|\r")

def _gib(size: int) -> str:
    return f"{size / 1024 ** 3:.1f} GiB"

class QemuImagePreparation(MultiStageProcess):
    """Creates disk image of stage for QEMU (in virtual machine on macOS, as root on Linux)."""

    def __init__(self, project_directory, build, support):
        self.project_directory = project_directory
        self.project_id = project_directory.id if project_directory else None
        self.build = build
        self.support = support
        super().__init__(title="QEMU disk image")

    def setup_stages(self):
        self.stages.append(_StepImage(multistage_process=self))

    def name(self) -> str:
        return self.build.stage_name

    def complete_process(self, success: bool):
        pass

class _StepImage(MultiStageProcessStage):
    def __init__(self, multistage_process):
        super().__init__(name="Create disk image", description="Extracts stage to disk image used by QEMU",
                         multistage_process=multistage_process)

    def start(self):
        super().start()
        process = self.multistage_process
        connection = None
        try:
            from .deploy_image import image_connection
            build = process.build
            if not process.support.kernel:
                raise RuntimeError("Stage has no kernel to boot")
            directory = stage_image_directory(build)
            os.makedirs(directory, exist_ok=True)
            connection = image_connection(process.project_directory)
            if error := connection.connect():
                raise RuntimeError(error)
            # Size: stage unpacked (about 4 times its archive) and free space, sparse file takes only used space.
            size = max(8 << 30, (os.path.getsize(build.artifact_path) * 5 // (1 << 30) + 4) << 30)
            script = stage_image_script(build.artifact_path, directory, size, process.support.kernel,
                                        process.support.initramfs, process.support.machine.console)
            if not connection.run(script, self.log):
                raise RuntimeError("Failed to create disk image")
            self.complete(MultiStageProcessStageState.COMPLETED)
        except Exception as e:
            print(f"Failed to create QEMU image: {e}")
            self.complete(MultiStageProcessStageState.FAILED)
        finally:
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    pass

class QemuRunView(Gtk.Box):
    """Runs build in QEMU when shown (preparing disk image of stage first), stops machine when closed."""

    def __init__(self, project_directory, build, content_navigation_view: Adw.NavigationView | None = None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.project_directory = project_directory
        self.build = build
        self.support = qemu_support(project_directory, build)
        self.run: QemuRun | None = None
        self._serial_pending = ""
        self.preparation: QemuImagePreparation | None = None
        settings = Repository.Settings.value
        # The view selector is added to the window's top header panel, like stage view options.
        self.view_toggle = CLToggleGroup(valign=Gtk.Align.CENTER)
        self.view_toggle.add_css_class("round")
        self.view_toggle.add_css_class("caption")
        for title in ("Screen", "Console", "QEMU"):
            self.view_toggle.add(CLToggle(label=title))
        self.view_toggle.set_active(0)
        self.view_toggle.connect("notify::active", self._on_view_toggle)
        self.stack = Adw.ViewStack(vexpand=True)
        self.stack.connect("notify::visible-child-name", self._on_stack_page_changed)

        bar = Gtk.Box(spacing=6, margin_start=12, margin_end=12, margin_top=6, margin_bottom=6)
        self.status_label = Gtk.Label(xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END, selectable=True)
        self.status_label.add_css_class("caption")
        bar.append(self.status_label)
        self.buttons = []
        for icon, tooltip, handler in (("system-shutdown-symbolic", "Power off (ACPI, system shuts down)", lambda: self.run.power_off()),
                                       ("view-refresh-symbolic", "Reset", lambda: self.run.reset()),
                                       ("input-keyboard-symbolic", "Send Ctrl+Alt+Del", lambda: self.run.send_ctrl_alt_delete()),
                                       ("media-playback-stop-symbolic", "Stop machine immediately", lambda: self.run.stop())):
            button = Gtk.Button(icon_name=icon, tooltip_text=tooltip, valign=Gtk.Align.CENTER, sensitive=False)
            button.add_css_class("flat")
            button.connect("clicked", lambda _, handler=handler: self.run and GLib.idle_add(lambda: (handler(), False)[1]))
            bar.append(button)
            self.buttons.append(button)
        self.start_button = Gtk.Button(label="Start", valign=Gtk.Align.CENTER, visible=False)
        self.start_button.add_css_class("suggested-action")
        self.start_button.connect("clicked", lambda _: self.start())
        bar.append(self.start_button)
        self.append(bar)
        # Screen, console and log.
        self.vnc_view = VncView()
        self.vnc_view.status_handler = self._set_screen_status
        screen = Gtk.Overlay(child=self.vnc_view)
        screen.add_css_class("qemu-screen")
        self._screen_css = Gtk.CssProvider()
        self._screen_css.load_from_data(b".qemu-screen { background-color: #000; }")
        Gtk.StyleContext.add_provider_for_display(self.get_display(), self._screen_css,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.screen_status = Gtk.Label(wrap=True, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER, visible=False)
        self.screen_status.add_css_class("dimmed")
        screen.add_overlay(self.screen_status)
        self.launch_panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                                    halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER,
                                    margin_start=24, margin_end=24, margin_top=24, margin_bottom=24)
        self.launch_panel.add_css_class("card")
        self.launch_group = Adw.PreferencesGroup(title="Run in QEMU", description="Choose resources for this run.")
        self.launch_cpus_row = Adw.SpinRow(title="CPUs", adjustment=Gtk.Adjustment(
            lower=1, upper=64, step_increment=1, value=settings.qemu_cpus))
        self.launch_memory_row = Adw.SpinRow(title="Memory (GiB)", adjustment=Gtk.Adjustment(
            lower=1, upper=256, step_increment=1, value=settings.qemu_memory_gib))
        self.launch_group.add(self.launch_cpus_row)
        self.launch_group.add(self.launch_memory_row)
        self.launch_panel.append(self.launch_group)
        self.launch_message = Gtk.Label(wrap=True, xalign=0, visible=False)
        self.launch_message.add_css_class("error")
        self.launch_panel.append(self.launch_message)
        self.launch_button = Gtk.Button(label="Start virtual machine", halign=Gtk.Align.END)
        self.launch_button.add_css_class("suggested-action")
        self.launch_button.connect("clicked", lambda _: self.start())
        self.launch_panel.append(self.launch_button)
        screen.add_overlay(self.launch_panel)
        self.stack.add_titled_with_icon(screen, "screen", "Screen", "video-display-symbolic")
        self.console_view, console = self._text_view(editable_input=True)
        self.stack.add_titled_with_icon(console, "console", "Console", "utilities-terminal-symbolic")
        self.log_view, log = self._text_view(editable_input=False)
        self.usage_cpu_row = self._usage_row("CPU (host)")
        self.usage_memory_row = self._usage_row("Memory (resident)")
        self.usage_group = Adw.PreferencesGroup(
            title="VM usage",
            description="Usage of the QEMU process on this computer. Memory is resident memory, not guest-reported usage.",
            visible=False,
        )
        self.usage_group.add(self.usage_cpu_row)
        self.usage_group.add(self.usage_memory_row)
        qemu_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_start=24, margin_end=24,
                            margin_top=12, margin_bottom=24)
        qemu_page.append(self.usage_group)
        log_title = Gtk.Label(label="QEMU log", xalign=0, margin_top=6)
        log_title.add_css_class("heading")
        qemu_page.append(log_title)
        qemu_page.append(log)
        self.stack.add_titled_with_icon(qemu_page, "log", "QEMU", "text-x-generic-symbolic")
        self._usage_timer: int | None = None
        self._reading_usage = False
        QemuRun.running_runs_event_bus.subscribe(QemuRunEvent.RUNNING_RUNS_CHANGED, self._on_global_runs_changed, self)
        self.connect("map", lambda _: self._start_usage_updates())
        self.connect("unmap", lambda _: self._stop_usage_updates())
        self.append(self.stack)
        # Machine stops when view is closed: its page is popped from navigation (not when other page covers it), or
        # its window is closed.
        self.connect("realize", self._on_realize)
        self.connect("unrealize", lambda _: self.stop())
        self._set_status("Configure the virtual machine and start it")
        self._update_launch_availability()

    def header_bar_end_widgets(self) -> list[Gtk.Widget]:
        return [self.view_toggle]

    def _on_view_toggle(self, toggle_group, _param):
        names = ("screen", "console", "log")
        index = toggle_group.get_active()
        if 0 <= index < len(names):
            self.stack.set_visible_child_name(names[index])

    def _on_stack_page_changed(self, stack, _param):
        names = ("screen", "console", "log")
        name = stack.get_visible_child_name()
        if name in names and self.view_toggle.get_active() != names.index(name):
            self.view_toggle.set_active(names.index(name))

    def _text_view(self, editable_input: bool):
        view = Gtk.TextView(monospace=True, editable=False, cursor_visible=editable_input, wrap_mode=Gtk.WrapMode.CHAR,
                            top_margin=8, bottom_margin=8, left_margin=8, right_margin=8)
        if editable_input:
            # Typed keys go to serial console of machine.
            keys = Gtk.EventControllerKey()
            keys.connect("key-pressed", self._on_console_key)
            view.add_controller(keys)
        return view, Gtk.ScrolledWindow(child=view, vexpand=True)

    def _append(self, view: Gtk.TextView, text: str):
        buffer = view.get_buffer()
        buffer.insert(buffer.get_end_iter(), text)
        if buffer.get_line_count() > 5000:
            buffer.delete(buffer.get_start_iter(), buffer.get_iter_at_line(buffer.get_line_count() - 5000)[1])
        view.scroll_to_iter(buffer.get_end_iter(), 0, False, 0, 1)

    def _on_console_key(self, controller, keyval, keycode, state) -> bool:
        if self.run is None:
            return False
        special = {Gdk.KEY_Return: b"\r", Gdk.KEY_KP_Enter: b"\r", Gdk.KEY_BackSpace: b"\x7f", Gdk.KEY_Tab: b"\t",
                   Gdk.KEY_Escape: b"\x1b", Gdk.KEY_Up: b"\x1b[A", Gdk.KEY_Down: b"\x1b[B", Gdk.KEY_Right: b"\x1b[C",
                   Gdk.KEY_Left: b"\x1b[D"}
        if keyval in special:
            data = special[keyval]
        elif state & Gdk.ModifierType.CONTROL_MASK and Gdk.KEY_a <= Gdk.keyval_to_lower(keyval) <= Gdk.KEY_z:
            data = bytes([Gdk.keyval_to_lower(keyval) - Gdk.KEY_a + 1])
        elif (character := Gdk.keyval_to_unicode(keyval)) and character >= 32:
            data = chr(character).encode()
        else:
            return False
        self.run.send_serial(data)
        return True

    # Running

    def _set_status(self, text: str):
        self.status_label.set_label(text)

    def _set_screen_status(self, text: str):
        self.screen_status.set_label(text)
        self.screen_status.set_visible(bool(text))

    def _set_running(self, running: bool):
        for button in self.buttons:
            button.set_sensitive(running)
        self.start_button.set_visible(not running and self.support.available)
        self.launch_panel.set_visible(not running)
        self._update_launch_availability()

    def _update_launch_availability(self):
        other = next((run for run in QemuRun.running_runs if run is not self.run and run.is_running), None)
        if other is not None:
            self.launch_message.set_label(f"Another QEMU machine is already running: {other.name}.")
            self.launch_message.set_visible(True)
            self.launch_button.set_sensitive(False)
        else:
            self.launch_message.set_visible(False)
            self.launch_button.set_sensitive(self.support.available)

    def _on_global_runs_changed(self):
        GLib.idle_add(lambda: (self._update_launch_availability(), False)[1])

    def start(self):
        if any(run is not self.run and run.is_running for run in QemuRun.running_runs):
            self._update_launch_availability()
            self._set_status("Only one QEMU machine can run at a time")
            return
        if not self.support.available:
            self._set_status(self.support.reason)
            self._set_screen_status(self.support.reason)
            return
        if self.support.kind == QemuBuildKind.STAGE and not stage_image_ready(self.build, self.support):
            self._launch_cpus = round(self.launch_cpus_row.get_value())
            self._launch_memory_mib = round(self.launch_memory_row.get_value()) * 1024
            self.launch_panel.set_visible(False)
            self._prepare_image()
            return
        self._launch_cpus = round(self.launch_cpus_row.get_value())
        self._launch_memory_mib = round(self.launch_memory_row.get_value()) * 1024
        self.launch_panel.set_visible(False)
        self.run = QemuRun(self.project_directory, self.build, self.support,
                           memory_mib=self._launch_memory_mib, cpus=self._launch_cpus)
        self.run.view = self
        self.run.event_bus.subscribe(QemuRunEvent.STATE_CHANGED, self._on_state_changed, self)
        self.run.event_bus.subscribe(QemuRunEvent.SERIAL_OUTPUT, self._on_serial, self)
        self.console_view.get_buffer().set_text("")
        try:
            self.run.start()
        except Exception as e:
            self.run = None
            self._set_status(f"Failed to start QEMU: {e}")
            self.launch_panel.set_visible(True)
            self._update_launch_availability()
            return
        self.vnc_view.connect_vnc(self.run.vnc_socket, lambda: self.run is not None and self.run.is_running)
        self._set_screen_status("Starting machine…")

    def _prepare_image(self):
        self._set_status("Preparing disk image of stage (once, kept with build)…")
        self._set_screen_status("Preparing disk image of stage…")
        self.preparation = QemuImagePreparation(self.project_directory, self.build, self.support)
        from .multistage_process import MultiStageProcessStageEvent
        # Kept: event bus refers to handlers weakly.
        self._preparation_log = lambda line: GLib.idle_add(lambda: (self._append(self.log_view, line + "\n"), False)[1])
        for stage in self.preparation.stages:
            stage.event_bus.subscribe(MultiStageProcessStageEvent.OUTPUT_LINE_ADDED, self._preparation_log)
        self.preparation.event_bus.subscribe(MultiStageProcessEvent.STATE_CHANGED, self._on_preparation_state, self)
        self.preparation.start()

    def _on_preparation_state(self, state):
        def update():
            if state == MultiStageProcessState.COMPLETED:
                self.preparation.mark_result_seen()
                self.start()
            elif state == MultiStageProcessState.FAILED:
                self._set_status("Failed to prepare disk image, see QEMU tab")
                self._set_screen_status("Failed to prepare disk image")
            return False
        GLib.idle_add(update)

    def _on_state_changed(self, run: QemuRun):
        def update():
            if run is not self.run:
                return False
            running = run.is_running
            self._set_running(running)
            if running:
                speed = f"accelerated ({run.accelerator.upper()})" if run.accelerator else "emulated (slow)"
                self._set_status(f"Running, {speed} · SSH: ssh -p {run.ssh_port} root@localhost")
            else:
                self._set_status(f"Stopped: {run.error}" if run.error else "Stopped")
                self._set_screen_status(f"Machine stopped{': ' + run.error if run.error else ''}")
                self.vnc_view.disconnect_vnc()
                self.vnc_view.clear()
            self.log_view.get_buffer().set_text("\n".join(run.output) + "\n")
            self.usage_group.set_visible(running)
            if not running:
                self._clear_usage()
            return False
        GLib.idle_add(update)

    @staticmethod
    def _usage_row(title: str) -> Adw.ActionRow:
        row = Adw.ActionRow(title=title, subtitle="Waiting for sample…")
        row.graph = UsageGraph()
        row.add_suffix(row.graph)
        return row

    def _start_usage_updates(self):
        if self._usage_timer is None:
            self._usage_timer = GLib.timeout_add_seconds(USAGE_INTERVAL_SECONDS, self._read_usage)
            self._read_usage()

    def _stop_usage_updates(self):
        if self._usage_timer is not None:
            GLib.source_remove(self._usage_timer)
            self._usage_timer = None

    def _read_usage(self) -> bool:
        run = self.run
        if run is not None and run.is_running and not self._reading_usage:
            self._reading_usage = True
            def work():
                usage = run.read_usage()
                GLib.idle_add(self._show_usage, run, usage)
            threading.Thread(target=work, daemon=True).start()
        return True

    def _show_usage(self, run: QemuRun, usage: tuple[float, int] | None):
        self._reading_usage = False
        if run is not self.run or not run.is_running:
            return False
        self.usage_group.set_visible(True)
        if usage is None:
            self.usage_cpu_row.set_subtitle("Usage sample unavailable")
            self.usage_memory_row.set_subtitle("Usage sample unavailable")
            return False
        cpu_fraction, resident_bytes = usage
        self.usage_cpu_row.graph.add(cpu_fraction)
        self.usage_cpu_row.set_subtitle(f"{cpu_fraction:.0%} of {run.cpus} vCPUs")
        memory_limit = run.memory_mib * 1024 * 1024
        self.usage_memory_row.graph.add(min(resident_bytes / memory_limit, 1) if memory_limit else 0)
        self.usage_memory_row.set_subtitle(f"{_gib(resident_bytes)} of {run.memory_mib / 1024:.1f} GiB configured")
        return False

    def _clear_usage(self):
        for row in (self.usage_cpu_row, self.usage_memory_row):
            row.graph.clear()
            row.set_subtitle("Waiting for sample…")

    def _on_serial(self, text: str):
        def append():
            # Control sequences can be split between reads, incomplete one waits for its rest.
            pending = self._serial_pending + text
            incomplete = re.search(r"\x1b[^A-Za-z]*$", pending)
            self._serial_pending = pending[incomplete.start():] if incomplete else ""
            complete = pending[:incomplete.start()] if incomplete else pending
            self._append(self.console_view, _ANSI.sub("", complete))
            return False
        GLib.idle_add(append)

    def _on_realize(self, widget):
        page = self.get_ancestor(Adw.NavigationPage)
        navigation = self.get_ancestor(Adw.NavigationView)
        if page is not None and navigation is not None and not getattr(self, "_page_observed", False):
            self._page_observed = True
            def hidden(_):
                stack = navigation.get_navigation_stack()
                if all(stack.get_item(index) is not page for index in range(stack.get_n_items())):
                    self.stop()
            page.connect("hidden", hidden)

    def stop(self):
        if self.run is not None and self.run.is_running:
            self.run.stop()
        self.vnc_view.disconnect_vnc()

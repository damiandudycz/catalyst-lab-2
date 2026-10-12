from __future__ import annotations
import os, sys, threading
from dataclasses import dataclass
from enum import Enum
from gi.repository import Gtk, Adw, GLib
from .deploy_live_iso import (
    LiveIsoDeployment, LiveIsoTarget, RaspberryPiBootSettings, supports_raspberry_pi_iso, raspberry_pi_iso_path
)
from .deploy_target import TargetMachine, TargetFirmware, format_size, machine_architecture
from .deploy_image import image_supported_reason
from .local_disk_connection import LocalDiskConnection
from .item_select_view import ItemSelectionViewEvent

class LiveIsoBoot(Enum):
    """How live CD is started."""
    AS_BUILT = "as-built"
    RASPBERRY_PI = "raspberry-pi"

@dataclass
class LiveIsoMethod:
    """Where live CD is deployed, with reason when it can't be used for this ISO or on this computer."""
    target: LiveIsoTarget
    title: str
    description: str
    unavailable_reason: str | None = None

    @property
    def unavailable(self) -> bool:
        return self.unavailable_reason is not None

    @property
    def details(self) -> str:
        return self.unavailable_reason or self.description

@Gtk.Template(resource_path='/com/damiandudycz/CatalystLab/ui/deploy/live_iso_deploy_view.ui')
class LiveIsoDeployView(Gtk.Box):
    """Wizard deploying live CD build (ISO image): to disk of this computer (Linux only), or as new ISO file. ISO with
    Raspberry Pi kernel can be prepared for Raspberry Pi firmware, with its boot options."""
    __gtype_name__ = "LiveIsoDeployView"

    wizard_view = Gtk.Template.Child()
    method_page = Gtk.Template.Child()
    method_selection_view = Gtk.Template.Child()
    boot_page = Gtk.Template.Child()
    boot_group = Gtk.Template.Child()
    raspberry_pi_group = Gtk.Template.Child()
    serial_console_row = Gtk.Template.Child()
    scan_delay_row = Gtk.Template.Child()
    older_models_row = Gtk.Template.Child()
    kernel_arguments_row = Gtk.Template.Child()
    config_row = Gtk.Template.Child()
    disk_page = Gtk.Template.Child()
    disks_group = Gtk.Template.Child()
    find_disks_button = Gtk.Template.Child()
    disk_status_label = Gtk.Template.Child()

    def __init__(self, project_directory=None, build=None, installation_in_progress: LiveIsoDeployment | None = None,
                 content_navigation_view: Adw.NavigationView | None = None):
        super().__init__()
        self.installation_in_progress = installation_in_progress
        self.content_navigation_view = content_navigation_view
        self.project_directory = project_directory or (installation_in_progress.project_directory if installation_in_progress else None)
        self.build = build if build else installation_in_progress.build if installation_in_progress else None
        self.connection: LocalDiskConnection | None = None
        self.machine: TargetMachine | None = None
        self.selected_disk = None
        self.disk_rows: list[Adw.ActionRow] = []
        self.searching = False
        self.raspberry_pi = bool(self.build) and supports_raspberry_pi_iso(self.project_directory, self.build)
        # Boot options: as built, and Raspberry Pi firmware for ISO with Raspberry Pi kernel.
        self.boot_checks: dict[LiveIsoBoot, Gtk.CheckButton] = {}
        group = None
        boots = [LiveIsoBoot.AS_BUILT] + ([LiveIsoBoot.RASPBERRY_PI] if self.raspberry_pi else [])
        for boot in boots:
            title, subtitle = {
                LiveIsoBoot.AS_BUILT: ("As built", "ISO boots like it was built by catalyst (GRUB on UEFI machines)"),
                LiveIsoBoot.RASPBERRY_PI: ("Raspberry Pi", "Boot partition for Raspberry Pi firmware is added, kernel of live CD is for Raspberry Pi"),
            }[boot]
            row = Adw.ActionRow(title=title, subtitle=subtitle)
            check = Gtk.CheckButton(valign=Gtk.Align.CENTER, active=boot == boots[-1])
            if group:
                check.set_group(group)
            group = group or check
            check.connect("toggled", self._on_boot_changed)
            row.add_prefix(check)
            row.set_activatable_widget(check)
            self.boot_group.add(row)
            self.boot_checks[boot] = check
        # Lines added to config.txt, edited in text view.
        self.config_view = Gtk.TextView(monospace=True, top_margin=8, bottom_margin=8, left_margin=8, right_margin=8)
        self.config_view.get_buffer().set_text(RaspberryPiBootSettings().config_lines)
        self.config_view.set_size_request(-1, 72)
        self.config_row.add_row(Gtk.ListBoxRow(activatable=False, selectable=False, child=Gtk.Frame(
            child=self.config_view, margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)))
        self.methods = self._methods()
        self.method_selection_view.set_static_list(self.methods)
        self.method_selection_view.event_bus.subscribe(ItemSelectionViewEvent.ITEM_CHANGED, self.on_method_changed)
        self._on_boot_changed(None)
        self.on_method_changed(None)
        if self.build:
            size = os.path.getsize(self.build.artifact_path) if os.path.isfile(self.build.artifact_path or "") else 0
            self.wizard_view.set_property("welcome_screen_description",
                f"Writes {self.build.stage_name} ({self.build.artifact}, {format_size(size)}) to USB drive or SD card"
                + (" connected to this computer" if sys.platform.startswith("linux") else "")
                + ", or saves it as new ISO file to write later" + (" (for Raspberry Pi)" if self.raspberry_pi else "") + ".")
        self.connect("realize", self.on_realize)
        self.connect("unrealize", self.on_unrealize)

    def _methods(self) -> list[LiveIsoMethod]:
        disk_reason = None if sys.platform.startswith("linux") else \
            "Not available: needs Linux, virtual machines on this system can't access disks of this computer"
        iso_reason = None
        if not self.raspberry_pi:
            iso_reason = "Not available: ISO of build is used as it is, it can be written to disk directly"
        elif reason := image_supported_reason(self.project_directory):
            iso_reason = reason
        return [
            LiveIsoMethod(LiveIsoTarget.LOCAL_DISK, "Disk of this computer",
                          "USB drive or SD card connected to this computer, booted later on other machine", disk_reason),
            LiveIsoMethod(LiveIsoTarget.ISO_FILE, "ISO image file",
                          "New ISO saved next to build, written later to USB drive or SD card with dd, Raspberry Pi "
                          "Imager or Etcher", iso_reason),
        ]

    def on_realize(self, widget):
        self.wizard_view.content_navigation_view = self.content_navigation_view
        self.wizard_view._window = self._window
        self.wizard_view.set_installation(self.installation_in_progress)

    def on_unrealize(self, widget):
        # Root access not passed to deployment is released with wizard.
        if self.connection and self.installation_in_progress is None:
            connection, self.connection = self.connection, None
            threading.Thread(target=connection.close, daemon=True).start()

    # Pages

    @Gtk.Template.Callback()
    def is_page_ready_to_continue(self, sender, page) -> bool:
        if page == self.method_page:
            method = self.method_selection_view.selected_item
            return method is not None and not method.unavailable
        if page == self.disk_page:
            return self.selected_disk is not None and self.connection is not None
        return True

    @Gtk.Template.Callback()
    def is_method_selectable(self, sender, method) -> bool:
        return not method.unavailable

    @Gtk.Template.Callback()
    def is_method_usable(self, sender, method) -> bool:
        return True

    def _target(self) -> LiveIsoTarget | None:
        method = self.method_selection_view.selected_item
        return method.target if method else None

    def _boot(self) -> LiveIsoBoot:
        return next((boot for boot, check in self.boot_checks.items() if check.get_active()), LiveIsoBoot.AS_BUILT)

    def on_method_changed(self, view):
        # Disk is selected only when writing to disk. ISO without Raspberry Pi kernel has only one way to boot.
        self.wizard_view.set_page_visible(self.disk_page, self._target() == LiveIsoTarget.LOCAL_DISK)
        self.wizard_view.set_page_visible(self.boot_page, self.raspberry_pi)
        self.wizard_view._refresh_buttons_state()

    def _on_boot_changed(self, check):
        if check is not None and not check.get_active():
            return
        self.raspberry_pi_group.set_visible(self._boot() == LiveIsoBoot.RASPBERRY_PI)

    # Disks

    @Gtk.Template.Callback()
    def on_find_disks_clicked(self, button):
        """Disks of this computer, listed as root (root access is kept while wizard is open)."""
        self.searching = True
        self.find_disks_button.set_sensitive(False)
        self._set_status(None)
        previous = self.connection
        architecture = self.project_directory.get_architecture() if self.project_directory else None
        def work():
            connection = previous if previous and previous.is_alive() else None
            error = None
            if connection is None:
                connection = LocalDiskConnection()
                error = connection.connect()
            machine = None
            if error is None:
                try:
                    machine = TargetMachine.load_local(connection, machine_architecture(architecture.value if architecture else None) or "",
                                                       TargetFirmware.OTHER)
                except Exception as e:
                    error = f"Failed to list disks: {e}"
                    connection.close()
            def done():
                self.searching = False
                self.find_disks_button.set_sensitive(True)
                self.find_disks_button.set_label("Find disks again")
                self.connection = connection if machine else None
                self.machine = machine
                self._set_status(error)
                self._load_disks()
            GLib.idle_add(done)
        threading.Thread(target=work, daemon=True).start()

    def _set_status(self, error: str | None):
        self.disk_status_label.set_visible(error is not None)
        self.disk_status_label.set_label(error or "")
        if error:
            self.disk_status_label.add_css_class("error")

    def _load_disks(self):
        for row in self.disk_rows:
            self.disks_group.remove(row)
        self.disk_rows = []
        self.selected_disk = None
        disks = self.machine.disks if self.machine else []
        size = os.path.getsize(self.build.artifact_path) if self.build and os.path.isfile(self.build.artifact_path or "") else 0
        group = None
        for disk in disks:
            too_small = disk.size < size * 1.1
            subtitle = disk.subtitle + (", too small for ISO" if too_small else "")
            row = Adw.ActionRow(title=GLib.markup_escape_text(disk.title), subtitle=GLib.markup_escape_text(subtitle))
            check = Gtk.CheckButton(valign=Gtk.Align.CENTER)
            if group:
                check.set_group(group)
            group = group or check
            check.connect("toggled", self._on_disk_toggled, disk)
            row.add_prefix(check)
            row.set_activatable_widget(check)
            row.set_sensitive(not disk.in_use and not too_small)
            self.disks_group.add(row)
            self.disk_rows.append(row)
        if not disks and self.machine:
            row = Adw.ActionRow(title="No disks found")
            self.disks_group.add(row)
            self.disk_rows.append(row)
        self.wizard_view._refresh_buttons_state()

    def _on_disk_toggled(self, check: Gtk.CheckButton, disk):
        if check.get_active():
            self.selected_disk = disk
            self.wizard_view._refresh_buttons_state()

    # Deploying

    def _raspberry_pi_settings(self) -> RaspberryPiBootSettings | None:
        if not self.raspberry_pi or self._boot() != LiveIsoBoot.RASPBERRY_PI:
            return None
        buffer = self.config_view.get_buffer()
        return RaspberryPiBootSettings(
            serial_console=self.serial_console_row.get_active(),
            scan_delay=int(self.scan_delay_row.get_value()),
            extra_arguments=self.kernel_arguments_row.get_text().strip(),
            config_lines=buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False),
            older_models_firmware=self.older_models_row.get_active(),
        )

    @Gtk.Template.Callback()
    def begin_installation(self, view):
        target = self._target()
        if target == LiveIsoTarget.LOCAL_DISK:
            disk = self.selected_disk
            if disk is None or self.connection is None:
                return
            dialog = Adw.AlertDialog(heading="Erase disk?",
                                     body=f"All data on {disk.path} ({disk.subtitle}, {format_size(disk.size)}) of this "
                                          f"computer will be erased and {self.build.stage_name} will be written to it.")
            dialog.add_response("cancel", "Cancel")
            dialog.add_response("deploy", "Erase and write")
            dialog.set_response_appearance("deploy", Adw.ResponseAppearance.DESTRUCTIVE)
        else:
            path = raspberry_pi_iso_path(self.build)
            replaced = " It replaces ISO created before." if os.path.isfile(path) else ""
            dialog = Adw.AlertDialog(heading="Create ISO?",
                                     body=f"{os.path.basename(path)} will be created next to {self.build.artifact}.{replaced}")
            dialog.add_response("cancel", "Cancel")
            dialog.add_response("deploy", "Create ISO")
            dialog.set_response_appearance("deploy", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._on_confirm_response)
        dialog.present(self.get_root())

    def _on_confirm_response(self, dialog, response: str):
        if response != "deploy":
            return
        target = self._target()
        installation = LiveIsoDeployment(
            self.project_directory, self.build, target,
            disk=self.selected_disk if target == LiveIsoTarget.LOCAL_DISK else None,
            disk_connection=self.connection if target == LiveIsoTarget.LOCAL_DISK else None,
            raspberry_pi=self._raspberry_pi_settings(),
        )
        if target != LiveIsoTarget.LOCAL_DISK and self.connection:
            threading.Thread(target=self.connection.close, daemon=True).start()
        self.connection = None # Owned by deployment now.
        self.installation_in_progress = installation
        installation.start()
        self.wizard_view.set_installation(installation)

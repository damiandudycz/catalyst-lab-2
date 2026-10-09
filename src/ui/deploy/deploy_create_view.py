from __future__ import annotations
import os, threading
from gi.repository import Gtk, Adw, GLib
from .deploy_installation import DeployInstallation, DeploySystemSettings
from .deploy_target import TargetMachine, TargetFilesystem, PartitionPlan, GIB, MIB, format_size, architecture_supported
from .ssh_connection import SSHConnection

@Gtk.Template(resource_path='/com/damiandudycz/CatalystLab/ui/deploy/deploy_create_view.ui')
class DeployCreateView(Gtk.Box):
    """Wizard deploying stage build to machine booted from Gentoo LiveCD: connection, disk partitioning, system
    settings."""
    __gtype_name__ = "DeployCreateView"

    wizard_view = Gtk.Template.Child()
    connection_page = Gtk.Template.Child()
    host_row = Gtk.Template.Child()
    port_row = Gtk.Template.Child()
    user_row = Gtk.Template.Child()
    password_row = Gtk.Template.Child()
    connect_button = Gtk.Template.Child()
    machine_group = Gtk.Template.Child()
    machine_row = Gtk.Template.Child()
    connection_status_label = Gtk.Template.Child()
    disk_page = Gtk.Template.Child()
    disks_group = Gtk.Template.Child()
    filesystem_row = Gtk.Template.Child()
    swap_row = Gtk.Template.Child()
    efi_row = Gtk.Template.Child()
    partitions_group = Gtk.Template.Child()
    system_page = Gtk.Template.Child()
    hostname_row = Gtk.Template.Child()
    root_password_row = Gtk.Template.Child()
    root_password_confirm_row = Gtk.Template.Child()
    ssh_row = Gtk.Template.Child()
    bootloader_row = Gtk.Template.Child()
    reboot_row = Gtk.Template.Child()
    system_status_label = Gtk.Template.Child()

    def __init__(self, project_directory=None, build=None, installation_in_progress: DeployInstallation | None = None,
                 content_navigation_view: Adw.NavigationView | None = None):
        super().__init__()
        self.installation_in_progress = installation_in_progress
        self.content_navigation_view = content_navigation_view
        self.project_directory = project_directory
        self.build = build if build else installation_in_progress.build if installation_in_progress else None
        self.connection: SSHConnection | None = None
        self.machine: TargetMachine | None = None
        self.connecting = False
        self.selected_disk = None
        self.disk_rows: list[Adw.ActionRow] = []
        self.partition_rows: list[Adw.ActionRow] = []
        if self.build:
            size = os.path.getsize(self.build.artifact_path) if self.build.artifact_path and os.path.isfile(self.build.artifact_path) else 0
            self.wizard_view.set_property("welcome_screen_description",
                f"Installs {self.build.stage_name} ({self.build.artifact}, {format_size(size)}) on disk of another machine. "
                "Boot that machine from Gentoo LiveCD and allow SSH connections to it, Catalyst Lab partitions its disk, "
                "installs the stage and configures the system.")
        self.connect("realize", self.on_realize)
        self.connect("unrealize", self.on_unrealize)

    def on_realize(self, widget):
        self.wizard_view.content_navigation_view = self.content_navigation_view
        self.wizard_view._window = self._window
        self.wizard_view.set_installation(self.installation_in_progress)

    def on_unrealize(self, widget):
        # Connection not passed to installation is closed with wizard.
        if self.connection and self.installation_in_progress is None:
            connection, self.connection = self.connection, None
            threading.Thread(target=connection.close, daemon=True).start()

    # Pages

    @Gtk.Template.Callback()
    def is_page_ready_to_continue(self, sender, page) -> bool:
        if page == self.connection_page:
            return self.machine is not None
        if page == self.disk_page:
            return self.selected_disk is not None
        if page == self.system_page:
            return self._system_error() is None
        return True

    # Connection

    @Gtk.Template.Callback()
    def on_connection_changed(self, row):
        # Changed connection details need connecting again.
        if self.machine is not None and not self.connecting:
            self._set_machine(None)
            if self.connection:
                threading.Thread(target=self.connection.close, daemon=True).start()
                self.connection = None
        self._update_connect_button()

    def _update_connect_button(self):
        self.connect_button.set_sensitive(not self.connecting and bool(self.host_row.get_text().strip())
                                          and self.port_row.get_text().strip().isdigit())
        self.connect_button.set_label("Connecting..." if self.connecting else "Connect" if self.machine is None else "Connect again")

    @Gtk.Template.Callback()
    def on_connect_clicked(self, sender):
        if self.connecting or not self.connect_button.get_sensitive():
            return
        host = self.host_row.get_text().strip()
        port = int(self.port_row.get_text().strip() or 22)
        user = self.user_row.get_text().strip() or "root"
        password = self.password_row.get_text()
        self.connecting = True
        self._set_status(None)
        self._update_connect_button()
        previous = self.connection
        def work():
            if previous:
                previous.close()
            connection = SSHConnection(host=host, port=port, user=user)
            machine, error = None, connection.connect(password)
            if error is None:
                try:
                    machine = TargetMachine.load(connection)
                except Exception as e:
                    error = f"Failed to read machine details: {e}"
                    connection.close()
            def done():
                self.connecting = False
                self.connection = connection if machine else None
                self._set_machine(machine)
                self._set_status(error)
                self._update_connect_button()
            GLib.idle_add(done)
        threading.Thread(target=work, daemon=True).start()

    def _set_status(self, error: str | None):
        self.connection_status_label.set_visible(error is not None)
        self.connection_status_label.set_label(error or "")
        if error:
            self.connection_status_label.add_css_class("error")

    def _set_machine(self, machine: TargetMachine | None):
        self.machine = machine
        self.machine_group.set_visible(machine is not None)
        if machine:
            self.machine_row.set_title(GLib.markup_escape_text(f"Connected to {machine.hostname or self.connection.host}"))
            subtitle = machine.description
            architecture = self.project_directory.get_architecture() if self.project_directory else None
            if architecture and architecture_supported(architecture.value, machine.architecture) is False:
                subtitle += f"\nWarning: build is for {architecture.value}, it won't run on this machine"
            self.machine_row.set_subtitle(GLib.markup_escape_text(subtitle))
            # Swap equal to memory, up to 8 GiB.
            self.swap_row.set_value(min(8, max(1, round(machine.memory / GIB))) if machine.memory else 0)
            self.efi_row.set_visible(machine.uefi)
            if not self.hostname_row.get_text():
                self.hostname_row.set_text("gentoo")
        self._load_disks()
        self.wizard_view._refresh_buttons_state()

    # Disk

    def _load_disks(self):
        for row in self.disk_rows:
            self.disks_group.remove(row)
        self.disk_rows = []
        self.selected_disk = None
        disks = self.machine.disks if self.machine else []
        group = None
        for disk in disks:
            row = Adw.ActionRow(title=GLib.markup_escape_text(disk.title), subtitle=GLib.markup_escape_text(disk.subtitle))
            check = Gtk.CheckButton(valign=Gtk.Align.CENTER)
            if group:
                check.set_group(group)
            group = group or check
            check.connect("toggled", self._on_disk_toggled, disk)
            row.add_prefix(check)
            row.set_activatable_widget(check)
            row.set_sensitive(not disk.in_use)
            self.disks_group.add(row)
            self.disk_rows.append(row)
        if not disks and self.machine:
            row = Adw.ActionRow(title="No disks found")
            self.disks_group.add(row)
            self.disk_rows.append(row)
        self._update_partitions()

    def _on_disk_toggled(self, check: Gtk.CheckButton, disk):
        if check.get_active():
            self.selected_disk = disk
            self._update_partitions()
            self.wizard_view._refresh_buttons_state()

    @Gtk.Template.Callback()
    def on_partitioning_changed(self, *args):
        self._update_partitions()

    def _plan(self) -> PartitionPlan | None:
        if self.selected_disk is None or self.machine is None:
            return None
        return PartitionPlan(
            disk=self.selected_disk,
            uefi=self.machine.uefi,
            efi_size=int(self.efi_row.get_value()) * MIB,
            swap_size=int(self.swap_row.get_value()) * GIB,
            filesystem=list(TargetFilesystem)[self.filesystem_row.get_selected()],
        )

    def _update_partitions(self):
        for row in self.partition_rows:
            self.partitions_group.remove(row)
        self.partition_rows = []
        plan = self._plan()
        if plan is None:
            self.partitions_group.set_description("Select disk")
            return
        root_size = plan.root_size
        # Unpacked stage takes few times more than its archive.
        archive_size = os.path.getsize(self.build.artifact_path) if self.build and os.path.isfile(self.build.artifact_path or "") else 0
        needed = max(8 * GIB, archive_size * 4)
        description = f"New GPT partition table on {plan.disk.path}"
        if root_size < needed:
            description += f". Warning: root partition is small, at least {format_size(needed)} is recommended"
        self.partitions_group.set_description(description)
        for index, partition in enumerate(plan.partitions, start=1):
            size = format_size(partition.size if partition.size else max(0, root_size))
            details = [size, partition.filesystem or "no filesystem"]
            if partition.mount_point:
                details.append(f"mounted at {partition.mount_point}")
            row = Adw.ActionRow(title=f"{plan.partition_device(index)} · {partition.name}", subtitle=", ".join(details))
            self.partitions_group.add(row)
            self.partition_rows.append(row)

    # System

    @Gtk.Template.Callback()
    def on_system_changed(self, row):
        error = self._system_error()
        # Missing password is not an error until something was typed.
        self.system_status_label.set_visible(error is not None and bool(self.root_password_row.get_text() or self.root_password_confirm_row.get_text()))
        self.system_status_label.set_label(error or "")
        self.wizard_view._refresh_buttons_state()

    def _system_error(self) -> str | None:
        hostname = self.hostname_row.get_text().strip()
        if hostname and not all(character.isalnum() or character in "-." for character in hostname):
            return "Hostname can contain only letters, digits, dots and hyphens"
        if not self.root_password_row.get_text():
            return "Set root password, root account of stage is locked"
        if self.root_password_row.get_text() != self.root_password_confirm_row.get_text():
            return "Passwords don't match"
        return None

    # Installation

    @Gtk.Template.Callback()
    def begin_installation(self, view):
        plan = self._plan()
        if plan is None or self.connection is None:
            return
        dialog = Adw.AlertDialog(
            heading="Erase disk?",
            body=f"All data on {plan.disk.path} ({plan.disk.subtitle}, {format_size(plan.disk.size)}) of {self.connection.host} will be erased and {self.build.stage_name} will be installed on it."
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("deploy", "Erase and deploy")
        dialog.set_response_appearance("deploy", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._on_confirm_response, plan)
        dialog.present(self.get_root())

    def _on_confirm_response(self, dialog, response: str, plan: PartitionPlan):
        if response != "deploy":
            return
        settings = DeploySystemSettings(
            hostname=self.hostname_row.get_text().strip() or None,
            root_password=self.root_password_row.get_text() or None,
            enable_ssh=self.ssh_row.get_active(),
            install_bootloader=self.bootloader_row.get_active(),
            reboot=self.reboot_row.get_active(),
        )
        installation = DeployInstallation(
            build=self.build, project_name=self.project_directory.name if self.project_directory else "",
            connection=self.connection, machine=self.machine, plan=plan, settings=settings,
        )
        self.installation_in_progress = installation
        installation.start()
        self.wizard_view.set_installation(installation)

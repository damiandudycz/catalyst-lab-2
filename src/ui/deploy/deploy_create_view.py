from __future__ import annotations
import os, threading
from gi.repository import Gtk, Adw, GLib
import re
from .deploy_installation import DeployInstallation, DeploySystemSettings, DeployUser, DEFAULT_USER_GROUPS
from .deploy_boot import Bootloader, StageContents, default_bootloader
from .deploy_target import TargetMachine, TargetFilesystem, PartitionPlan, GIB, MIB, format_size, architecture_supported
from .ssh_connection import SSHConnection

@Gtk.Template(resource_path='/com/damiandudycz/CatalystLab/ui/deploy/deploy_create_view.ui')
class DeployCreateView(Gtk.Box):
    """Wizard deploying stage build to machine booted from Gentoo LiveCD: connection, disk partitioning, system
    settings, users and bootloader."""
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
    ssh_row = Gtk.Template.Child()
    reboot_row = Gtk.Template.Child()
    system_status_label = Gtk.Template.Child()
    users_page = Gtk.Template.Child()
    root_password_row = Gtk.Template.Child()
    root_password_confirm_row = Gtk.Template.Child()
    users_group = Gtk.Template.Child()
    users_status_label = Gtk.Template.Child()
    boot_page = Gtk.Template.Child()
    stage_contents_row = Gtk.Template.Child()
    stage_contents_spinner = Gtk.Template.Child()
    bootloader_row = Gtk.Template.Child()
    kernel_row = Gtk.Template.Child()
    boot_info_label = Gtk.Template.Child()

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
        self.user_rows: list[UserRow] = []
        self.contents: StageContents | None = None # Read from archive in background.
        self.contents_error: str | None = None
        self.bootloader_options: list[Bootloader] = []
        if self.build and installation_in_progress is None:
            threading.Thread(target=self._scan_stage, daemon=True).start()
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
        if page == self.users_page:
            return self._users_error() is None
        if page == self.boot_page:
            return self.contents is not None or self.contents_error is not None
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
            self._update_boot_options()
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
        self.system_status_label.set_visible(error is not None)
        self.system_status_label.set_label(error or "")
        self.wizard_view._refresh_buttons_state()

    # Users

    @Gtk.Template.Callback()
    def on_users_changed(self, *args):
        error = self._users_error()
        # Missing root password is not shown as error until something was typed.
        typed = bool(self.root_password_row.get_text() or self.root_password_confirm_row.get_text() or self.user_rows)
        self.users_status_label.set_visible(error is not None and typed)
        self.users_status_label.set_label(error or "")
        self.wizard_view._refresh_buttons_state()

    @Gtk.Template.Callback()
    def on_add_user_clicked(self, button):
        row = UserRow(on_changed=self.on_users_changed, on_remove=self._remove_user)
        self.users_group.add(row)
        self.user_rows.append(row)
        row.set_expanded(True)
        self.on_users_changed()

    def _remove_user(self, row: UserRow):
        self.users_group.remove(row)
        self.user_rows.remove(row)
        self.on_users_changed()

    def _users_error(self) -> str | None:
        if not self.root_password_row.get_text():
            return "Set root password"
        if self.root_password_row.get_text() != self.root_password_confirm_row.get_text():
            return "Root passwords don't match"
        names = set()
        for row in self.user_rows:
            if error := row.error():
                return error
            name = row.user().name
            if name in names:
                return f"User {name} is added more than once"
            names.add(name)
        return None

    # Boot

    def _scan_stage(self):
        try:
            contents, error = StageContents.scan(self.build.artifact_path), None
        except Exception as e:
            contents, error = None, str(e)
        def done():
            self.contents, self.contents_error = contents, error
            self._update_boot_options()
            self.wizard_view._refresh_buttons_state()
        GLib.idle_add(done)

    def _update_boot_options(self):
        self.stage_contents_spinner.set_visible(self.contents is None and self.contents_error is None)
        if self.contents_error:
            self.stage_contents_row.set_subtitle(GLib.markup_escape_text(f"Failed to check stage: {self.contents_error}"))
        elif self.contents:
            self.stage_contents_row.set_subtitle(GLib.markup_escape_text(self.contents.summary))
        if self.machine is None:
            return
        contents = self.contents or StageContents()
        architecture, uefi = self.machine.architecture, self.machine.uefi
        self.bootloader_options = [item for item in Bootloader if item.supported(architecture, uefi)]
        labels = []
        for item in self.bootloader_options:
            if item == Bootloader.NONE:
                labels.append(item.display_name)
            elif item in contents.bootloaders:
                labels.append(f"{item.display_name}, in stage")
            else:
                labels.append(f"{item.display_name}, will be installed")
        self.bootloader_row.set_model(Gtk.StringList.new(labels))
        self.bootloader_row.set_selected(self.bootloader_options.index(default_bootloader(contents, architecture, uefi)))
        has_kernel = bool(contents.kernels)
        self.kernel_row.set_visible(self.contents is not None and not has_kernel)
        self.kernel_row.set_active(self.contents is not None and not has_kernel)
        self.on_boot_changed()

    def _selected_bootloader(self) -> Bootloader:
        index = self.bootloader_row.get_selected()
        return self.bootloader_options[index] if 0 <= index < len(self.bootloader_options) else Bootloader.NONE

    @Gtk.Template.Callback()
    def on_boot_changed(self, *args):
        if not hasattr(self, "bootloader_options"):
            return
        contents = self.contents or StageContents()
        bootloader = self._selected_bootloader()
        installs = []
        if bootloader != Bootloader.NONE and bootloader not in contents.bootloaders:
            installs.append(bootloader.package(contents.init))
        if self.kernel_row.get_visible() and self.kernel_row.get_active():
            installs.append("sys-kernel/gentoo-kernel-bin")
        notes = []
        if installs:
            notes.append(f"{', '.join(installs)} will be installed with emerge on the machine, it needs internet connection. "
                         "Gentoo repository is downloaded when stage doesn't contain it, packages are compiled unless stage has binary packages repository configured.")
        if bootloader != Bootloader.NONE and not contents.kernels and not (self.kernel_row.get_visible() and self.kernel_row.get_active()):
            notes.append("Stage has no kernel, bootloader can't start the system until kernel is installed.")
        if bootloader == Bootloader.NONE:
            notes.append("System won't boot until bootloader is installed manually.")
        self.boot_info_label.set_label(" ".join(notes))
        self.boot_info_label.set_visible(bool(notes))

    def _system_error(self) -> str | None:
        hostname = self.hostname_row.get_text().strip()
        if hostname and not all(character.isalnum() or character in "-." for character in hostname):
            return "Hostname can contain only letters, digits, dots and hyphens"
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
            users=[row.user() for row in self.user_rows],
            enable_ssh=self.ssh_row.get_active(),
            bootloader=self._selected_bootloader(),
            install_kernel=self.kernel_row.get_visible() and self.kernel_row.get_active(),
            reboot=self.reboot_row.get_active(),
        )
        installation = DeployInstallation(
            build=self.build, project_name=self.project_directory.name if self.project_directory else "",
            connection=self.connection, machine=self.machine, plan=plan, settings=settings,
            contents=self.contents or StageContents(),
        )
        self.installation_in_progress = installation
        installation.start()
        self.wizard_view.set_installation(installation)


_USERNAME = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")

class UserRow(Adw.ExpanderRow):
    """Account created on installed system."""

    def __init__(self, on_changed, on_remove):
        super().__init__(title="New user", icon_name="avatar-default-symbolic")
        self.on_changed = on_changed
        self.name_row = Adw.EntryRow(title="Username")
        self.full_name_row = Adw.EntryRow(title="Full name")
        self.password_row = Adw.PasswordEntryRow(title="Password")
        self.password_confirm_row = Adw.PasswordEntryRow(title="Confirm password")
        self.groups_row = Adw.EntryRow(title="Groups", text=", ".join(DEFAULT_USER_GROUPS))
        for row in (self.name_row, self.full_name_row, self.password_row, self.password_confirm_row, self.groups_row):
            row.connect("changed", self._on_changed)
            self.add_row(row)
        remove_button = Gtk.Button(icon_name="user-trash-symbolic", tooltip_text="Remove user", valign=Gtk.Align.CENTER)
        remove_button.add_css_class("flat")
        remove_button.connect("clicked", lambda button: on_remove(self))
        self.add_suffix(remove_button)

    def _on_changed(self, row):
        user = self.user()
        self.set_title(GLib.markup_escape_text(user.name or "New user"))
        self.set_subtitle(GLib.markup_escape_text(user.full_name))
        self.on_changed()

    def user(self) -> DeployUser:
        return DeployUser(
            name=self.name_row.get_text().strip(),
            password=self.password_row.get_text(),
            full_name=self.full_name_row.get_text().strip(),
            groups=[group for group in re.split(r"[,\s]+", self.groups_row.get_text()) if group],
        )

    def error(self) -> str | None:
        user = self.user()
        if not _USERNAME.match(user.name):
            return "Username must start with a lowercase letter and contain only lowercase letters, digits, - and _"
        if user.name == "root":
            return "Root account already exists"
        if not user.password:
            return f"Set password of {user.name}"
        if user.password != self.password_confirm_row.get_text():
            return f"Passwords of {user.name} don't match"
        if any(not re.match(r"^[a-z_][a-z0-9_-]*$", group) for group in user.groups):
            return f"Groups of {user.name} contain invalid name"
        return None

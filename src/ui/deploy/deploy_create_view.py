from __future__ import annotations
import os, threading
from gi.repository import Gtk, Adw, GLib
import re
from .deploy_installation import DeployInstallation, DeploySystemSettings, DeployUser, DEFAULT_USER_GROUPS
from .deploy_boot import Bootloader, Kernel, FIRMWARE_PACKAGE, StageContents, default_bootloader
from .deploy_system import (
    NetworkService, NetworkSettings, LocalizationSettings, default_network_service, local_localization, local_public_keys
)
from .deploy_target import (
    TargetMachine, TargetFilesystem, PartitionPlan, PartitionSpec, PartitionType, GIB, MIB, format_size,
    architecture_supported, default_layout, PartitionTable
)
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
    partitions_group = Gtk.Template.Child()
    partitions_status_label = Gtk.Template.Child()
    table_row = Gtk.Template.Child()
    system_page = Gtk.Template.Child()
    hostname_row = Gtk.Template.Child()
    ssh_row = Gtk.Template.Child()
    reboot_row = Gtk.Template.Child()
    system_status_label = Gtk.Template.Child()
    timezone_row = Gtk.Template.Child()
    locale_row = Gtk.Template.Child()
    keymap_row = Gtk.Template.Child()
    network_service_row = Gtk.Template.Child()
    network_mode_row = Gtk.Template.Child()
    interface_row = Gtk.Template.Child()
    address_row = Gtk.Template.Child()
    gateway_row = Gtk.Template.Child()
    dns_row = Gtk.Template.Child()
    ssh_keys_group = Gtk.Template.Child()
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
    firmware_row = Gtk.Template.Child()
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
        self.partition_rows: list[PartitionRow] = []
        self.layout: list[PartitionSpec] = []
        self.user_rows: list[UserRow] = []
        self.contents: StageContents | None = None # Read from archive in background.
        self.contents_error: str | None = None
        self.bootloader_options: list[Bootloader] = []
        self.kernel_options: list[Kernel] = []
        self.network_options: list[NetworkService] = []
        localization = local_localization()
        self.timezone_row.set_text(localization.timezone)
        self.locale_row.set_text(localization.locale)
        self.keymap_row.set_text(localization.keymap)
        self.key_checks: list[tuple[Gtk.CheckButton, str]] = []
        for key in local_public_keys():
            check = Gtk.CheckButton(active=True, valign=Gtk.Align.CENTER)
            row = Adw.ActionRow(title=GLib.markup_escape_text(key.title), subtitle=GLib.markup_escape_text(key.subtitle))
            row.add_prefix(check)
            row.set_activatable_widget(check)
            self.ssh_keys_group.add(row)
            self.key_checks.append((check, key.key))
        if not self.key_checks:
            self.ssh_keys_group.add(Adw.ActionRow(title="No SSH keys found in ~/.ssh"))
        self._update_network_options()
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
            plan = self._plan()
            return plan is not None and plan.error() is None
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
            if not self.hostname_row.get_text():
                self.hostname_row.set_text("gentoo")
            # Static address suggested from LiveCD network.
            self.interface_row.set_text(machine.interface)
            self.address_row.set_text(machine.address)
            self.gateway_row.set_text(machine.gateway)
            self.dns_row.set_text(machine.dns)
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
        self.layout = []
        self._load_partition_rows()

    def _on_disk_toggled(self, check: Gtk.CheckButton, disk):
        if check.get_active():
            self.selected_disk = disk
            self.layout = self._default_layout()
            self._load_partition_rows()

    def _plan(self) -> PartitionPlan | None:
        if self.selected_disk is None or self.machine is None:
            return None
        return PartitionPlan(disk=self.selected_disk, uefi=self.machine.uefi, layout=self.layout, table=self._table())

    def _table(self) -> PartitionTable:
        return list(PartitionTable)[self.table_row.get_selected()]

    def _default_layout(self) -> list[PartitionSpec]:
        return default_layout(self.selected_disk, self.machine.uefi, self.machine.memory, self.machine.architecture, self._table())

    @Gtk.Template.Callback()
    def on_table_changed(self, row, param):
        if self.selected_disk and self.machine:
            self.layout = self._default_layout()
            self._load_partition_rows()

    # Partitions

    @Gtk.Template.Callback()
    def on_default_layout_clicked(self, button):
        if self.selected_disk and self.machine:
            self.layout = self._default_layout()
            self._load_partition_rows()

    @Gtk.Template.Callback()
    def on_add_partition_clicked(self, button):
        # New partition uses remaining space when previous last one has fixed size.
        uses_rest = any(spec.size is None for spec in self.layout)
        self.layout.append(PartitionSpec(PartitionType.LINUX, 10 * GIB if uses_rest else None, TargetFilesystem.EXT4, ""))
        self._load_partition_rows(expand=len(self.layout) - 1)

    def _move_partition(self, spec: PartitionSpec, offset: int):
        index = self.layout.index(spec)
        target = index + offset
        if 0 <= target < len(self.layout):
            self.layout[index], self.layout[target] = self.layout[target], self.layout[index]
            self._load_partition_rows(expand=target)

    def _remove_partition(self, spec: PartitionSpec):
        self.layout.remove(spec)
        self._load_partition_rows()

    def _load_partition_rows(self, expand: int | None = None):
        """Rows are created again when partitions are added, removed or moved. Edited values update them."""
        for row in self.partition_rows:
            self.partitions_group.remove(row)
        self.partition_rows = []
        enabled = self.selected_disk is not None
        self.partitions_group.get_header_suffix().set_sensitive(enabled)
        self.table_row.set_sensitive(enabled)
        for index, spec in enumerate(self.layout):
            row = PartitionRow(spec, on_changed=self._update_partitions, on_move=self._move_partition, on_remove=self._remove_partition)
            row.set_expanded(index == expand)
            self.partitions_group.add(row)
            self.partition_rows.append(row)
        self._update_partitions()

    def _update_partitions(self):
        plan = self._plan()
        if plan is None:
            self.partitions_group.set_description("Select disk")
            self.partitions_status_label.set_visible(False)
            self.wizard_view._refresh_buttons_state()
            return
        for index, row in enumerate(self.partition_rows, start=1):
            row.update(plan.partition_device(index), plan.size_of(row.spec), first=index == 1, last=index == len(self.partition_rows))
        free = plan.remaining_size if all(spec.size for spec in self.layout) else 0
        description = f"New {plan.table.display_name} partition table on {plan.disk.path}, {format_size(plan.usable_size)}"
        if free > 0:
            description += f", {format_size(free)} not used"
        self.partitions_group.set_description(description)
        messages = []
        if error := plan.error():
            messages.append(error)
        messages += [f"Warning: {warning}" for warning in plan.warnings(self.machine.architecture)]
        self.partitions_status_label.set_label("\n".join(messages))
        self.partitions_status_label.set_visible(bool(messages))
        self.wizard_view._refresh_buttons_state()

    # System

    @Gtk.Template.Callback()
    def on_system_changed(self, row, *args):
        if not hasattr(self, "network_options"):
            return
        network = self._network()
        static = network.service != NetworkService.NONE and network.static
        self.network_mode_row.set_visible(network.service != NetworkService.NONE)
        for widget in (self.interface_row, self.address_row, self.gateway_row, self.dns_row):
            widget.set_visible(static)
        error = self._system_error()
        self.system_status_label.set_visible(error is not None)
        self.system_status_label.set_label(error or "")
        self.wizard_view._refresh_buttons_state()
        self.on_boot_changed() # Lists packages installed for selected network service.

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
            self._update_network_options()
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
            if not item.needs_package:
                labels.append(item.display_name)
            elif item in contents.bootloaders:
                labels.append(f"{item.display_name}, in stage")
            else:
                labels.append(f"{item.display_name}, will be installed")
        self.bootloader_row.set_model(Gtk.StringList.new(labels))
        self.bootloader_row.set_selected(self.bootloader_options.index(default_bootloader(contents, architecture, uefi)))
        # Kernel from stage is used when it has one, otherwise prebuilt distribution kernel is installed.
        self.kernel_options = ([Kernel.STAGE] if contents.kernels else []) + [Kernel.DISTRIBUTION_BINARY, Kernel.DISTRIBUTION, Kernel.NONE]
        self.kernel_row.set_model(Gtk.StringList.new([
            f"{item.display_name} ({', '.join(contents.kernels)})" if item == Kernel.STAGE else item.display_name
            for item in self.kernel_options
        ]))
        self.kernel_row.set_selected(0)
        self.firmware_row.set_active(not contents.kernels)
        self.on_boot_changed()

    def _selected_kernel(self) -> Kernel:
        index = self.kernel_row.get_selected()
        return self.kernel_options[index] if 0 <= index < len(self.kernel_options) else Kernel.NONE

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
        if bootloader.needs_package and bootloader not in contents.bootloaders:
            installs.append(bootloader.package(contents.init))
        kernel = self._selected_kernel()
        network = self._network().service
        if network.package and network not in contents.network_services:
            installs.append(network.package)
        if self.firmware_row.get_active():
            installs.append(FIRMWARE_PACKAGE)
        if kernel.package:
            installs.append(kernel.package)
        notes = []
        if installs:
            notes.append(f"{', '.join(installs)} will be installed with emerge on the machine, it needs internet connection. "
                         "Gentoo repository is downloaded when stage doesn't contain it, packages are compiled unless stage has binary packages repository configured.")
        if kernel.package:
            notes.append("Installed kernel gets initramfs generated by dracut.")
        if kernel == Kernel.DISTRIBUTION:
            notes.append("Compiling kernel can take an hour or more.")
        if kernel == Kernel.NONE and bootloader != Bootloader.NONE:
            notes.append("Bootloader can't start the system until kernel is installed.")
        if bootloader == Bootloader.NONE:
            notes.append("System won't boot until bootloader is installed manually.")
        self.boot_info_label.set_label(" ".join(notes))
        self.boot_info_label.set_visible(bool(notes))

    def _system_error(self) -> str | None:
        hostname = self.hostname_row.get_text().strip()
        if hostname and not all(character.isalnum() or character in "-." for character in hostname):
            return "Hostname can contain only letters, digits, dots and hyphens"
        return self._localization().error() or self._network().error()

    def _localization(self) -> LocalizationSettings:
        return LocalizationSettings(timezone=self.timezone_row.get_text().strip(), locale=self.locale_row.get_text().strip(),
                                    keymap=self.keymap_row.get_text().strip())

    def _network(self) -> NetworkSettings:
        index = self.network_service_row.get_selected()
        service = self.network_options[index] if 0 <= index < len(self.network_options) else NetworkService.NONE
        return NetworkSettings(
            service=service, static=self.network_mode_row.get_selected() == 1,
            interface=self.interface_row.get_text().strip(), address=self.address_row.get_text().strip(),
            gateway=self.gateway_row.get_text().strip(), dns=self.dns_row.get_text().strip(),
        )

    def _update_network_options(self):
        """Services found in stage, or dhcpcd installed from repository when stage has none."""
        contents = self.contents or StageContents()
        selected = self._network().service if self.network_options else None
        self.network_options = [service for service in NetworkService if service.supported(contents.init)
                                and (service in contents.network_services or service in (NetworkService.DHCPCD, NetworkService.NONE))]
        labels = [service.display_name if service == NetworkService.NONE else
                  f"{service.display_name}, {'in stage' if service in contents.network_services else 'will be installed'}"
                  for service in self.network_options]
        self.network_service_row.set_model(Gtk.StringList.new(labels))
        default = selected if selected in self.network_options and self.contents is None else default_network_service(contents.network_services, contents.init)
        self.network_service_row.set_selected(self.network_options.index(default))
        self.on_system_changed(None)

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
            ssh_keys=[key for check, key in self.key_checks if check.get_active()],
            network=self._network(),
            localization=self._localization(),
            bootloader=self._selected_bootloader(),
            kernel=self._selected_kernel(),
            install_firmware=self.firmware_row.get_active(),
            reboot=self.reboot_row.get_active(),
        )
        installation = DeployInstallation(
            build=self.build, project_name=self.project_directory.name if self.project_directory else "",
            connection=self.connection, machine=self.machine, plan=plan, settings=settings,
            contents=self.contents or StageContents(),
        )
        installation.project_id = self.project_directory.id if self.project_directory else None
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


class PartitionRow(Adw.ExpanderRow):
    """Partition of layout: type, filesystem, mount point and size."""

    _TYPES = list(PartitionType)
    _FILESYSTEMS = [TargetFilesystem.EXT4, TargetFilesystem.XFS, TargetFilesystem.BTRFS, TargetFilesystem.VFAT]

    def __init__(self, spec: PartitionSpec, on_changed, on_move, on_remove):
        super().__init__()
        self.spec = spec
        self.on_changed = on_changed
        self._loading = True
        self.type_row = Adw.ComboRow(title="Type", model=Gtk.StringList.new([item.display_name for item in self._TYPES]))
        self.type_row.set_selected(self._TYPES.index(spec.type))
        self.filesystem_row = Adw.ComboRow(title="Filesystem", model=Gtk.StringList.new([item.value for item in self._FILESYSTEMS]))
        self.filesystem_row.set_selected(self._FILESYSTEMS.index(spec.filesystem or TargetFilesystem.EXT4))
        self.mount_row = Adw.EntryRow(title="Mount point", text=spec.mount_point or "")
        self.rest_row = Adw.SwitchRow(title="Use remaining space", active=spec.size is None)
        self.size_row = Adw.SpinRow(title="Size", subtitle="GiB", digits=1,
                                    adjustment=Gtk.Adjustment(lower=0.1, upper=1024 * 64, step_increment=1, page_increment=10))
        self.size_row.set_value(round((spec.size or 10 * GIB) / GIB, 1))
        for row in (self.type_row, self.filesystem_row, self.mount_row, self.rest_row, self.size_row):
            self.add_row(row)
        self.type_row.connect("notify::selected", self._on_edited)
        self.filesystem_row.connect("notify::selected", self._on_edited)
        self.mount_row.connect("changed", self._on_edited)
        self.rest_row.connect("notify::active", self._on_edited)
        self.size_row.connect("notify::value", self._on_edited)
        buttons = Gtk.Box(spacing=0, valign=Gtk.Align.CENTER)
        self.up_button = self._button("go-up-symbolic", "Move up", lambda button: on_move(spec, -1))
        self.down_button = self._button("go-down-symbolic", "Move down", lambda button: on_move(spec, 1))
        for button in (self.up_button, self.down_button, self._button("user-trash-symbolic", "Remove partition", lambda button: on_remove(spec))):
            buttons.append(button)
        self.add_suffix(buttons)
        self._loading = False
        self._update_visibility()

    @staticmethod
    def _button(icon: str, tooltip: str, handler) -> Gtk.Button:
        button = Gtk.Button(icon_name=icon, tooltip_text=tooltip)
        button.add_css_class("flat")
        button.connect("clicked", handler)
        return button

    def _on_edited(self, *args):
        if self._loading:
            return
        previous_type = self.spec.type
        self.spec.type = self._TYPES[self.type_row.get_selected()]
        if self.spec.type != previous_type:
            # Suggested values for new type.
            self._loading = True
            if self.spec.type == PartitionType.EFI:
                self.mount_row.set_text("/efi")
                self.rest_row.set_active(False)
                self.size_row.set_value(1)
            elif self.spec.type == PartitionType.LINUX and self.mount_row.get_text() == "/efi":
                self.mount_row.set_text("")
            self._loading = False
        self.spec.filesystem = self._FILESYSTEMS[self.filesystem_row.get_selected()]
        self.spec.mount_point = self.mount_row.get_text().strip() or None
        if self.spec.type == PartitionType.BIOS_BOOT:
            self.spec.size = 1 * MIB
        else:
            self.spec.size = None if self.rest_row.get_active() else int(self.size_row.get_value() * GIB) // MIB * MIB
        self._update_visibility()
        self.on_changed()

    def _update_visibility(self):
        partition_type = self.spec.type
        self.filesystem_row.set_visible(partition_type == PartitionType.LINUX)
        self.mount_row.set_visible(partition_type in (PartitionType.LINUX, PartitionType.EFI))
        self.rest_row.set_visible(partition_type in (PartitionType.LINUX, PartitionType.SWAP))
        self.size_row.set_visible(partition_type != PartitionType.BIOS_BOOT and not (self.rest_row.get_visible() and self.rest_row.get_active()))

    def update(self, device: str, size: int, first: bool, last: bool):
        spec = self.spec
        purpose = spec.mount_point if spec.type in (PartitionType.LINUX, PartitionType.EFI) and spec.mount_point else spec.type.display_name
        self.set_title(GLib.markup_escape_text(f"{device} · {purpose}"))
        details = [format_size(size) + (" (remaining space)" if spec.size is None else "")]
        if spec.type == PartitionType.LINUX:
            details.append((spec.filesystem or TargetFilesystem.EXT4).value)
        elif spec.type == PartitionType.EFI:
            details.append("EFI system, vfat")
        else:
            details.append(spec.type.display_name)
        self.set_subtitle(GLib.markup_escape_text(", ".join(details)))
        self.up_button.set_sensitive(not first)
        self.down_button.set_sensitive(not last)

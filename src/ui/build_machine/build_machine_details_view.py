from __future__ import annotations
import threading
from gi.repository import Gtk, Adw, GLib
from .build_machine import BuildMachine
from .build_machine_manager import BuildMachineManager
from .repository import Repository
from .event_bus import SharedEvent

class BuildMachineDetailsView(Gtk.Box):
    """Status of virtual machine, starting and stopping it, toolsets running in it, removing it."""

    def __init__(self, machine: BuildMachine, content_navigation_view: Adw.NavigationView | None = None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.machine = machine
        self.content_navigation_view = content_navigation_view
        self.working = False
        page = Adw.PreferencesPage()
        page.set_vexpand(True)
        self.append(page)

        # Status
        status_group = Adw.PreferencesGroup(title="Status")
        self.status_row = Adw.ActionRow(title="State", icon_name="virtual-machine-symbolic")
        self.spinner = Adw.Spinner(visible=False)
        self.status_row.add_suffix(self.spinner)
        self.action_button = Gtk.Button(valign=Gtk.Align.CENTER)
        self.action_button.connect("clicked", self.on_action_clicked)
        self.status_row.add_suffix(self.action_button)
        status_group.add(self.status_row)
        status_group.add(Adw.ActionRow(
            title="Resources",
            subtitle=f"{machine.cpus} CPUs, {machine.memory_gib} GiB memory, {machine.disk_gib} GiB disk"
        ))
        instance_row = Adw.ActionRow(title="Lima instance", subtitle=machine.instance_name, subtitle_selectable=True)
        status_group.add(instance_row)
        page.add(status_group)

        # Toolsets
        toolsets_group = Adw.PreferencesGroup(title="Toolsets", description="Toolsets loaded inside this machine. Select machine in toolset details to move toolset here.")
        toolsets = [toolset for toolset in Repository.Toolset.value if getattr(toolset, "machine_id", None) == machine.id]
        for toolset in toolsets:
            toolsets_group.add(Adw.ActionRow(title=GLib.markup_escape_text(toolset.name), subtitle=GLib.markup_escape_text(toolset.short_details), icon_name="toolbox-symbolic"))
        if not toolsets:
            toolsets_group.add(Adw.ActionRow(title="No toolsets run in this machine yet"))
        page.add(toolsets_group)

        # Delete
        delete_group = Adw.PreferencesGroup()
        delete_button = Gtk.Button(label="Delete virtual machine", halign=Gtk.Align.CENTER)
        delete_button.add_css_class("destructive-action")
        delete_button.add_css_class("pill")
        delete_button.connect("clicked", self.on_delete_clicked)
        delete_group.add(delete_button)
        page.add(delete_group)

        machine.event_bus.subscribe(SharedEvent.STATE_UPDATED, self._on_machine_updated)
        self.connect("map", lambda widget: self._refresh_in_background())
        self._update_display()

    # Status

    def _refresh_in_background(self):
        threading.Thread(target=self.machine.refresh_status, daemon=True).start()

    def _on_machine_updated(self, machine):
        self._update_display()

    def _update_display(self):
        status = self.machine._status or "Checking..."
        self.status_row.set_subtitle(status)
        self.spinner.set_visible(self.working)
        self.action_button.set_sensitive(not self.working and self.machine._status in (BuildMachine.STATUS_RUNNING, BuildMachine.STATUS_STOPPED))
        self.action_button.set_label("Stop" if self.machine._status == BuildMachine.STATUS_RUNNING else "Start")

    def on_action_clicked(self, button):
        running = self.machine._status == BuildMachine.STATUS_RUNNING
        self.working = True
        self.status_row.set_subtitle("Stopping..." if running else "Starting...")
        self._update_display()
        def work():
            try:
                if running:
                    self.machine.stop()
                else:
                    self.machine.start()
            except Exception as e:
                print(f"Failed to change state of machine: {e}")
            def done():
                self.working = False
                self._update_display()
            GLib.idle_add(done)
        threading.Thread(target=work, daemon=True).start()

    # Delete

    def on_delete_clicked(self, button):
        toolsets = [toolset.name for toolset in Repository.Toolset.value if getattr(toolset, "machine_id", None) == self.machine.id]
        body = f"Virtual machine \"{self.machine.name}\" will be deleted with its disk, including extracted toolsets and package caches stored in it. Files in shared folders are kept."
        if toolsets:
            body += f"\n\nToolsets {', '.join(toolsets)} will be moved to this computer."
        dialog = Adw.AlertDialog(heading="Delete virtual machine?", body=body)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Delete")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", self.on_delete_response)
        dialog.present(self.get_root())

    def on_delete_response(self, dialog, response: str):
        if response != "delete":
            return
        BuildMachineManager.shared().remove_machine(self.machine)
        if self.content_navigation_view:
            self.content_navigation_view.pop()

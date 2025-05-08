from gi.repository import Gtk, Adw, GObject
from functools import partial
from .app_events import AppEvents, app_event_bus
from .root_helper_client import RootHelperClient, root_function
from .root_helper_server import ServerCommand, ServerFunction
from .settings import *

class RootAccessButton(Gtk.Overlay):

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        self.displayed_requests_views = []

        # Create the button and spinner
        self.root_access_button = Gtk.Button.new()
        self.root_access_spinner = Adw.Spinner()

        # Set up the button
        self.root_access_button.set_tooltip_text("Toggle root access")
        self.root_access_button.get_style_context().add_class("circular")
        self.root_access_button.get_style_context().add_class("image-button")
        self.root_access_button.set_focusable(False)

        # Set up the spinner
        self.root_access_spinner.set_can_target(False)
        self.root_access_spinner.set_visible(RootHelperClient.shared().running_actions)

        # Add the button and spinner to the overlay
        self.set_child(self.root_access_button)
        self.add_overlay(self.root_access_spinner)

        # Create popover to display when the button is clicked
        self.popover = Gtk.Popover.new()
        self.popover.set_parent(self)
        self.popover.set_focusable(False)

        # Popover task list
        self.task_list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.task_list_box.set_focusable(False)
        self.task_list_box.set_vexpand(True)
        self.popover.set_child(self.task_list_box)

        # Popover close button
        self.stop_button = Gtk.Button()
        self.stop_button.set_focusable(False)
        self.stop_button.set_label("Disable root access")
        self.stop_button.get_style_context().add_class("destructive-action")
        self.stop_button.connect("clicked", self.disable_root_access)
        self.task_list_box.append(self.stop_button)

        # Popover start button
        self.start_button = Gtk.Button()
        self.start_button.set_focusable(False)
        self.start_button.set_label("Enable root access")
        self.start_button.get_style_context().add_class("suggested-action")
        self.start_button.connect("clicked", self.toggle_root_access)
        self.task_list_box.append(self.start_button)

        # CheckButton for "Keep root access unlocked"
        self.keep_unlocked_checkbox = Gtk.CheckButton(label="Keep unlocked")
        self.keep_unlocked_checkbox.set_active(Settings.current.keep_root_unlocked)
        self.keep_unlocked_checkbox.set_focusable(False)
        self.keep_unlocked_checkbox.connect("toggled", self.on_keep_unlocked_toggled)
        self.keep_unlocked_checkbox.get_style_context().add_class("caption-heading")
        self.keep_unlocked_checkbox.set_margin_top(6)
        self.task_list_box.append(self.keep_unlocked_checkbox)

        # Divider
        #self.root_tasks_separator = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        #self.root_tasks_separator.set_visible(RootHelperClient.shared().running_actions)
        #self.root_tasks_separator.set_margin_top(12)
        #self.task_list_box.append(self.root_tasks_separator)

        # "Root tasks:" label (initially hidden)
        self.root_tasks_label = Gtk.Label(label="Root tasks:")
        self.root_tasks_label.get_style_context().add_class("caption-heading")
        self.root_tasks_label.set_halign(Gtk.Align.START)
        self.root_tasks_label.set_margin_top(12)
        self.root_tasks_label.set_margin_bottom(6)
        self.root_tasks_label.set_visible(RootHelperClient.shared().running_actions)
        self.root_tasks_label.get_style_context().add_class("dim-label")
        self.task_list_box.append(self.root_tasks_label)

        # Add initial requests to list
        for request in RootHelperClient.shared().running_actions:
            self.add_request_to_list(request)

        # Event handling for the button click
        self.root_access_button.connect("clicked", self.toggle_root_access)

        # Subscribe to events
        app_event_bus.subscribe(AppEvents.CHANGE_ROOT_ACCESS, self.root_access_changed)
        app_event_bus.subscribe(AppEvents.ROOT_REQUEST_STATUS, self.root_requests_status_changed)
        Settings.current.event_bus.subscribe(SettingsEvents.KEEP_ROOT_UNLOCKED_CHANGED, self.keep_root_unlocked_changed)

        # Set initial state based on root access status
        self.root_access_changed(RootHelperClient.shared().is_server_process_running)

    def toggle_root_access(self, sender):
        """Toggle root access state and show the popover."""
        if RootHelperClient.shared().is_server_process_running:
            self.popover.show()
        else:
            self.popover.hide()
            RootHelperClient.shared().start_root_helper()

    def root_access_changed(self, enabled: bool):
        """Handle changes to root access state."""
        if enabled:
            self.root_access_button.get_style_context().add_class("destructive-action")
            icon = Gtk.Image.new_from_icon_name("changes-allow-symbolic")
            self.root_access_button.set_child(icon)
        else:
            self.root_access_button.get_style_context().remove_class("destructive-action")
            icon = Gtk.Image.new_from_icon_name("changes-prevent-symbolic")
            self.root_access_button.set_child(icon)
        self.start_button.set_visible(not enabled)
        self.stop_button.set_visible(enabled)

    def root_requests_status_changed(self, client: RootHelperClient, request: GObject.Object, status: bool):
        """Handle changes to root access request status."""
        self.root_access_spinner.set_visible(client.running_actions)
        if status:
            self.add_request_to_list(request)
        else:
            self.remove_request_from_list(request)
        self.root_tasks_label.set_visible(RootHelperClient.shared().running_actions)
        self.root_tasks_separator.set_visible(RootHelperClient.shared().running_actions)

    def disable_root_access(self, sender):
        """Disable root access when the button is clicked."""
        RootHelperClient.shared().stop_root_helper()
        self.popover.hide()

    def add_request_to_list(self, request: ServerCommand | ServerFunction):
        action_row = RootActionInfoRow(request=request)
        self.task_list_box.append(action_row)
        self.displayed_requests_views.append(action_row)

    def remove_request_from_list(self, request: ServerCommand | ServerFunction):
        for row in self.displayed_requests_views:
            if row.request == request:
                self.task_list_box.remove(row)
                self.displayed_requests_views.remove(row)
                break

    def on_keep_unlocked_toggled(self, button):
        Settings.current.keep_root_unlocked = button.get_active()

    def keep_root_unlocked_changed(self, value: bool):
        print(f"-- {value}")

class RootActionInfoRow(Gtk.Box):
    def __init__(self, request: ServerCommand | ServerFunction):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.request = request
        self.set_hexpand(True)

        # Divider (separator at the top)
        separator = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        self.append(separator)

        # Inner horizontal row layout
        row_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        row_box.set_hexpand(True)
        row_box.set_valign(Gtk.Align.CENTER)

        # Create label on the left
        label = Gtk.Label(label=request.function_name)
        label.get_style_context().add_class("caption-heading")
        label.set_xalign(0)
        label.set_hexpand(True)
        row_box.append(label)

        # Icon button on the right
        icon = Gtk.Image.new_from_icon_name("window-close-symbolic")
        button = Gtk.Button()
        button.set_child(icon)
        button.set_valign(Gtk.Align.CENTER)
        button.set_focusable(False)
        button.get_style_context().add_class("circular")
        button.get_style_context().add_class("flat")
        button.set_margin_top(4)
        button.set_margin_bottom(4)
        row_box.append(button)

        # Add the horizontal row to the vertical box
        self.append(row_box)


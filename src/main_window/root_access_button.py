from gi.repository import Gtk, Adw, GObject
from functools import partial
from .app_events import AppEvents, app_event_bus
from .root_helper_client import RootHelperClient, root_function
from .root_helper_server import ServerCommand, ServerFunction

class RootAccessButton(Gtk.Overlay):

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

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

        # Event handling for the button click
        self.root_access_button.connect("clicked", self.toggle_root_access)

        # Subscribe to events
        app_event_bus.subscribe(AppEvents.CHANGE_ROOT_ACCESS, self.root_access_changed)
        app_event_bus.subscribe(AppEvents.ROOT_REQUEST_STATUS, self.root_requests_status_changed)

        # Set initial state based on root access status
        self.root_access_changed(RootHelperClient.shared().is_server_process_running)

    def toggle_root_access(self, sender):
        """Toggle root access state and show the popover."""
        if RootHelperClient.shared().is_server_process_running:
            self.show_root_tasks_popover()
            #RootHelperClient.shared().stop_root_helper()
        else:
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

    def root_requests_status_changed(self, client: RootHelperClient, request: GObject.Object, status: bool):
        """Handle changes to root access request status."""
        self.root_access_spinner.set_visible(client.running_actions)

    def show_root_tasks_popover(self):
        """Show a popover with a list of root tasks and a 'Disable root access' button."""
        # Create a vertical list box to display the running actions
        self.list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.list_box.set_focusable(False)  # Prevent focus on the ListBox itself
        self.list_box.set_vexpand(True)

        # Add rows for each running action
        for action in RootHelperClient.shared().running_actions:
            action_row = RootActionInfoRow(request=action)
            action_row.set_title(action.function_name)
            action_row.set_icon_name("user-desktop-symbolic")
            action_row.set_focusable(False)  # Prevent focus on individual rows
            self.list_box.append(action_row)

        # Create the 'Disable root access' ActionRow as a Gtk.Button
        disable_row = Gtk.Button()
        disable_row.set_focusable(False)  # Disable focus on the button
        disable_row.set_label("Disable root access")
        disable_row.get_style_context().add_class("destructive-action")
        disable_row.connect("clicked", self.disable_root_access)
        self.list_box.append(disable_row)

        # Make sure the popover itself doesn't gain focus
        self.popover.set_focusable(False)

        # Update the popover content
        self.popover.set_child(self.list_box)

        # Show the popover
        self.popover.show()

    def disable_root_access(self, sender):
        """Disable root access when the button is clicked."""
        RootHelperClient.shared().stop_root_helper()
        self.popover.hide()

class RootActionInfoRow(Adw.ActionRow):
    def __init__(self, request: ServerCommand | ServerFunction):
        super().__init__()
        self.request = request

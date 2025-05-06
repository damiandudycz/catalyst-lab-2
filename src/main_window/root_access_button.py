from gi.repository import Gtk, Adw, GObject
from functools import partial
from .app_events import AppEvents, app_event_bus
from .root_helper_client import RootHelperClient, root_function
from .root_helper_server import ServerCommand, ServerFunction

class RootAccessButton(Gtk.Overlay):
    """Overlay widget containing a button for toggling root access with a spinner."""

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

        # Event handling
        self.root_access_button.connect("clicked", self.toggle_root_access)

        # Subscribe to events
        app_event_bus.subscribe(AppEvents.CHANGE_ROOT_ACCESS, self.root_access_changed)
        app_event_bus.subscribe(AppEvents.ROOT_REQUEST_STATUS, self.root_requests_status_changed)

        # Set initial state based on root access status
        self.root_access_changed(RootHelperClient.shared().is_server_process_running)

    def toggle_root_access(self, sender):
        """Toggle root access state."""
        if RootHelperClient.shared().is_server_process_running:
            RootHelperClient.shared().stop_root_helper()
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

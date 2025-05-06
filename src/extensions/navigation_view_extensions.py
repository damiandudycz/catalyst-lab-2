from gi.repository import Gtk
from gi.repository import Adw
from .app_section import AppSection
from .app_section_details import AppSectionDetails
from .root_access_button import RootAccessButton

def _push_section(self, section: AppSection):
    section_details = AppSectionDetails(section)
    view = section_details.create_section(content_navigation_view=self)
    title = section_details.title
    self.push_view(view, title)

def _push_view(self, view: Gtk.Widget, title: str):
    # Create a header bar
    header = Adw.HeaderBar()
    # Wrap the content in a ToolbarView with the header
    toolbar_view = Adw.ToolbarView()
    toolbar_view.set_content(view)
    toolbar_view.add_top_bar(header)
    # Add the root access button to the header
    root_access_button = RootAccessButton()
    header.pack_end(root_access_button)
    # Create a navigation page with title and child
    page = Adw.NavigationPage.new(toolbar_view, title)
    self.push(page)

Adw.NavigationView.push_section = _push_section
Adw.NavigationView.push_view = _push_view

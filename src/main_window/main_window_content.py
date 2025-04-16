from gi.repository import Adw
from gi.repository import Gtk

@Gtk.Template(resource_path='/com/damiandudycz/CatalystLab/main_window/main_window_content.ui')
class CatalystlabWindowContent(Adw.NavigationPage):
    __gtype_name__ = 'CatalystlabWindowContent'

    # Toggle sidebar visiblity with button.
    @Gtk.Template.Callback()
    def sidebar_toggle_button_clicked(self, button):
        """Callback function that is called when we click the button"""
        print("Toggle sidebar")
        #self.split_view.set_show_sidebar(not self.split_view.get_show_sidebar())


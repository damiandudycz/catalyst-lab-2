# window.py
#
# Copyright 2025 Unknown
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
#
# SPDX-License-Identifier: GPL-3.0-or-later

from gi.repository import Adw
from gi.repository import Gtk
from gi.repository import Gio

@Gtk.Template(resource_path='/com/damiandudycz/CatalystLab/window.ui')
class CatalystlabWindow(Adw.ApplicationWindow):
    __gtype_name__ = 'CatalystlabWindow'

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        # Define the window action
        action_toggle_sidebar = Gio.SimpleAction.new("toggle-sidebar", None)
        action_toggle_sidebar.connect("activate", self.on_toggle_sidebar)
        self.add_action(action_toggle_sidebar)

    split_view = Gtk.Template.Child()

    def on_toggle_sidebar(self, action, parameter):
        # Toggle the collapsed state of the split view
        self.split_view.set_show_sidebar(not self.split_view.get_show_sidebar())


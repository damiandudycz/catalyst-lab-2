from gi.repository import Gtk, Adw, Gio, GLib
from .snapshot_manager import SnapshotManager
from .snapshot import Snapshot
from .repository import Repository
import threading, os

@Gtk.Template(resource_path='/com/damiandudycz/CatalystLab/ui/snapshot_details/snapshot_details_view.ui')
class SnapshotDetailsView(Gtk.Box):
    __gtype_name__ = "SnapshotDetailsView"

    packages_list = Gtk.Template.Child()
    search_entry = Gtk.Template.Child()
    loading_page = Gtk.Template.Child()
    no_results_page = Gtk.Template.Child()

    def __init__(self, snapshot: Snapshot, content_navigation_view: Adw.NavigationView | None = None):
        super().__init__()
        self.snapshot = snapshot
        self.content_navigation_view = content_navigation_view
        threading.Thread(target=self.load_packages).start()
        self.search_entry.connect("search-changed", self.on_search_changed)

    def load_packages(self):
        self.category_rows = []
        for category, packages in self.snapshot.load_ebuilds().items():
            category_row = Adw.ExpanderRow(title=category)
            category_row.category = category          # eq. app-emulation
            category_row.packages = packages          # dict with all packages in this category
            category_row.packages_filtered = packages # Currently filtered packages
            category_row.package_rows_shown = []      # Store currently displayed rows (cleared when collapsed)
            self.category_rows.append(category_row)
            def on_expanded(row, _param):
                if row.get_expanded():
                    for package, versions in row.packages_filtered.items():
                        package_row = Adw.ActionRow(title=package)
                        package_row.package = package # eq. qemu
                        row.add_row(package_row)
                        row.package_rows_shown.append(package_row)
                else:
                    for child in row.package_rows_shown:
                        row.remove(child)
                    row.package_rows_shown.clear()
            category_row.connect("notify::expanded", on_expanded)
        self.category_rows_filtered = self.category_rows
        self.display_rows()
        self.search_entry.set_sensitive(True)
        self.loading_page.set_visible(False)

    def on_search_changed(self, entry):
        # Clear current rows first:
        for row in self.category_rows_filtered:
            self.packages_list.remove(row)
        for row in self.category_rows:
            row.set_expanded(False)
        search_text = entry.get_text().lower()
        if len(search_text) < 3: # Start search from 3 chars
            self.category_rows_filtered = self.category_rows
            for category_row in self.category_rows:
                category_row.set_expanded(False)
                category_row.packages_filtered = category_row.packages
            self.display_rows()
            return
        # Filter results:
        if "/" in search_text:
            search_full_name = True
            search_text_category, search_text_package = search_text.split("/", 1)
        else:
            search_full_name = False
            search_text_category = search_text
            search_text_package = search_text
        # Update category_rows_filtered and category_row.packages_filtered:
        for row in self.category_rows:
            row.packages_filtered = {
                package: versions
                for package, versions in row.packages.items()
                if (
                    not search_full_name and search_text_package in package
                    or search_full_name and search_text in f"{row.category}/{package}"
                )
            }
        filtered_category_rows = []
        expanded_rows = []
        for row in self.category_rows:
            if row.packages_filtered:
                expanded_rows.append(row)
            if search_text_category in row.category and not search_full_name:
                row.packages_filtered = row.packages
            if row.packages_filtered:
                filtered_category_rows.append(row)
        self.category_rows_filtered = filtered_category_rows
        for row in expanded_rows:
            row.set_expanded(True)
        # Display results:
        self.display_rows()

    def display_rows(self):
        for category_row in self.category_rows_filtered:
            self.packages_list.add(category_row)
        self.no_results_page.set_visible(not self.category_rows_filtered)

    @Gtk.Template.Callback()
    def button_delete_clicked(self, sender):
        SnapshotManager.shared().remove_snapshot(self.snapshot)
        if hasattr(self, "_window"):
            self._window.close()
        elif hasattr(self, "content_navigation_view"):
            self.content_navigation_view.pop()

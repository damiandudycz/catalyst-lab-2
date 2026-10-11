from gi.repository import Gtk, Adw, GLib, Gio
from .app_section import app_section
from .cl_toggle_group import CLToggle, CLToggleGroup
from .build_failures import collect_package_failures, bugzilla_url, PackageFailure
from .files_browser_view import FileContentView

# Ways of grouping failures: title, key of group, title of group.
_GROUPINGS = [
    ("Package", lambda failure: failure.atom, lambda failure: failure.atom),
    ("Project", lambda failure: failure.project_directory.id, lambda failure: failure.project_directory.name),
    ("Stage", lambda failure: (failure.project_directory.id, failure.build.stage_name),
     lambda failure: f"{failure.project_directory.name} · {failure.build.stage_name}"),
    ("Date", lambda failure: failure.date.date(), lambda failure: failure.date.strftime("%Y-%m-%d")),
]

@app_section(title="Bugs", icon="bug-svgrepo-com-symbolic", order=7_500)
class BugsSection(Gtk.Box):
    """Packages that failed to build in stages of projects, grouped by package, project, stage or date, with links to
    their bugs in Gentoo Bugzilla and logs of failures."""
    __gtype_name__ = "BugsSection"

    def __init__(self, content_navigation_view: Adw.NavigationView, **kwargs):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, **kwargs)
        self.content_navigation_view = content_navigation_view
        self.failures: list[PackageFailure] = []
        self.groups: list[Gtk.Widget] = []
        scrolled_window = Gtk.ScrolledWindow(hexpand=True, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24, margin_start=24, margin_end=24,
                               margin_bottom=24)
        scrolled_window.set_child(self.content)
        self.append(scrolled_window)
        # Search and grouping.
        controls = Gtk.Box(spacing=12)
        self.search_entry = Gtk.SearchEntry(hexpand=True, placeholder_text="Search packages, projects, stages...")
        self.search_entry.connect("search-changed", lambda entry: self._show_failures())
        controls.append(self.search_entry)
        self.grouping_toggle = CLToggleGroup(valign=Gtk.Align.CENTER)
        self.grouping_toggle.add_css_class("round")
        self.grouping_toggle.add_css_class("caption")
        for title, _, _ in _GROUPINGS:
            self.grouping_toggle.add(CLToggle(label=title))
        self.grouping_toggle.set_active(0)
        self.grouping_toggle.connect("notify::active", lambda group, _: self._show_failures())
        controls.append(self.grouping_toggle)
        self.content.append(controls)
        # Failures are read again when section is shown (builds could fail meanwhile).
        self.connect("map", lambda widget: self.load())
        self.load()

    def load(self):
        self.failures = collect_package_failures()
        self._show_failures()

    def _show_failures(self):
        for group in self.groups:
            self.content.remove(group)
        self.groups = []
        if not self.failures:
            self._add(Adw.StatusPage(icon_name="bug-svgrepo-com-symbolic", title="No failed packages",
                                     description="Packages that fail to build in stages of projects are listed here."))
            return
        text = self.search_entry.get_text().strip().lower()
        failures = [failure for failure in self.failures if not text or text in self._search_text(failure)]
        if not failures:
            self._add(Adw.StatusPage(icon_name="edit-find-symbolic", title="No results",
                                     description="Try a different search term."))
            return
        grouping, group_key, group_title = _GROUPINGS[max(self.grouping_toggle.get_active(), 0)]
        if grouping == "Package":
            # Packages are entries of one list.
            groups = {None: failures}
        else:
            groups = {}
            for failure in failures: # Newest first, so groups are ordered by their newest failure.
                groups.setdefault(group_key(failure), []).append(failure)
        for items in groups.values():
            newest = items[0]
            if grouping == "Package":
                group = Adw.PreferencesGroup(title="Failed packages")
            else:
                group = Adw.PreferencesGroup(title=GLib.markup_escape_text(group_title(newest)),
                                             description=GLib.markup_escape_text(_failed_times(items)))
            # Failures of the same package are one entry, its instances are shown when it's expanded.
            packages: dict = {}
            for failure in items:
                packages.setdefault(failure.atom, []).append(failure)
            for instances in packages.values():
                group.add(self._row(instances[0], grouping, show_package=True) if len(instances) == 1
                          else self._package_row(instances, grouping))
            self._add(group)

    def _package_row(self, instances: list[PackageFailure], grouping: str) -> Adw.ExpanderRow:
        """Package that failed more times, expanded to its failures."""
        newest = instances[0]
        row = Adw.ExpanderRow(title=GLib.markup_escape_text(newest.atom), subtitle=GLib.markup_escape_text(_failed_times(instances)))
        icon = Gtk.Image(icon_name="error-box-svgrepo-com-symbolic", pixel_size=24)
        icon.add_css_class("error")
        row.add_prefix(icon)
        if newest.is_gentoo:
            row.add_suffix(self._bugzilla_button(newest))
        for failure in instances:
            row.add_row(self._row(failure, grouping, show_package=False))
        return row

    def _add(self, widget: Gtk.Widget):
        self.content.append(widget)
        self.groups.append(widget)

    @staticmethod
    def _search_text(failure: PackageFailure) -> str:
        return " ".join(str(value) for value in (failure.package, failure.repository, failure.phase, failure.reason,
                                                 failure.project_directory.name, failure.build.stage_name)).lower()

    def _row(self, failure: PackageFailure, grouping: str, show_package: bool) -> Adw.ActionRow:
        """Failure of package. Rows of package entries show where it failed instead of package. Rows don't repeat what
        title of group shows."""
        location = {"Project": failure.build.stage_name, "Stage": None}.get(
            grouping, f"{failure.project_directory.name} · {failure.build.stage_name}")
        if show_package:
            title = failure.package
            details = [] if failure.is_gentoo else [f"::{failure.repository}"]
            if location:
                details.append(location)
        else:
            title = location or failure.date.strftime("%Y-%m-%d %H:%M")
            details = [failure.version]
        if failure.phase:
            details.append(f"{failure.phase} phase")
        if title != failure.date.strftime("%Y-%m-%d %H:%M"):
            details.append(failure.date.strftime("%Y-%m-%d %H:%M"))
        subtitle = " · ".join(details)
        if failure.reason:
            subtitle += f"\n{failure.reason}"
        row = Adw.ActionRow(title=GLib.markup_escape_text(title), subtitle=GLib.markup_escape_text(subtitle))
        icon = Gtk.Image(icon_name="error-box-svgrepo-com-symbolic", pixel_size=24)
        icon.add_css_class("error")
        row.add_prefix(icon)
        if show_package and failure.is_gentoo:
            row.add_suffix(self._bugzilla_button(failure))
        if directory := failure.failure_directory or failure.build.path:
            button = Gtk.Button(icon_name="folder-open-symbolic", tooltip_text="Open logs of failure", valign=Gtk.Align.CENTER)
            button.add_css_class("flat")
            button.connect("clicked", lambda _, path=directory: Gtk.FileLauncher.new(Gio.File.new_for_path(path)).launch(self.get_root(), None, None))
            row.add_suffix(button)
        if log_path := failure.log_path:
            row.set_activatable(True)
            row.set_tooltip_text("Show summary of failure")
            row.connect("activated", lambda _, path=log_path: self._show_log(path, failure))
            arrow = Gtk.Image(icon_name="go-next-symbolic")
            arrow.add_css_class("dimmed")
            row.add_suffix(arrow)
        return row

    def _bugzilla_button(self, failure: PackageFailure) -> Gtk.Button:
        button = Gtk.Button(icon_name="bug-svgrepo-com-symbolic", tooltip_text=f"Bugs of {failure.atom} in Gentoo Bugzilla",
                            valign=Gtk.Align.CENTER)
        button.add_css_class("flat")
        button.connect("clicked", lambda _: Gtk.UriLauncher.new(bugzilla_url(failure.atom)).launch(self.get_root(), None, None))
        return button

    def _show_log(self, path: str, failure: PackageFailure):
        if self.content_navigation_view is not None:
            self.content_navigation_view.push_view(FileContentView(path), title=failure.package)

def _failed_times(failures: list[PackageFailure]) -> str:
    """Count of failures and date of newest (failures are newest first)."""
    return f"Failed {len(failures)} time{'s' if len(failures) != 1 else ''}, last {failures[0].date.strftime('%Y-%m-%d %H:%M')}"

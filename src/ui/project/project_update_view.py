from __future__ import annotations
import threading
from gi.repository import Gtk, GLib, Adw
from .cl_toggle_group import CLToggle, CLToggleGroup
from .project_template_update import (
    prepare_template_update, prepare_repository_update, describe_value, TemplateChange, TemplateState
)
from .project_template import fetch_template_repository, ProjectTemplate
from .project_template_views import ProjectTemplateOptionsView

class ProjectUpdateView(Gtk.Box):
    """Updates project from its template or Git repository: downloads latest version, lists changes and lets user
    decide about changes made also in project (keep project version or use new one)."""

    def __init__(self, project_directory, source: str, overlays: list | None = None):
        """Source is "template" or "repository". Given overlays are updated first."""
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.project_directory = project_directory
        self.source = source
        self.overlays = overlays or []
        self.overlay_errors: list[str] = []
        self.source_name = "template" if source == "template" else "repository"
        self.update = None
        self._window = None
        scrolled_window = Gtk.ScrolledWindow(hexpand=True, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24, margin_start=24, margin_end=24,
                               margin_top=12, margin_bottom=24)
        scrolled_window.set_child(self.content)
        self.append(scrolled_window)
        self.selected = None # Options of template chosen for update.
        if source == "template":
            # Options of latest template, with values selected before.
            self._show_loading("Downloading options of latest template…")
            threading.Thread(target=self._load_options, daemon=True).start()
        else:
            self._start_update()

    def _start_update(self):
        self._show_loading()
        threading.Thread(target=self._prepare, daemon=True).start()

    # Options of template:

    def _load_options(self):
        state = TemplateState.load(self.project_directory.directory_path())
        try:
            if state is None:
                raise RuntimeError("Project wasn't created from template")
            templates = fetch_template_repository(state.repository_url)
            template = next((item for item in templates if isinstance(item, ProjectTemplate)
                             and item.repository_path == state.repository_path), None)
            if template is None:
                error = next((item[1] for item in templates if not isinstance(item, ProjectTemplate) and item[0] == state.repository_path), None)
                raise RuntimeError(str(error) if error else f"Template {state.template_name} is not in repository anymore")
            GLib.idle_add(self._show_options, template, state.selected, None)
        except Exception as e:
            GLib.idle_add(self._show_options, None, None, e)

    def _show_options(self, template, selected, error):
        if error is not None:
            self._show_message("dialog-error-symbolic", "Update failed", str(error))
            return False
        options = ProjectTemplateOptionsView()
        options.set_template(template, selected)
        description = self._label("Options of the latest version of template, with values selected for project. "
                                  "Changed options change generated stages and files.")
        continue_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        continue_list.add_css_class("boxed-list")
        continue_row = Adw.ButtonRow(title="Continue", end_icon_name="go-next-symbolic")
        continue_row.add_css_class("suggested-action")
        def on_continue(row):
            self.selected = dict(options.selected)
            self._start_update()
        continue_row.connect("activated", on_continue)
        continue_list.append(continue_row)
        def on_changed():
            continue_row.set_sensitive(options.generated is not None)
        options.on_changed = on_changed
        on_changed()
        self._set_content([description, options, continue_list])
        return False

    # Preparing:

    def _show_loading(self, text: str | None = None):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, valign=Gtk.Align.CENTER, vexpand=True)
        box.append(Adw.Spinner(width_request=32, height_request=32))
        self.loading_label = Gtk.Label(label=text or f"Downloading latest version of {self.source_name}…", wrap=True)
        self.loading_label.add_css_class("dimmed")
        box.append(self.loading_label)
        self._set_content([box])

    def _set_content(self, widgets: list[Gtk.Widget]):
        while child := self.content.get_first_child():
            self.content.remove(child)
        for widget in widgets:
            self.content.append(widget)

    def _prepare(self):
        def log(line: str):
            print(line)
            GLib.idle_add(self.loading_label.set_label, line)
        from .git_update import update_git_directory
        for overlay in self.overlays:
            log(f"Updating overlay {overlay.name}")
            try:
                update_git_directory(overlay, log)
            except Exception as e:
                # Project is still updated, failure is shown with changes.
                self.overlay_errors.append(f"{overlay.name}: {e}")
        try:
            if self.source == "template":
                update = prepare_template_update(self.project_directory, log, selected=self.selected)
            else:
                update = prepare_repository_update(self.project_directory, log)
            GLib.idle_add(self._show_update, update, None)
        except Exception as e:
            GLib.idle_add(self._show_update, None, e)

    # Changes:

    def _show_update(self, update, error):
        self.update = update
        if error is not None:
            self._show_message("dialog-error-symbolic", "Update failed", str(error))
            return False
        if update is None:
            if self.overlay_errors:
                self._show_message("dialog-error-symbolic", "Overlays were not updated", "\n".join(self.overlay_errors))
                return False
            self._show_message("check-square-svgrepo-com-symbolic", "Project is up to date",
                               f"There are no changes in {self.source_name} to apply.")
            return False
        automatic = [change for change in update.changes if not change.conflict]
        conflicts = [change for change in update.changes if change.conflict]
        widgets = []
        if self.overlay_errors:
            widgets.append(self._label("Overlays were not updated: " + "; ".join(self.overlay_errors)))
        if update.fast_forward:
            widgets.append(self._label(f"Project has no own commits, it's moved to the latest version of {self.source_name}."))
        else:
            widgets.append(self._label(f"Own commits of project are replayed on top of the latest version of "
                                       f"{self.source_name}, with versions chosen below."))
        if conflicts:
            group = Adw.PreferencesGroup(
                title=f"Changed in project and in {self.source_name}",
                description=f"Choose version to use for every change.")
            for change in conflicts:
                group.add(self._conflict_row(change))
            widgets.append(group)
        if automatic:
            group = Adw.PreferencesGroup(
                title=f"Changes from {self.source_name}",
                description="Not changed in project, they are applied.")
            for change in automatic:
                subtitle = (f"{len(change.parts)} settings and files" if change.parts
                            else f"{describe_value(change.base)} → {describe_value(change.template)}")
                row = Adw.ActionRow(title=GLib.markup_escape_text(change.title), subtitle=GLib.markup_escape_text(subtitle))
                row.set_subtitle_lines(3)
                group.add(row)
            widgets.append(group)
        if not update.changes:
            widgets.append(self._label(f"Files of project don't change, only version of {self.source_name} is recorded."))
        apply_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        apply_list.add_css_class("boxed-list")
        apply_row = Adw.ButtonRow(title="Apply update", start_icon_name="check-square-svgrepo-com-symbolic")
        apply_row.add_css_class("suggested-action")
        apply_row.connect("activated", self._on_apply)
        apply_list.append(apply_row)
        widgets.append(apply_list)
        self._set_content([widget for widget in widgets if widget is not None])
        return False

    def _conflict_row(self, change: TemplateChange) -> Adw.ActionRow:
        row = Adw.ActionRow(title=GLib.markup_escape_text(change.title), subtitle=GLib.markup_escape_text(
            f"Project: {describe_value(change.project)}\n{self.source_name.capitalize()}: {describe_value(change.template)}"))
        row.set_subtitle_lines(4)
        toggle_group = CLToggleGroup(valign=Gtk.Align.CENTER)
        toggle_group.add_css_class("round")
        toggle_group.add_css_class("caption")
        toggle_group.add(CLToggle(label="Keep project"))
        toggle_group.add(CLToggle(label=f"Use {self.source_name}"))
        toggle_group.set_active(1 if change.use_template else 0)
        def on_toggled(group, _):
            change.use_template = group.get_active() == 1
        toggle_group.connect("notify::active", on_toggled)
        row.add_suffix(toggle_group)
        return row

    def _label(self, text: str) -> Gtk.Label | None:
        if not text:
            return None
        label = Gtk.Label(label=text, wrap=True, xalign=0)
        label.add_css_class("dimmed")
        return label

    def _show_message(self, icon_name: str, title: str, description: str):
        status = Adw.StatusPage(icon_name=icon_name, title=GLib.markup_escape_text(title),
                                description=GLib.markup_escape_text(description), vexpand=True)
        status.add_css_class("compact")
        self._set_content([status])

    def _on_apply(self, row):
        row.set_sensitive(False)
        try:
            self.update.apply(self.project_directory, log=print)
        except Exception as e:
            self._show_message("dialog-error-symbolic", "Update failed", str(e))
            return
        finally:
            self.update.cleanup()
        if self._window:
            self._window.close()

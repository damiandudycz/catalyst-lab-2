from __future__ import annotations
import threading
from typing import Any, Callable
from gi.repository import Gtk, GLib, Adw
from .project_template import (
    ProjectTemplate, TemplateError, TemplateVariableType, GeneratedProject,
    local_templates, template_repositories, fetch_template_repository
)
from .project_stage import stage_target_icon

class ProjectTemplateChooser(Gtk.Box):
    """List of templates included in app and templates from Git repositories. Repository templates are downloaded
    when selected, to read their options."""

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.selected_template: ProjectTemplate | None = None
        self.on_changed: Callable[[], None] | None = None
        self._fetch_generation = 0 # Results of earlier downloads are ignored when other template was selected.
        group = Adw.PreferencesGroup(title="Template")
        group.set_margin_bottom(12)
        self.append(group)
        check_group = Gtk.CheckButton() # Not displayed, rows start without selection.
        for template in local_templates():
            if isinstance(template, ProjectTemplate):
                row = Adw.ActionRow(title=GLib.markup_escape_text(template.name), subtitle=GLib.markup_escape_text(template.description))
                self._add_check(row, check_group, lambda row, template=template: self._select(template))
            else:
                path, error = template
                row = Adw.ActionRow(title=GLib.markup_escape_text(path.rstrip("/").split("/")[-1]), subtitle=GLib.markup_escape_text(str(error)))
                row.add_css_class("error")
                row.set_sensitive(False)
            group.add(row)
        for repository in template_repositories():
            row = Adw.ActionRow(title=GLib.markup_escape_text(repository.title), subtitle=GLib.markup_escape_text(repository.url))
            self._add_check(row, check_group, lambda row, repository=repository: self._fetch(row, repository))
            group.add(row)

    def _add_check(self, row: Adw.ActionRow, check_group: Gtk.CheckButton, on_selected: Callable):
        check_button = Gtk.CheckButton()
        check_button.set_group(check_group)
        check_button.connect("toggled", lambda button: on_selected(row) if button.get_active() else None)
        row.add_prefix(check_button)
        row.set_activatable_widget(check_button)

    def _select(self, template: ProjectTemplate | None):
        self._fetch_generation += 1
        self._set_selected(template)

    def _set_selected(self, template: ProjectTemplate | None):
        self.selected_template = template
        if self.on_changed:
            self.on_changed()

    def _fetch(self, row: Adw.ActionRow, repository):
        self._fetch_generation += 1
        generation = self._fetch_generation
        self._set_selected(None)
        spinner = Adw.Spinner()
        row.add_suffix(spinner)
        row.set_subtitle("Downloading template…")
        row.remove_css_class("error")
        def finish(template: ProjectTemplate | None, error: str | None):
            row.remove(spinner)
            if error:
                row.set_subtitle(GLib.markup_escape_text(f"{repository.url}\n{error}"))
                row.add_css_class("error")
            else:
                row.set_title(GLib.markup_escape_text(template.name))
                row.set_subtitle(GLib.markup_escape_text(f"{template.description}\n{repository.url}" if template.description else repository.url))
            if generation == self._fetch_generation:
                self._set_selected(template)
            return False
        def fetch():
            try:
                template, error = fetch_template_repository(repository.url), None
            except Exception as e: # Also errors of git and files.
                template, error = None, str(e)
            GLib.idle_add(finish, template, error)
        threading.Thread(target=fetch, daemon=True).start()

class ProjectTemplateOptionsView(Gtk.Box):
    """Options (variables) of template and preview of stages created for them."""

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.template: ProjectTemplate | None = None
        self.selected: dict[str, Any] = {}
        self.generated: GeneratedProject | None = None
        self.on_changed: Callable[[], None] | None = None
        self._rebuild_scheduled = False
        self.options_group = Adw.PreferencesGroup(title="Options")
        self.stages_group = Adw.PreferencesGroup(title="Stages", description="Stages created in project for selected options. Their settings can be changed later.")
        self.error_label = Gtk.Label(wrap=True, xalign=0)
        self.error_label.add_css_class("error")
        self.append(self.options_group)
        self.append(self.error_label)
        self.append(self.stages_group)
        self._option_rows: list[Gtk.Widget] = []
        self._stage_rows: list[Gtk.Widget] = []
        self._rebuild()

    def set_template(self, template: ProjectTemplate | None):
        if template is self.template:
            return
        self.template = template
        self.selected = {}
        self._rebuild()

    def _schedule_rebuild(self):
        # Rows are rebuilt after signal handler of changed row returns.
        if not self._rebuild_scheduled:
            self._rebuild_scheduled = True
            GLib.idle_add(self._rebuild)

    def _rebuild(self):
        self._rebuild_scheduled = False
        for row in self._option_rows:
            self.options_group.remove(row)
        for row in self._stage_rows:
            self.stages_group.remove(row)
        self._option_rows, self._stage_rows = [], []
        self.generated = None
        error = None
        if self.template is not None:
            try:
                names = self.template.resolve(self.selected)
                # Values used, also for options that were not available and changed to default.
                self.selected = {variable.id: names[variable.id] for variable in self.template.variables}
                for variable in self.template.visible_variables(names):
                    self._add_variable_row(variable, names)
                self.generated = self.template.generate(names)
                for stage in self.generated.stages:
                    details = [stage.target.replace("_", "-")]
                    if stage.releng_template:
                        details.append(stage.releng_template)
                    row = Adw.ActionRow(title=GLib.markup_escape_text(stage.name), subtitle=GLib.markup_escape_text(" · ".join(details)))
                    row.add_prefix(Gtk.Image.new_from_icon_name(stage_target_icon(stage.target)))
                    self.stages_group.add(row)
                    self._stage_rows.append(row)
            except TemplateError as e:
                error = str(e)
        self.options_group.set_visible(bool(self._option_rows))
        self.stages_group.set_visible(self.generated is not None and bool(self.generated.stages))
        self.error_label.set_label(f"Template can't be used: {error}" if error else "")
        self.error_label.set_visible(error is not None)
        if self.on_changed:
            self.on_changed()
        return False

    def _add_variable_row(self, variable, names: dict[str, Any]):
        if variable.type == TemplateVariableType.BOOLEAN:
            row = Adw.SwitchRow(title=GLib.markup_escape_text(variable.title))
            row.set_active(bool(names[variable.id]))
            row.connect("notify::active", lambda row, _: self._set_value(variable.id, row.get_active()))
        else:
            options = variable.available_options(names)
            row = Adw.ComboRow(title=GLib.markup_escape_text(variable.title))
            row.set_model(Gtk.StringList.new([option.title for option in options]))
            values = [option.value for option in options]
            row.set_selected(values.index(names[variable.id]))
            row.connect("notify::selected", lambda row, _: self._set_value(variable.id, values[row.get_selected()]))
        if variable.description:
            row.set_subtitle(GLib.markup_escape_text(variable.description))
        self.options_group.add(row)
        self._option_rows.append(row)

    def _set_value(self, variable_id: str, value):
        if self.selected.get(variable_id) == value:
            return
        self.selected[variable_id] = value
        self._schedule_rebuild()

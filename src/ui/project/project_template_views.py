from __future__ import annotations
import threading
from typing import Any, Callable
from gi.repository import Gtk, GLib, Adw
from .project_template import (
    ProjectTemplate, TemplateError, TemplateVariableType, GeneratedProject, _GROUP_STAGES_NAME,
    local_templates, download_template_repositories, fetch_template_repository
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
        # Repositories: latest list is always downloaded, so templates removed from it are not offered anymore.
        self._group = group
        self._check_group = check_group
        self._repository_rows: list[Gtk.Widget] = []
        self._load_repositories()

    def _load_repositories(self):
        for row in self._repository_rows:
            self._group.remove(row)
        loading_row = Adw.ActionRow(title="Loading templates from repositories…")
        loading_row.add_css_class("dimmed")
        loading_row.add_prefix(Adw.Spinner())
        self._group.add(loading_row)
        self._repository_rows = [loading_row]
        def download():
            try:
                repositories, error = download_template_repositories(), None
            except Exception as e:
                repositories, error = None, e
            GLib.idle_add(self._show_repositories, repositories, error)
        threading.Thread(target=download, daemon=True).start()

    def _show_repositories(self, repositories, error):
        for row in self._repository_rows:
            self._group.remove(row)
        self._repository_rows = []
        if repositories is None:
            print(f"Failed to download list of template repositories: {error}")
            row = Adw.ActionRow(title="Templates from repositories couldn't be loaded",
                                subtitle="Check internet connection and try again.")
            row.add_prefix(Gtk.Image.new_from_icon_name("danger-triangle-svgrepo-com-symbolic"))
            retry_button = Gtk.Button(label="Try again", valign=Gtk.Align.CENTER)
            retry_button.add_css_class("flat")
            retry_button.connect("clicked", lambda button: self._load_repositories())
            row.add_suffix(retry_button)
            self._group.add(row)
            self._repository_rows.append(row)
            return False
        for repository in repositories:
            row = Adw.ActionRow(title=GLib.markup_escape_text(repository.title), subtitle=GLib.markup_escape_text(repository.url))
            self._add_check(row, self._check_group, lambda row, repository=repository: self._fetch(row, repository))
            self._group.add(row)
            self._repository_rows.append(row)
        return False

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
        self.groups_group = Adw.PreferencesGroup(description="Enabled configuration is added to selected stages.")
        self.stages_group = Adw.PreferencesGroup(title="Stages", description="Stages created in project for selected options. Their settings can be changed later.")
        self.error_label = Gtk.Label(wrap=True, xalign=0)
        self.error_label.add_css_class("error")
        self.append(self.options_group)
        self.append(self.groups_group)
        self.append(self.error_label)
        self.append(self.stages_group)
        self._option_rows: list[Gtk.Widget] = []
        self._group_rows: list[Gtk.Widget] = []
        self._stage_rows: list[Gtk.Widget] = []
        self._expanded_rows: set[str] = set() # Expander rows kept expanded when rows are rebuilt.
        self._rebuild()

    def set_template(self, template: ProjectTemplate | None):
        if template is self.template:
            return
        self.template = template
        self.selected = {}
        self._expanded_rows = set()
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
        for row in self._group_rows:
            self.groups_group.remove(row)
        for row in self._stage_rows:
            self.stages_group.remove(row)
        self._option_rows, self._group_rows, self._stage_rows = [], [], []
        self.generated = None
        error = None
        if self.template is not None:
            try:
                names = self.template.resolve(self.selected)
                # Values used, also for options that were not available and changed to default. Selections of groups
                # are kept, also of groups not available now.
                self.selected = {key: value for key, value in self.selected.items() if key.startswith("group:")} | {
                    variable.id: names[variable.id] for variable in self.template.variables}
                for variable in self.template.visible_variables(names):
                    self._add_variable_row(variable, names)
                self.generated = self.template.generate(names)
                for group in self.template.available_groups(names):
                    self._add_group_row(group, names)
                for stage in self.generated.stages:
                    details = [stage.target.replace("_", "-")]
                    if stage.releng_template:
                        details.append(stage.releng_template)
                    if stage.groups:
                        details.append("+ " + ", ".join(stage.groups))
                    row = Adw.ActionRow(title=GLib.markup_escape_text(stage.name), subtitle=GLib.markup_escape_text(" · ".join(details)))
                    row.add_prefix(Gtk.Image.new_from_icon_name(stage_target_icon(stage.target)))
                    self.stages_group.add(row)
                    self._stage_rows.append(row)
            except TemplateError as e:
                error = str(e)
        self.options_group.set_visible(bool(self._option_rows))
        self.groups_group.set_title(GLib.markup_escape_text(self.template.groups_title) if self.template else "")
        self.groups_group.set_visible(bool(self._group_rows))
        self.stages_group.set_visible(self.generated is not None and bool(self.generated.stages))
        self.error_label.set_label(f"Template can't be used: {error}" if error else "")
        self.error_label.set_visible(error is not None)
        if self.on_changed:
            self.on_changed()
        return False

    def _expander_row(self, key: str, title: str, subtitle: str | None) -> Adw.ExpanderRow:
        row = Adw.ExpanderRow(title=GLib.markup_escape_text(title))
        if subtitle:
            row.set_subtitle(GLib.markup_escape_text(subtitle))
        row.set_expanded(key in self._expanded_rows)
        def on_expanded(row, _):
            if row.get_expanded():
                self._expanded_rows.add(key)
            else:
                self._expanded_rows.discard(key)
        row.connect("notify::expanded", on_expanded)
        return row

    def _add_group_row(self, group, names: dict[str, Any]):
        """Group enabled with switch of expander row, with stages it's applied to inside."""
        key = f"group:{group.id}"
        enabled = group.id in names["groups"]
        selected_stages = names[_GROUP_STAGES_NAME].get(group.id, group.default_stages)
        row = self._expander_row(key, group.title, group.description)
        row.set_show_enable_switch(True)
        row.set_enable_expansion(enabled)
        row.connect("notify::enable-expansion", lambda row, _: self._set_group(group.id, enabled=row.get_enable_expansion()))
        generated = {stage.template_id: stage for stage in self.generated.stages}
        for stage_id in group.stages:
            stage = generated.get(stage_id)
            if stage is None:
                continue # Not created for selected options.
            stage_row = Adw.ActionRow(title=GLib.markup_escape_text(stage.name))
            check_button = Gtk.CheckButton(active=stage_id in selected_stages)
            check_button.connect("toggled", lambda button, stage_id=stage_id: self._set_group_stage(group.id, stage_id, button.get_active()))
            stage_row.add_prefix(check_button)
            stage_row.set_activatable_widget(check_button)
            row.add_row(stage_row)
        self.groups_group.add(row)
        self._group_rows.append(row)

    def _group_selection(self, group_id: str) -> dict:
        group = next(group for group in self.template.groups if group.id == group_id)
        selection = self.selected.get(f"group:{group_id}") or {}
        return {"enabled": selection.get("enabled", group.default), "stages": list(selection.get("stages", group.default_stages))}

    def _set_group(self, group_id: str, enabled: bool):
        selection = self._group_selection(group_id)
        if selection["enabled"] == enabled:
            return
        selection["enabled"] = enabled
        self.selected[f"group:{group_id}"] = selection
        self._schedule_rebuild()

    def _set_group_stage(self, group_id: str, stage_id: str, selected: bool):
        selection = self._group_selection(group_id)
        stages = [item for item in selection["stages"] if item != stage_id] + ([stage_id] if selected else [])
        selection["stages"] = stages
        self.selected[f"group:{group_id}"] = selection
        self._schedule_rebuild()

    def _add_variable_row(self, variable, names: dict[str, Any]):
        if variable.type == TemplateVariableType.MULTIPLE:
            # Expander with switch for every available option.
            options = variable.available_options(names)
            selected = list(names[variable.id])
            row = self._expander_row(variable.id, variable.title, variable.description)
            for option in options:
                option_row = Adw.SwitchRow(title=GLib.markup_escape_text(option.title), active=option.value in selected)
                def on_toggled(option_row, _, value=option.value):
                    current = [item for item in (self.selected.get(variable.id) or []) if item != value]
                    self._set_value(variable.id, current + [value] if option_row.get_active() else current)
                option_row.connect("notify::active", on_toggled)
                row.add_row(option_row)
            self.options_group.add(row)
            self._option_rows.append(row)
            return
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

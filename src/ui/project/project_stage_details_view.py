import threading, uuid
from gi.repository import Gtk, Gdk, Adw, GLib
from dataclasses import dataclass
from .project_directory import ProjectDirectory
from .project_stage import (
    ProjectStage, load_catalyst_targets, load_releng_templates,
    load_catalyst_stage_arguments_options, load_catalyst_stage_arguments_options_for_boolean,
    load_catalyst_stage_arguments_details, load_catalyst_stage_automatic_arguments_options,
    ProjectStageEvent, ProjectStage
)
from .project_stage_arguments import (
    StageArguments, StageArgumentTargetDetails, StageArgumentOption,
    StageArgumentType, StageArgumentDetails
)
from .project_manager import ProjectManager
from .git_directory import GitDirectoryEvent
from .project_stage import ProjectStageEvent
from .repository_list_view import ItemRow
from .architecture import Architecture
from .item_select_view import ItemSelectionViewEvent
from .project_stage import ProjectStage, StageArgumentOption
from .item_select_expander_row import ItemSelectionExpanderRow
from .event_bus import EventBus
from .project_stage_automatic_option import StageAutomaticOption

@Gtk.Template(resource_path='/com/damiandudycz/CatalystLab/ui/project/project_stage_details_view.ui')
class ProjectStageDetailsView(Gtk.Box):
    __gtype_name__ = "ProjectStageDetailsView"

    stage_name_row = Gtk.Template.Child()
    name_used_row = Gtk.Template.Child()
    basic_config_pref_group = Gtk.Template.Child()
    architecture_pref_group = Gtk.Template.Child()
    release_pref_group = Gtk.Template.Child()
    packages_pref_group = Gtk.Template.Child()
    configuration_pref_group = Gtk.Template.Child()

    def __init__(self, project_directory: ProjectDirectory, stage: ProjectStage, content_navigation_view: Adw.NavigationView | None = None):
        super().__init__()
        self.project_directory = project_directory
        self.stage = stage
        self.content_navigation_view = content_navigation_view
        self.connect("realize", self.on_realize)

    def on_realize(self, widget):
        self.get_root().set_focus(None)
        self.load_stage_details()
        self.load_configuration_rows()
        self.monitor_information_changes()

    # Loading stage data
    # --------------------------------------------------------------------------

    def load_stage_details(self):
        self.stage_name_row.set_text(self.stage.name)

    def load_configuration_rows(self):
        # Reset configuration rows
        if hasattr(self, 'configuration_rows'):
            for row in self.configuration_rows:
                row.pref_group.remove(row)
        self.configuration_rows = []
        # Load arguments rows
        arguments_details = load_catalyst_stage_arguments_details(
            toolset=self.project_directory.get_toolset(),
            target_name=self.stage.target
        )
        for name, arg in arguments_details.items():
            group = self.pref_group_for_argument(argument=arg)
            if group:
                row = self.create_row_for_argument(argument=arg)
                row.pref_group = group
                row.event_bus.subscribe(
                    ItemSelectionViewEvent.ITEM_CHANGED,
                    self.argument_changed,
                    self
                )
                row.pref_group.add(row)
                self.configuration_rows.append(row)

    def create_row_for_argument(self, argument: StageArgumentTargetDetails) -> Adw.PreferencesRow:
        match argument.type:
            case StageArgumentType.select | StageArgumentType.multiselect | StageArgumentType.boolean:
                return StageOptionExpanderRow(
                    project_directory=self.project_directory,
                    stage=self.stage,
                    argument=argument,
                    is_item_selectable_handler=self.is_config_option_selectable
                )
            case StageArgumentType.string_list | StageArgumentType.raw:
                return StageTextListRow(stage=self.stage, argument=argument)
            case _:
                return StageTextEntryRow(stage=self.stage, argument=argument)

    def can_change_argument(self, option: StageArgumentOption) -> bool:
        match option.argument:
            case (
                StageArgumentDetails.target
            ):
                return False
            case _:
                return True

    def pref_group_for_argument(self, argument: StageArgumentTargetDetails) -> Adw.PreferencesGroup | None:
        if not argument.details:
            return self.configuration_pref_group
        match argument.details:
            case (
                StageArgumentDetails.name |
                StageArgumentDetails.snapshot_treeish
            ):
                return None
            case (
                StageArgumentDetails.parent |
                StageArgumentDetails.profile |
                StageArgumentDetails.target |
                StageArgumentDetails.releng_template
            ):
                return self.basic_config_pref_group
            case (
                StageArgumentDetails.subarch |
                StageArgumentDetails.asflags |
                StageArgumentDetails.cbuild |
                StageArgumentDetails.cflags |
                StageArgumentDetails.chost |
                StageArgumentDetails.common_flags |
                StageArgumentDetails.cxxflags |
                StageArgumentDetails.fcflags |
                StageArgumentDetails.fflags |
                StageArgumentDetails.ldflags |
                StageArgumentDetails.interpreter
            ):
                return self.architecture_pref_group
            case (
                StageArgumentDetails.rel_type |
                StageArgumentDetails.version_stamp |
                StageArgumentDetails.compression_mode
            ):
                return self.release_pref_group
            case (
                StageArgumentDetails.repos |
                StageArgumentDetails.keep_repos |
                StageArgumentDetails.binrepo_path |
                StageArgumentDetails.pkgcache_path |
                StageArgumentDetails.kerncache_path |
                StageArgumentDetails.snapshot_treeish
            ):
                return self.packages_pref_group
            case _:
                return self.configuration_pref_group

    def is_config_option_selectable(self, row, option):
        return self.can_change_argument(option=option)

    def argument_changed(self, row):
        if row.argument.type in (StageArgumentType.raw_single_line, StageArgumentType.string_list, StageArgumentType.raw):
            ProjectManager.shared().change_stage_argument(
                project=self.project_directory,
                stage=self.stage,
                argument=row.argument.details or row.argument.name,
                value=row.value
            )
            # Text values don't affect other options, and reloading would discard unapplied edits.
            return
        if row.argument.details.type == StageArgumentType.select:
            ProjectManager.shared().change_stage_argument(
                project=self.project_directory,
                stage=self.stage,
                argument=row.argument.details,
                value=row.selected_item.value if row.selected_item else None
            )
        if row.argument.details.type == StageArgumentType.multiselect:
            ProjectManager.shared().change_stage_argument(
                project=self.project_directory,
                stage=self.stage,
                argument=row.argument.details,
                value=[item.value for item in row.selected_items] if row.selected_items else None
            )
        if row.argument.details.type == StageArgumentType.boolean:
            ProjectManager.shared().change_stage_argument(
                project=self.project_directory,
                stage=self.stage,
                argument=row.argument.details,
                value=row.selected_item.value if row.selected_item is not None else None
            )
        # Reload other option rows (text rows keep their own, possibly unapplied, content)
        for r in self.configuration_rows:
            if r.argument != row.argument and isinstance(r, StageOptionExpanderRow):
                r.load_state()

    # Monitoring stage changes
    # --------------------------------------------------------------------------

    def monitor_information_changes(self):
        """React to changes in UI and store them."""
        subscriptions = [
            (self.stage.event_bus, ProjectStageEvent.NAME_CHANGED, self.on_name_changed)
        ]
        for bus, event, handler in subscriptions:
            bus.subscribe(event, handler)

    def on_name_changed(self, data):
        self._page.set_title(self.stage.name)

    # Handle UI
    # --------------------------------------------------------------------------

    @Gtk.Template.Callback()
    def on_stage_name_activate(self, sender):
        new_name = self.stage_name_row.get_text()
        if new_name == self.stage.name:
            self.get_root().set_focus(None)
            return
        is_name_available = ProjectManager.shared().is_stage_name_available(
            project=self.project_directory,
            name=self.stage_name_row.get_text()
        ) or self.stage_name_row.get_text() == self.stage.name
        try:
            if not is_name_available:
                raise RuntimeError(f"Stage name {new_name} is not available")
            ProjectManager.shared().rename_stage(project=self.project_directory, stage=self.stage, name=new_name)
            self.get_root().set_focus(None)
            self.load_stage_details()
        except Exception as e:
            print(f"Error renaming stage: {e}")
            self.stage_name_row.add_css_class("error")
            self.stage_name_row.grab_focus()

    @Gtk.Template.Callback()
    def on_stage_name_changed(self, sender):
        is_name_available = ProjectManager.shared().is_stage_name_available(
            project=self.project_directory,
            name=self.stage_name_row.get_text()
        ) or self.stage_name_row.get_text() == self.stage.name
        self.name_used_row.set_visible(not is_name_available)
        self.stage_name_row.remove_css_class("error")

    # Helper functions
    # --------------------------------------------------------------------------

#    def pref_group_for_option(self, option) -> Adw.PreferencesGroup:

class StageOptionExpanderRow(ItemSelectionExpanderRow):

    def __init__(self, project_directory: ProjectDirectory, stage: ProjectStage, argument: StageArgumentTargetDetails, is_item_selectable_handler):
        super().__init__()
        self.title = argument.display_name
        self.item_title_property_name = 'display'
        self.item_subtitle_property_name = 'subtitle'
        self.item_unsupported_property_name = 'unsupported'
        self.argument = argument
        self.project_directory = project_directory
        self.stage = stage
        self.display_none = not argument.required
        self.allow_multiselect = argument.details.type == StageArgumentType.multiselect if argument.details else False
        self.connect("is-item-selectable", is_item_selectable_handler)
        self.load_state()

    def load_state(self):
        if self.argument.details and self.argument.details.type == StageArgumentType.select:
            current_value = getattr(self.stage, self.argument.details.name, None) # Mapped to object
            automatic_options = load_catalyst_stage_automatic_arguments_options(stage=self.stage, arg_details=self.argument)
            options = automatic_options + (load_catalyst_stage_arguments_options(project_directory=self.project_directory, stage=self.stage, arg_details=self.argument) or [])
            # Add entries for unsupported values
            option_values = {opt.value for opt in options}
            missing_values = [val for val in [current_value] if val not in option_values and val is not None]
            unsupported_options = self.create_unsupported_options(missing_values=missing_values, argument=self.argument.details)
            options = unsupported_options + options
            self.selected_item = next((item for item in options if item.value == current_value), None)
            self.set_static_list(list=options)
        if self.argument.details and self.argument.details.type == StageArgumentType.multiselect:
            current_values = getattr(self.stage, self.argument.details.name, []) # Mapped to object
            automatic_options = load_catalyst_stage_automatic_arguments_options(stage=self.stage, arg_details=self.argument)
            options = automatic_options + (load_catalyst_stage_arguments_options(project_directory=self.project_directory, stage=self.stage, arg_details=self.argument) or [])
            # Add entries for unsupported values
            option_values = {opt.value for opt in options}
            missing_values = [val for val in current_values if val not in option_values and val is not None]
            unsupported_options = self.create_unsupported_options(missing_values=missing_values, argument=self.argument.details)
            options = unsupported_options + options
            self.selected_items = [option for option in options if option.value in current_values] if current_values else []
            self.set_static_list(list=options)
        if self.argument.details and self.argument.details.type == StageArgumentType.boolean:
            current_value = getattr(self.stage, self.argument.details.name, None) # Mapped to object
            automatic_options = load_catalyst_stage_automatic_arguments_options(stage=self.stage, arg_details=self.argument)
            options = automatic_options + load_catalyst_stage_arguments_options_for_boolean(arg_details=self.argument)
            # Add entries for unsupported values
            option_values = {opt.value for opt in options}
            missing_values = [val for val in [current_value] if val not in option_values and val is not None]
            unsupported_options = self.create_unsupported_options(missing_values=missing_values, argument=self.argument.details)
            options = unsupported_options + options
            self.selected_item = next((item for item in options if item.value == current_value), None)
            self.set_static_list(list=options)

    def create_unsupported_options(self, missing_values: list, argument: StageArgumentDetails) -> list[StageArgumentOption]:
        """Creates dummy entries for options that are currently set but not available in available options."""
        if not missing_values:
            return []
        return [
            StageArgumentOption(
                raw=value,
                display="Unsupported value",
                subtitle=value if isinstance(value, str) or isinstance(value, uuid.UUID) else value.name if isinstance(value, StageAutomaticOption) else "(unknown)",
                value=value,
                argument=argument,
                unsupported=True
            )
            for value in missing_values
        ]


class StageTextEntryRow(Adw.EntryRow):
    """Edits single line text arguments. Value is stored as str, or None when empty."""

    def __init__(self, stage: ProjectStage, argument: StageArgumentTargetDetails):
        super().__init__()
        self.stage = stage
        self.argument = argument
        self.value = None
        self.event_bus = EventBus[ItemSelectionViewEvent]()
        self.set_title(argument.display_name)
        self.set_show_apply_button(True)
        self.warning_icon = Gtk.Image.new_from_icon_name("danger-triangle-svgrepo-com-symbolic")
        self.warning_icon.add_css_class("warning")
        self.warning_icon.set_tooltip_text("This value is required")
        self.add_suffix(self.warning_icon)
        self.connect("apply", self.on_apply)
        self.load_state()

    def load_state(self):
        current_value = getattr(self.stage, self.argument.attribute_name, None)
        if isinstance(current_value, list):
            current_value = " ".join(str(item) for item in current_value)
        self.value = str(current_value) if current_value else None
        self.set_text(self.value or "")
        self.update_warning()

    def on_apply(self, sender):
        self.value = self.get_text().strip() or None
        # Only rewrite when trimming changed something, as set_text makes AdwEntryRow show apply button again.
        if self.get_text() != (self.value or ""):
            self.set_text(self.value or "")
        self.update_warning()
        self.event_bus.emit(ItemSelectionViewEvent.ITEM_CHANGED, self)
        if root := self.get_root():
            root.set_focus(None)

    def update_warning(self):
        self.warning_icon.set_visible(self.argument.required and not self.value)

class StageTextListRow(Adw.ExpanderRow):
    """Edits list arguments (packages, use, rcadd...) one entry per line. Value is stored as list[str], or None when empty."""

    SUBTITLE_MAX_ITEMS = 3

    def __init__(self, stage: ProjectStage, argument: StageArgumentTargetDetails):
        super().__init__()
        self.stage = stage
        self.argument = argument
        self.value = None
        self.event_bus = EventBus[ItemSelectionViewEvent]()
        self.set_title(argument.display_name)
        self.warning_icon = Gtk.Image.new_from_icon_name("danger-triangle-svgrepo-com-symbolic")
        self.warning_icon.add_css_class("warning")
        self.warning_icon.set_tooltip_text("This value is required")
        self.add_suffix(self.warning_icon)
        self._setup_editor()
        self.load_state()

    def _setup_editor(self):
        self.text_view = Gtk.TextView()
        self.text_view.set_monospace(True)
        self.text_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.text_view.set_accepts_tab(False)
        self.text_view.set_top_margin(8)
        self.text_view.set_bottom_margin(8)
        self.text_view.set_left_margin(8)
        self.text_view.set_right_margin(8)
        self.text_view.set_size_request(-1, 96)
        self.text_view.get_buffer().connect("changed", self.on_buffer_changed)
        key_controller = Gtk.EventControllerKey()
        key_controller.connect("key-pressed", self.on_key_pressed)
        self.text_view.add_controller(key_controller)
        frame = Gtk.Frame()
        frame.set_child(self.text_view)

        hint_label = Gtk.Label(label="One entry per line. Ctrl+Enter to apply.")
        hint_label.set_halign(Gtk.Align.START)
        hint_label.set_hexpand(True)
        hint_label.add_css_class("dimmed")
        hint_label.add_css_class("caption")
        self.revert_button = Gtk.Button(label="Revert")
        self.revert_button.connect("clicked", lambda button: self.load_state())
        self.apply_button = Gtk.Button(label="Apply")
        self.apply_button.add_css_class("suggested-action")
        self.apply_button.connect("clicked", lambda button: self.apply())
        buttons_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        buttons_box.append(hint_label)
        buttons_box.append(self.revert_button)
        buttons_box.append(self.apply_button)

        editor_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        editor_box.set_margin_top(12)
        editor_box.set_margin_bottom(12)
        editor_box.set_margin_start(12)
        editor_box.set_margin_end(12)
        editor_box.append(frame)
        editor_box.append(buttons_box)
        editor_row = Gtk.ListBoxRow()
        editor_row.set_activatable(False)
        editor_row.set_selectable(False)
        editor_row.set_child(editor_box)
        self.add_row(editor_row)

    def load_state(self):
        current_value = getattr(self.stage, self.argument.attribute_name, None)
        if isinstance(current_value, str):
            current_value = current_value.splitlines()
        self.value = [str(item) for item in current_value if str(item).strip()] if current_value else None
        self.value = self.value or None
        self.text_view.get_buffer().set_text("\n".join(self.value or []))
        self.update_display()

    def apply(self):
        buffer = self.text_view.get_buffer()
        text = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False)
        self.value = [line.strip() for line in text.splitlines() if line.strip()] or None
        buffer.set_text("\n".join(self.value or []))
        self.update_display()
        self.event_bus.emit(ItemSelectionViewEvent.ITEM_CHANGED, self)

    def is_modified(self) -> bool:
        buffer = self.text_view.get_buffer()
        text = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False)
        lines = [line.strip() for line in text.splitlines() if line.strip()] or None
        return lines != self.value

    def on_buffer_changed(self, buffer):
        modified = self.is_modified()
        self.apply_button.set_sensitive(modified)
        self.revert_button.set_sensitive(modified)

    def on_key_pressed(self, controller, keyval, keycode, state):
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and state & Gdk.ModifierType.CONTROL_MASK:
            self.apply()
            return True
        return False

    def update_display(self):
        if self.value:
            shown = ", ".join(self.value[:self.SUBTITLE_MAX_ITEMS])
            hidden_count = len(self.value) - self.SUBTITLE_MAX_ITEMS
            subtitle = f"{shown} (+{hidden_count} more)" if hidden_count > 0 else shown
        else:
            subtitle = "(None)"
        self.set_subtitle(GLib.markup_escape_text(subtitle))
        self.warning_icon.set_visible(self.argument.required and not self.value)
        self.on_buffer_changed(self.text_view.get_buffer())

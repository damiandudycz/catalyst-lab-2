import threading, uuid
from gi.repository import Gtk, Adw, GLib
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
                row = StageOptionExpanderRow(
                    project_directory=self.project_directory,
                    stage=self.stage,
                    argument=arg,
                    is_item_selectable_handler=self.is_config_option_selectable
                )
                row.pref_group = group
                row.event_bus.subscribe(
                    ItemSelectionViewEvent.ITEM_CHANGED,
                    self.argument_changed,
                    self
                )
                row.pref_group.add(row)
                self.configuration_rows.append(row)

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
        # Reload other rows
        for r in self.configuration_rows:
            if r.argument != row.argument:
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


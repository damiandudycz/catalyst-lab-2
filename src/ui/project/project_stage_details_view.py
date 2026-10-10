import threading, uuid, os
from gi.repository import Gtk, Gdk, Adw, GLib, Gio
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
    StageArgumentType, StageArgumentDetails, StageArgumentLevel, stage_argument_level
)
from .cl_toggle_group import CLToggle, CLToggleGroup
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
from .project_stage_value_resolver import resolved_stage_argument_display
from .project_stage_portage_confdir import (
    StagePortageConfdirSource, stage_overlay_path, stage_root_overlay_path, root_overlay_values, ROOT_OVERLAY_ARGUMENTS
)
from .project_stage_cache import CACHE_ARGUMENTS, is_automatic_cache, stage_cache_path, display_path
from .project_stage_kernels import (
    KERNEL_SETTINGS, KERNEL_SETTING_LEVELS, stage_kernel_names, kernel_setting, own_kernel_settings,
    set_kernel_setting, kernel_name_error, stage_inherits_kernels, releng_kernel_names, set_kernel_names, remove_kernel,
    new_kernel_name, rename_kernel, KernelSettingType, kernel_setting_enabled, releng_kernel_setting
)
from .project_kernel_packages import load_kernel_packages, KernelPackageKind, default_kernel_package


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
    kernels_pref_group = Gtk.Template.Child()

    # Basic or advanced settings (STAGE_ARGUMENT_LEVELS), last choice is used by stages opened later while app runs.
    advanced_default = False

    def __init__(self, project_directory: ProjectDirectory, stage: ProjectStage, content_navigation_view: Adw.NavigationView | None = None):
        super().__init__()
        self.project_directory = project_directory
        self.stage = stage
        self.content_navigation_view = content_navigation_view
        self.advanced = ProjectStageDetailsView.advanced_default
        self._setup_mode_toggle()
        self.connect("realize", self.on_realize)

    # Basic and advanced mode
    # --------------------------------------------------------------------------

    def _setup_mode_toggle(self):
        """Basic / Advanced toggle in header bar, like Compact / Full of stages. Basic mode hides advanced settings,
        note under them lists hidden settings with values set by stage."""
        self.mode_toggle = CLToggleGroup(valign=Gtk.Align.CENTER)
        self.mode_toggle.add_css_class("round")
        self.mode_toggle.add_css_class("caption")
        self.mode_toggle.add(CLToggle(label="Basic"))
        self.mode_toggle.add(CLToggle(label="Advanced"))
        self.mode_toggle.set_active(1 if self.advanced else 0)
        self.mode_toggle.connect("notify::active", self._on_mode_changed)
        self.hidden_settings_label = Gtk.Label(wrap=True, xalign=0, margin_start=12, margin_end=12, visible=False)
        self.hidden_settings_label.add_css_class("caption")
        self.hidden_settings_label.add_css_class("dimmed")
        self.configuration_pref_group.get_parent().insert_child_after(self.hidden_settings_label, self.configuration_pref_group)

    def header_bar_end_widgets(self) -> list[Gtk.Widget]:
        return [self.mode_toggle]

    def _on_mode_changed(self, group, _param):
        self.advanced = group.get_active() == 1
        ProjectStageDetailsView.advanced_default = self.advanced
        self.apply_mode()

    def apply_mode(self):
        """Shows rows of current mode, and groups which have any of them."""
        hidden_custom = []
        for row in getattr(self, "configuration_rows", []):
            visible = self.advanced or row.level == StageArgumentLevel.BASIC
            row.set_visible(visible)
            if not visible and _has_own_value(self.stage, row.argument.attribute_name):
                hidden_custom.append(row.argument.display_name)
        for row in getattr(self, "kernel_rows", []):
            if isinstance(row, StageKernelRow):
                hidden_custom += [f"{row.kernel_name}: {title}" for title in row.set_advanced(self.advanced)]
        for group in (self.architecture_pref_group, self.release_pref_group, self.packages_pref_group, self.configuration_pref_group):
            group.set_visible(any(row.pref_group is group and row.get_visible() for row in self.configuration_rows))
        self.kernels_pref_group.set_visible(getattr(self, "boot_kernel_argument", None) is not None)
        self.hidden_settings_label.set_label(
            f"Advanced settings set by this stage: {', '.join(hidden_custom)}. Switch to Advanced to see them." if hidden_custom else "")
        self.hidden_settings_label.set_visible(bool(hidden_custom))

    def on_realize(self, widget):
        self.get_root().set_focus(None)
        self._window_active_handler = self.get_root().connect("notify::is-active", self._on_window_active_changed)
        self.connect("unrealize", self._on_unrealize)
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
        self.boot_kernel_argument = None
        # Load arguments rows
        arguments_details = load_catalyst_stage_arguments_details(
            toolset=self.project_directory.get_toolset(),
            target_name=self.stage.target
        )
        for name, arg in arguments_details.items():
            if arg.details == StageArgumentDetails.boot_kernel:
                # Edited as list of kernels in Kernels group (load_kernel_rows).
                self.boot_kernel_argument = arg
                continue
            group = self.pref_group_for_argument(argument=arg)
            if group:
                row = self.create_row_for_argument(argument=arg)
                row.pref_group = group
                row.level = stage_argument_level(arg.details)
                row.event_bus.subscribe(
                    ItemSelectionViewEvent.ITEM_CHANGED,
                    self.argument_changed,
                    self
                )
                row.pref_group.add(row)
                self.configuration_rows.append(row)
                # Kernels depend on boot/kernel and releng template, refreshed after argument is saved.
                row.event_bus.subscribe(ItemSelectionViewEvent.ITEM_CHANGED, self.load_kernel_rows, "kernels")
        self.load_kernel_rows()
        self.apply_mode()

    def load_kernel_rows(self, *args):
        """Kernels of stage (boot/kernel of catalyst, list of names) with settings of each (boot/kernel/<name>/...).
        Kernels are added and removed one by one. Stage uses kernels of releng template until it changes them.
        Expanded kernels stay expanded."""
        expanded = {row.kernel_name for row in getattr(self, "kernel_rows", []) if isinstance(row, StageKernelRow) and row.get_expanded()}
        expanded |= getattr(self, "_expand_kernel", set())
        self._expand_kernel = set()
        for row in getattr(self, "kernel_rows", []):
            self.kernels_pref_group.remove(row)
        self.kernel_rows = []
        if self.boot_kernel_argument is None:
            return # Target doesn't build kernels.
        names = stage_kernel_names(self.project_directory, self.stage)
        inherits = stage_inherits_kernels(self.stage)
        releng_names = releng_kernel_names(self.project_directory, self.stage)
        for name in names:
            row = StageKernelRow(project_directory=self.project_directory, stage=self.stage, kernel_name=name,
                                 on_rename=self._rename_kernel)
            row.set_expanded(name in expanded)
            remove_button = Gtk.Button(icon_name="user-trash-symbolic", tooltip_text="Remove kernel", valign=Gtk.Align.CENTER)
            remove_button.add_css_class("flat")
            remove_button.connect("clicked", lambda _, name=name: self._remove_kernel(name))
            row.add_suffix(remove_button)
            self._add_kernel_row(row)
        add_row = Adw.ButtonRow(title="Add kernel", start_icon_name="add-square-svgrepo-com-symbolic")
        add_row.connect("activated", self._on_add_kernel)
        self._add_kernel_row(add_row)
        if not inherits and releng_names:
            releng_row = Adw.ButtonRow(title=f"Use kernels of releng spec ({', '.join(releng_names)})")
            releng_row.connect("activated", lambda _: self._set_kernel_names(None))
            self._add_kernel_row(releng_row)
        required = "This stage needs at least one kernel. " if self.boot_kernel_argument.required and not names else ""
        releng = "Kernels of releng spec, adding, removing or renaming kernel makes stage use its own list. " if inherits and names else ""
        self.kernels_pref_group.set_description(
            required + releng + "Catalyst builds these kernels with their settings. Empty settings use values of releng template.")
        if args:
            self.apply_mode() # Kernel rows changed after argument was saved.

    def _add_kernel_row(self, row):
        self.kernels_pref_group.add(row)
        self.kernel_rows.append(row)

    def _on_add_kernel(self, row):
        """New kernel with free name (renamed in its settings), its settings are shown. It's distribution kernel, of
        overlay used by stage when it has one, or of Gentoo."""
        names = stage_kernel_names(self.project_directory, self.stage)
        name = new_kernel_name(names)
        self._expand_kernel = {name}
        set_kernel_names(self.project_directory, self.stage, names + [name])
        set_kernel_setting(self.project_directory, self.stage, name, "sources", default_kernel_package(self.project_directory, self.stage))
        set_kernel_setting(self.project_directory, self.stage, name, "distkernel", "yes")
        self.load_kernel_rows(True)

    def _rename_kernel(self, old_name: str, new_name: str) -> str | None:
        """Returns error when name can't be used."""
        if new_name == old_name:
            return None
        names = stage_kernel_names(self.project_directory, self.stage)
        if error := kernel_name_error(new_name, names):
            return error
        rename_kernel(self.project_directory, self.stage, old_name, new_name)
        self._expand_kernel = {new_name}
        GLib.idle_add(lambda: self.load_kernel_rows(True) and False) # After apply signal of row being replaced.
        return None

    def _remove_kernel(self, name: str):
        remove_kernel(self.project_directory, self.stage, name)
        self.load_kernel_rows(True)

    def _set_kernel_names(self, names: list[str] | None):
        set_kernel_names(self.project_directory, self.stage, names)
        self.load_kernel_rows(True)

    def create_row_for_argument(self, argument: StageArgumentTargetDetails) -> Adw.PreferencesRow:
        if argument.details in CACHE_ARGUMENTS:
            return StageCacheRow(project_directory=self.project_directory, stage=self.stage, argument=argument)
        match argument.type:
            case StageArgumentType.select | StageArgumentType.multiselect | StageArgumentType.boolean:
                return StageOptionExpanderRow(
                    project_directory=self.project_directory,
                    stage=self.stage,
                    argument=argument,
                    is_item_selectable_handler=self.is_config_option_selectable
                )
            case StageArgumentType.string_list | StageArgumentType.raw:
                return StageTextListRow(project_directory=self.project_directory, stage=self.stage, argument=argument)
            case _:
                # Stored automatic option is shown as unsupported value even if argument doesn't allow any.
                if (argument.details and argument.details.automatic_options) or isinstance(getattr(self.stage, argument.attribute_name, None), StageAutomaticOption):
                    return StageTextEntrySourceRow(project_directory=self.project_directory, stage=self.stage, argument=argument)
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
                # Hidden, set by CatalystLab: name has own row, snapshot is selected for project and source_subpath is
                # generated from parent stage, as all stages of the tree are built by the app.
                StageArgumentDetails.name |
                StageArgumentDetails.snapshot_treeish |
                StageArgumentDetails.source_subpath
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
            # Reloading option rows would discard unapplied edits of text rows, refresh only resolved values.
            for r in self.configuration_rows:
                if r.argument != row.argument and isinstance(r, (StageTextSourceRow, StageCacheRow)):
                    r.refresh_options()
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
            elif r.argument != row.argument and isinstance(r, (StageTextSourceRow, StageCacheRow)):
                r.refresh_options() # Keeps possibly unapplied text, only updates availability of automatic options.

    def _on_unrealize(self, widget):
        if getattr(self, "_window_active_handler", None) and (root := self.get_root()):
            root.disconnect(self._window_active_handler)
            self._window_active_handler = None

    def _on_window_active_changed(self, window, param):
        # Overlay files could be edited outside of app, refresh their counts.
        if window.is_active():
            for row in getattr(self, "configuration_rows", []):
                if isinstance(row, StageOptionExpanderRow) and row.uses_stage_overlay_folder:
                    row.load_state()

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
    def on_delete_clicked(self, sender):
        detached_stages = ProjectManager.shared().stages_using_parent(project=self.project_directory, stage=self.stage)
        body = f"Stage \"{self.stage.name}\" will be removed from the project. This can't be undone."
        if detached_stages:
            names = ", ".join(f"\"{stage.name}\"" for stage in detached_stages)
            body += f"\n\nIt is used as parent by {names}. Their parent will be cleared, and values they inherit from it will need to be set again."
        dialog = Adw.AlertDialog(heading="Delete stage?", body=body)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Delete")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", self.on_delete_response)
        dialog.present(self.get_root())

    def on_delete_response(self, dialog, response: str):
        if response != "delete":
            return
        try:
            ProjectManager.shared().remove_stage(project=self.project_directory, stage=self.stage)
        except Exception as e:
            print(f"Error deleting stage: {e}")
            return
        if self.content_navigation_view:
            self.content_navigation_view.pop()

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

def create_unsupported_options(missing_values: list, argument: StageArgumentDetails | None) -> list[StageArgumentOption]:
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
            current_values = getattr(self.stage, self.argument.details.name, None) # Mapped to object
            # Stage can store None (all items deselected) or single value, show it as list.
            if current_values is None:
                current_values = []
            elif not isinstance(current_values, list):
                current_values = [current_values]
            if self.argument.details in ROOT_OVERLAY_ARGUMENTS:
                # Values stored by older versions (inherit options, path of stage folder) are shown as sources.
                current_values = root_overlay_values(self.project_directory, self.stage)
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
        return create_unsupported_options(missing_values=missing_values, argument=argument)

    def set_static_list(self, list: list):
        # Show values automatic options resolve to (inherit from parent, releng template...) when they can be determined.
        self.resolved_values: dict[StageAutomaticOption, str] = {}
        for option in list:
            if isinstance(option.value, StageAutomaticOption) and not option.unsupported:
                if resolved := resolved_stage_argument_display(self.project_directory, self.stage, self.argument.name, option.value):
                    self.resolved_values[option.value] = resolved
                    option.subtitle = GLib.markup_escape_text(resolved)
        super().set_static_list(list=list)
        if self.uses_stage_overlay_folder:
            self._add_open_stage_overlay_button()

    @property
    def uses_stage_overlay_folder(self) -> bool:
        """Argument combined from sources that include folder in stage directory (portage_confdir, root overlay)."""
        return self.argument.details == StageArgumentDetails.portage_confdir or self.argument.details in ROOT_OVERLAY_ARGUMENTS

    def _add_open_stage_overlay_button(self):
        row = next((row for row in getattr(self, "rows", []) if row.item.value == StagePortageConfdirSource.STAGE_OVERLAY), None)
        if row is None:
            return
        button = Gtk.Button(icon_name="folder-open-symbolic", tooltip_text="Open stage overlay folder", valign=Gtk.Align.CENTER)
        button.add_css_class("flat")
        button.connect("clicked", self._on_open_stage_overlay_clicked)
        row.add_suffix(button)

    def _on_open_stage_overlay_clicked(self, button):
        # Folder is created when needed and kept when overlay is disabled, to allow enabling it back.
        if self.argument.details in ROOT_OVERLAY_ARGUMENTS:
            path = stage_root_overlay_path(self.project_directory, self.stage)
        else:
            path = stage_overlay_path(self.project_directory, self.stage)
        os.makedirs(path, exist_ok=True)
        Gtk.FileLauncher.new(Gio.File.new_for_path(path)).launch(self.get_root(), None, None)

    def display_selected_item(self):
        super().display_selected_item()
        selected = (self.selected_items or []) if self.allow_multiselect else ([self.selected_item] if self.selected_item else [])
        if any(isinstance(item.value, StageAutomaticOption) for item in selected):
            self.set_subtitle(", ".join(GLib.markup_escape_text(self._item_display(item)) for item in selected))

    def _item_display(self, item: StageArgumentOption) -> str:
        resolved = getattr(self, "resolved_values", {}).get(item.value) if isinstance(item.value, StageAutomaticOption) else None
        return f"{item.display}: {resolved}" if resolved else item.display


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

class StageTextSourceRow(Adw.ExpanderRow):
    """Base for text arguments edited inside expander row. If argument allows automatic options (inherit from parent,
    releng template...), they can be selected instead of custom value edited by subclass editor.
    Value is StageAutomaticOption, custom value, or None when empty."""

    def __init__(self, project_directory: ProjectDirectory, stage: ProjectStage, argument: StageArgumentTargetDetails):
        super().__init__()
        self.project_directory = project_directory
        self.stage = stage
        self.argument = argument
        self.value = None
        self.resolved_values: dict[StageAutomaticOption, str] = {}
        self._loading = False
        self.event_bus = EventBus[ItemSelectionViewEvent]()
        self.set_title(argument.display_name)
        self.warning_icon = Gtk.Image.new_from_icon_name("danger-triangle-svgrepo-com-symbolic")
        self.warning_icon.add_css_class("warning")
        self.add_suffix(self.warning_icon)
        self._setup_sources()
        self.editor_row = self._create_editor_row()
        self.add_row(self.editor_row)
        self.load_state()

    # Sources:

    def _setup_sources(self):
        """Adds rows to select automatic option or custom value. Without automatic options only custom value is used."""
        self.automatic_options: list[StageArgumentOption] = load_catalyst_stage_automatic_arguments_options(stage=self.stage, arg_details=self.argument) or []
        # Add entries for unsupported values (automatic option stored, but not allowed for this argument)
        current_value = getattr(self.stage, self.argument.attribute_name, None)
        missing_values = [current_value] if isinstance(current_value, StageAutomaticOption) and current_value not in {option.value for option in self.automatic_options} else []
        self.unsupported_options = create_unsupported_options(missing_values=missing_values, argument=self.argument.details)
        self.source_rows: dict[StageAutomaticOption | None, Adw.ActionRow] = {}
        self.source_check_buttons: dict[StageAutomaticOption | None, Gtk.CheckButton] = {}
        if not self.automatic_options and not self.unsupported_options:
            return
        group: Gtk.CheckButton | None = None
        for option in self.unsupported_options + self.automatic_options + [None]: # None stands for custom value.
            # Same rows as in option lists (eg. Profile), so unavailable options look the same.
            row = ItemRow(
                item=option,
                item_title_property_name='display',
                item_subtitle_property_name='subtitle',
                item_status_property_name=None,
                item_unsupported_property_name='unsupported',
                item_icon=None
            ) if option else Adw.ActionRow(title="Custom value")
            check_button = Gtk.CheckButton()
            if group:
                check_button.set_group(group)
            else:
                group = check_button
            check_button.connect("toggled", self._on_source_toggled, option.value if option else None)
            row.add_prefix(check_button)
            row.set_activatable_widget(check_button)
            self.add_row(row)
            self.source_rows[option.value if option else None] = row
            self.source_check_buttons[option.value if option else None] = check_button
        self._update_source_subtitles()

    def refresh_options(self):
        """Updates availability of automatic options, which depends on other arguments (parent, releng template)."""
        if not self.source_rows:
            return
        self.automatic_options = load_catalyst_stage_automatic_arguments_options(stage=self.stage, arg_details=self.argument) or []
        self._update_source_subtitles()
        self.update_display()

    def _update_source_subtitles(self):
        """Shows values automatic options resolve to (inherit from parent, releng template...) when they can be determined."""
        self.resolved_values = {}
        for option in self.automatic_options:
            resolved = None if option.unsupported else resolved_stage_argument_display(self.project_directory, self.stage, self.argument.name, option.value)
            if resolved:
                self.resolved_values[option.value] = resolved
            row = self.source_rows[option.value]
            row.set_subtitle(GLib.markup_escape_text(resolved or option.subtitle or ""))
            # Availability can change after creating row (parent or releng template changed).
            if option.unsupported:
                row.add_css_class('warning')
            else:
                row.remove_css_class('warning')

    def _on_source_toggled(self, button: Gtk.CheckButton, source: StageAutomaticOption | None):
        if self._loading or not button.get_active():
            return
        self.value = source if source is not None else self.get_editor_value()
        self.update_display()
        self.event_bus.emit(ItemSelectionViewEvent.ITEM_CHANGED, self)

    def is_custom(self) -> bool:
        return not isinstance(self.value, StageAutomaticOption)

    # State:

    def load_state(self):
        current_value = getattr(self.stage, self.argument.attribute_name, None)
        self._loading = True
        if isinstance(current_value, StageAutomaticOption):
            self.value = current_value
            self.set_editor_value(None)
        else:
            self.value = self.normalized_custom_value(current_value)
            self.set_editor_value(self.value)
        source = self.value if isinstance(self.value, StageAutomaticOption) else None
        if check_button := self.source_check_buttons.get(source):
            check_button.set_active(True)
        self._loading = False
        self.update_display()

    def apply_custom_value(self):
        """Call from editor when custom value is applied."""
        self.value = self.get_editor_value()
        self.set_editor_value(self.value)
        self.update_display()
        self.event_bus.emit(ItemSelectionViewEvent.ITEM_CHANGED, self)

    def update_display(self):
        self.editor_row.set_visible(self.is_custom())
        automatic_option = next((option for option in self.unsupported_options + self.automatic_options if option.value == self.value), None)
        if self.is_custom():
            subtitle = self.custom_value_display(self.value) if self.value else "(None)"
            show_warning = self.argument.required and not self.value
            warning = "This value is required"
        else:
            resolved = self.resolved_values.get(self.value)
            subtitle = (f"{automatic_option.display}: {resolved}" if resolved else automatic_option.display) if automatic_option else f"Unsupported value: {self.value.name}"
            show_warning = automatic_option is None or automatic_option.unsupported
            warning = "Selected option is not available for this stage"
        self.set_subtitle(GLib.markup_escape_text(subtitle))
        self.warning_icon.set_visible(show_warning)
        self.warning_icon.set_tooltip_text(warning)

    # Subclass editor interface:

    def _create_editor_row(self) -> Gtk.ListBoxRow:
        raise NotImplementedError

    def get_editor_value(self):
        """Returns custom value currently entered in editor, normalized."""
        raise NotImplementedError

    def set_editor_value(self, value):
        raise NotImplementedError

    def normalized_custom_value(self, value):
        """Converts stored value to custom value format used by editor."""
        raise NotImplementedError

    def custom_value_display(self, value) -> str:
        raise NotImplementedError

class StageTextEntrySourceRow(StageTextSourceRow):
    """Edits single line text arguments that also allow automatic options. Custom value is stored as str."""

    def _create_editor_row(self) -> Gtk.ListBoxRow:
        self.entry_row = Adw.EntryRow(title="Value")
        self.entry_row.set_show_apply_button(True)
        self.entry_row.connect("apply", self.on_apply)
        return self.entry_row

    def on_apply(self, sender):
        self.apply_custom_value()
        if root := self.get_root():
            root.set_focus(None)

    def get_editor_value(self):
        return self.entry_row.get_text().strip() or None

    def set_editor_value(self, value):
        # Only rewrite when text differs, as set_text makes AdwEntryRow show apply button again.
        if self.entry_row.get_text() != (value or ""):
            self.entry_row.set_text(value or "")

    def normalized_custom_value(self, value):
        if isinstance(value, list):
            value = " ".join(str(item) for item in value)
        return str(value) if value else None

    def custom_value_display(self, value) -> str:
        return value

class StageTextListRow(StageTextSourceRow):
    """Edits list arguments (packages, use, rcadd...) one entry per line. Custom value is stored as list[str]."""

    SUBTITLE_MAX_ITEMS = 3

    def _create_editor_row(self) -> Gtk.ListBoxRow:
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
        self.revert_button.connect("clicked", lambda button: self.set_editor_value(self.value if self.is_custom() else None))
        self.apply_button = Gtk.Button(label="Apply")
        self.apply_button.add_css_class("suggested-action")
        self.apply_button.connect("clicked", lambda button: self.apply_custom_value())
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
        return editor_row

    def get_editor_value(self):
        buffer = self.text_view.get_buffer()
        text = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False)
        return [line.strip() for line in text.splitlines() if line.strip()] or None

    def set_editor_value(self, value):
        self.text_view.get_buffer().set_text("\n".join(value or []))

    def normalized_custom_value(self, value):
        if isinstance(value, str):
            value = value.splitlines()
        return [str(item) for item in value if str(item).strip()] or None if value else None

    def custom_value_display(self, value) -> str:
        shown = ", ".join(value[:self.SUBTITLE_MAX_ITEMS])
        hidden_count = len(value) - self.SUBTITLE_MAX_ITEMS
        return f"{shown} (+{hidden_count} more)" if hidden_count > 0 else shown

    def is_modified(self) -> bool:
        return self.get_editor_value() != (self.value if self.is_custom() else None)

    def on_buffer_changed(self, buffer):
        modified = self.is_modified()
        self.apply_button.set_sensitive(modified)
        self.revert_button.set_sensitive(modified)

    def on_key_pressed(self, controller, keyval, keycode, state):
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and state & Gdk.ModifierType.CONTROL_MASK:
            self.apply_custom_value()
            return True
        return False

    def update_display(self):
        super().update_display()
        self.on_buffer_changed(self.text_view.get_buffer())

class StageCacheRow(Adw.ExpanderRow):
    """Selects package or kernel cache of stage: automatic folder in project builds (shared by stages with the same
    rel_type), folder selected by user, or no cache. Value is StageAutomaticOption, folder path or None."""

    def __init__(self, project_directory: ProjectDirectory, stage: ProjectStage, argument: StageArgumentTargetDetails):
        super().__init__()
        self.project_directory = project_directory
        self.stage = stage
        self.argument = argument
        self.value = None
        self._loading = False
        self.event_bus = EventBus[ItemSelectionViewEvent]()
        self.set_title(argument.display_name)
        self.check_buttons: dict[str, Gtk.CheckButton] = {}
        self.automatic_row = self._add_source_row("automatic", "Automatic")
        self.folder_row = self._add_source_row("folder", "Folder")
        self.none_row = self._add_source_row("none", "None", subtitle="Disabled, built packages are not kept" if argument.details == StageArgumentDetails.pkgcache_path else "Disabled, built kernels are not kept")
        choose_button = Gtk.Button(icon_name="folder-open-symbolic", tooltip_text="Select folder", valign=Gtk.Align.CENTER)
        choose_button.add_css_class("flat")
        choose_button.connect("clicked", lambda button: self._select_folder())
        self.folder_row.add_suffix(choose_button)
        self.load_state()

    def _add_source_row(self, source: str, title: str, subtitle: str | None = None) -> Adw.ActionRow:
        row = Adw.ActionRow(title=title, subtitle=subtitle or "")
        check_button = Gtk.CheckButton()
        if self.check_buttons:
            check_button.set_group(next(iter(self.check_buttons.values())))
        check_button.connect("toggled", self._on_source_toggled, source)
        row.add_prefix(check_button)
        row.set_activatable_widget(check_button)
        self.add_row(row)
        self.check_buttons[source] = check_button
        return row

    # State:

    def _source(self) -> str:
        if is_automatic_cache(self.value):
            return "automatic"
        return "folder" if self.value else "none"

    def load_state(self):
        value = getattr(self.stage, self.argument.attribute_name, None)
        # Automatic options other than automatic cache come from stages made before caches had it, see is_automatic_cache.
        self.value = StageAutomaticOption.GENERATE_AUTOMATICALLY if is_automatic_cache(value) else (value if isinstance(value, str) and value else None)
        self._folder = self.value if isinstance(self.value, str) else getattr(self, "_folder", None)
        self._loading = True
        self.check_buttons[self._source()].set_active(True)
        self._loading = False
        self.update_display()

    def refresh_options(self):
        """Automatic folder depends on rel_type."""
        self.update_display()

    def update_display(self):
        automatic_path = stage_cache_path(self.project_directory, _StageWithValue(self.stage, self.argument.attribute_name, StageAutomaticOption.GENERATE_AUTOMATICALLY), self.argument.details)
        automatic_display = display_path(automatic_path) if automatic_path else "Folder in project builds, shared by stages with the same Rel type"
        self.automatic_row.set_subtitle(GLib.markup_escape_text(automatic_display))
        self.folder_row.set_subtitle(GLib.markup_escape_text(display_path(self._folder) if self._folder else "No folder selected"))
        match self._source():
            case "automatic": subtitle = f"Automatic: {automatic_display}" if automatic_path else "Automatic"
            case "folder": subtitle = display_path(self.value)
            case _: subtitle = "None"
        self.set_subtitle(GLib.markup_escape_text(subtitle))

    def _set_value(self, value):
        self.value = value
        self.update_display()
        self.event_bus.emit(ItemSelectionViewEvent.ITEM_CHANGED, self)

    # Selecting:

    def _on_source_toggled(self, button: Gtk.CheckButton, source: str):
        if self._loading or not button.get_active():
            return
        match source:
            case "automatic": self._set_value(StageAutomaticOption.GENERATE_AUTOMATICALLY)
            case "none": self._set_value(None)
            case "folder":
                if self._folder:
                    self._set_value(self._folder)
                else:
                    self._select_folder()

    def _select_folder(self):
        dialog = Gtk.FileDialog(title=f"Select {self.argument.display_name.lower()} folder", modal=True)
        if self._folder and os.path.isdir(self._folder):
            dialog.set_initial_folder(Gio.File.new_for_path(self._folder))
        dialog.select_folder(self.get_root(), None, self._on_folder_selected)

    def _on_folder_selected(self, dialog: Gtk.FileDialog, result: Gio.AsyncResult):
        try:
            folder = dialog.select_folder_finish(result)
        except GLib.Error:
            folder = None # Cancelled.
        if folder and folder.get_path():
            self._folder = folder.get_path()
            self._loading = True
            self.check_buttons["folder"].set_active(True)
            self._loading = False
            self._set_value(self._folder)
        else:
            # Restore previous selection when folder wasn't selected.
            self._loading = True
            self.check_buttons[self._source()].set_active(True)
            self._loading = False
            self.update_display()

class _StageWithValue:
    """Stage with one argument replaced, used to preview value of option that is not selected."""

    def __init__(self, stage, attribute_name: str, value):
        self._stage = stage
        self._attribute_name = attribute_name
        self._value = value

    def __getattr__(self, name):
        return self._value if name == self._attribute_name else getattr(self._stage, name)

class StageKernelRow(Adw.ExpanderRow):
    """Kernel of stage with its settings. Settings not set by stage use values of releng template, which are shown
    with them. Settings are edited by their type: switches, lists (one entry per line) and texts."""

    def __init__(self, project_directory: ProjectDirectory, stage: ProjectStage, kernel_name: str, on_rename=None):
        super().__init__(title=GLib.markup_escape_text(kernel_name))
        self.project_directory = project_directory
        self.stage = stage
        self.kernel_name = kernel_name
        self._loading = False
        # Name is used by catalyst in file names (/boot/<name>) and names of settings (boot/kernel/<name>/...).
        self.name_row = Adw.EntryRow(title="Name", text=kernel_name, show_apply_button=True)
        self.name_row.set_tooltip_text("Name of kernel, used in names of its files in /boot")
        self.name_row.connect("changed", lambda row: (row.remove_css_class("error"), row.set_tooltip_text("Name of kernel, used in names of its files in /boot")))
        if on_rename:
            self.name_row.connect("apply", lambda row: self._on_rename(row, on_rename))
        self.add_row(self.name_row)
        self.entry_rows = []
        for setting in KERNEL_SETTINGS:
            match setting.type:
                case _ if setting.key == "sources":
                    row = KernelPackageRow(project_directory, stage, setting, on_select=self._on_package_selected)
                case KernelSettingType.BOOLEAN:
                    row = Adw.SwitchRow(title=GLib.markup_escape_text(setting.title))
                    row.connect("notify::active", self._on_switch_toggled, setting)
                case KernelSettingType.LIST:
                    row = KernelListRow(setting, on_apply=lambda values, setting=setting: self._set(setting, values))
                case _:
                    row = Adw.EntryRow(show_apply_button=True)
                    row.connect("apply", lambda row, setting=setting: self._set(setting, row.get_text().strip()))
            row.set_tooltip_text(setting.description)
            self.add_row(row)
            self.entry_rows.append((setting, row))
        self.load_state()

    def _on_rename(self, row: Adw.EntryRow, on_rename):
        if error := on_rename(self.kernel_name, row.get_text().strip()):
            row.add_css_class("error")
            row.set_tooltip_text(error)

    def set_advanced(self, advanced: bool) -> list[str]:
        """Shows settings of mode, returns titles of hidden settings set by stage."""
        own = own_kernel_settings(self.stage).get(self.kernel_name, {})
        hidden = []
        for setting, row in self.entry_rows:
            visible = advanced or KERNEL_SETTING_LEVELS[setting.key] == StageArgumentLevel.BASIC
            row.set_visible(visible)
            if not visible and own.get(setting.key) not in (None, "", []):
                hidden.append(setting.title)
        return hidden

    def load_state(self):
        self._loading = True
        own = own_kernel_settings(self.stage).get(self.kernel_name, {})
        summary = []
        for setting, row in self.entry_rows:
            value, is_own = kernel_setting(self.project_directory, self.stage, self.kernel_name, setting.key)
            inherited = value is not None and not is_own
            match setting.type:
                case _ if setting.key == "sources":
                    row.load(own.get(setting.key), value if inherited else None)
                case KernelSettingType.BOOLEAN:
                    row.set_active(kernel_setting_enabled(value))
                    row.set_subtitle("From releng spec" if inherited else "Set by this stage" if is_own else "")
                case KernelSettingType.LIST:
                    row.load(own.get(setting.key) or [], value if inherited else None)
                case _:
                    row.set_text(self._text(own.get(setting.key)))
                    # Empty entry shows title as placeholder, with value of releng template used then.
                    row.set_title(GLib.markup_escape_text(f"{setting.title} (releng: {self._text(value)})" if inherited else setting.title))
            if setting.key == "sources" and value:
                summary.append(self._text(value))
            elif setting.key == "distkernel":
                summary.append("distribution kernel" if kernel_setting_enabled(value) else "genkernel")
        self.set_subtitle(GLib.markup_escape_text(" · ".join(summary)))
        self._loading = False

    def _on_switch_toggled(self, row: Adw.SwitchRow, _param, setting):
        if self._loading:
            return
        # Stage stores value only when it differs from releng template (no turns off releng yes, and isn't written).
        releng = kernel_setting_enabled(releng_kernel_setting(self.project_directory, self.stage, self.kernel_name, setting.key))
        enabled = row.get_active()
        self._set(setting, None if enabled == releng else "yes" if enabled else "no")

    def _set(self, setting, value):
        set_kernel_setting(self.project_directory, self.stage, self.kernel_name, setting.key, value or None)
        self.load_state()

    def _own_value_for(self, key: str, value):
        """Value stored by stage: none when it's the same as value of releng template."""
        releng = releng_kernel_setting(self.project_directory, self.stage, self.kernel_name, key)
        if key == "distkernel":
            return None if value == kernel_setting_enabled(releng) else "yes" if value else "no"
        return None if value == releng else value

    def _on_package_selected(self, atom: str | None, kind: KernelPackageKind | None):
        """Package of kernel (None uses releng template), Distribution kernel follows kind of selected package."""
        set_kernel_setting(self.project_directory, self.stage, self.kernel_name, "sources",
                           None if atom is None else self._own_value_for("sources", atom))
        if kind in (KernelPackageKind.DISTRIBUTION, KernelPackageKind.SOURCES):
            set_kernel_setting(self.project_directory, self.stage, self.kernel_name, "distkernel",
                               self._own_value_for("distkernel", kind == KernelPackageKind.DISTRIBUTION))
        self.load_state()

    @staticmethod
    def _text(value) -> str:
        if value is None:
            return ""
        return " ".join(str(item) for item in value) if isinstance(value, list) else str(value)

class KernelPackageRow(Adw.ExpanderRow):
    """Package of kernel, selected from kernel packages of snapshot and overlays of stage (read when row is expanded
    first time), from releng template, or typed (eg. with version). Packages without kernel sources can't be selected."""

    def __init__(self, project_directory, stage, setting, on_select):
        super().__init__(title=GLib.markup_escape_text(setting.title))
        self.project_directory = project_directory
        self.stage = stage
        self.on_select = on_select
        self.packages = None # Read when expanded.
        self.own = None
        self.inherited = None
        self.rows = []
        self._loading = False
        self._building = False
        self.connect("notify::expanded", self._on_expanded)

    def load(self, own, inherited):
        self.own, self.inherited = own, inherited
        value = own or inherited
        self.set_subtitle(GLib.markup_escape_text(
            value if own else f"From releng spec: {inherited}" if inherited
            else "Not set, catalyst uses sys-kernel/gentoo-kernel (distribution kernel) or sys-kernel/gentoo-sources"))
        if self.packages is not None:
            # Rows are updated in place, rebuilding them would move the page (its height and focus change).
            if self._options_key() == getattr(self, "_built_key", None):
                self._update_rows()
            else:
                self._build_rows()

    def _options_key(self):
        return (self.inherited, tuple(package.atom for package in self.packages))

    def _selected_option(self):
        """Option checked for current value: None (releng), package atom, or "" (custom value)."""
        known = {package.atom for package in self.packages}
        if not self.own:
            return None if self.inherited else "-" # Nothing checked when catalyst default is used.
        return self.own if self.own in known else ""

    def _update_rows(self):
        self._building = True
        selected = self._selected_option()
        for value, check in self.checks.items():
            check.set_active(value == selected)
        if selected == "": # Text typed but not applied stays otherwise.
            self.custom_row.set_text(self.own)
        self._building = False

    def _on_expanded(self, row, _param):
        if not self.get_expanded() or self.packages is not None or self._loading:
            return
        self._loading = True
        self._replace_rows([Adw.ActionRow(title="Reading kernel packages of snapshot and overlays...")])
        def work():
            try:
                packages = load_kernel_packages(self.project_directory, self.stage)
            except Exception as e:
                print(f"Failed to read kernel packages: {e}")
                packages = []
            def done():
                self._loading = False
                self.packages = packages
                self._build_rows()
                return False
            GLib.idle_add(done)
        threading.Thread(target=work, daemon=True).start()

    def _replace_rows(self, rows):
        for row in self.rows:
            self.remove(row)
        self.rows = rows
        for row in rows:
            self.add_row(row)

    def _build_rows(self):
        rows, group = [], None
        self.checks = {} # Option value (None for releng, atom) -> its check button.
        selected = self._selected_option()
        def option(title, subtitle, value, on_activate, sensitive=True):
            nonlocal group
            check = Gtk.CheckButton(valign=Gtk.Align.CENTER, active=value == selected)
            self.checks[value] = check
            if group:
                check.set_group(group)
            group = group or check
            row = Adw.ActionRow(title=GLib.markup_escape_text(title), subtitle=GLib.markup_escape_text(subtitle))
            row.add_prefix(check)
            row.set_activatable_widget(check)
            row.set_sensitive(sensitive)
            # Selection rebuilds rows (with this one), after its signal.
            check.connect("toggled", lambda check: check.get_active() and not self._building and GLib.idle_add(lambda: on_activate() and False))
            rows.append(row)
        self._building = True
        if self.inherited:
            option(f"From releng spec: {self.inherited}", "Package set by releng template of stage", None,
                   lambda: self.on_select(None, None))
        for package in self.packages:
            option(package.atom, package.details, package.atom,
                   lambda package=package: self.on_select(package.atom, package.kind), sensitive=package.supported)
        if not self.packages:
            rows.append(Adw.ActionRow(title="No kernel packages found", subtitle="Project has no snapshot yet, and overlays of stage don't have kernels"))
        custom = Adw.EntryRow(title="Custom package, eg. =sys-kernel/gentoo-kernel-6.12.8", show_apply_button=True,
                              text=self.own if selected == "" else "")
        self.custom_row = custom
        custom.connect("apply", lambda row: row.get_text().strip() and GLib.idle_add(lambda: self.on_select(row.get_text().strip(), None) and False))
        rows.append(custom)
        self._replace_rows(rows)
        self._built_key = self._options_key()
        self._building = False

class KernelListRow(Adw.ExpanderRow):
    """List setting of kernel, edited one entry per line like list settings of stage. Empty list uses value of
    releng template, shown in subtitle."""

    def __init__(self, setting, on_apply):
        super().__init__(title=GLib.markup_escape_text(setting.title))
        self.on_apply = on_apply
        self.values: list[str] = []
        self._loaded = False
        self.text_view = Gtk.TextView(monospace=True, wrap_mode=Gtk.WrapMode.WORD_CHAR, accepts_tab=False,
                                      top_margin=8, bottom_margin=8, left_margin=8, right_margin=8)
        self.text_view.set_size_request(-1, 72)
        key_controller = Gtk.EventControllerKey()
        key_controller.connect("key-pressed", self._on_key_pressed)
        self.text_view.add_controller(key_controller)
        hint = Gtk.Label(label="One entry per line. Ctrl+Enter to apply.", halign=Gtk.Align.START, hexpand=True)
        hint.add_css_class("dimmed")
        hint.add_css_class("caption")
        revert_button = Gtk.Button(label="Revert")
        revert_button.connect("clicked", lambda _: self._set_text(self.values))
        apply_button = Gtk.Button(label="Apply")
        apply_button.add_css_class("suggested-action")
        apply_button.connect("clicked", lambda _: self._apply())
        buttons = Gtk.Box(spacing=6)
        for widget in (hint, revert_button, apply_button):
            buttons.append(widget)
        editor = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)
        editor.append(Gtk.Frame(child=self.text_view))
        editor.append(buttons)
        self.add_row(Gtk.ListBoxRow(activatable=False, selectable=False, child=editor))

    def load(self, values: list[str], inherited: list[str] | None):
        # Text is replaced only when value changed, edits not applied yet stay (and page doesn't move).
        if list(values) != self.values or not self._loaded:
            self._set_text(list(values))
        self._loaded = True
        self.values = list(values)
        if self.values:
            subtitle = ", ".join(self.values)
        elif inherited:
            subtitle = f"From releng spec: {', '.join(inherited)}"
        else:
            subtitle = "None"
        self.set_subtitle(GLib.markup_escape_text(subtitle))

    def _set_text(self, values: list[str]):
        self.text_view.get_buffer().set_text("\n".join(values))

    def _apply(self):
        buffer = self.text_view.get_buffer()
        text = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False)
        self.on_apply([line.strip() for line in text.splitlines() if line.strip()])

    def _on_key_pressed(self, controller, keyval, keycode, state):
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and state & Gdk.ModifierType.CONTROL_MASK:
            self._apply()
            return True
        return False

def _has_own_value(stage: ProjectStage, attribute: str) -> bool:
    """Stage sets value itself (not automatic option like inheriting from parent or releng spec)."""
    value = getattr(stage, attribute, None)
    if value is None or value == "" or value == []:
        return False
    if isinstance(value, list):
        return not all(isinstance(item, StageAutomaticOption) for item in value)
    return not isinstance(value, StageAutomaticOption)

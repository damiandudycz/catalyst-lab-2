from __future__ import annotations
from gi.repository import Gtk, Adw, GLib
from .multistage_process import MultiStageProcessState
from .project_installation import ProjectInstallation
from .git_directory_create_config_view import GitDirectoryCreateConfigViewEvent
from .git_directory_default_content_builder import DefaultDirContentBuilder
from .git_installation import GitDirectorySetupConfiguration
from .toolset_application import ToolsetApplication
from .toolset import ToolsetEvents
from .wizard_view import WizardView
from .item_select_view import ItemSelectionViewEvent
from .toolset import Toolset
from .releng_directory import RelengDirectory
from .snapshot import Snapshot
from .project_stage import load_catalyst_targets, load_releng_templates
from .project_stage_installation import ProjectStageInstallation
from .project_manager import ProjectManager
from .item_select_view import ItemRow
from enum import Enum, auto
import os, threading, uuid

class ProjectStageSeedSpecial(Enum):
    NONE = auto()
    DOWNLOAD = auto()

@Gtk.Template(resource_path='/com/damiandudycz/CatalystLab/ui/project/project_stage_create_view.ui')
class ProjectStageCreateView(Gtk.Box):
    __gtype_name__ = "ProjectStageCreateView"

    # Main views:
    wizard_view = Gtk.Template.Child()
    # Setup view elements:
    spec_type_page = Gtk.Template.Child()
    spec_type_selection_view = Gtk.Template.Child()
    releng_base_page = Gtk.Template.Child()
    options_page = Gtk.Template.Child()
    releng_base_selection_view = Gtk.Template.Child()
    use_releng_template_checkbox = Gtk.Template.Child()
    stage_name_row = Gtk.Template.Child()
    name_used_label = Gtk.Template.Child()
    seed_list_view = Gtk.Template.Child()
    use_automatic_seed_row = Gtk.Template.Child()
    use_automatic_seed_checkbox = Gtk.Template.Child()

    def __init__(self, project_directory: ProjectDirectory, installation_in_progress: ProjectInstallation | None = None, content_navigation_view: Adw.NavigationView | None = None):
        super().__init__()
        self.project_directory = project_directory
        self.installation_in_progress = installation_in_progress
        self.content_navigation_view = content_navigation_view
        self.filename_is_free = False
        self.selected_seed_is_correct = False
        self.selected_seed: ProjectStageSeedSpecial | uuid.UUID | None = None
        self.load_targets()
        self.load_seed_list()
        self.spec_type_selection_view.event_bus.subscribe(
            ItemSelectionViewEvent.ITEM_CHANGED,
            self.spec_type_changed
        )
        self.releng_base_selection_view.event_bus.subscribe(
            ItemSelectionViewEvent.ITEM_CHANGED,
            self.releng_base_changed
        )
        self.connect("realize", self.on_realize)

    def spec_type_changed(self, data):
        self.wizard_view._refresh_buttons_state()
        class RelengTemplateContainer:
            def __init__(self, releng_template_subpath: str):
                self.releng_template_subpath = releng_template_subpath
            @property
            def name(self) -> str:
                return self.releng_template_subpath
        def worker():
            template_subpaths = load_releng_templates(releng_directory=self.project_directory.get_releng_directory(), stage_name=self.spec_type_selection_view.selected_item.name)
            templates = [RelengTemplateContainer(releng_template_subpath=subpath) for subpath in template_subpaths]
            GLib.idle_add(self.releng_base_selection_view.set_static_list, templates)
        if self.spec_type_selection_view.selected_item is not None:
            threading.Thread(target=worker, daemon=True).start()
        can_select_download_seed = (
            self.spec_type_selection_view.selected_item
            and self.spec_type_selection_view.selected_item.target_name == 'stage1'
        )
        self.use_automatic_seed_row.set_activatable(can_select_download_seed)
        self.use_automatic_seed_row.set_sensitive(can_select_download_seed)
        self._update_default_stage_name()
        self.check_if_seed_correct()

    def releng_base_changed(self, data):
        self.wizard_view._refresh_buttons_state()
        self._update_default_stage_name()

    def on_realize(self, widget):
        self.wizard_view.content_navigation_view = self.content_navigation_view
        self.wizard_view._window = self._window
        self.wizard_view.set_installation(self.installation_in_progress)

    def load_targets(self):
        class SpecTargetContainer:
            """Helper class used to display elements in item_select_view."""
            def __init__(self, target_name: str):
                self.target_name = target_name
            @property
            def name(self) -> str:
                return self.target_name
        def worker():
            target_names = load_catalyst_targets(toolset=self.project_directory.get_toolset())
            targets = [SpecTargetContainer(target_name=name) for name in target_names]
            GLib.idle_add(self.spec_type_selection_view.set_static_list, targets)
        threading.Thread(target=worker, daemon=True).start()

    def load_seed_list(self):
        for stage in self.project_directory.stages:
            row = ItemRow(
                item=stage,
                item_title_property_name='name',
                item_subtitle_property_name='short_details',
                item_status_property_name=None,
                item_icon=None
            )
            check_button = Gtk.CheckButton()
            check_button.set_group(self.use_automatic_seed_checkbox)
            check_button.connect("toggled", self._on_seed_item_selected, stage)
            row.add_prefix(check_button)
            row.set_activatable_widget(check_button)
            self.seed_list_view.add(row)

    @Gtk.Template.Callback()
    def on_use_releng_toggled(self, sender):
        self.releng_base_selection_view.set_sensitive(sender.get_active())
        self.wizard_view._refresh_buttons_state()
        self._update_default_stage_name()

    @Gtk.Template.Callback()
    def is_page_ready_to_continue(self, sender, page) -> bool:
        match page:
            case self.spec_type_page:
                return self.spec_type_selection_view.selected_item is not None
            case self.releng_base_page:
                return self.releng_base_selection_view.selected_item is not None or not self.use_releng_template_checkbox.get_active()
            case self.options_page:
                return self.filename_is_free and self.selected_seed_is_correct
        return True

    @Gtk.Template.Callback()
    def is_item_selectable(self, sender, item) -> bool:
        match sender:
            case self.spec_type_selection_view:
                return True
            case self.releng_base_selection_view:
                return True
        return False

    @Gtk.Template.Callback()
    def is_item_usable(self, sender, item) -> bool:
        match sender:
            case self.spec_type_selection_view:
                return True
            case self.releng_base_selection_view:
                return True
        return False

    @Gtk.Template.Callback()
    def on_stage_name_activate(self, sender):
        self.check_filename_is_free()
        self.get_root().set_focus(None)

    @Gtk.Template.Callback()
    def on_stage_name_changed(self, sender):
        self.check_filename_is_free()

    def check_filename_is_free(self) -> bool:
        self.filename_is_free = ProjectManager.shared().is_stage_name_available(project=self.project_directory, name=self.stage_name_row.get_text())
        self.name_used_label.set_visible(not self.filename_is_free)
        self.wizard_view._refresh_buttons_state()
        return self.filename_is_free

    def _update_default_stage_name(self):
        if self.releng_base_selection_view.selected_item is not None and self.use_releng_template_checkbox.get_active():
            self.stage_name_row.set_text(os.path.splitext(os.path.basename(self.releng_base_selection_view.selected_item.name))[0])
        elif self.spec_type_selection_view.selected_item is not None:
            self.stage_name_row.set_text(self.spec_type_selection_view.selected_item.name)
        self.check_filename_is_free()

    @Gtk.Template.Callback()
    def on_use_automatic_seed_toggled(self, sender):
        self.selected_seed = ProjectStageSeedSpecial.DOWNLOAD
        self.check_if_seed_correct()
    @Gtk.Template.Callback()
    def on_use_none_seed_toggled(self, sender):
        self.selected_seed = ProjectStageSeedSpecial.NONE
        self.check_if_seed_correct()
    def _on_seed_item_selected(self, sender, item):
        self.selected_seed = item.id
        self.check_if_seed_correct()
    def check_if_seed_correct(self) -> bool:
        self.selected_seed_is_correct = (
            self.selected_seed is not None
            and not (
                self.selected_seed == ProjectStageSeedSpecial.DOWNLOAD
                and self.spec_type_selection_view.selected_item.target_name != "stage1"
            )
        )
        self.wizard_view._refresh_buttons_state()
        return self.selected_seed_is_correct

    @Gtk.Template.Callback()
    def begin_installation(self, view):
        self._start_installation(
            project_directory=self.project_directory,
            target_name=self.spec_type_selection_view.selected_item.target_name,
            releng_template_name=(
                self.releng_base_selection_view.selected_item.releng_template_subpath
                if self.releng_base_selection_view.selected_item and self.use_releng_template_checkbox.get_active()
                else None
            ),
            stage_name=self.stage_name_row.get_text(),
            parent_id=(
                None if self.selected_seed == ProjectStageSeedSpecial.NONE
                else ProjectStage.DOWNLOAD_SEED_ID if self.selected_seed == ProjectStageSeedSpecial.NONE
                else self.selected_seed if isinstance(self.selected_seed, uuid.UUID)
                else None
            )
        )

    def _start_installation(
        self,
        project_directory: ProjectDirectory,
        target_name: str,
        releng_template_name: str | None,
        stage_name: str,
        parent_id: uuid.UUID | None
    ):
        installation_in_progress = ProjectStageInstallation(
            project_directory=project_directory,
            target_name=target_name,
            releng_template_name=releng_template_name,
            stage_name=stage_name,
            parent_id=parent_id
        )
        installation_in_progress.start()
        self.wizard_view.set_installation(installation_in_progress)


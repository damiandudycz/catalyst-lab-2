from gi.repository import Gtk, Adw, GLib
from dataclasses import dataclass
from .project_directory import ProjectDirectory
from .project_stage import ProjectStage, load_catalyst_targets, load_releng_templates, load_catalyst_stage_arguments
from .project_manager import ProjectManager
from .git_directory import GitDirectoryEvent
from .project_stage import ProjectStageEvent
from .repository_list_view import ItemRow
from .architecture import Architecture
from .item_select_view import ItemSelectionViewEvent
from .project_stage import ProjectStage, DownloadSeedStage
import threading

@Gtk.Template(resource_path='/com/damiandudycz/CatalystLab/ui/project/project_stage_details_view.ui')
class ProjectStageDetailsView(Gtk.Box):
    __gtype_name__ = "ProjectStageDetailsView"

    ALLOW_CHANGING_TARGET = False

    stage_name_row = Gtk.Template.Child()
    name_used_row = Gtk.Template.Child()
    stage_seed_row = Gtk.Template.Child()
    stage_target_static_row = Gtk.Template.Child()
    stage_target_row = Gtk.Template.Child()
    stage_releng_template_row = Gtk.Template.Child()
    profile_selection_row = Gtk.Template.Child()
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
        self.load_releng_templates()
        self.load_seeds()
        self.load_targets()
        self.load_profiles()
        self.load_configuration_rows()
        self.monitor_information_changes()

    # Loading stage data
    # --------------------------------------------------------------------------

    def load_stage_details(self):
        self.stage_name_row.set_text(self.stage.name)
        self.stage_target_row.set_visible(ProjectStageDetailsView.ALLOW_CHANGING_TARGET)
        self.stage_target_static_row.set_visible(not ProjectStageDetailsView.ALLOW_CHANGING_TARGET)
        self.stage_target_static_row.set_subtitle(self.stage.target_name)

    def load_seeds(self):
        child_ids = self._get_descendant_ids()
        available_stages = self.project_directory.stages[:]
        download_seed_stage = DownloadSeedStage()
        if self.stage.target_name == "stage1":
            available_stages.insert(0, download_seed_stage)
        values = [
            stage for stage in available_stages
            if stage.id not in child_ids and stage.id != self.stage.id
        ]
        selected = download_seed_stage if self.stage.parent_id == download_seed_stage.id else next(
            (stage for stage in available_stages if stage.id == self.stage.parent_id),
            None
        )
        self.stage_seed_row.select(selected)
        self.stage_seed_row.set_static_list(values)

    def load_targets(self):
        values = load_catalyst_targets(toolset=self.project_directory.get_toolset())
        selected = self.stage.target_name
        self.stage_target_row.select(selected)
        self.stage_target_row.set_static_list(values)

    def load_profiles(self):
        self.profile_selection_row.display_none = self.stage.releng_template_name is not None
        values = sorted(
            self.project_directory.get_snapshot().load_profiles(arch=self.project_directory.get_architecture()),
            key=lambda profile: profile.path
        )
        selected = self.stage.profile
        self.profile_selection_row.select(selected)
        self.profile_selection_row.set_static_list(values)

    def load_releng_templates(self):
        values = load_releng_templates(
            releng_directory=self.project_directory.get_releng_directory(),
            stage_name=self.stage.target_name,
            architecture=self.project_directory.get_architecture()
        )
        selected = self.stage.releng_template_name
        self.stage_releng_template_row.select(selected)
        self.stage_releng_template_row.set_static_list(values)

    def load_configuration_rows(self):
        supported_arguments = load_catalyst_stage_arguments(toolset=self.project_directory.get_toolset(), target_name=self.stage.target_name)
        for arg_name in sorted(supported_arguments.valid):
            is_required = arg_name in supported_arguments.required
            row = Adw.ActionRow(title=arg_name)
            self.configuration_pref_group.add(row)

    # Monitoring stage changes
    # --------------------------------------------------------------------------

    def monitor_information_changes(self):
        """React to changes in UI and store them."""
        subscriptions = [
            (self.stage.event_bus, ProjectStageEvent.NAME_CHANGED, self.on_name_changed),
            (self.stage_seed_row.event_bus, ItemSelectionViewEvent.ITEM_CHANGED, self.on_seed_selected),
            (self.profile_selection_row.event_bus, ItemSelectionViewEvent.ITEM_CHANGED, self.on_profile_selected),
            (self.stage_releng_template_row.event_bus, ItemSelectionViewEvent.ITEM_CHANGED, self.on_releng_template_selected),
            (self.stage_target_row.event_bus, ItemSelectionViewEvent.ITEM_CHANGED, self.on_target_selected),
        ]
        for bus, event, handler in subscriptions:
            bus.subscribe(event, handler)

    def on_name_changed(self, data):
        self._page.set_title(self.stage.name)

    def on_target_selected(self, sender):
        ProjectManager.shared().change_stage_target(project=self.project_directory, stage=self.stage, target_name=sender.selected_item)
        self.load_releng_templates()
        self.load_seeds()
        self.load_configuration_rows()

    def on_profile_selected(self, sender):
        ProjectManager.shared().change_stage_profile(project=self.project_directory, stage=self.stage, profile=sender.selected_item)

    def on_seed_selected(self, sender):
        ProjectManager.shared().change_stage_parent(project=self.project_directory, stage=self.stage, parent_id=sender.selected_item.id if sender.selected_item else None)

    def on_releng_template_selected(self, sender):
        ProjectManager.shared().change_stage_releng_template(project=self.project_directory, stage=self.stage, releng_template_name=sender.selected_item)
        self.load_profiles()
        self.load_configuration_rows()

    # Handle UI
    # --------------------------------------------------------------------------

    @Gtk.Template.Callback()
    def on_stage_name_activate(self, sender):
        new_name = self.stage_name_row.get_text()
        if new_name == self.stage.name:
            self.get_root().set_focus(None)
            return
        is_name_available = ProjectManager.shared().is_stage_name_available(project=self.project_directory, name=self.stage_name_row.get_text()) or self.stage_name_row.get_text() == self.stage.name
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
        is_name_available = ProjectManager.shared().is_stage_name_available(project=self.project_directory, name=self.stage_name_row.get_text()) or self.stage_name_row.get_text() == self.stage.name
        self.name_used_row.set_visible(not is_name_available)
        self.stage_name_row.remove_css_class("error")

    @Gtk.Template.Callback()
    def is_item_selectable(self, sender, item) -> bool:
        match sender:
            case self.profile_selection_row:
                return True
            case self.stage_seed_row:
                return True
            case self.stage_releng_template_row:
                return True
            case self.stage_target_row:
                return True
        return False

    @Gtk.Template.Callback()
    def is_item_usable(self, sender, item) -> bool:
        match sender:
            case self.profile_selection_row:
                return True
            case self.stage_seed_row:
                return True
            case self.stage_releng_template_row:
                return True
            case self.stage_target_row:
                return True
        return False

    # Helper functions
    # --------------------------------------------------------------------------

    def _get_descendant_ids(self) -> list[int]:
        project_stages_tree = self.project_directory.stages_tree()
        def find_node(nodes: list, target_id: int):
            for node in nodes:
                if node.value.id == target_id:
                    return node
                result = find_node(node.children, target_id)
                if result:
                    return result
            return None
        def collect_ids(node) -> list[int]:
            ids = []
            for child in node.children:
                ids.append(child.value.id)
                ids.extend(collect_ids(child))
            return ids
        root_node = find_node(project_stages_tree, self.stage.id)
        if not root_node:
            return []
        return collect_ids(root_node)


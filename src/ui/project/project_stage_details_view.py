from gi.repository import Gtk, Adw, GLib
from .project_directory import ProjectDirectory
from .project_stage import ProjectStage, load_catalyst_targets, load_releng_templates, load_catalyst_stage_arguments
from .project_manager import ProjectManager
from .git_directory import GitDirectoryEvent
from .project_stage import ProjectStageEvent
from .repository_list_view import ItemRow
from .project_stage_create_view import ProjectStageSeedSpecial
from .architecture import Architecture
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
    stage_releng_template_none_checkbox = Gtk.Template.Child()
    configuration_pref_group = Gtk.Template.Child()
    use_automatic_seed_row = Gtk.Template.Child()
    use_automatic_seed_checkbox = Gtk.Template.Child()
    use_none_seed_row = Gtk.Template.Child()
    use_none_seed_checkbox = Gtk.Template.Child()
    profile_selection_view = Gtk.Template.Child()

    def __init__(self, project_directory: ProjectDirectory, stage: ProjectStage, content_navigation_view: Adw.NavigationView | None = None):
        super().__init__()
        self.project_directory = project_directory
        self.stage = stage
        self.content_navigation_view = content_navigation_view
        self.connect("realize", self.on_realize)
#        print(self.project_directory.get_snapshot().load_profiles(arch=Architecture.amd64))

    def on_realize(self, widget):
        self.get_root().set_focus(None)
        self.monitor_information_changes()
        self.load_seeds()
        self.load_targets()
        self.load_profiles()
        self.setup_stage_details()
        self.load_configuration_rows()
        self.refresh_allow_download_seed()

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
            self.setup_stage_details()
        except Exception as e:
            print(f"Error renaming stage: {e}")
            self.stage_name_row.add_css_class("error")
            self.stage_name_row.grab_focus()

    @Gtk.Template.Callback()
    def on_stage_name_changed(self, sender):
        is_name_available = ProjectManager.shared().is_stage_name_available(project=self.project_directory, name=self.stage_name_row.get_text()) or self.stage_name_row.get_text() == self.stage.name
        self.name_used_row.set_visible(not is_name_available)
        self.stage_name_row.remove_css_class("error")

    def setup_stage_details(self):
        self.stage_name_row.set_text(self.stage.name)
        self.stage_target_row.set_visible(ProjectStageDetailsView.ALLOW_CHANGING_TARGET)
        self.stage_target_static_row.set_visible(not ProjectStageDetailsView.ALLOW_CHANGING_TARGET)
        self.stage_target_static_row.set_subtitle(self.stage.target_name)
        self.stage_target_row.set_subtitle(self.stage.target_name)
        self.stage_releng_template_row.set_subtitle(self.stage.releng_template_name or "(None)")
        self.load_releng_templates()

    def monitor_information_changes(self):
        self.stage.event_bus.subscribe(
            ProjectStageEvent.NAME_CHANGED,
            self._update_name
        )

    def _update_name(self, data):
        self._page.set_title(self.stage.name)

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

    def load_seeds(self):
        # Load project children IDs
        child_ids = self._get_descendant_ids()

        stages = self.project_directory.stages
        stage_rows = [
            StageSelectionRow(
                object=stage,
                title=stage.name,
                subtitle=stage.target_name,
                icon_name=None,
                group=[self.use_automatic_seed_checkbox],
                checked=(self.stage.parent_id == stage.id),
                on_selected=self.on_seed_selected
            )
            for stage in stages
            if stage != self.stage
            and not stage.id in child_ids
        ]
        for stage_row in stage_rows:
            self.stage_seed_row.add_row(stage_row)
        self.use_automatic_seed_checkbox.set_active(self.stage.parent_id==ProjectStage.DOWNLOAD_SEED_ID)
        self.use_none_seed_checkbox.set_active(self.stage.parent_id is None)
        self._update_stage_seed_row_subtitle()

    def load_targets(self):
        def worker():
            target_names = load_catalyst_targets(toolset=self.project_directory.get_toolset())
            group = []
            target_rows = [
                StageSelectionRow(
                    object=name,
                    title=name,
                    subtitle=None,
                    icon_name="ruler-cross-pen-svgrepo-com-symbolic",
                    group=group,
                    checked=(name==self.stage.target_name),
                    on_selected=self.on_target_type_selected
                )
                for name in target_names
            ]
            for target_row in target_rows:
                GLib.idle_add(self.stage_target_row.add_row, target_row)
        threading.Thread(target=worker, daemon=True).start()

    def load_profiles(self):
        self.profile_selection_view.set_static_list(
            sorted(
                self.project_directory.get_snapshot().load_profiles(arch=self.project_directory.get_architecture()),
                key=lambda profile: profile.path
            )
        )

    def load_releng_templates(self):
        if hasattr(self, 'template_rows'):
            for row in self.template_rows:
                self.stage_releng_template_row.remove(row)
        def worker():
            template_subpaths = load_releng_templates(
                releng_directory=self.project_directory.get_releng_directory(),
                stage_name=self.stage.target_name,
                architecture=self.project_directory.get_architecture()
            )
            group = [self.stage_releng_template_none_checkbox]
            self.template_rows = [
                StageSelectionRow(
                    object=subpath,
                    title=subpath,
                    subtitle=None,
                    icon_name="clapperboard-edit-svgrepo-com-symbolic",
                    group=group,
                    checked=(subpath==self.stage.releng_template_name),
                    on_selected=self.on_releng_template_selected
                )
                for subpath in template_subpaths
            ]
            for template_row in self.template_rows:
                GLib.idle_add(self.stage_releng_template_row.add_row, template_row)
        threading.Thread(target=worker, daemon=True).start()
        if not self.stage.releng_template_name:
            self.stage_releng_template_none_checkbox.set_active(True)
        self.stage_releng_template_row.set_subtitle(self.stage.releng_template_name or "(None)")

    def on_target_type_selected(self, button, value):
        if button.get_active():
            ProjectManager.shared().change_stage_target(project=self.project_directory, stage=self.stage, target_name=value)
            self.stage_target_static_row.set_subtitle(self.stage.target_name)
            self.stage_target_row.set_subtitle(self.stage.target_name)
            self.load_releng_templates()
            self.load_configuration_rows()
            self.refresh_allow_download_seed()

    @Gtk.Template.Callback()
    def on_releng_template_selected(self, button, value: str | None = None):
        if button.get_active():
            ProjectManager.shared().change_stage_releng_template(project=self.project_directory, stage=self.stage, releng_template_name=value)
            self.stage_releng_template_row.set_subtitle(self.stage.releng_template_name or "(None)")
            self.load_configuration_rows()

    def load_configuration_rows(self):
        supported_arguments = load_catalyst_stage_arguments(toolset=self.project_directory.get_toolset(), target_name=self.stage.target_name)
        for arg_name in sorted(supported_arguments.valid):
            is_required = arg_name in supported_arguments.required
            row = Adw.ActionRow(title=arg_name)
            self.configuration_pref_group.add(row)
            print(f"{arg_name}: {'required' if is_required else 'optional'}")

    @Gtk.Template.Callback()
    def on_use_automatic_seed_toggled(self, sender):
        if sender.get_active():
            ProjectManager.shared().change_stage_parent(project=self.project_directory, stage=self.stage, parent_id=ProjectStage.DOWNLOAD_SEED_ID)
            self._update_stage_seed_row_subtitle()
    @Gtk.Template.Callback()
    def on_use_none_seed_toggled(self, sender):
        if sender.get_active():
            ProjectManager.shared().change_stage_parent(project=self.project_directory, stage=self.stage, parent_id=None)
            self._update_stage_seed_row_subtitle()
    def on_seed_selected(self, sender, value):
        if sender.get_active():
            ProjectManager.shared().change_stage_parent(project=self.project_directory, stage=self.stage, parent_id=value.id)
            self._update_stage_seed_row_subtitle()

    def refresh_allow_download_seed(self):
        """Refresh use_automatic_seed_row availability and change default seed to none if needed."""
        can_select_download_seed = self.stage.target_name == 'stage1'
        self.use_automatic_seed_row.set_activatable(can_select_download_seed)
        self.use_automatic_seed_row.set_sensitive(can_select_download_seed)
        if self.stage.parent_id == ProjectStage.DOWNLOAD_SEED_ID and not can_select_download_seed:
            ProjectManager.shared().change_stage_parent(project=self.project_directory, stage=self.stage, parent_id=None)
            self.use_none_seed_checkbox.set_active(True)
            self._update_stage_seed_row_subtitle()

    def _update_stage_seed_row_subtitle(self):
        if self.stage.parent_id == None:
            self.stage_seed_row.set_subtitle('(None)')
        elif self.stage.parent_id == ProjectStage.DOWNLOAD_SEED_ID:
            self.stage_seed_row.set_subtitle('(Download)')
        else:
            matching_stage_name = next((stage.name for stage in self.project_directory.stages if stage.id == self.stage.parent_id), None)
            self.stage_seed_row.set_subtitle(matching_stage_name)

class StageSelectionRow(Adw.ActionRow):
    def __init__(self, object, title: str, subtitle: str | None, icon_name: str | None, group: list, checked: bool, on_selected):
        super().__init__(title=title, subtitle=subtitle)
        check_button = Gtk.CheckButton()
        check_button.set_active(checked)
        self.add_prefix(check_button)
        if group:
            check_button.set_group(group[0])
        check_button.connect("toggled", on_selected, object)
        group.append(check_button)
        self.set_activatable_widget(check_button)
        self.set_sensitive(True)
        if icon_name:
            self.set_icon_name(icon_name)


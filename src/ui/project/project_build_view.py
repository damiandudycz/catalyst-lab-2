from __future__ import annotations
from gi.repository import Gtk, Adw, GLib
from .project_directory import ProjectDirectory
from .project_build import StageBuildPlan, StageBuildMode, load_project_builds, project_builds_directory
from .project_build_process import ProjectBuild
from .root_helper_client import RootHelperClient
from .multistage_process import MultiStageProcessState
from .wizard_view import WizardView

@Gtk.Template(resource_path='/com/damiandudycz/CatalystLab/ui/project/project_build_view.ui')
class ProjectBuildView(Gtk.Box):
    """Wizard for building project stages. Parents of selected stages are reused from previous builds when possible,
    otherwise they are built too."""
    __gtype_name__ = "ProjectBuildView"

    # Main views:
    wizard_view = Gtk.Template.Child()
    # Setup view elements:
    stages_page = Gtk.Template.Child()
    summary_page = Gtk.Template.Child()
    stages_group = Gtk.Template.Child()
    build_order_group = Gtk.Template.Child()
    reused_group = Gtk.Template.Child()
    location_row = Gtk.Template.Child()

    def __init__(self, project_directory: ProjectDirectory, installation_in_progress: ProjectBuild | None = None, content_navigation_view: Adw.NavigationView | None = None):
        super().__init__()
        self.project_directory = project_directory
        self.installation_in_progress = installation_in_progress
        self.content_navigation_view = content_navigation_view
        self.selected_stage_ids: set = set()
        self.builds = load_project_builds(project_directory)
        self.rows: dict = {} # stage id -> (row, check_button)
        self.summary_rows: list = []
        self._updating = False
        self.connect("realize", self.on_realize)

    def on_realize(self, widget):
        self.wizard_view.content_navigation_view = self.content_navigation_view
        self.wizard_view._window = self._window
        self.wizard_view.set_installation(self.installation_in_progress)
        self.location_row.set_subtitle(GLib.markup_escape_text(project_builds_directory(self.project_directory)))
        self.load_stages()
        self.update_plan()

    # Loading stages
    # --------------------------------------------------------------------------

    def load_stages(self):
        for stage, depth in self._stages_in_tree_order():
            row = Adw.ActionRow(title=GLib.markup_escape_text(stage.name))
            check_button = Gtk.CheckButton(valign=Gtk.Align.CENTER, margin_start=depth * 24)
            check_button.connect("toggled", self.on_stage_toggled, stage)
            row.add_prefix(check_button)
            row.set_activatable_widget(check_button)
            self.stages_group.add(row)
            self.rows[stage.id] = (row, check_button)
        if not self.rows:
            self.stages_group.add(Adw.ActionRow(title="This project has no stages yet"))

    def _stages_in_tree_order(self) -> list[tuple]:
        result = []
        def visit(node, depth):
            result.append((node.value, depth))
            for child in node.children:
                visit(child, depth + 1)
        for root in self.project_directory.stages_tree():
            visit(root, 0)
        return result

    # Build plan
    # --------------------------------------------------------------------------

    def update_plan(self):
        self.plan = StageBuildPlan(self.project_directory, self.selected_stage_ids, builds=self.builds)
        self._updating = True
        for stage_id, (row, check_button) in self.rows.items():
            entry = self.plan.entries[stage_id]
            check_button.set_active(entry.is_built)
            # Stages required by selected ones can't be unchecked, they are needed as seed.
            check_button.set_sensitive(entry.mode != StageBuildMode.REQUIRED)
            row.set_subtitle(GLib.markup_escape_text(self._entry_description(entry)))
            if entry.mode == StageBuildMode.REQUIRED:
                row.add_css_class("warning")
            else:
                row.remove_css_class("warning")
        self._updating = False
        self._update_summary()
        self.wizard_view._refresh_buttons_state()

    def _update_summary(self):
        for group, row in self.summary_rows:
            group.remove(row)
        self.summary_rows = []
        def add(group, title: str, subtitle: str, icon: str):
            row = Adw.ActionRow(title=GLib.markup_escape_text(title), subtitle=GLib.markup_escape_text(subtitle), icon_name=icon)
            group.add(row)
            self.summary_rows.append((group, row))
        for index, stage in enumerate(self.plan.build_order(), start=1):
            entry = self.plan.entries[stage.id]
            add(self.build_order_group, f"{index}. {stage.name}", self._entry_description(entry), "sledgehammer-svgrepo-com-symbolic")
        reused = [entry for entry in self.plan.entries.values() if entry.mode == StageBuildMode.REUSE]
        for entry in reused:
            add(self.reused_group, entry.stage.name, f"Build from {_format_date(entry.reused_build.date)}", "archive-minimalistic-svgrepo-com-symbolic")
        self.reused_group.set_visible(bool(reused))

    def _entry_description(self, entry) -> str:
        names = ", ".join(stage.name for stage in entry.required_by)
        match entry.mode:
            case StageBuildMode.BUILD:
                return "Will be built"
            case StageBuildMode.REQUIRED:
                return f"Will be built, required by {names} ({self._no_build_reason(entry.stage)})"
            case StageBuildMode.REUSE:
                return f"Reusing build from {_format_date(entry.reused_build.date)} for {names}. Select to build again."
            case _:
                latest = self.plan.latest_build(entry.stage)
                return f"Last build: {_format_date(latest.date)}" if latest else self._no_build_reason(entry.stage).capitalize()

    def _no_build_reason(self, stage) -> str:
        attempt = self.plan.latest_attempt(stage)
        if attempt is None:
            return "not built yet"
        return f"last build {'failed' if attempt.status.value == 'failed' else 'not finished'}: {_format_date(attempt.date)}"

    # Handle UI
    # --------------------------------------------------------------------------

    def on_stage_toggled(self, check_button: Gtk.CheckButton, stage):
        if self._updating:
            return
        if check_button.get_active():
            self.selected_stage_ids.add(stage.id)
        else:
            self.selected_stage_ids.discard(stage.id)
        self.update_plan()

    @Gtk.Template.Callback()
    def on_select_all_clicked(self, button):
        self.selected_stage_ids = set(self.rows.keys())
        self.update_plan()

    @Gtk.Template.Callback()
    def on_clear_clicked(self, button):
        self.selected_stage_ids = set()
        self.update_plan()

    @Gtk.Template.Callback()
    def is_page_ready_to_continue(self, sender, page) -> bool:
        match page:
            case self.stages_page | self.summary_page:
                return bool(getattr(self, "plan", None) and self.plan.build_order())
        return True

    @Gtk.Template.Callback()
    def begin_installation(self, view):
        plan = self.plan
        def start(authorization_keeper):
            # Called from background thread. Keeper is released when this callback returns, retain it until build
            # is started on main thread and retains it by itself.
            if authorization_keeper:
                authorization_keeper.retain()
            GLib.idle_add(self._start_installation, plan, authorization_keeper)
        RootHelperClient.shared().authorize_and_run(name="Build stages", callback=start)

    def _start_installation(self, plan: StageBuildPlan, authorization_keeper):
        if authorization_keeper is None:
            return False # Authorization cancelled.
        try:
            installation_in_progress = ProjectBuild(project_directory=self.project_directory, plan=plan)
            installation_in_progress.start(authorization_keeper=authorization_keeper)
        finally:
            authorization_keeper.release()
        if installation_in_progress.status == MultiStageProcessState.SETUP:
            print("Failed to start build")
            return False
        self.wizard_view.set_installation(installation_in_progress)
        return False

def _format_date(date) -> str:
    return date.strftime("%Y-%m-%d %H:%M")

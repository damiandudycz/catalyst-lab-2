from __future__ import annotations
from gi.repository import Gtk, Adw, GLib
from .project_directory import ProjectDirectory
from .project_build import StageBuildPlan, StageBuildMode, load_project_builds, project_builds_directory
from .project_build_process import ProjectBuild
from .root_helper_client import RootHelperClient
from .multistage_process_execution_view import MultistageProcessExecutionView
from .multistage_process import MultiStageProcessState

class ProjectBuildView(Gtk.Box):
    """Selecting stages of project to build. Parents of selected stages are reused from previous builds when possible,
    otherwise they are built too."""

    def __init__(self, project_directory: ProjectDirectory, content_navigation_view: Adw.NavigationView | None = None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.project_directory = project_directory
        self.content_navigation_view = content_navigation_view
        self.selected_stage_ids: set = set()
        self.builds = load_project_builds(project_directory)
        self.rows: dict = {} # stage id -> (row, check_button)
        self._updating = False
        self._setup_view()
        self._update_plan()

    # --------------------------------------------------------------------------
    # View:

    def _setup_view(self):
        page = Adw.PreferencesPage()
        page.set_vexpand(True)
        self.append(page)
        # Stages:
        self.stages_group = Adw.PreferencesGroup(
            title="Stages",
            description="Select stages to build. Parent stages are reused from their latest build, or built too if they don't have one."
        )
        buttons = Gtk.Box(spacing=6)
        for label, handler in (("Select all", self._on_select_all), ("Clear", self._on_clear)):
            button = Gtk.Button(label=label, valign=Gtk.Align.CENTER)
            button.add_css_class("flat")
            button.connect("clicked", handler)
            buttons.append(button)
        self.stages_group.set_header_suffix(buttons)
        page.add(self.stages_group)
        for stage, depth in self._stages_in_tree_order():
            self._add_stage_row(stage=stage, depth=depth)
        if not self.rows:
            self.stages_group.add(Adw.ActionRow(title="This project has no stages yet"))
        # Summary:
        summary_group = Adw.PreferencesGroup(title="Build")
        self.order_row = Adw.ActionRow(title="Build order")
        self.order_row.set_subtitle_selectable(True)
        summary_group.add(self.order_row)
        location_row = Adw.ActionRow(title="Builds location", subtitle=GLib.markup_escape_text(project_builds_directory(self.project_directory)))
        location_row.set_subtitle_selectable(True)
        summary_group.add(location_row)
        page.add(summary_group)
        # Start:
        self.start_button = Gtk.Button(label="Start build", halign=Gtk.Align.CENTER, margin_top=12, margin_bottom=24)
        self.start_button.add_css_class("pill")
        self.start_button.add_css_class("suggested-action")
        self.start_button.connect("clicked", self._on_start_clicked)
        self.append(self.start_button)

    def _stages_in_tree_order(self) -> list[tuple]:
        result = []
        def visit(node, depth):
            result.append((node.value, depth))
            for child in node.children:
                visit(child, depth + 1)
        for root in self.project_directory.stages_tree():
            visit(root, 0)
        return result

    def _add_stage_row(self, stage, depth: int):
        row = Adw.ActionRow(title=GLib.markup_escape_text(stage.name))
        check_button = Gtk.CheckButton(valign=Gtk.Align.CENTER, margin_start=depth * 24)
        check_button.connect("toggled", self._on_stage_toggled, stage)
        row.add_prefix(check_button)
        row.set_activatable_widget(check_button)
        self.stages_group.add(row)
        self.rows[stage.id] = (row, check_button)

    # --------------------------------------------------------------------------
    # Selection:

    def _on_stage_toggled(self, check_button: Gtk.CheckButton, stage):
        if self._updating:
            return
        if check_button.get_active():
            self.selected_stage_ids.add(stage.id)
        else:
            self.selected_stage_ids.discard(stage.id)
        self._update_plan()

    def _on_select_all(self, button):
        self.selected_stage_ids = set(self.rows.keys())
        self._update_plan()

    def _on_clear(self, button):
        self.selected_stage_ids = set()
        self._update_plan()

    def _update_plan(self):
        self.plan = StageBuildPlan(self.project_directory, self.selected_stage_ids, builds=self.builds)
        self._updating = True
        for stage_id, (row, check_button) in self.rows.items():
            entry = self.plan.entries[stage_id]
            check_button.set_active(entry.is_built)
            # Stages required by selected ones can't be unchecked, they are needed as seed.
            check_button.set_sensitive(entry.mode != StageBuildMode.REQUIRED)
            row.set_subtitle(GLib.markup_escape_text(self._entry_description(entry)))
            for css_class in ("accent", "warning"):
                row.remove_css_class(css_class)
            if entry.mode == StageBuildMode.REQUIRED:
                row.add_css_class("warning")
        self._updating = False
        order = self.plan.build_order()
        self.order_row.set_subtitle(GLib.markup_escape_text(" → ".join(stage.name for stage in order) if order else "Select stages to build"))
        self.start_button.set_sensitive(bool(order))

    # --------------------------------------------------------------------------
    # Building:

    def _on_start_clicked(self, button):
        plan = self.plan
        self.start_button.set_sensitive(False)
        def start(authorization_keeper):
            # Called from background thread. Keeper is released when this callback returns, retain it until build
            # is started on main thread and retains it by itself.
            if authorization_keeper:
                authorization_keeper.retain()
            GLib.idle_add(self._start_build, plan, authorization_keeper)
        RootHelperClient.shared().authorize_and_run(name="Build stages", callback=start)

    def _start_build(self, plan: StageBuildPlan, authorization_keeper):
        self.start_button.set_sensitive(bool(plan.build_order()))
        if authorization_keeper is None:
            return False # Authorization cancelled.
        try:
            build = ProjectBuild(project_directory=self.project_directory, plan=plan)
            build.start(authorization_keeper=authorization_keeper)
        finally:
            authorization_keeper.release()
        if build.status == MultiStageProcessState.SETUP:
            print("Failed to start build")
            return False
        execution_view = MultistageProcessExecutionView()
        execution_view.set_multistage_process(multistage_process=build)
        self.content_navigation_view.push_view(execution_view, title=f"Building {self.project_directory.name}")
        return False

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

def _format_date(date) -> str:
    return date.strftime("%Y-%m-%d %H:%M")

from gi.repository import Gtk, Adw
from .app_section import AppSection
from .app_events import AppEvents, app_event_bus
from .repository import Repository

# ------------------------------------------------------------------------------
# Initial setup: sections needed before first project (environments, releng directories, snapshots), shown one after
# another with Next / Finish button. Header back button returns to previous step.

def initial_setup_steps() -> list[AppSection]:
    return [AppSection.EnvironmentsSection, AppSection.RelengSection, AppSection.SnapshotsSection]

class InitialSetupPage(Gtk.Box):
    """Section of initial setup step with bottom bar."""

    def __init__(self, content_navigation_view: Adw.NavigationView, step: int = 0):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.content_navigation_view = content_navigation_view
        self.step = step
        steps = initial_setup_steps()
        section_view = steps[step](content_navigation_view=content_navigation_view)
        section_view.set_vexpand(True)
        self.append(section_view)
        self.append(Gtk.Separator())
        self.append(self._bottom_bar(is_last=step == len(steps) - 1, steps_count=len(steps)))

    def _bottom_bar(self, is_last: bool, steps_count: int) -> Gtk.Widget:
        bar = Gtk.CenterBox()
        bar.set_margin_start(24)
        bar.set_margin_end(24)
        bar.set_margin_top(12)
        bar.set_margin_bottom(12)
        label = Gtk.Label(label=f"Step {self.step + 1} of {steps_count}")
        label.add_css_class("dimmed")
        bar.set_center_widget(label)
        button = Gtk.Button(label="Finish" if is_last else "Next")
        button.add_css_class("suggested-action")
        button.connect("clicked", self._on_finish_pressed if is_last else self._on_next_pressed)
        bar.set_end_widget(button)
        return bar

    @classmethod
    def push(cls, content_navigation_view: Adw.NavigationView, step: int = 0):
        section = initial_setup_steps()[step]
        content_navigation_view.push_view(cls(content_navigation_view, step), section.section_details.title)

    def _on_next_pressed(self, _):
        InitialSetupPage.push(self.content_navigation_view, self.step + 1)

    def _on_finish_pressed(self, _):
        Repository.Settings.value.initial_setup_done = True
        app_event_bus.emit(AppEvents.OPEN_APP_SECTION, AppSection.ProjectsSection)

from .multistage_process import (
    MultiStageProcess, MultiStageProcessStage,
    MultiStageProcessState, MultiStageProcessStageState,
    MultiStageProcessEvent, MultiStageProcessStageEvent,
)
from .repository import Repository

# ------------------------------------------------------------------------------
# Toolset installation.
# ------------------------------------------------------------------------------

class RelengInstallation(MultiStageProcess):
    """Handles the full releng directory installation lifecycle."""
    def __init__(self):
        super().__init__(title="Releng directory installation")

    def setup_stages(self):
        super().setup_stages()

    def complete_process(self, success: bool):
        if success:
            Repository.RELENG.value.append(self.releng_directory)


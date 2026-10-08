from __future__ import annotations
import os
from enum import Enum
from .architecture import Architecture
from .project_stage_arguments import StageArgumentDetails, StageArgumentOption

# ------------------------------------------------------------------------------
# portage_confdir is generated when building stage, by combining selected sources
# in fixed order (later ones overwrite files of earlier ones):
# releng portage configuration -> parent overlays -> stage overlay.

class StagePortageConfdirSource(Enum):
    RELENG = "releng"
    PARENT_OVERLAY = "parent_overlay"
    STAGE_OVERLAY = "stage_overlay"

    @property
    def display_name(self) -> str:
        match self:
            case StagePortageConfdirSource.RELENG: return "Releng configuration"
            case StagePortageConfdirSource.PARENT_OVERLAY: return "Parent overlay"
            case StagePortageConfdirSource.STAGE_OVERLAY: return "Stage overlay"

# Order in which sources are combined, and in which they are displayed.
PORTAGE_CONFDIR_SOURCES_ORDER = [
    StagePortageConfdirSource.RELENG,
    StagePortageConfdirSource.PARENT_OVERLAY,
    StagePortageConfdirSource.STAGE_OVERLAY,
]

def default_portage_confdir_sources(has_parent: bool) -> list[StagePortageConfdirSource]:
    """Sources used by new stages. Stage overlay is not enabled by default, to not create empty folders."""
    return [StagePortageConfdirSource.RELENG] + ([StagePortageConfdirSource.PARENT_OVERLAY] if has_parent else [])

def portage_confdir_sources(stage) -> list[StagePortageConfdirSource]:
    value = getattr(stage, StageArgumentDetails.portage_confdir.name, None)
    values = value if isinstance(value, list) else [value]
    return [source for source in PORTAGE_CONFDIR_SOURCES_ORDER if source in values]

# ------------------------------------------------------------------------------
# Overlays:

def stage_overlay_path(project_directory, stage) -> str:
    """Folder with stage portage configuration overlay, kept in stage directory of project."""
    return os.path.join(project_directory.stage_directory_path(name=stage.name), "portage")

def count_files(path: str) -> int:
    """Number of files in folder, including subfolders."""
    return sum(len(files) for _, _, files in os.walk(path))

def _parent_stage(project_directory, stage):
    parent_id = getattr(stage, StageArgumentDetails.parent.name, None)
    return next((item for item in project_directory.stages if item.id == parent_id), None) if parent_id else None

def overlay_stages(project_directory, stage, _visited: frozenset = frozenset()) -> list:
    """Stages whose overlays are used by stage, in order they are applied. Follows parent overlay through the chain
    and stops at stage that doesn't use parent overlay."""
    if stage.id in _visited:
        return [] # Parents loop.
    sources = portage_confdir_sources(stage)
    stages = []
    if StagePortageConfdirSource.PARENT_OVERLAY in sources:
        stages += parent_overlay_stages(project_directory, stage, _visited | {stage.id})
    if StagePortageConfdirSource.STAGE_OVERLAY in sources:
        stages.append(stage)
    return stages

def parent_overlay_stages(project_directory, stage, _visited: frozenset = frozenset()) -> list:
    """Stages whose overlays are inherited from parent (all overlays used by parent)."""
    parent = _parent_stage(project_directory, stage)
    return overlay_stages(project_directory, parent, _visited) if parent else []

# ------------------------------------------------------------------------------
# Releng configuration variant (releng/releases/portage/<variant>):

# Base variant for targets. Other targets use stages configuration.
_releng_portage_target_variants: dict[str, str] = {
    "livecd_stage1": "isos",
    "livecd_stage2": "isos",
    "diskimage_stage1": "diskimage",
    "diskimage_stage2": "diskimage",
}
# Architecture specific variants of base variant.
_releng_portage_arch_variants: dict[tuple[str, Architecture], str] = {
    ("isos", Architecture.x86): "isos-x86",
}
# Variants used when building under emulation (qemu).
_RELENG_PORTAGE_EMULATION_SUFFIX = "-qemu"
# Architectures that host can build natively, other than its own.
_natively_supported_architectures: dict[Architecture, set[Architecture]] = {
    Architecture.amd64: {Architecture.x86},
}

def is_emulated_build(architecture: Architecture) -> bool:
    """Stages of other architectures than host (and ones host supports natively) are built under qemu."""
    return architecture != Architecture.HOST and architecture not in _natively_supported_architectures.get(Architecture.HOST, set())

def releng_portage_variant(project_directory, stage) -> str | None:
    """Name of releng portage configuration directory matching stage target, architecture and emulation. Variants
    missing in used releng directory fall back to more generic ones."""
    architecture = project_directory.get_architecture()
    if architecture is None or not stage.target:
        return None
    base = _releng_portage_target_variants.get(stage.target, "stages")
    variant = _releng_portage_arch_variants.get((base, architecture), base)
    candidates = [variant, base]
    if is_emulated_build(architecture):
        candidates = [name + _RELENG_PORTAGE_EMULATION_SUFFIX for name in candidates] + candidates
    releng_directory = project_directory.get_releng_directory()
    if releng_directory is not None:
        portage_path = os.path.join(releng_directory.directory_path(), "releases", "portage")
        existing = next((name for name in candidates if os.path.isdir(os.path.join(portage_path, name))), None)
        if existing:
            return existing
    return candidates[0]

# ------------------------------------------------------------------------------
# Options displayed in UI:

def portage_confdir_options(project_directory, stage) -> list[StageArgumentOption]:
    argument = StageArgumentDetails.portage_confdir
    # Releng:
    variant = releng_portage_variant(project_directory, stage)
    releng = StageArgumentOption(
        raw=StagePortageConfdirSource.RELENG, value=StagePortageConfdirSource.RELENG, argument=argument,
        display=StagePortageConfdirSource.RELENG.display_name,
        subtitle=f"releases/portage/{variant}" if variant else "Releng configuration matching stage"
    )
    # Parent overlay:
    has_parent = _parent_stage(project_directory, stage) is not None
    parent_stages = parent_overlay_stages(project_directory, stage)
    parent_files = sum(count_files(stage_overlay_path(project_directory, item)) for item in parent_stages)
    parent_subtitle = (
        "Overlays used by parent stage" if not has_parent else
        f"{_files_text(parent_files)} from {', '.join(item.name for item in parent_stages)}" if parent_stages else
        "Parent stages don't use overlays"
    )
    parent = StageArgumentOption(
        raw=StagePortageConfdirSource.PARENT_OVERLAY, value=StagePortageConfdirSource.PARENT_OVERLAY, argument=argument,
        display=StagePortageConfdirSource.PARENT_OVERLAY.display_name, subtitle=parent_subtitle,
        unsupported=not has_parent
    )
    # Stage overlay:
    stage_files = count_files(stage_overlay_path(project_directory, stage))
    own = StageArgumentOption(
        raw=StagePortageConfdirSource.STAGE_OVERLAY, value=StagePortageConfdirSource.STAGE_OVERLAY, argument=argument,
        display=StagePortageConfdirSource.STAGE_OVERLAY.display_name,
        subtitle=f"{_files_text(stage_files)}, overwrite releng and parent files"
    )
    return [releng, parent, own]

def _files_text(count: int) -> str:
    return "1 file" if count == 1 else f"{count} files"

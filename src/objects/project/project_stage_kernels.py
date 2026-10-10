from __future__ import annotations
from dataclasses import dataclass
from typing import Any
from .project_stage_arguments import StageArgumentDetails

# ------------------------------------------------------------------------------
# Kernels of stages (livecd-stage2, stage4...): catalyst builds kernels listed in boot/kernel, each configured with
# boot/kernel/<name>/<setting> spec options (sources, distkernel, dracut_args...). Names of these options depend on
# names of kernels, so they are not arguments of catalyst targets. Stage stores own settings in boot_kernels:
# {kernel name: {setting: value}}. Settings not set by stage come from releng template of stage, so stages made from
# releng specs (eg. installation ISO) keep their kernel configuration. For example custom kernel from overlay:
#   boot/kernel: ps3
#   boot/kernel/ps3/distkernel: yes
#   boot/kernel/ps3/sources: sys-kernel/gentoo-kernel-ps3

BOOT_KERNELS_ATTRIBUTE = "boot_kernels"

@dataclass(frozen=True)
class KernelSetting:
    key: str # Name in spec, after boot/kernel/<name>/.
    title: str
    description: str
    is_list: bool = False

KERNEL_SETTINGS = [
    KernelSetting("sources", "Sources", "Kernel package, eg. sys-kernel/gentoo-kernel-bin or kernel from overlay"),
    KernelSetting("distkernel", "Distribution kernel", "yes: kernel package builds kernel and initramfs itself (gentoo-kernel)"),
    KernelSetting("config", "Configuration", "Kernel .config file, @STAGE_DIR@ and @REPO_DIR@ can be used"),
    KernelSetting("dracut_args", "Dracut arguments", "Arguments of dracut creating initramfs of distribution kernel"),
    KernelSetting("extraversion", "Extra version", "Added to kernel version"),
    KernelSetting("packages", "Packages", "Packages built with kernel (eg. external modules)", is_list=True),
    KernelSetting("use", "USE flags", "USE flags of kernel packages", is_list=True),
    KernelSetting("gk_kernargs", "Genkernel arguments", "Arguments of genkernel (kernels which aren't distribution kernels)"),
    KernelSetting("gk_action", "Genkernel action", "Genkernel action, eg. all"),
    KernelSetting("aliases", "Aliases", "Other names of kernel in boot menu", is_list=True),
    KernelSetting("console", "Console", "Kernel console options, eg. ttyS0,115200", is_list=True),
    KernelSetting("initramfs_overlay", "Initramfs overlay", "Folder with files added to initramfs"),
    KernelSetting("softlevel", "Soft level", "OpenRC runlevel used when booting"),
]
_SETTINGS_BY_KEY = {setting.key: setting for setting in KERNEL_SETTINGS}

def stage_supports_kernels(project_directory, stage) -> bool:
    """Target of stage has boot/kernel option."""
    from .project_stage import load_catalyst_stage_arguments_details
    try:
        arguments = load_catalyst_stage_arguments_details(toolset=project_directory.get_toolset(), target_name=stage.target)
    except Exception:
        return False
    return StageArgumentDetails.boot_kernel.value in arguments

def stage_kernel_names(project_directory, stage) -> list[str]:
    """Kernels built by stage (boot/kernel value, own or from releng template)."""
    from .project_stage_value_resolver import resolve_stage_argument
    value = resolve_stage_argument(project_directory, stage, StageArgumentDetails.boot_kernel.value)
    names = value if isinstance(value, list) else [value]
    return [name.strip() for name in names if isinstance(name, str) and name.strip()]

def own_kernel_settings(stage) -> dict[str, dict[str, Any]]:
    value = getattr(stage, BOOT_KERNELS_ATTRIBUTE, None)
    return value if isinstance(value, dict) else {}

def releng_kernel_setting(project_directory, stage, name: str, key: str):
    """Value of kernel setting in releng template of stage, None when it doesn't set it."""
    from .project_stage_value_resolver import load_stage_releng_template_values
    values = load_stage_releng_template_values(project_directory=project_directory, stage=stage) or {}
    value = values.get(f"{StageArgumentDetails.boot_kernel.value}/{name}/{key}")
    if value is not None and _SETTINGS_BY_KEY[key].is_list and not isinstance(value, list):
        value = [value]
    return value

def kernel_setting(project_directory, stage, name: str, key: str) -> tuple[Any, bool]:
    """Value of kernel setting used when building, and whether it's set by stage (otherwise from releng template)."""
    own = own_kernel_settings(stage).get(name, {}).get(key)
    if not _is_empty(own):
        return own, True
    return releng_kernel_setting(project_directory, stage, name, key), False

def set_kernel_setting(project_directory, stage, name: str, key: str, value):
    """Stores own value of kernel setting, empty value removes it (releng template value is used again)."""
    from .project_manager import ProjectManager
    settings = {kernel: dict(values) for kernel, values in own_kernel_settings(stage).items()}
    kernel = settings.setdefault(name, {})
    if _is_empty(value):
        kernel.pop(key, None)
    else:
        kernel[key] = value
    settings = {kernel: values for kernel, values in settings.items() if values}
    ProjectManager.shared().change_stage_argument(project=project_directory, stage=stage, argument=BOOT_KERNELS_ATTRIBUTE,
                                                  value=settings or None)

def kernel_spec_values(project_directory, stage) -> list[tuple[str, Any]]:
    """Spec options of kernels of stage, (option, value) in order of kernels and settings."""
    values = []
    for name in stage_kernel_names(project_directory, stage):
        for setting in KERNEL_SETTINGS:
            value, _ = kernel_setting(project_directory, stage, name, setting.key)
            if not _is_empty(value):
                values.append((f"{StageArgumentDetails.boot_kernel.value}/{name}/{setting.key}", value))
    return values

def kernel_settings_from_template(value, context: str) -> dict[str, dict[str, Any]]:
    """Kernel settings given by project template: {kernel name: {setting: text, boolean or list of texts}}."""
    from .project_template import TemplateError
    if not isinstance(value, dict):
        raise TemplateError(f"{context}: kernels must be table of kernel names")
    result = {}
    for name, settings in value.items():
        if not isinstance(settings, dict):
            raise TemplateError(f"{context}: settings of kernel {name} must be table")
        kernel = {}
        for key, item in settings.items():
            setting = _SETTINGS_BY_KEY.get(key)
            if setting is None:
                raise TemplateError(f"{context}: unknown setting {key} of kernel {name}")
            if isinstance(item, bool):
                item = "yes" if item else "no"
            if setting.is_list and isinstance(item, str):
                item = item.split()
            if setting.is_list:
                if not isinstance(item, list) or not all(isinstance(entry, str) for entry in item):
                    raise TemplateError(f"{context}: {key} of kernel {name} must be list of texts")
                item = [entry for entry in item if entry.strip()]
            elif not isinstance(item, (str, int, float)):
                raise TemplateError(f"{context}: {key} of kernel {name} must be text")
            else:
                item = str(item)
            if not _is_empty(item):
                kernel[key] = item
        if kernel:
            result[str(name)] = kernel
    return result

def merge_kernel_settings(existing: dict, added: dict) -> dict:
    """Settings of groups are added to settings of stage, kernel by kernel."""
    result = {name: dict(settings) for name, settings in existing.items()}
    for name, settings in added.items():
        result.setdefault(name, {}).update(settings)
    return result

def _is_empty(value) -> bool:
    return value is None or value == "" or value == []

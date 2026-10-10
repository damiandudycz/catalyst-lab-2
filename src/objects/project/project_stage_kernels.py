from __future__ import annotations
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any
from .project_stage_arguments import StageArgumentDetails, StageArgumentLevel
from .project_stage_automatic_option import StageAutomaticOption

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

class KernelSettingType(Enum):
    TEXT = "text"
    PATH = "path"       # File or folder, placeholders (@STAGE_DIR@, @REPO_DIR@...) can be used.
    LIST = "list"       # Values separated by spaces in spec, edited one per line.
    BOOLEAN = "boolean" # Catalyst checks only if option exists, so it's written only when enabled (as yes).

@dataclass(frozen=True)
class KernelSetting:
    key: str # Name in spec, after boot/kernel/<name>/.
    title: str
    description: str
    type: KernelSettingType = KernelSettingType.TEXT

    @property
    def is_list(self) -> bool:
        return self.type == KernelSettingType.LIST

# Accepted by catalyst, but its scripts don't use them (catalyst 4).
_NOT_USED = " Catalyst accepts this setting, but doesn't use it when building kernels."

KERNEL_SETTINGS = [
    KernelSetting("sources", "Kernel package", "Package with kernel sources, eg. sys-kernel/gentoo-kernel (distribution kernel) or "
                  "sys-kernel/gentoo-sources. Prebuilt kernels without sources (eg. raspberrypi-image) are installed as packages of stage instead."),
    KernelSetting("distkernel", "Distribution kernel", "Kernel package builds kernel itself (gentoo-kernel), catalyst creates its "
                  "initramfs with dracut. Otherwise kernel is built from sources with genkernel.", KernelSettingType.BOOLEAN),
    KernelSetting("config", "Configuration", "Kernel .config file, eg. @STAGE_DIR@/kernel.config. @STAGE_DIR@, @PROJECT_DIR@ and "
                  "@REPO_DIR@ can be used.", KernelSettingType.PATH),
    KernelSetting("dracut_args", "Dracut arguments", "Arguments of dracut creating initramfs of distribution kernel."),
    KernelSetting("extraversion", "Extra version", "Added to kernel version."),
    KernelSetting("packages", "Packages", "Packages built with kernel, eg. external modules.", KernelSettingType.LIST),
    KernelSetting("use", "USE flags", "USE flags of kernel packages.", KernelSettingType.LIST),
    KernelSetting("gk_kernargs", "Genkernel arguments", "Arguments of genkernel, for kernels which aren't distribution kernels."),
    KernelSetting("gk_action", "Genkernel action", "Genkernel action, eg. all." + _NOT_USED),
    KernelSetting("aliases", "Aliases", "Other names of kernel in boot menu." + _NOT_USED, KernelSettingType.LIST),
    KernelSetting("console", "Console", "Console of kernel in boot menu of ISO, eg. ttyS0,115200.", KernelSettingType.LIST),
    KernelSetting("initramfs_overlay", "Initramfs overlay", "Folder with files added to initramfs (genkernel). @STAGE_DIR@, "
                  "@PROJECT_DIR@ and @REPO_DIR@ can be used.", KernelSettingType.PATH),
    KernelSetting("softlevel", "Soft level", "OpenRC runlevel used when booting." + _NOT_USED),
]
_SETTINGS_BY_KEY = {setting.key: setting for setting in KERNEL_SETTINGS}

# Kernel settings shown in basic mode of stage details (others only in advanced mode), like STAGE_ARGUMENT_LEVELS.
KERNEL_SETTING_LEVELS: dict[str, StageArgumentLevel] = {
    "sources": StageArgumentLevel.BASIC,
    "distkernel": StageArgumentLevel.BASIC,
    "config": StageArgumentLevel.BASIC,
    "dracut_args": StageArgumentLevel.ADVANCED,
    "extraversion": StageArgumentLevel.ADVANCED,
    "packages": StageArgumentLevel.ADVANCED,
    "use": StageArgumentLevel.ADVANCED,
    "gk_kernargs": StageArgumentLevel.ADVANCED,
    "gk_action": StageArgumentLevel.ADVANCED,
    "aliases": StageArgumentLevel.ADVANCED,
    "console": StageArgumentLevel.ADVANCED,
    "initramfs_overlay": StageArgumentLevel.ADVANCED,
    "softlevel": StageArgumentLevel.ADVANCED,
}

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

def kernel_setting_enabled(value) -> bool:
    """Boolean setting is enabled (yes, like catalyst checks it)."""
    return isinstance(value, str) and value.strip().lower() == "yes"

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

# Names of kernels are used by catalyst in file names (/boot/<name>, boot/kernel/<name>/... options).
_KERNEL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

def kernel_name_error(name: str, names: list[str]) -> str | None:
    if not _KERNEL_NAME.match(name):
        return "Kernel name can contain letters, digits, dots, underscores and hyphens"
    if name in names:
        return f"Kernel {name} is already added"
    return None

def stage_inherits_kernels(stage) -> bool:
    """Stage uses kernels of its releng template (boot/kernel isn't set by stage)."""
    value = getattr(stage, StageArgumentDetails.boot_kernel.name, None)
    if isinstance(value, list) and len(value) == 1:
        value = value[0]
    return isinstance(value, StageAutomaticOption)

def releng_kernel_names(project_directory, stage) -> list[str]:
    """Kernels defined by releng template of stage."""
    from .project_stage_value_resolver import resolve_stage_argument
    value = resolve_stage_argument(project_directory, stage, StageArgumentDetails.boot_kernel.value,
                                   option=StageAutomaticOption.INHERIT_FROM_RELENG_TEMPLATE)
    names = value if isinstance(value, list) else [value]
    return [name.strip() for name in names if isinstance(name, str) and name.strip()]

def set_kernel_names(project_directory, stage, names: list[str] | None):
    """Kernels of stage (boot/kernel), None uses kernels of releng template again."""
    from .project_manager import ProjectManager
    value = StageAutomaticOption.INHERIT_FROM_RELENG_TEMPLATE if names is None else (names or None)
    ProjectManager.shared().change_stage_argument(project=project_directory, stage=stage,
                                                  argument=StageArgumentDetails.boot_kernel, value=value)

def remove_kernel(project_directory, stage, name: str):
    """Removes kernel from stage, with settings stage set for it."""
    from .project_manager import ProjectManager
    set_kernel_names(project_directory, stage, [kernel for kernel in stage_kernel_names(project_directory, stage) if kernel != name])
    settings = {kernel: values for kernel, values in own_kernel_settings(stage).items() if kernel != name}
    ProjectManager.shared().change_stage_argument(project=project_directory, stage=stage, argument=BOOT_KERNELS_ATTRIBUTE,
                                                  value=settings or None)

def new_kernel_name(names: list[str]) -> str:
    """Name of added kernel, not used by other kernels of stage (kernel, kernel-2...). It can be renamed."""
    name, number = "kernel", 2
    while name in names:
        name, number = f"kernel-{number}", number + 1
    return name

def rename_kernel(project_directory, stage, old_name: str, new_name: str):
    """Renames kernel, keeping its position and settings."""
    from .project_manager import ProjectManager
    set_kernel_names(project_directory, stage, [new_name if name == old_name else name
                                                for name in stage_kernel_names(project_directory, stage)])
    settings = {new_name if kernel == old_name else kernel: values for kernel, values in own_kernel_settings(stage).items()}
    ProjectManager.shared().change_stage_argument(project=project_directory, stage=stage, argument=BOOT_KERNELS_ATTRIBUTE,
                                                  value=settings or None)

def kernel_spec_values(project_directory, stage) -> list[tuple[str, Any]]:
    """Spec options of kernels of stage, (option, value) in order of kernels and settings."""
    values = []
    for name in stage_kernel_names(project_directory, stage):
        for setting in KERNEL_SETTINGS:
            value, _ = kernel_setting(project_directory, stage, name, setting.key)
            if setting.type == KernelSettingType.BOOLEAN:
                # Catalyst checks only if option exists, "no" would start preparing distribution kernel.
                value = "yes" if kernel_setting_enabled(value) else None
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

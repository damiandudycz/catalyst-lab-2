from __future__ import annotations
import platform
from enum import Enum

class Architecture(Enum):
    x86 = "x86"             # 32-bit Intel/AMD (i386, i686, etc.)
    amd64 = "amd64"         # 64-bit Intel/AMD (x86_64)
    arm = "arm"             # 32-bit ARM
    arm64 = "arm64"         # 64-bit ARM (AArch64)
    hppa = "hppa"           # HP PA-RISC
    ia64 = "ia64"           # Intel Itanium (IA-64)
    mips = "mips"           # MIPS (32-bit and 64-bit)
    ppc = "ppc"             # PowerPC 32-bit
    ppc64 = "ppc64"         # PowerPC 64-bit (big-endian)
    ppc64le = "ppc64le"     # PowerPC 64-bit (little-endian)
    riscv = "riscv"         # RISC-V (32-bit and 64-bit)
    sparc = "sparc"         # SPARC 64-bit
    alpha = "alpha"         # DEC Alpha
    m68k = "m68k"           # Motorola 68000 series
    loong = "loong"         # LoongArch (e.g., Loongson)
    s390 = "s390"           # IBM S/390 (31-bit)
    s390x = "s390x"         # IBM S/390 (64-bit)

    def releng_base_arch(self) -> RelengBaseArch | None:
        return RelengBaseArch[self.name]

class RelengBaseArch(Enum):
    """Basearch families used by releng templates. Value represents base directory in releng"""
    x86 = "x86"
    amd64 = "amd64"
    arm = "arm"
    arm64 = "arm64"
    hppa = "hppa"
    ia64 = "ia64"
    ppc = "ppc/ppc32"
    ppc64 = "ppc/ppc64"
    ppc64le = "ppc/ppc64le"
    s390 = "s390/s390"
    s390x = "s390/s390x"
    sparc = "sparc"
    # Missing: riscv, loong, m64k, alpha

# Mappings:
_arch_mapping = {
    'i386': Architecture.x86,
    'i486': Architecture.x86,
    'i586': Architecture.x86,
    'i686': Architecture.x86,
    'x86': Architecture.x86,
    'x86_64': Architecture.amd64,
    'amd64': Architecture.amd64,
    'aarch64': Architecture.arm64,
    'arm64': Architecture.arm64,
    'armv7l': Architecture.arm,
    'armv6l': Architecture.arm,
    'arm': Architecture.arm,
    'ppc': Architecture.ppc,
    'ppc64': Architecture.ppc64,
    'ppc64le': Architecture.ppc64le,
    'mips': Architecture.mips,
    'mips64': Architecture.mips,
    'sparc': Architecture.sparc,
    'sparc64': Architecture.sparc,
    'ia64': Architecture.ia64,
    'hppa': Architecture.hppa,
    'alpha': Architecture.alpha,
    'm68k': Architecture.m68k,
    'loongarch64': Architecture.loong,
    's390': Architecture.s390,
    's390x': Architecture.s390x,
    'riscv64': Architecture.riscv,
    'riscv32': Architecture.riscv,
}

# Set as a class-level constant.
Architecture.HOST = _arch_mapping.get(platform.machine().lower())

# Raise on unknown at definition time.
if Architecture.HOST is None:
    raise ValueError(f"Unsupported host architecture: {platform.machine()}")

class Emulation:
    qemu_interpreters: dict[Architecture, list[str]] = {
        Architecture.x86: ["qemu-system-i386"],
        Architecture.amd64: ["qemu-system-x86_64", "qemu-system-i386"],  # To run both 64-bit and 32-bit x86
        Architecture.arm: ["qemu-system-arm"],
        Architecture.arm64: ["qemu-system-aarch64"],
        Architecture.hppa: ["qemu-system-hppa"],
        Architecture.ia64: ["qemu-system-ia64"],
        Architecture.mips: ["qemu-system-mips", "qemu-system-mipsel", "qemu-system-mips64", "qemu-system-mips64el"],
        Architecture.ppc: ["qemu-system-ppc"],
        Architecture.ppc64: ["qemu-system-ppc64"],
        Architecture.ppc64le: ["qemu-system-ppc64le"],  # Often just part of qemu-system-ppc64 with option flags
        Architecture.riscv: ["qemu-system-riscv32", "qemu-system-riscv64"],
        Architecture.sparc: ["qemu-system-sparc", "qemu-system-sparc64"],
        Architecture.alpha: ["qemu-system-alpha"],
        Architecture.m68k: ["qemu-system-m68k"],
        Architecture.loong: ["qemu-system-loongarch64"],
        Architecture.s390: ["qemu-system-s390x"],  # s390 usually handled via s390x emulator
        Architecture.s390x: ["qemu-system-s390x"],
    }

    @staticmethod
    def get_all_qemu_systems() -> list[str]:
        systems = set()
        for interpreters in Emulation.qemu_interpreters.values():
            systems.update(interpreters)
        return sorted(systems)


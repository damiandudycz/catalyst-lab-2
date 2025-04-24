import os
import shutil
import subprocess
from pathlib import Path

@dataclass
class PatchSpec:
    source_path: str    # Path to the file to be patched
    patch_filename: str # The name of the patch file (located in resources/patches/)

def apply_patch_and_store_copy(runtime_env: RuntimeEnv, temp_dir: str, patch_spec: PatchSpec) -> str:
    """
    Applies a patch file (from project resources) to a copy of the original file and stores the result in <temp_dir>/<original_relative_path>.
    Returns the path to the patched file.
    """
    # Resolve the original file's full path using runtime_env
    original_path = runtime_env.resolve_path_for_host_access(patch_spec.source_path)

    # Path to the patch file in project resources
    patch_file_path = os.path.join("resources", "patches", patch_spec.patch_filename)

    if not os.path.isfile(original_path):
        raise FileNotFoundError(f"Original file not found: {original_path}")
    if not os.path.isfile(patch_file_path):
        raise FileNotFoundError(f"Patch file not found: {patch_file_path}")

    # Compute where to store the patched version
    relative_path = os.path.relpath(original_path, "/")  # Remove leading slash
    target_path = os.path.join(temp_dir, relative_path)
    target_dir = os.path.dirname(target_path)
    os.makedirs(target_dir, exist_ok=True)

    # Copy the original file to the temporary location
    shutil.copy2(original_path, target_path)

    # Apply the patch
    result = subprocess.run(
        ["patch", target_path, patch_file_path],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    if result.returncode != 0:
        raise RuntimeError(
            f"Failed to apply patch:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )

    return target_path

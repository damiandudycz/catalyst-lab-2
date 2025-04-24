import os
import shutil
import subprocess
from gi.repository import Gio
from pathlib import Path
from dataclasses import dataclass
from .environment import RuntimeEnv
import tempfile

@dataclass
class PatchSpec:
    source_path: str    # Path to the file to be patched
    patch_filename: str # The name of the patch file (located in resources/patches/)

def apply_patch_and_store_for_isolated_system(runtime_env: RuntimeEnv, storage_root: str, patch_spec: PatchSpec) -> str:
    """
    Applies a patch file (from project resources) to a copy of the original file
    and stores the result in <storage_root>/<original_relative_path>.
    Returns the path to the patched file.
    """
    # Resolve the original file's full path using runtime_env
    original_path = runtime_env.resolve_path_for_host_access(patch_spec.source_path)

    # Load patch content from GResource
    resource_path = f"/com/damiandudycz/CatalystLab/patches/{patch_spec.patch_filename}"

    try:
        gfile = Gio.File.new_for_uri(f"resource://{resource_path}")
        content_bytes = gfile.load_contents(None)[1]
        patch_contents = content_bytes.decode("utf-8")
    except Exception as e:
        raise FileNotFoundError(f"Failed to load patch from resource '{resource_path}': {e}")

    if not os.path.isfile(original_path):
        raise FileNotFoundError(f"Original file not found: {original_path}")

    # Compute target path for the patched version
    relative_path = os.path.relpath(patch_spec.source_path, "/")
    target_path = os.path.join(storage_root, relative_path)
    target_dir = os.path.dirname(target_path)
    os.makedirs(target_dir, exist_ok=True)

    # Copy the original file to the temporary location
    shutil.copy2(original_path, target_path)

    # Write the patch to a temp file
    with tempfile.NamedTemporaryFile(mode="w", suffix=".patch", delete=False) as temp_patch_file:
        temp_patch_file.write(patch_contents)
        temp_patch_path = temp_patch_file.name

    # Apply the patch
    result = subprocess.run(
        ["patch", target_path, temp_patch_path],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    # Clean up the temp patch file
    os.remove(temp_patch_path)

    if result.returncode != 0:
        raise RuntimeError(
            f"Failed to apply patch:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )

    return target_path


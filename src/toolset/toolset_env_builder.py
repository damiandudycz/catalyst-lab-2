# This class is a helper that builds toolset env from stage3 gentoo tarball.
# It downloads stage3 matching host architecture, extracts it in tmp directory,
# and emerges required tools like catalyst, qemu, releng.

import tempfile, requests
from urllib.parse import urlparse, unquote, ParseResult
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse, urljoin, ParseResult
from .architecture import Architecture
from concurrent.futures import ThreadPoolExecutor

class ToolsetEnvBuilder:

    @staticmethod
    def get_stage3_urls(completion_handler: Callable[[list[ParseResult] | Exception], None], architecture: Architecture = Architecture.HOST):
        base_url = f"https://distfiles.gentoo.org/releases/{architecture.value}/autobuilds/"
        file_url = urljoin(base_url, "latest-stage3.txt")
        with ThreadPoolExecutor() as executor:
            future = executor.submit(ToolsetEnvBuilder._fetch_stage3_urls, file_url, base_url, completion_handler)
            future.result()

    @staticmethod
    def _fetch_stage3_urls(file_url: str, base_url: str, completion_handler: Callable[[list[ParseResult] | Exception], None]) -> None:
        try:
            response = requests.get(file_url)
            response.raise_for_status()
            lines = response.text.splitlines()
            stage3_files = [
                line.split()[0] for line in lines
                if line.strip() and line[0].isdigit() and line.split()[0].endswith('.tar.xz')
            ]
            urls = [urlparse(urljoin(base_url, path)) for path in stage3_files]
            completion_handler(urls)

        except requests.RequestException as e:
            print(f"Error fetching data: {e}")
            completion_handler(e)





    def __init__(self):
        # TODO: Dynamic link based on architecture and latest available
        self.stage_url = urlparse("https://distfiles.gentoo.org/releases/amd64/autobuilds/20250420T121009Z/stage3-amd64-openrc-20250420T121009Z.tar.xz")

    def build_toolset(self):
        # TODO: Get env name set by user and check if it is free
        temp_dir_path = self._prepare_temp_dir()
        stage_tarball_path = self._download_stage_file(self.stage_url, temp_dir_path)

    def _prepare_temp_dir(self) -> Path:
        """Create and return a temporary working directory as a Path object."""
        temp_dir_path = Path(tempfile.mkdtemp(prefix="gentoo_toolset_setup_"))
        print(f"[*] Created temp directory at { temp_dir_path }")
        return temp_dir_path

#    def _get_stage_url() -> url:
#        pass

    def _download_stage_file(self, stage_url: ParseResult, temp_dir_path: Path) -> Path:
        """Download the stage3 tarball to the specified temp directory and return its path."""
        filename = Path(unquote(stage_url.path)).name or "stage3.tar.xz"
        stage_tarball_path = temp_dir_path / filename
        print(f"[*] Downloading stage3 tarball to {stage_tarball_path}")

        response = requests.get(stage_url.geturl(), stream=True)
        response.raise_for_status()

        with open(stage_tarball_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)

        print("[*] Download complete.")
        return stage_tarball_path

#    def _install_dependencies
#    def _create_squashfs
#    def _clean_temp

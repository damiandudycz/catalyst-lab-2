from dataclasses import dataclass, field
from datetime import datetime
from .repository import Serializable, Repository
from typing import Self
import os, shutil
import subprocess
import re
from collections import defaultdict
from .releng_directory import RelengDirectory

class RelengManager:
    _instance = None

    # Note: To get releng directories list use Repository.RELENG.value

    @classmethod
    def shared(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def refresh_releng_directories(self):
        releng_directories = Repository.RELENG.value
        # Detect missing releng directories and add them to repository if they contain releng
        releng_location = os.path.realpath(os.path.expanduser(Repository.SETTINGS.value.releng_location))
        # --- Step 1: Scan directory for existing releng directories ---
        if not os.path.isdir(releng_location):
            os.makedirs(releng_location, exist_ok=True)
        found_directories = {
            f for f in os.listdir(releng_location)
            if os.path.isdir(os.path.join(releng_location, f))
        }
        # --- Step 2: Check for new releng directories not in repository ---
        existing_dirnames = {releng_directory.sanitized_name() for releng_directory in releng_directories}
        missing_dirs = found_directories - existing_dirnames
        for dirname in missing_dirs:
            full_path = os.path.join(releng_location, dirname)
            self.add_releng_directory(RelengDirectory(name=dirname))
        # --- Step 3: Remove records for deleted releng directories ---
        deleted_releng_directories = [releng_directory for releng_directory in releng_directories if releng_directory.sanitized_name() not in found_directories]
        for releng_directory in deleted_releng_directories:
            self.remove_releng_directory(releng_directory)
        # Update statuses of all releng directories
        for releng_directory in Repository.RELENG.value:
            releng_directory.update_status()

    def add_releng_directory(self, releng_directory: RelengDirectory):
        # Remove existing releng directory with the same name
        Repository.RELENG.value = [s for s in Repository.RELENG.value if s.name != releng_directory.name]
        Repository.RELENG.value.append(releng_directory)

    def remove_releng_directory(self, releng_directory: RelengDirectory):
        print(f"RM: {releng_directory}")
        if os.path.isdir(releng_directory.directory_path()):
            shutil.rmtree(releng_directory.directory_path())
        Repository.RELENG.value.remove(releng_directory)


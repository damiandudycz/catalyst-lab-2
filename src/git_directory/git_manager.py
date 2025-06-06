import os, shutil, subprocess, re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Self
from collections import defaultdict
from abc import ABC, abstractmethod
from typing import Type
from .releng_directory import GitDirectory
from .repository import Serializable, Repository

class GitManager:
    _instances = {}

    # --------------------------------------------------------------------------
    # Overwrite:

    # Overwrite in subclassed
    @classmethod
    @abstractmethod
    def repository(cls) -> Repository:
        pass

    @classmethod
    @abstractmethod
    def item_class(cls) -> Type[GitDirectory]:
        pass

    # --------------------------------------------------------------------------

    @classmethod
    def shared(cls):
        """Separate instance for every subclass."""
        if cls not in cls._instances:
            cls._instances[cls] = cls()
        return cls._instances[cls]

    def refresh(self):
        repository = self.__class__.repository()
        git_directories = repository.value
        # Detect missing git directories and add them to repository.
        storage_location = self.__class__.item_class().base_location()
        # --- Step 1: Scan directory for existing directories ---
        if not os.path.isdir(storage_location):
            os.makedirs(storage_location, exist_ok=True)
        found_directories = {
            f for f in os.listdir(storage_location)
            if os.path.isdir(os.path.join(storage_location, f))
        }
        # --- Step 2: Check for new directories not in repository ---
        existing_dirnames = {
            directory.sanitized_name() for directory in git_directories
        }
        missing_dirs = found_directories - existing_dirnames
        for dirname in missing_dirs:
            full_path = os.path.join(storage_location, dirname)
            self.add_directory(self.__class__.item_class()(name=dirname))
        # --- Step 3: Remove records for deleted directories ---
        deleted_directories = [
            directory for directory in git_directories
            if directory.sanitized_name() not in found_directories
        ]
        for directory in deleted_directories:
            self.remove_directory(directory)
        # Update statuses of all directories
        for directory in repository.value:
            directory.update_status()

    def add_directory(self, directory: GitDirectory):
        # Remove existing directory with the same name
        self.__class__.repository().value = [
            s for s in self.__class__.repository().value
            if s.id != directory.id
        ]
        self.__class__.repository().value.append(directory)

    def remove_directory(self, directory: GitDirectory):
        if os.path.isdir(directory.directory_path()):
            shutil.rmtree(directory.directory_path())
        self.__class__.repository().value.remove(directory)

    def is_name_available(self, name: str) -> bool:
        directory_path = self.__class__.item_class().directory_path_for_name(
            name=name
        )
        return not os.path.exists(directory_path)

    def rename_directory(self, directory: GitDirectory, name: str):
        if not self.is_name_available(name=name):
            raise RuntimeError(f"GIT directory name {name} is not available")
        new_directory = self.__class__.item_class().directory_path_for_name(
            name=name
        )
        shutil.move(directory.directory_path(), new_directory)
        directory.name = name
        self.__class__.repository().save()


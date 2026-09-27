"""Rule-based directory organizer."""

from .organizer import (
    DEFAULT_IGNORE,
    FileOrganizer,
    Operation,
    OpStatus,
    OrganizePlan,
    OrganizeReport,
    UndoReport,
)
from .rules import PRESETS, RULE_FIELDS, Action, FileInfo, Rule, default_rules, load_rules, preset
from .watcher import FolderWatcher

__all__ = [
    "DEFAULT_IGNORE",
    "PRESETS",
    "RULE_FIELDS",
    "Action",
    "FileInfo",
    "FileOrganizer",
    "FolderWatcher",
    "OpStatus",
    "Operation",
    "OrganizePlan",
    "OrganizeReport",
    "Rule",
    "UndoReport",
    "default_rules",
    "load_rules",
    "preset",
]

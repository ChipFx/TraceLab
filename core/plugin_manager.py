"""
core/plugin_manager.py
Modular plugin system.
Plugins are Python files in the ./plugins/ folder.

A plugin file must define:
    PLUGIN_NAME = "My Plugin"
    PLUGIN_DESCRIPTION = "What it does"
    PLUGIN_VERSION = "1.0"
    PLUGIN_TYPE = "processor"  # or "importer", "exporter", "analyzer"

    def run(traces, context) -> result:
        ...

For "processor" plugins:
    Input:  list of TraceModel objects (copies)
    Output: list of modified TraceModel objects (same length, same names)
    Context: dict with keys like "sample_rate", "view_range", etc.

For "analyzer" plugins:
    Input:  list of TraceModel objects
    Output: None (show their own dialog/window)
    Context: same as above
"""

import os
import re
import sys
import importlib.util
import traceback
from typing import List, Dict, Callable, Optional, Any
from dataclasses import dataclass, field


def _normalise_group(raw: str) -> str:
    """Normalise a group name: collapse separator chars, then Title Case each word.
    e.g. "my_GROUP-name" → "My Group Name"."""
    s = re.sub(r'[-_\t /\\]+', ' ', raw).strip()
    return ' '.join(w.capitalize() for w in s.split()) if s else ""


def _group_canonical(display: str) -> str:
    """Lowercase canonical key for case-insensitive group deduplication."""
    return re.sub(r'\s+', ' ', display).strip().lower()


@dataclass
class PluginInfo:
    name: str
    description: str
    version: str
    plugin_type: str  # processor, analyzer, importer, exporter
    filepath: str
    module: Any
    run_fn: Callable
    group: str = ""   # normalised display group name; "" means Ungrouped


class PluginManager:
    """Discovers and manages plugins from the plugins/ directory."""

    PLUGIN_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "plugins")

    def __init__(self):
        self._plugins: Dict[str, PluginInfo] = {}
        self._load_errors: List[str] = []

    def discover(self):
        """Scan plugin directory (and one level of sub-folders) for plugins.
        Sub-folder name is used as the fallback group for plugins inside it."""
        self._plugins.clear()
        self._load_errors.clear()

        if not os.path.isdir(self.PLUGIN_DIR):
            os.makedirs(self.PLUGIN_DIR, exist_ok=True)

        # Top-level .py files — no folder group
        for fname in sorted(os.listdir(self.PLUGIN_DIR)):
            fpath = os.path.join(self.PLUGIN_DIR, fname)
            if os.path.isfile(fpath) and fname.endswith(".py") and not fname.startswith("_"):
                self._load_plugin(fpath, folder_group="")

        # One level of sub-directories — folder name is fallback group
        for entry in sorted(os.listdir(self.PLUGIN_DIR)):
            entry_path = os.path.join(self.PLUGIN_DIR, entry)
            if os.path.isdir(entry_path) and not entry.startswith("_"):
                for fname in sorted(os.listdir(entry_path)):
                    fpath = os.path.join(entry_path, fname)
                    if (os.path.isfile(fpath) and
                            fname.endswith(".py") and not fname.startswith("_")):
                        self._load_plugin(fpath, folder_group=entry)

    def _load_plugin(self, filepath: str, folder_group: str = ""):
        module_name = os.path.splitext(os.path.basename(filepath))[0]
        try:
            spec = importlib.util.spec_from_file_location(
                f"tracelab_plugin_{module_name}", filepath)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)

            # Validate required attributes
            required = ["PLUGIN_NAME", "PLUGIN_TYPE", "run"]
            for attr in required:
                if not hasattr(mod, attr):
                    self._load_errors.append(
                        f"{filepath}: missing '{attr}'")
                    return

            # Group: module attribute beats folder name.
            # Accepts either 'group' or 'PLUGIN_GROUP' attribute.
            raw_group = (getattr(mod, "PLUGIN_GROUP", None)
                         or getattr(mod, "group", None)
                         or folder_group
                         or "")
            normalised_group = _normalise_group(str(raw_group)) if raw_group else ""

            plugin = PluginInfo(
                name=getattr(mod, "PLUGIN_NAME", module_name),
                description=getattr(mod, "PLUGIN_DESCRIPTION", ""),
                version=getattr(mod, "PLUGIN_VERSION", "?"),
                plugin_type=getattr(mod, "PLUGIN_TYPE", "processor"),
                filepath=filepath,
                module=mod,
                run_fn=mod.run,
                group=normalised_group,
            )
            self._plugins[plugin.name] = plugin

        except Exception as e:
            self._load_errors.append(
                f"{filepath}: {e}\n{traceback.format_exc()}")

    def get_plugins(self, plugin_type: Optional[str] = None) -> List[PluginInfo]:
        plugins = list(self._plugins.values())
        if plugin_type:
            plugins = [p for p in plugins if p.plugin_type == plugin_type]
        return plugins

    def run_plugin(self, plugin_name: str, traces, context: dict):
        """Run a named plugin. Returns result or raises."""
        plugin = self._plugins.get(plugin_name)
        if not plugin:
            raise ValueError(f"Plugin '{plugin_name}' not found.")
        return plugin.run_fn(traces, context)

    def reload(self):
        """Reload all plugins from disk."""
        self.discover()

    @property
    def load_errors(self) -> List[str]:
        return self._load_errors.copy()

    @property
    def plugin_count(self) -> int:
        return len(self._plugins)

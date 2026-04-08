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
import sys
import importlib.util
import traceback
from typing import List, Dict, Callable, Optional, Any
from dataclasses import dataclass


@dataclass
class PluginInfo:
    name: str
    description: str
    version: str
    plugin_type: str  # processor, analyzer, importer, exporter
    filepath: str
    module: Any
    run_fn: Callable


class PluginManager:
    """Discovers and manages plugins from the plugins/ directory."""

    PLUGIN_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "plugins")

    def __init__(self):
        self._plugins: Dict[str, PluginInfo] = {}
        self._load_errors: List[str] = []

    def discover(self):
        """Scan plugin directory and load all valid plugins."""
        self._plugins.clear()
        self._load_errors.clear()

        if not os.path.isdir(self.PLUGIN_DIR):
            os.makedirs(self.PLUGIN_DIR, exist_ok=True)

        for fname in sorted(os.listdir(self.PLUGIN_DIR)):
            if not fname.endswith(".py") or fname.startswith("_"):
                continue
            fpath = os.path.join(self.PLUGIN_DIR, fname)
            self._load_plugin(fpath)

    def _load_plugin(self, filepath: str):
        module_name = os.path.splitext(os.path.basename(filepath))[0]
        try:
            spec = importlib.util.spec_from_file_location(
                f"pyscope_plugin_{module_name}", filepath)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)

            # Validate required attributes
            required = ["PLUGIN_NAME", "PLUGIN_TYPE", "run"]
            for attr in required:
                if not hasattr(mod, attr):
                    self._load_errors.append(
                        f"{filepath}: missing '{attr}'")
                    return

            plugin = PluginInfo(
                name=getattr(mod, "PLUGIN_NAME", module_name),
                description=getattr(mod, "PLUGIN_DESCRIPTION", ""),
                version=getattr(mod, "PLUGIN_VERSION", "?"),
                plugin_type=getattr(mod, "PLUGIN_TYPE", "processor"),
                filepath=filepath,
                module=mod,
                run_fn=mod.run,
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

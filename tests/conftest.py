"""Shared fixtures and the import helper for the test suite.

The tools here are executable scripts with a hyphen in the filename
(`correlate.py` is fine, `tcp-probe.py` is not importable). `load_script()`
loads them through importlib's file-path API instead.

Only pure functions are tested: no mocks, no running services, no measurement
data from a production system. Every input is created inside the test's own
tmp_path.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def load_script(relative_path: str) -> ModuleType:
    """Load a script with a hyphen in its name as a module.

    Args:
        relative_path: path relative to the repository root, e.g.
            "src/analyze/correlate.py".

    Returns:
        The loaded module.

    Raises:
        FileNotFoundError: if the script does not exist.
        ImportError: if importlib provides no loader.
    """
    path = REPO_ROOT / relative_path
    if not path.is_file():
        raise FileNotFoundError(f"script not found: {path}")

    modname = path.stem.replace("-", "_")
    spec = importlib.util.spec_from_file_location(modname, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"no loader for {path}")

    module = importlib.util.module_from_spec(spec)
    # Register before exec so the module's self-references work.
    sys.modules[modname] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def correlate() -> ModuleType:
    """src/analyze/correlate.py as an imported module.

    Session-scoped because loading is side-effect free: the script only defines
    constants and regexes at module level; main() hangs off __main__.
    """
    return load_script("src/analyze/correlate.py")


@pytest.fixture(scope="session")
def switch_probe() -> ModuleType:
    return load_script("src/switch/switch-probe.py")


@pytest.fixture(scope="session")
def fdb_probe() -> ModuleType:
    return load_script("src/switch/fdb-probe.py")

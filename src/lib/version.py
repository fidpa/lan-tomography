"""Read the project version from the VERSION file at the repository root.

One place. The tools this project came from carried a version number in every
file header, and after a while every one of them said something different from
the release it shipped in - which makes `--help` output worse than useless,
because it looks authoritative.
"""

from __future__ import annotations

from pathlib import Path


def read_version() -> str:
    """Project version, or "unknown" if the file is missing.

    Missing rather than fatal: a single tool copied out of the tree onto a probe
    should still run. It says "unknown", which is honest, instead of a number
    that was true when the file was written.
    """
    for base in (Path(__file__).resolve().parent.parent.parent,
                 Path(__file__).resolve().parent.parent,
                 Path(__file__).resolve().parent):
        candidate = base / "VERSION"
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8").strip() or "unknown"
    return "unknown"

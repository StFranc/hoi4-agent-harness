"""Checks on the repo itself.

Cheap guards for the kind of defect that hides in a warning nobody reads.
"""

from __future__ import annotations

import warnings
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SOURCES = sorted(p for p in (ROOT / "src").rglob("*.py")) + sorted((ROOT / "tests").rglob("*.py"))


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: p.name)
def test_no_file_compiles_with_a_syntax_warning(path: Path):
    """An invalid escape sequence is a warning today and an error in a future
    Python. It also usually means a regex or a Windows path lost a backslash,
    which is a real defect wearing a warning's clothes."""
    source = path.read_text(encoding="utf-8")
    with warnings.catch_warnings():
        warnings.simplefilter("error", SyntaxWarning)
        compile(source, str(path), "exec")

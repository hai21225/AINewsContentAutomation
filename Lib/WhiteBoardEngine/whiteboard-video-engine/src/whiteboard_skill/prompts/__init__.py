"""Prompt loading helpers."""

from __future__ import annotations

from importlib.resources import files


def load_prompt(name: str) -> str:
    """Load a packaged prompt template by filename."""

    return files(__package__).joinpath(name).read_text(encoding="utf-8")

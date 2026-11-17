"""Model policies the server can drive. Pick one with MOS_POLICY (default: maiui).

A policy is a module with NAME, HISTORY_N, system_prompt(app_labels),
build_messages(system, task, steps, png, history_n), parse_response(raw) -> (thinking,
canonical args) and format_response(thinking, canonical args). Canonical args are
MAI-UI's action format with 0..999 coordinates; everything downstream (pixel mapping,
safety check, loop guard, logs, SFT export) works on those.
"""
from __future__ import annotations

from . import guiowl, maiui

POLICIES = {maiui.NAME: maiui, guiowl.NAME: guiowl}


def get(name: str | None):
    key = (name or maiui.NAME).lower().replace("-", "").replace("_", "")
    if key not in POLICIES:
        raise ValueError(f"unknown policy {name!r}; choose one of {sorted(POLICIES)}")
    return POLICIES[key]

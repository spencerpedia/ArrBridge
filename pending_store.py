"""Persists in-flight download trackers so a restart doesn't orphan their embeds.

Each entry is a Discord message showing live progress plus what it's tracking.
Entries are written when tracking starts and on every status change, and removed
once tracking finishes - so whatever is left at startup was interrupted mid-flight
and gets resumed in on_ready.
"""
from __future__ import annotations

import json
import logging
import os

log = logging.getLogger("arrbridge")

# Relative to the working dir (/app in the image); mount a volume here to persist.
DATA_DIR = os.environ.get("DATA_DIR", "data")
PENDING_PATH = os.path.join(DATA_DIR, "pending.json")


def _key(kind: str, message_id: int) -> str:
    return f"{kind}:{message_id}"


def _load() -> dict[str, dict]:
    try:
        with open(PENDING_PATH, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        log.warning("Could not read %s - treating it as empty", PENDING_PATH, exc_info=True)
        return {}


def _save(entries: dict[str, dict]) -> None:
    # Write-then-rename so a crash mid-write can't leave a truncated file behind.
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp_path = PENDING_PATH + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(entries, f, indent=2)
        os.replace(tmp_path, PENDING_PATH)
    except OSError:
        log.warning("Could not write %s - trackers won't survive a restart", PENDING_PATH, exc_info=True)


def add(kind: str, message_id: int, channel_id: int, item_id: int, status: str | None) -> None:
    entries = _load()
    entries[_key(kind, message_id)] = {
        "kind": kind,
        "message_id": message_id,
        "channel_id": channel_id,
        "item_id": item_id,
        "status": status,
    }
    _save(entries)


def update(kind: str, message_id: int, **fields) -> None:
    entries = _load()
    entry = entries.get(_key(kind, message_id))
    if entry is None:
        return
    entry.update(fields)
    _save(entries)


def remove(kind: str, message_id: int) -> None:
    entries = _load()
    if entries.pop(_key(kind, message_id), None) is not None:
        _save(entries)


def pending(kind: str) -> list[dict]:
    return [entry for entry in _load().values() if entry.get("kind") == kind]

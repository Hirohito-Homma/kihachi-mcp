"""Index only the directories named by KIHACHI_SAMPLE_LIBRARY_PATHS.

The walk does not leave those directories, and it never writes the audio files.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from kihachi_mcp.gateway.samples.meta import (
    AUDIO_EXTENSIONS,
    filename_metadata,
    header_metadata,
)

_INDEX_VERSION = 1


def library_roots(environ: dict[str, str] | None = None) -> list[Path]:
    """Split the configured list on semicolons. Missing entries are not searched for."""
    env = os.environ if environ is None else environ
    raw = env.get("KIHACHI_SAMPLE_LIBRARY_PATHS", "")
    roots: list[Path] = []
    for part in raw.split(";"):
        text = part.strip().strip('"').strip("'")
        if text:
            roots.append(Path(text).expanduser())
    return roots


def index_path(environ: dict[str, str] | None = None) -> Path:
    env = os.environ if environ is None else environ
    override = env.get("KIHACHI_SAMPLE_INDEX_PATH", "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / "Library/Application Support/KIHACHI/sample-index.json"


def selection_path(environ: dict[str, str] | None = None) -> Path:
    env = os.environ if environ is None else environ
    override = env.get("KIHACHI_SAMPLE_SELECTION_PATH", "").strip()
    if override:
        return Path(override).expanduser()
    return index_path(env).with_name("sample-selections.json")


def manifest_dir(environ: dict[str, str] | None = None) -> Path:
    env = os.environ if environ is None else environ
    override = env.get("KIHACHI_SAMPLE_MANIFEST_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    return index_path(env).with_name("manifests")


def library_status() -> dict[str, Any]:
    roots = library_roots()
    present = [root for root in roots if root.is_dir()]
    index = _read_index()
    samples = index.get("samples") if isinstance(index.get("samples"), list) else []
    return {
        "ok": True,
        "configured": bool(roots),
        "root_count": len(roots),
        "readable_root_count": len(present),
        "missing_root_count": len(roots) - len(present),
        "indexed_count": len(samples),
        "index_present": bool(index),
        "extensions": sorted(AUDIO_EXTENSIONS),
    }


def index_library() -> dict[str, Any]:
    """Rebuild the local index. Unchanged files keep their previous record."""
    roots = library_roots()
    if not roots:
        return {
            "ok": False,
            "error": "rejected",
            "message": "KIHACHI_SAMPLE_LIBRARY_PATHS が空です。サンプルの場所は推測しません",
            "indexed_count": 0,
        }
    previous = {row["path"]: row for row in _samples() if isinstance(row, dict) and isinstance(row.get("path"), str)}
    records: list[dict[str, Any]] = []
    unreadable = 0
    for root in roots:
        if not root.is_dir():
            continue
        records.extend(_walk(root, previous))
    unreadable = sum(1 for row in records if row.get("header_read") is False)
    payload = {"version": _INDEX_VERSION, "samples": [row for row in records if row.get("header_read") is not False]}
    _write_json(index_path(), payload)
    return {
        "ok": True,
        "indexed_count": len(payload["samples"]),
        "unreadable_count": unreadable,
        "missing_root_count": sum(1 for root in roots if not root.is_dir()),
    }


def load_samples() -> list[dict[str, Any]]:
    return [row for row in _samples() if isinstance(row, dict)]


def find_sample(sample_id: str) -> dict[str, Any] | None:
    for row in load_samples():
        if row.get("sample_id") == sample_id:
            return row
    return None


_IMPORT_EXTENSIONS = frozenset({".wav", ".aif", ".aiff"})


def resolve_import_target(sample_id: str) -> dict[str, Any]:
    """Resolve one indexed sample to a file inside a configured root.

    The returned path is for the localhost Ableton payload only. Public tools
    must drop it. ``..``, a symlink, a missing file, and any extension other
    than wav/aif/aiff are rejected.
    """
    if not isinstance(sample_id, str) or not sample_id or any(mark in sample_id for mark in ("/", "\\", "..")):
        return {"ok": False, "error": "rejected"}
    record = find_sample(sample_id)
    if record is None:
        return {"ok": False, "error": "not_found"}
    raw = record.get("path")
    if not isinstance(raw, str) or not raw or "\x00" in raw:
        return {"ok": False, "error": "rejected"}
    candidate = Path(raw)
    if ".." in candidate.parts:
        return {"ok": False, "error": "rejected"}
    if candidate.suffix.lower() not in _IMPORT_EXTENSIONS:
        return {"ok": False, "error": "unsupported_format"}
    if candidate.is_symlink():
        return {"ok": False, "error": "rejected"}
    if not candidate.is_file():
        return {"ok": False, "error": "missing"}
    try:
        resolved = candidate.resolve()
    except OSError:
        return {"ok": False, "error": "missing"}
    if not resolved.is_file():
        return {"ok": False, "error": "missing"}
    for root in library_roots():
        if not root.is_dir() or root.is_symlink():
            continue
        try:
            resolved.relative_to(root.resolve())
        except (OSError, ValueError):
            continue
        return {"ok": True, "record": record, "path": resolved}
    return {"ok": False, "error": "outside_library"}


def resolve_inside_library(record: dict[str, Any]) -> Path | None:
    """Return the audio file only when it still sits inside a configured root."""
    raw = record.get("path")
    if not isinstance(raw, str) or not raw:
        return None
    candidate = Path(raw)
    if candidate.is_symlink() or not candidate.is_file():
        return None
    resolved = candidate.resolve()
    for root in library_roots():
        if not root.is_dir():
            continue
        try:
            resolved.relative_to(root.resolve())
        except ValueError:
            continue
        return resolved
    return None


def remember_selection(title: str, role: str, sample_id: str) -> None:
    path = selection_path()
    current = _read_json(path)
    songs = current.get("songs") if isinstance(current.get("songs"), dict) else {}
    chosen = songs.get(title) if isinstance(songs.get(title), dict) else {}
    chosen[role] = sample_id
    songs[title] = chosen
    _write_json(path, {"songs": songs})


def selections_for(title: str) -> dict[str, str]:
    current = _read_json(selection_path())
    songs = current.get("songs") if isinstance(current.get("songs"), dict) else {}
    chosen = songs.get(title)
    if not isinstance(chosen, dict):
        return {}
    return {str(role): str(sample_id) for role, sample_id in chosen.items() if isinstance(sample_id, str)}


def write_manifest(plan_id: str, manifest: dict[str, Any]) -> None:
    directory = manifest_dir()
    directory.mkdir(parents=True, exist_ok=True)
    _write_json(directory / f"{plan_id}.json", manifest)


def _walk(root: Path, previous: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    base = root.resolve()
    found: list[dict[str, Any]] = []
    for current, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = [name for name in dirnames if not name.startswith(".")]
        folder = Path(current)
        if folder.is_symlink():
            dirnames[:] = []
            continue
        try:
            folder.resolve().relative_to(base)
        except ValueError:
            dirnames[:] = []
            continue
        for name in filenames:
            if name.startswith("."):
                continue
            path = folder / name
            if path.suffix.lower() not in AUDIO_EXTENSIONS or path.is_symlink():
                continue
            try:
                resolved = path.resolve()
                resolved.relative_to(base)
            except (OSError, ValueError):
                continue
            found.append(_record(path, resolved, base, previous))
    return found


def _record(path: Path, resolved: Path, root: Path, previous: dict[str, dict[str, Any]]) -> dict[str, Any]:
    stat = path.stat()
    key = str(resolved)
    old = previous.get(key)
    if (
        isinstance(old, dict)
        and old.get("file_size") == stat.st_size
        and old.get("mtime_ns") == stat.st_mtime_ns
        and old.get("header_read") is not False
    ):
        old.setdefault("format", path.suffix.lower().lstrip("."))
        old.setdefault("metadata_source", "filename")
        if "internal_path" not in old:
            old["internal_path"] = resolved.relative_to(root).as_posix()
        return old
    header = header_metadata(path)
    parts = {part.lower() for part in resolved.parts}
    if "splice" in parts:
        source = "splice"
    elif "kihachi" in parts:
        source = "kihachi"
    else:
        source = "user"
    record: dict[str, Any] = {
        "sample_id": hashlib.sha256(key.encode("utf-8")).hexdigest()[:16],
        "path": key,
        "filename": path.name,
        "extension": path.suffix.lower(),
        "file_size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "duration_seconds": None if header is None else header["duration_seconds"],
        "sample_rate": None if header is None else header["sample_rate"],
        "channels": None if header is None else header["channels"],
        "filename_metadata": filename_metadata(path.name),
        "analysis": {"bpm": None, "key": None, "energy": None},
        "source": source,
        "format": path.suffix.lower().lstrip("."),
        "internal_path": resolved.relative_to(root).as_posix(),
        "metadata_source": "filename",
        "parent_folder": path.parent.name,
        "pack": None,
        "creator": None,
        "license": None,
        "header_read": header is not None,
    }
    return record


def _samples() -> list[Any]:
    index = _read_index()
    samples = index.get("samples")
    return samples if isinstance(samples, list) else []


def _read_index() -> dict[str, Any]:
    return _read_json(index_path())


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)

"""Cross-platform Live Set path handling.

This lives in the model layer because ``set_path`` feeds the Set fingerprint.
Live spells the same Set differently on macOS and Windows, and two spellings of
one Set must not hash to two different fingerprints.
"""

import re
from pathlib import PurePath, PureWindowsPath

_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:[\\/]")


def is_windows_path(path: str) -> bool:
    """Report whether a Live Set path uses Windows spelling."""
    return bool(_WINDOWS_DRIVE.match(path)) or path.startswith("\\\\")


def normalize_set_path(path: str) -> str:
    """Return a Set path in a form that is comparable across platforms.

    Backslashes become forward slashes and a drive letter is upper-cased,
    because Windows treats ``c:`` and ``C:`` as the same drive while a hash
    does not.
    """
    if not path:
        return ""
    if is_windows_path(path):
        text = PureWindowsPath(path).as_posix()
        if len(text) > 1 and text[1] == ":":
            return text[0].upper() + text[1:]
        return text
    return PurePath(path).as_posix()


def set_name_from_path(path: str) -> str:
    """Return the Set name implied by a Live Set path."""
    normalized = normalize_set_path(path)
    if not normalized:
        return ""
    name = PurePath(normalized).name
    return name[:-4] if name.lower().endswith(".als") else name

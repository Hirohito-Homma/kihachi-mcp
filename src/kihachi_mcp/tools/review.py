from typing import Any

from kihachi_mcp.api import Brain

_brain = Brain()


def review_songspec(songspec: dict[str, Any]) -> dict[str, Any]:
    """Review a SongSpec through the Brain review boundary."""
    return _brain.review_song(songspec).to_dict()

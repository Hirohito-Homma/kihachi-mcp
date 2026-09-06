from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class MemoryEntry:
    """A compact, JSON-safe record of a generated song decision."""

    genre: str
    songspec: dict[str, Any]
    review: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "genre": self.genre,
            "songspec": dict(self.songspec),
        }
        if self.review is not None:
            result["review"] = dict(self.review)
        return result

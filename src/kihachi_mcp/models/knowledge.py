from dataclasses import dataclass
from typing import Any, Self

from kihachi_mcp.models.genre_template import GenreTemplate


@dataclass(frozen=True)
class KnowledgeEntry:
    """One retrieved knowledge item with provenance. Reuses GenreTemplate."""

    id: str
    version: str
    source: str
    kind: str
    template: GenreTemplate | None = None

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("id is required")
        if not self.version.strip():
            raise ValueError("version is required")
        if not self.source.strip():
            raise ValueError("source is required")
        if not self.kind.strip():
            raise ValueError("kind is required")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """Create a KnowledgeEntry from a JSON-compatible dict."""
        raw_template = data.get("template")
        template = (
            GenreTemplate.from_dict(raw_template)
            if isinstance(raw_template, dict)
            else None
        )
        return cls(
            id=str(data.get("id") or ""),
            version=str(data.get("version") or ""),
            source=str(data.get("source") or ""),
            kind=str(data.get("kind") or ""),
            template=template,
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize this entry, including the structured template when present."""
        data: dict[str, Any] = {
            "id": self.id,
            "version": self.version,
            "source": self.source,
            "kind": self.kind,
        }
        if self.template is not None:
            data["template"] = self.template.to_dict()
        return data

    def to_provenance(self) -> dict[str, str]:
        """Return the trace identifiers without expanding the template."""
        return {
            "id": self.id,
            "version": self.version,
            "source": self.source,
            "kind": self.kind,
        }


@dataclass(frozen=True)
class KnowledgeContext:
    """Selected knowledge available to a generation step."""

    entries: tuple[KnowledgeEntry, ...] = ()

    @classmethod
    def from_entries(
        cls, entries: list[KnowledgeEntry] | tuple[KnowledgeEntry, ...]
    ) -> Self:
        """Build a context from an ordered collection of entries."""
        return cls(entries=tuple(entries))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """Create a KnowledgeContext from a JSON-compatible dict."""
        raw_entries = data.get("entries") or []
        return cls.from_entries(
            [
                KnowledgeEntry.from_dict(item)
                for item in raw_entries
                if isinstance(item, dict)
            ]
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize selected knowledge."""
        return {"entries": [entry.to_dict() for entry in self.entries]}

    def to_provenance(self) -> list[dict[str, str]]:
        """Return provenance for every selected entry."""
        return [entry.to_provenance() for entry in self.entries]

    def primary(self) -> KnowledgeEntry | None:
        """Return the first selected entry, if any."""
        return self.entries[0] if self.entries else None

    def template(self) -> GenreTemplate | None:
        """Return the primary genre template, if selected."""
        entry = self.primary()
        return entry.template if entry is not None else None

    def select(
        self,
        *,
        kind: str | None = None,
        source: str | None = None,
        ids: list[str] | None = None,
    ) -> Self:
        """Return a filtered copy of this context."""
        wanted_ids = {item.strip() for item in ids} if ids else None
        selected = [
            entry
            for entry in self.entries
            if (kind is None or entry.kind == kind)
            and (source is None or entry.source == source)
            and (wanted_ids is None or entry.id in wanted_ids)
        ]
        return type(self)(entries=tuple(selected))

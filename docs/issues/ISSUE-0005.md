# ISSUE-0005

# Knowledge-driven Generation

Status: Done

Priority: High

Milestone: v0.2

---

Generation retrieves structured genre knowledge before it builds a SongSpec
or an audio request. Knowledge stays a domain object (`GenreTemplate` inside
`KnowledgeEntry`), not a prompt string. Generators receive a
`GenerationContext` and do not read YAML themselves.

Unknown genres still raise for SongSpec generation. Audio generation still
works when no knowledge is found. Existing MCP JSON shapes stay unchanged.
`Brain.generate_from_knowledge()` is the Python API that returns provenance.

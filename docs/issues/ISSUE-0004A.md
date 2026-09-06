# ISSUE-0004A

# Knowledge Engine (Genre Only)

Status: Done

Priority: High

Milestone: v0.2

---

Genre knowledge lives in YAML. SongService asks the Knowledge Engine.
It never reads files. Unknown genres raise `UnknownGenreError`.

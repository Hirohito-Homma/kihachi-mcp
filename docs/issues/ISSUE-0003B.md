# ISSUE-0003B

# Service Layer Enhancement

Status: Done

Priority: High

Milestone: Sprint 1

Depends on: ISSUE-0003A

---

# Goal

Expand SongService, ProjectService, and ReviewService without changing
the public MCP API or putting logic in tools or domain models.

---

# Methods

SongService: generate, validate, default_tracks, default_arrangement,
estimate_duration, bars_from_minutes, minutes_from_bars, to_dict, from_dict

ProjectService: project_name, output_directory, metadata, created_at

ReviewService: score, comments, warnings, suggestions

# KIHACHI Brain

Brain MCP for the KIHACHI MUSIC AI Platform.

KIHACHI MUSIC AI is not an AI music generator.

It is an AI music production platform.

The Brain creates musical plans.

Ableton builds projects.

Google Lyria generates audio.

Memory learns from every song.

Review improves every iteration.

---

This repository is the Brain MCP. It produces SongSpec, Arrangement, and
ProjectPlan from structured genre knowledge. Audio rendering is delegated to
Google Lyria 3.5 through `generate_audio`. The Brain does not operate Ableton
or treat a successful Lyria receipt as Live authorization.

Primary audio generator: Google Lyria 3.5 API. ACE-Step is not a runtime path.

Public tools: `hello`, `generate_songspec`, `create_project_from_songspec`, `create_ableton_plan`, `create_midi_plan`, `prepare_ableton_handoff`, `request_live_execution`, `execute_live_request`, `generate_audio`, `review_songspec`, `remember_song`, `search_memory`, `orchestrate_song`.
See [API.md](API.md) and [VISION.md](VISION.md).

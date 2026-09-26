# KIHACHI Brain

Brain MCP for the KIHACHI MUSIC AI Platform.

KIHACHI MUSIC AI is not an AI music generator.

It is an AI music production platform.

The Brain creates musical plans.

KIHACHI builds them inside Ableton Live.

Memory learns from every song.

Review improves every iteration.

---

This repository is the Brain MCP plus the Ableton Live automation boundary. It
produces SongSpec, Arrangement, and ProjectPlan from structured genre
knowledge, then builds those plans as Session View patterns and Arrangement
View clips inside a running Ableton Live Set.

**Google Lyria は廃止済みです。** 音声生成プロバイダは存在せず、ACE-Step も runtime path ではありません。成果物は編集可能な Live Set です。

Max for Live は Live 系 Tool の必須要件です。

### Brain が守る境界

Brain 層から Live を直接操作しません。Brain は計画を作り、`services` が検証と実行を担い、MCP Tool は薄い JSON アダプターにとどまります。

- **Live への変更には人間承認が必要**です。Brain は承認を代行しません。
- **既存内容を自動上書きしません。** 利用者所有の要素は `conflicts` として返します。
- **自動再実行しません。**
- **読戻し成功まで完了扱いしません。** `verified` は読戻し一致のみを意味します。
- **CIでは実機接続しません。**

Public tools (17): `hello`, `generate_songspec`, `create_project_from_songspec`,
`create_ableton_plan`, `create_midi_plan`, `prepare_ableton_handoff`,
`inspect_live_state`, `live_device_catalogue`, `create_live_mutation_plan`,
`request_live_execution`, `execute_live_request`, `verify_live_execution`,
`expand_session_to_arrangement`, `review_songspec`, `remember_song`,
`search_memory`, `orchestrate_song`.

See [API.md](API.md), [ARCHITECTURE.md](ARCHITECTURE.md), [VISION.md](VISION.md),
and [adr/0006-ableton-live-automation.md](adr/0006-ableton-live-automation.md).

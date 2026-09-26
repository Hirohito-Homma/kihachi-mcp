# KIHACHI Brain Development Guide

## Philosophy

KIHACHI Brain is designed as a long-term maintainable platform.

Every implementation should prioritize

- readability
- maintainability
- backward compatibility
- testability

over short-term speed.

---

# Architecture

Always follow

```
FastMCP
    ↓
Tool
    ↓
Brain
    ↓
Service
    ↓
Domain Model
```

Business logic must never exist inside MCP tools.

---

# Repository Layout

```
src/
    kihachi_mcp/
        models/
        services/
        tools/
        shared/
```

Repository root should contain only

- README
- pyproject.toml
- server.py
- docs
- tests

---

# Git Workflow

Every issue

↓

feature branch

↓

review

↓

merge

Never implement directly on main.

Example

```
feature/issue-0003-service-layer
```

---

# Commit Style

Use Conventional Commits.

Examples

```
feat:
fix:
refactor:
docs:
test:
ci:
chore:
```

---

# Testing

Before every commit

Run

```bash
uv run pytest
uv run ruff check
uv run fastmcp list server.py
```

---

# Documentation

Every architectural decision

↓

ADR

Every implementation

↓

Issue

Every release

↓

CHANGELOG

---

# Code Style

Prefer

- dataclass
- type hints
- docstrings

Avoid

- global state
- duplicated logic
- circular imports

---

# Imports

Always use package imports.

Good

```python
from kihachi_mcp.models import SongSpec
```

Avoid

```python
from models import SongSpec
```

---

# Services

Services own business logic.

Examples

- SongService
- ProjectService
- ReviewService
- MemoryService
- AbletonService
- LiveStateInspector
- LiveMutationPlanner
- ApprovalGate
- LiveExecutionService
- ArrangementExpander

---

# MCP Tools

Tools should only

- receive arguments
- call services
- return results

Nothing else.

Live 系 Tool は例外を投げず、`status` と `error` を含む JSON を返します。

---

# Ableton Live 開発ルール

これらは仕様であり、実装時に必ず守ります。

- **Google Lyria は廃止済み**です。音声生成コードを追加しないでください。
- **Live への変更には人間承認が必要**です。承認トークンを Tool の戻り値へ含めないでください。
- **既存内容を自動上書きしない。** `[KIHACHI]` / `[K:` マーカーのない要素は読み取り以外行わないでください。
- **自動再実行しない。** リトライループを書かないでください。
- **読戻し成功まで完了扱いしない。** 読み戻せない操作に `verified` を返さないでください。
- **CIでは実機接続しない。** テストから Ableton Live や Max を起動しないでください。

## テストの二層構造

| 層 | 対象 | 実行環境 |
| --- | --- | --- |
| Python | fake transport による計画・承認・実行・読戻しの全経路 | `uv run pytest`（完全自動） |
| Max | メッセージ契約の一致（`tests/test_maxforlive_contract.py`） | `uv run pytest`（JSソース解析のみ） |
| 実機 | Live 上の実際の挙動 | [MANUAL_LIVE_TESTS.md](MANUAL_LIVE_TESTS.md)（人間が手動） |

`FakeLiveTransport` は Live のエミュレータではありません。fake transport で通ったことは、Max デバイスが実機で動くことを意味しません。

## 新しい Live 操作を追加するとき

1. `models/live_contract.py` に `op` 定数を追加し、`SUPPORTED_OPS` へ入れる。構造変更なら `STRUCTURAL_OPS` にも入れる。
2. planner で `expected_readback` と `preconditions` を必ず設定する。読み戻せない操作は追加しない。
3. `live_transport_fake.py` の `_mutate` と `_check_preconditions` に実装する。
4. `kihachi.device.js` の `applyOperation` と `checkPreconditions` に実装する。
5. `tests/test_maxforlive_contract.py` が Python と JS の一致を検証する。新しい precondition kind は `PRECONDITION_KINDS` へ追加する。
6. `docs/MAXFORLIVE.md` の操作表と precondition 表を更新する。
7. `docs/MANUAL_LIVE_TESTS.md` に実機確認項目を追加する。

## 秘密情報

Live 自動操作の秘密情報はループバック用セッショントークン1つだけです。

- 起動ごとに `secrets.token_urlsafe(32)` で生成します。
- 所有者のみ読める（`0600`）ハンドシェイクファイル経由で Max デバイスへ渡します。
- トランスポート層が付加します。planner、executor、MCP Tool はトークンを扱いません。
- ログ、エラーメッセージ、Tool 出力、Git に出しません。`tests/test_live_bridge.py` がこれを検証します。

外部公開ポートと固定トークンを作らないでください。bind は `127.0.0.1` のみです。

---

# Reviews

Every issue requires

- Architecture Review
- Code Review
- Testing Review
- Documentation Review

before merge.

---

# Definition of Done

- Ruff passes
- Pytest passes
- Documentation updated
- Tests updated
- Existing MCP tools unchanged
- Architecture respected

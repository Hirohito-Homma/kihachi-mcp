"""The MCP tools' request ids must never repeat across server processes.

The Max device remembers handled request ids for as long as it stays
loaded and refuses repeats as duplicates. Deterministic per-process
counters (the services' defaults) collide with the previous process on
its first call, so the tool session injects unique factories instead.
"""

from kihachi_mcp.tools.live import _LiveSession


def _ids(session: _LiveSession, count: int = 3) -> set[str]:
    found = {session.inspector._next_request_id() for _ in range(count)}
    found |= {session.executor._request_id_factory() for _ in range(count)}
    return found


def test_request_ids_are_unique_within_and_across_sessions() -> None:
    first = _ids(_LiveSession())
    second = _ids(_LiveSession())

    assert len(first) == 6
    assert len(second) == 6
    assert first.isdisjoint(second)


def test_request_ids_are_not_the_deterministic_service_defaults() -> None:
    found = _ids(_LiveSession())

    assert "inspect-000001" not in found
    assert "apply-000001" not in found
    assert all(not value.startswith(("inspect-", "apply-")) for value in found)

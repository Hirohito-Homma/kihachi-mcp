import pytest
from test_studio_app import _call

pytest_plugins = ["test_studio_app"]


@pytest.mark.parametrize(
    "route,method,payload",
    [
        ("/api/generate", "queue_generate", {"brief": "32小節の曲"}),
        ("/api/regenerate", "queue_regenerate", {"candidate_id": "example"}),
    ],
)
@pytest.mark.parametrize(
    "flag,expected", [(True, True), (False, False), ("false", False)]
)
def test_variety_flag_reaches_generation_only(
    studio, monkeypatch, route, method, payload, flag, expected
):
    port, runtime, live = studio
    received = []

    def queue(*args, **kwargs):
        received.append(kwargs)
        return {"ok": True, "started": True}

    monkeypatch.setattr(runtime, method, queue)
    status, result = _call(port, "POST", route, {**payload, "wide_variety": flag})
    assert status == 200 and result["ok"]
    assert received[0]["wide_variety"] is expected
    assert received[0]["confirmed_paid"] is False
    assert live.tracks == []

import pytest

_CALLER_SETTINGS = (
    "KIHACHI_AI_PROVIDER",
    "KIHACHI_OLLAMA_URL",
    "KIHACHI_OLLAMA_MODEL",
    "KIHACHI_IGNORE_SAVED_SETTINGS",
    "KIHACHI_PROJECT_DIR",
)


@pytest.fixture(autouse=True)
def _isolated_settings(tmp_path_factory, monkeypatch):
    """Keep the developer's saved Studio settings and shell overrides out of every test."""
    folder = tmp_path_factory.mktemp("settings")
    monkeypatch.setenv("KIHACHI_SETTINGS_FILE", str(folder / "settings.json"))
    for name in _CALLER_SETTINGS:
        monkeypatch.delenv(name, raising=False)

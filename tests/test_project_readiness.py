from __future__ import annotations

from pathlib import Path

from msc_sdk.project import ProjectClient


def test_project_readiness_uses_repo_env_without_exposing_secret(tmp_path: Path, monkeypatch):
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'demo'\n", encoding="utf-8")
    (tmp_path / "consortium").mkdir()
    (tmp_path / "results").mkdir()
    (tmp_path / ".env").write_text('OPENROUTER_API_KEY="not-a-real-key"\n', encoding="utf-8")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)

    readiness = ProjectClient(tmp_path).readiness()

    assert readiness["checks"]["openrouter_configured"] is True
    assert readiness["inspection"]["openrouter_source"] == "repo-env"
    assert "not-a-real-key" not in str(readiness)

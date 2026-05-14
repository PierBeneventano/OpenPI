"""Project-level SDK helpers for product-shell clients."""

from __future__ import annotations

import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ProjectInspection:
    """Read-only project and setup state."""

    cwd: str
    project_root: str | None
    results_dir: str | None
    python_executable: str
    openrouter_configured: bool
    writable_index_default: str
    openrouter_source: str | None = None
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ProjectClient:
    """Inspect local MSc project state without mutating it."""

    def __init__(self, start: str | Path | None = None):
        self.start = Path(start or Path.cwd()).resolve()

    def inspect(self) -> ProjectInspection:
        project_root = _find_project_root(self.start)
        results_dir = _find_results_dir(self.start, project_root)
        warnings: list[str] = []
        if project_root is None:
            warnings.append("project_root_not_found")
        if results_dir is None:
            warnings.append("results_dir_not_found")
        _env, sources = _resolved_runtime_env(project_root, self.start)
        return ProjectInspection(
            cwd=str(self.start),
            project_root=str(project_root) if project_root else None,
            results_dir=str(results_dir) if results_dir else None,
            python_executable=sys.executable,
            openrouter_configured=bool(_env.get("OPENROUTER_API_KEY")),
            writable_index_default=".msc_index",
            openrouter_source=sources.get("OPENROUTER_API_KEY"),
            warnings=warnings,
        )

    def readiness(self) -> dict[str, Any]:
        inspection = self.inspect()
        checks = {
            "project_root": inspection.project_root is not None,
            "python": bool(inspection.python_executable),
            "openrouter_configured": inspection.openrouter_configured,
            "results_dir": inspection.results_dir is not None,
        }
        return {
            "ok": checks["project_root"] and checks["python"],
            "checks": checks,
            "inspection": inspection.to_dict(),
        }


def inspect_project(start: str | Path | None = None) -> ProjectInspection:
    """Inspect a project from a starting path."""
    return ProjectClient(start).inspect()


def project_readiness(start: str | Path | None = None) -> dict[str, Any]:
    """Return structured readiness checks for machine clients."""
    return ProjectClient(start).readiness()


def _find_project_root(start: Path) -> Path | None:
    for candidate in (start, *start.parents):
        if (candidate / "pyproject.toml").is_file() and (candidate / "consortium").is_dir():
            return candidate
        if (candidate / "scripts" / "campaign_cli.py").is_file() and (candidate / "consortium").is_dir():
            return candidate
    return None


def _find_results_dir(start: Path, project_root: Path | None) -> Path | None:
    candidates = [
        start / "results",
        start.parent / "results",
        project_root / "results" if project_root else None,
    ]
    seen: set[Path] = set()
    for candidate in candidates:
        if candidate is None:
            continue
        resolved = candidate.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        if resolved.is_dir():
            return resolved
    return None


def _resolved_runtime_env(project_root: Path | None, start: Path) -> tuple[dict[str, str], dict[str, str]]:
    """Resolve non-secret runtime env availability for read-only project checks.

    This intentionally mirrors the CLI's shell/config/repo environment layering
    without importing CLI modules into the SDK. Values are returned for internal
    checks only; callers should expose sources, never secret values.
    """

    env = dict(os.environ)
    sources = {key: "shell" for key, value in env.items() if value}
    protected = set(sources)

    if project_root is not None and _should_use_repo_env(project_root, start):
        for key, value in _load_env_file(project_root / ".env").items():
            if not env.get(key):
                env[key] = value
                sources[key] = "repo-env"

    for key, value in _load_env_file(Path.home() / ".msc" / ".env").items():
        if key in protected:
            continue
        env[key] = value
        sources[key] = "config-dir"

    return env, sources


def _should_use_repo_env(project_root: Path, start: Path) -> bool:
    override = os.getenv("CONSORTIUM_USE_REPO_ENV", "").strip().lower()
    if override in {"1", "true", "yes", "on"}:
        return True
    if override in {"0", "false", "no", "off"}:
        return False
    try:
        current = start.resolve()
        root = project_root.resolve()
    except OSError:
        return False
    return current == root or root in current.parents


def _load_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _sep, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and value:
            values[key] = value
    return values

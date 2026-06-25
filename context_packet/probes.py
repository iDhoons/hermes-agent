"""Read-only probe runner.

Every probe is read-only by construction. Only two command shapes are ever
executed as a subprocess, both behind an allowlist:

  * ``systemctl --user is-active <unit>``
  * ``curl`` GET against a loopback ``.../health`` URL

File reads (``file_read``, ``file_staleness``, ``json_view``) are pure Python.
``command_plan`` probes are *documented but never executed* - they show the
read-only command a later phase would run, and always return ``skipped``.

The executor is injectable so tests run without touching the network or
systemd, and so a caller can swap in a stricter runner.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

from .models import ProbeResult


def _is_loopback_health(url: str) -> bool:
    """True only for an http(s) GET against loopback whose path is a health check."""
    parts = urlsplit(url)
    return (parts.scheme in {"http", "https"}
            and parts.hostname in {"127.0.0.1", "localhost", "::1"}
            and "health" in (parts.path or ""))


# (argv0, predicate) pairs. A command may only run if some entry matches.
# Predicates are structural (exact position / parsed host), not substring, so a
# stray verb cannot smuggle through.
_ALLOWED: list[tuple[str, Callable[[list[str]], bool]]] = [
    ("systemctl", lambda a: a[1:3] == ["--user", "is-active"]),
    ("curl", lambda a: any(_is_loopback_health(u) for u in a if u.startswith("http"))),
]

FRESH_DAYS = 7
AGING_DAYS = 30


class ProbeError(RuntimeError):
    """Raised when a probe asks to run a non-allowlisted command."""


def _allowed(argv: list[str]) -> bool:
    if not argv:
        return False
    name = Path(argv[0]).name
    return any(name == a0 and pred(argv) for a0, pred in _ALLOWED)


def _default_executor(argv: list[str], timeout: float) -> tuple[int, str, str]:
    if not _allowed(argv):
        raise ProbeError(f"refused non-allowlisted command: {argv!r}")
    proc = subprocess.run(  # noqa: S603 - argv is allowlisted, read-only
        argv, capture_output=True, text=True, timeout=timeout,
    )
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


def _classify_staleness(days: int) -> str:
    if days < FRESH_DAYS:
        return "fresh"
    if days < AGING_DAYS:
        return "aging"
    return "stale"


class ProbeRunner:
    """Runs probe specs and returns :class:`ProbeResult` objects."""

    def __init__(
        self,
        *,
        executor: Callable[[list[str], float], tuple[int, str, str]] | None = None,
        now: datetime | None = None,
        timeout: float = 4.0,
    ) -> None:
        self._exec = executor or _default_executor
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None:  # treat a naive clock as UTC, never crash on subtract
            now = now.replace(tzinfo=timezone.utc)
        self._now = now
        self._timeout = timeout

    # -- helpers ---------------------------------------------------------

    def _stamp(self) -> str:
        return self._now.isoformat()

    def _file_age_days(self, path: Path) -> int:
        mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        return max(0, (self._now - mtime).days)

    # -- dispatch --------------------------------------------------------

    def run(self, spec: dict) -> ProbeResult:
        kind = spec.get("kind")
        handler = getattr(self, f"_probe_{kind}", None)
        if handler is None:
            return ProbeResult(
                name=spec.get("name", "?"),
                status="failed",
                result_summary=f"unknown probe kind: {kind}",
                evidence="",
                observed_at=self._stamp(),
            )
        try:
            return handler(spec)
        except Exception as exc:  # one bad probe must not crash the whole packet
            return ProbeResult(
                name=spec.get("name", "?"),
                status="failed",
                result_summary=f"probe error: {type(exc).__name__}: {exc}",
                evidence="",
                observed_at=self._stamp(),
            )

    # -- probe kinds -----------------------------------------------------

    def _probe_file_staleness(self, spec: dict) -> ProbeResult:
        name, path = spec["name"], Path(spec["path"])
        if not path.exists():
            return ProbeResult(name, "failed", "file is missing",
                               f"{path} not found", self._stamp())
        days = self._file_age_days(path)
        updated = datetime.fromtimestamp(
            path.stat().st_mtime, tz=timezone.utc).strftime("%Y-%m-%d")
        cls = _classify_staleness(days)
        return ProbeResult(
            name, "ok", f"{cls}; last modified {days}d ago ({updated})",
            str(path), self._stamp(),
        )

    def _probe_file_read(self, spec: dict) -> ProbeResult:
        name, path = spec["name"], Path(spec["path"])
        n = int(spec.get("summary_lines", 3))
        if not path.exists():
            return ProbeResult(name, "failed", "file is missing",
                               f"{path} not found", self._stamp())
        text = path.read_text(encoding="utf-8", errors="replace")
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if not lines:
            return ProbeResult(name, "skipped", "file is empty / template only",
                               str(path), self._stamp())
        summary = " / ".join(lines[:n])
        return ProbeResult(name, "ok", summary[:280], str(path), self._stamp())

    def _probe_json_view(self, spec: dict) -> ProbeResult:
        name, path = spec["name"], Path(spec["path"])
        fields = spec.get("fields", [])
        if not path.exists():
            return ProbeResult(name, "failed", "view is missing",
                               f"{path} not found", self._stamp())
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            return ProbeResult(name, "failed", f"invalid json: {exc}",
                               str(path), self._stamp())
        if not isinstance(data, dict):
            return ProbeResult(
                name, "failed",
                f"json root is {type(data).__name__}, not an object",
                str(path), self._stamp())
        picked = {k: data.get(k) for k in fields} if fields else {}
        summary = ", ".join(f"{k}={v}" for k, v in picked.items()) or "parsed ok"
        return ProbeResult(name, "ok", summary, str(path), self._stamp())

    def _probe_systemctl_active(self, spec: dict) -> ProbeResult:
        name = spec["name"]
        units = spec.get("units", [])
        if not units:  # "checked zero services -> ok" would be a false green
            return ProbeResult(name, "skipped", "no units configured",
                               "systemctl --user is-active", self._stamp())
        states: list[str] = []
        overall = "ok"
        for unit in units:
            try:
                rc, out, _ = self._exec(
                    ["systemctl", "--user", "is-active", unit], self._timeout)
            except (ProbeError, subprocess.SubprocessError, OSError) as exc:
                states.append(f"{unit}=error")
                overall = "failed"
                continue
            state = out or ("active" if rc == 0 else "inactive")
            states.append(f"{unit}={state}")
            if rc != 0:
                overall = "failed"
        return ProbeResult(name, overall, ", ".join(states),
                           "systemctl --user is-active", self._stamp())

    def _probe_http_health(self, spec: dict) -> ProbeResult:
        name, url = spec["name"], spec["url"]
        if shutil.which("curl") is None:
            return ProbeResult(name, "skipped", "curl unavailable", url,
                               self._stamp())
        argv = ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
                "--max-time", str(int(self._timeout)), url]
        try:
            _, code, _ = self._exec(argv, self._timeout + 1)
        except (ProbeError, subprocess.SubprocessError, OSError) as exc:
            return ProbeResult(name, "failed", f"probe error: {exc}", url,
                               self._stamp())
        status = "ok" if code == "200" else "failed"
        return ProbeResult(name, status, f"HTTP {code or '000'}", url,
                           self._stamp())

    def _probe_command_plan(self, spec: dict) -> ProbeResult:
        # Documented, NEVER executed in read-only Phase 0.
        return ProbeResult(
            spec["name"], "skipped",
            spec.get("why", "documented read-only command; not executed"),
            spec.get("command", ""), self._stamp(),
        )

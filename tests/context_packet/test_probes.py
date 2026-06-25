"""Probe runner: read-only behaviour, allowlist, and each probe kind."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest

from context_packet.probes import ProbeError, ProbeRunner, _allowed


@pytest.fixture
def now():
    return datetime(2026, 6, 25, 12, 0, tzinfo=timezone.utc)


def test_allowlist_accepts_only_readonly_shapes():
    assert _allowed(["systemctl", "--user", "is-active", "hermes"])
    assert _allowed(["curl", "-s", "http://127.0.0.1:3101/api/health"])
    # writes / arbitrary commands are refused
    assert not _allowed(["systemctl", "--user", "restart", "hermes"])
    assert not _allowed(["curl", "http://example.com/data"])
    assert not _allowed(["rm", "-rf", "/"])
    assert not _allowed([])


def test_default_executor_refuses_non_allowlisted(now):
    runner = ProbeRunner(now=now)
    spec = {"name": "x", "kind": "systemctl_active", "units": ["hermes"]}
    # systemctl is allowlisted for is-active; force a refusal via http with a
    # bad url by going through the raw executor instead.
    from context_packet.probes import _default_executor
    with pytest.raises(ProbeError):
        _default_executor(["systemctl", "--user", "restart", "hermes"], 1.0)


def test_file_staleness_fresh_and_missing(tmp_path, now):
    f = tmp_path / "a.md"
    f.write_text("hi")
    os.utime(f, (now.timestamp(), now.timestamp()))
    runner = ProbeRunner(now=now)
    r = runner.run({"name": "s", "kind": "file_staleness", "path": str(f)})
    assert r.status == "ok" and "fresh" in r.result_summary

    missing = runner.run(
        {"name": "m", "kind": "file_staleness", "path": str(tmp_path / "nope")})
    assert missing.status == "failed"


def test_file_staleness_classifies_stale(tmp_path, now):
    f = tmp_path / "old.md"
    f.write_text("x")
    old = (now - timedelta(days=60)).timestamp()
    os.utime(f, (old, old))
    r = ProbeRunner(now=now).run(
        {"name": "s", "kind": "file_staleness", "path": str(f)})
    assert r.status == "ok" and "stale" in r.result_summary


def test_file_read_empty_is_skipped(tmp_path, now):
    empty = tmp_path / "e.md"
    empty.write_text("   \n\n")
    r = ProbeRunner(now=now).run(
        {"name": "e", "kind": "file_read", "path": str(empty)})
    assert r.status == "skipped"


def test_json_view_parses_fields(tmp_path, now):
    j = tmp_path / "v.json"
    j.write_text('{"overall_status": "ok", "n": 3}')
    r = ProbeRunner(now=now).run(
        {"name": "v", "kind": "json_view", "path": str(j),
         "fields": ["overall_status"]})
    assert r.status == "ok" and "overall_status=ok" in r.result_summary


def test_command_plan_never_executes(now):
    sentinel_called = []

    def exec_spy(argv, timeout):
        sentinel_called.append(argv)
        return 0, "", ""

    r = ProbeRunner(now=now, executor=exec_spy).run(
        {"name": "p", "kind": "command_plan", "command": "sqlite3 ... SELECT 1",
         "why": "documented"})
    assert r.status == "skipped"
    assert r.evidence == "sqlite3 ... SELECT 1"
    assert sentinel_called == []  # planned, not run


def test_json_view_non_dict_root_is_failed_not_crash(tmp_path, now):
    arr = tmp_path / "arr.json"
    arr.write_text("[1, 2, 3]")
    r = ProbeRunner(now=now).run(
        {"name": "v", "kind": "json_view", "path": str(arr), "fields": ["x"]})
    assert r.status == "failed" and "not an object" in r.result_summary


def test_systemctl_empty_units_is_skipped(now):
    r = ProbeRunner(now=now).run(
        {"name": "s", "kind": "systemctl_active", "units": []})
    assert r.status == "skipped" and "no units" in r.result_summary


def test_run_isolates_handler_exceptions(now):
    # a malformed spec (missing required key) must degrade to failed, not crash.
    r = ProbeRunner(now=now).run({"name": "bad", "kind": "json_view"})
    assert r.status == "failed"


def test_naive_now_is_normalized(tmp_path):
    f = tmp_path / "a.md"
    f.write_text("hi")
    r = ProbeRunner(now=datetime(2026, 6, 25, 12, 0)).run(  # tz-naive clock
        {"name": "s", "kind": "file_staleness", "path": str(f)})
    assert r.status == "ok"  # did not raise on naive - aware subtraction


def test_allowlist_rejects_non_loopback_health_url():
    # a host that merely starts with the literal 127.0.0.1 is not loopback
    assert not _allowed(["curl", "http://127.0.0.1.evil.com/health"])
    assert not _allowed(["systemctl", "--user", "restart", "hermes"])


def test_systemctl_and_http_use_injected_executor(now):
    def exec_ok(argv, timeout):
        if argv[:3] == ["systemctl", "--user", "is-active"]:
            return 0, "active", ""
        return 0, "200", ""

    runner = ProbeRunner(now=now, executor=exec_ok)
    sysd = runner.run(
        {"name": "svc", "kind": "systemctl_active", "units": ["hermes", "paperclip"]})
    assert sysd.status == "ok" and "hermes=active" in sysd.result_summary
    http = runner.run(
        {"name": "h", "kind": "http_health", "url": "http://127.0.0.1:3101/api/health"})
    assert http.status in {"ok", "skipped"}  # skipped only if curl absent

"""Builder: schema stability + the 7 Phase-0 verification requirements.

Each requirement from the handoff maps to a test below:

  1. packet builds for every domain                 -> test_all_domains_build
  2. JSON schema is stable across domains            -> test_schema_is_stable
  3. business.floow does not pollute WPC/deck         -> test_floow_does_not_own_deck
  4. a real WPC/deck domain is not suppressed         -> test_deck_domain_not_suppressed
  5. paperclip.issue refuses default goal on ambiguity-> test_paperclip_goal_guard
  6. infra.hermes confirms state via probe not doc    -> test_infra_state_from_probe
  7. daily-focus requires PIM/calendar/live evidence  -> test_daily_focus_requires_evidence
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from context_packet.builder import UnknownDomain, build_packet
from context_packet.models import ContextPacket
from context_packet.render import render_json, render_markdown

DOMAINS = ["business.floow", "paperclip.issue", "daily-focus", "infra.hermes"]

# Top-level keys every packet must carry (handoff schema + extras).
REQUIRED_KEYS = {
    "schema_version", "domain", "generated_at", "authority", "live_probes",
    "views", "history_policy", "defaults_and_guards", "do_not", "warnings",
    "recommended_use",
}


def _build(domain, temp_env, clock, runner) -> ContextPacket:
    vault, home = temp_env
    return build_packet(
        domain, vault_root=vault, home_root=home, now=clock, probe_runner=runner)


# -- 1. every domain builds ----------------------------------------------------

def test_all_domains_build(temp_env, clock, runner):
    for d in DOMAINS:
        pkt = _build(d, temp_env, clock, runner)
        assert pkt.domain == d
        assert pkt.live_probes  # each domain has at least one probe


def test_unknown_domain_raises(temp_env, clock, runner):
    vault, home = temp_env
    with pytest.raises(UnknownDomain):
        build_packet("nope", vault_root=vault, home_root=home, now=clock,
                     probe_runner=runner)


# -- 2. schema stability -------------------------------------------------------

def test_schema_is_stable(temp_env, clock, runner):
    keysets = []
    for d in DOMAINS:
        data = _build(d, temp_env, clock, runner).to_dict()
        assert REQUIRED_KEYS <= set(data), f"{d} missing keys"
        keysets.append(tuple(sorted(data)))
        # authority sub-shape is stable too
        assert {"intent_sources", "state_sources", "owner", "confidence"} <= set(
            data["authority"])
        # round-trips as JSON
        json.loads(render_json(_build(d, temp_env, clock, runner)))
    assert len(set(keysets)) == 1, "top-level key set differs across domains"


def test_probe_results_carry_status_and_timestamp(temp_env, clock, runner):
    pkt = _build("infra.hermes", temp_env, clock, runner)
    for p in pkt.live_probes:
        assert p.status in {"ok", "failed", "skipped"}
        assert p.observed_at


# -- 3. floow must not pollute WPC/deck ---------------------------------------

def test_floow_does_not_own_deck(temp_env, clock, runner):
    pkt = _build("business.floow", temp_env, clock, runner)
    # check semantic fields (name/owns/summary), NOT ref/evidence paths, which
    # live under a tmp dir whose name contains the test name "deck".

    # (a) no authority document CLAIMS to own deck/wpc
    owns = " ".join(s["owns"] for s in pkt.authority["intent_sources"]).lower()
    assert "deck" not in owns and "wpc" not in owns

    # (b) the stronger invariant from the A/B eval: no LIVE PROBE surfaces
    # deck/wpc as current state. This is what actually re-introduces pollution
    # (e.g. adding a {"name":"deck_state", path:".../deck.md"} probe).
    probe_blob = " ".join(f"{p.name} {p.result_summary}"
                          for p in pkt.live_probes).lower()
    assert "deck" not in probe_blob and "wpc" not in probe_blob
    src_names = " ".join(s["name"] for s in pkt.authority["state_sources"]).lower()
    assert "deck" not in src_names and "wpc" not in src_names

    # (c) deck/wpc are explicitly marked NOT authority for this domain
    not_auth = " ".join(pkt.views["not_authority"]).lower()
    assert "deck" in not_auth and "wpc" in not_auth


# -- 4. a real deck/WPC domain is not suppressed (design room) ----------------

def test_deck_domain_not_suppressed(tmp_path, clock, runner):
    reg = {
        "schema_version": "authority-registry-v0",
        "roots": {"vault": str(tmp_path), "home": str(tmp_path)},
        "domains": {
            "deck.planner": {
                "owner": "hermes", "confidence": "high",
                "intent_authority": [
                    {"path": "{vault}/deck-goal.md", "owns": "deck/WPC product goal"}],
                "state_probes": [],
                "views": {"allowed": [], "not_authority": []},
                "history_policy": {"default": "exclude", "allowed_when": []},
                "defaults_and_guards": [], "do_not": [],
                "recommended_use": "use the deck goal directly",
            }
        },
    }
    rp = tmp_path / "reg.json"
    rp.write_text(json.dumps(reg))
    (tmp_path / "deck-goal.md").write_text("# deck goal\n")
    pkt = build_packet("deck.planner", registry_path=str(rp), now=clock,
                       probe_runner=runner)
    blob = json.dumps(pkt.authority).lower()
    assert "deck" in blob  # deck IS authority here, not suppressed
    assert pkt.authority["confidence"] == "high"


# -- 5. paperclip.issue goal guard --------------------------------------------

def test_paperclip_goal_guard(temp_env, clock, runner):
    pkt = _build("paperclip.issue", temp_env, clock, runner)
    do_not = " ".join(pkt.do_not).lower()
    assert "default" in do_not and ("deck" in do_not or "wpc" in do_not)
    assert "ambiguous" in do_not
    assert "goalid" in pkt.recommended_use.lower()
    # the goal-ambiguity probe is documented, not silently executed/defaulted
    goal_probe = next(p for p in pkt.live_probes if p.name == "company_goals_ambiguity")
    assert goal_probe.status == "skipped"


# -- 6. infra.hermes confirms state via live probe, not the doc ---------------

def test_infra_state_from_probe(temp_env, clock, runner):
    pkt = _build("infra.hermes", temp_env, clock, runner)
    names = {p.name for p in pkt.live_probes}
    assert {"services_active", "ops_watch"} <= names
    svc = next(p for p in pkt.live_probes if p.name == "services_active")
    assert svc.status == "ok" and "hermes=active" in svc.result_summary
    guard_blob = (" ".join(pkt.do_not) + " " +
                  " ".join(pkt.views["not_authority"])).lower()
    assert "live probe" in guard_blob or "probe" in guard_blob
    assert "wiki" in guard_blob or "doc" in guard_blob
    # probe-wins-over-doc: every infra state source is a real probe kind, never
    # a file_read that could inline the intent-doc body into live state.
    kinds = {s["kind"] for s in pkt.authority["state_sources"]}
    assert kinds <= {"systemctl_active", "json_view", "http_health"}


# -- 7. daily-focus requires PIM/calendar/live evidence -----------------------

def test_daily_focus_requires_evidence(temp_env, clock, runner):
    pkt = _build("daily-focus", temp_env, clock, runner)
    names = {p.name for p in pkt.live_probes}
    assert {"pim_today_tasks", "calendar_today"} <= names
    for n in ("pim_today_tasks", "calendar_today"):
        assert next(p for p in pkt.live_probes if p.name == n).status == "skipped"
    do_not = " ".join(pkt.do_not).lower()
    assert "pim" in do_not and "calendar" in do_not
    # today's daily note is missing -> surfaced as a warning
    assert any("missing" in w.lower() or "failed" in w.lower()
               for w in pkt.warnings)


# -- read-only: building writes nothing ---------------------------------------

def test_build_is_read_only(temp_env, clock, runner):
    vault, home = temp_env

    def snapshot(root):
        return {p: p.stat().st_mtime
                for p in Path(root).rglob("*") if p.is_file()}

    before = {**snapshot(vault), **snapshot(home)}
    for d in DOMAINS:
        _build(d, temp_env, clock, runner)
    after = {**snapshot(vault), **snapshot(home)}
    assert before == after  # no new files, no mtime changes


def test_naive_now_does_not_crash_build(temp_env):
    # a tz-naive injected clock must be tolerated (treated as UTC), not crash.
    vault, home = temp_env
    pkt = build_packet("business.floow", vault_root=vault, home_root=home,
                       now=datetime(2026, 6, 25, 12, 0))
    assert pkt.domain == "business.floow"
    assert all(s.get("staleness") for s in pkt.authority["intent_sources"]
               if s["exists"])


def test_markdown_render_has_core_sections(temp_env, clock, runner):
    md = render_markdown(_build("paperclip.issue", temp_env, clock, runner))
    for needle in ("Context Packet", "Authority", "Live state",
                   "Do NOT", "Recommended use"):
        assert needle in md

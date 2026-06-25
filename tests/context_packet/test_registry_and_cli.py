"""Default registry sanity + CLI behaviour."""

from __future__ import annotations

import json

import pytest

from context_packet import cli
from context_packet.registry import list_domains, load_registry

EXPECTED = {"business.floow", "paperclip.issue", "daily-focus", "infra.hermes"}


def test_default_registry_has_four_domains():
    reg = load_registry()
    assert set(list_domains(reg)) == EXPECTED
    assert "vault" in reg["roots"] and "home" in reg["roots"]


def test_registry_is_pointers_not_copied_truth():
    """A thin registry routes; it must not inline live state values."""
    reg = load_registry()
    for name, entry in reg["domains"].items():
        for src in entry["intent_authority"]:
            assert "path" in src and "owns" in src
            # pointer only - no inlined document body
            assert "content" not in src and "value" not in src
        for probe in entry["state_probes"]:
            # every probe declares a kind and a target, never a cached result
            assert "kind" in probe
            assert "result" not in probe and "value" not in probe


def test_cli_list(capsys):
    rc = cli.main(["--list"])
    out = capsys.readouterr().out.split()
    assert rc == 0 and set(out) == EXPECTED


def test_cli_unknown_domain_exit_2(capsys):
    rc = cli.main(["does.not.exist"])
    assert rc == 2
    assert "unknown domain" in capsys.readouterr().err


def test_cli_no_domain_exit_2(capsys):
    rc = cli.main([])
    assert rc == 2


def test_cli_json_is_valid_with_required_keys(capsys):
    rc = cli.main(["infra.hermes", "--format", "json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["domain"] == "infra.hermes"
    assert data["schema_version"] == "context-packet-v0"
    assert "authority" in data and "live_probes" in data


def test_cli_default_writes_nothing(tmp_path, capsys, monkeypatch):
    # NB: the repo's autouse conftest creates tmp_path/"hermes_test" (HERMES_HOME
    # sandbox), so assert the precise contract: no packet artifacts are written.
    monkeypatch.chdir(tmp_path)
    cli.main(["infra.hermes"])  # no --out-dir
    assert not (tmp_path / "infra.hermes.json").exists()
    assert not (tmp_path / "infra.hermes.md").exists()
    assert not any(p.suffix in {".json", ".md"} and p.parent == tmp_path
                   for p in tmp_path.iterdir())


def test_cli_bad_registry_exit_2(tmp_path, capsys):
    # missing file
    assert cli.main(["infra.hermes", "--registry", str(tmp_path / "nope.json")]) == 2
    assert "cannot load registry" in capsys.readouterr().err
    # malformed json
    bad = tmp_path / "bad.json"
    bad.write_text("{not valid json")
    assert cli.main(["infra.hermes", "--registry", str(bad)]) == 2
    assert "cannot load registry" in capsys.readouterr().err


def test_cli_out_dir_writes_artifacts(tmp_path, capsys):
    out = tmp_path / "packets"
    rc = cli.main(["infra.hermes", "--format", "both", "--out-dir", str(out)])
    assert rc == 0
    assert (out / "infra.hermes.packet.json").exists()
    assert (out / "infra.hermes.packet.md").exists()
    json.loads((out / "infra.hermes.packet.json").read_text())


def test_cli_shadow_flag_outputs_comparison(capsys):
    rc = cli.main(["daily-focus", "--shadow", "--format", "json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["mode"] == "shadow"
    assert data["domain"] == "daily-focus"
    assert "comparison" in data and "divergence" in data["comparison"]

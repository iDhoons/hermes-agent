import json

from agent.routing_autotune import load_routing_decisions, suggest_routing_improvements


def test_load_routing_decisions_skips_invalid_jsonl_rows(tmp_path):
    path = tmp_path / "decisions.jsonl"
    path.write_text(
        json.dumps({"lane": "micro", "char_count": 6}) + "\n"
        "not-json\n"
        + json.dumps({"lane": None, "char_count": 220}) + "\n",
        encoding="utf-8",
    )

    records = load_routing_decisions(path)

    assert len(records) == 2
    assert records[0]["lane"] == "micro"
    assert records[1]["char_count"] == 220


def test_suggest_routing_improvements_raises_simple_threshold_for_safe_primary_spillover():
    records = [
        {
            "lane": None,
            "routing_reason": None,
            "routed_to_primary": True,
            "char_count": 220,
            "word_count": 42,
            "complex_signal": False,
            "protected_signal": False,
        }
        for _ in range(10)
    ]
    config = {
        "lanes": {
            "simple": {"max_chars": 160, "max_words": 28},
        }
    }

    suggestions = suggest_routing_improvements(records, config, min_samples=5)

    assert suggestions == [
        {
            "key": "smart_model_routing.lanes.simple.max_chars",
            "current": 160,
            "suggested": 220,
            "reason": "safe_primary_spillover_p90_chars",
            "sample_count": 10,
        },
        {
            "key": "smart_model_routing.lanes.simple.max_words",
            "current": 28,
            "suggested": 42,
            "reason": "safe_primary_spillover_p90_words",
            "sample_count": 10,
        },
    ]


def test_suggest_routing_improvements_waits_for_enough_samples():
    records = [
        {
            "lane": None,
            "routing_reason": None,
            "routed_to_primary": True,
            "char_count": 220,
            "word_count": 42,
            "complex_signal": False,
            "protected_signal": False,
        }
    ]

    assert suggest_routing_improvements(records, {"lanes": {"simple": {}}}, min_samples=5) == []


def test_suggest_routing_improvements_ignores_lane_null_non_primary_rows():
    records = [
        {
            "lane": None,
            "routing_reason": "simple_turn",
            "routed_to_primary": False,
            "char_count": 220,
            "word_count": 42,
            "complex_signal": False,
            "protected_signal": False,
        }
    ]

    assert suggest_routing_improvements(records, {"lanes": {"simple": {}}}, min_samples=1) == []


def test_suggest_routing_improvements_ignores_inconsistent_primary_rows():
    config = {"lanes": {"simple": {"max_chars": 100, "max_words": 20}}}
    assert suggest_routing_improvements(
        [
            {
                "lane": None,
                "routing_reason": "simple_turn",
                "routed_to_primary": True,
                "char_count": 220,
                "word_count": 42,
                "complex_signal": False,
                "protected_signal": False,
            }
        ],
        config,
        min_samples=1,
    ) == []
    assert suggest_routing_improvements(
        [
            {
                "lane": None,
                "routing_reason": None,
                "routed_to_primary": True,
                "char_count": 220,
                "word_count": 42,
            }
        ],
        config,
        min_samples=1,
    ) == []


def test_suggest_routing_improvements_ignores_complex_or_protected_primary_rows():
    records = [
        {
            "lane": None,
            "routed_to_primary": True,
            "char_count": 300,
            "word_count": 50,
            "complex_signal": True,
            "protected_signal": False,
        },
        {
            "lane": None,
            "routed_to_primary": True,
            "char_count": 300,
            "word_count": 50,
            "complex_signal": False,
            "protected_signal": True,
        },
    ]

    assert suggest_routing_improvements(records, {"lanes": {"simple": {}}}, min_samples=1) == []

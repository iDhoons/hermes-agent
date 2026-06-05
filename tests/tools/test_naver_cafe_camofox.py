"""Tests for the conservative Naver Cafe Camofox flow."""

import json
from unittest.mock import patch

from tools import naver_cafe_camofox as ncc


def test_clamps_result_limit_to_conservative_range():
    assert ncc._clamp_result_limit(0) == 1
    assert ncc._clamp_result_limit(3) == 3
    assert ncc._clamp_result_limit(99) == 5


def test_builds_mobile_cafe_search_url_when_cafe_id_is_given():
    url = ncc._build_search_url("키워드", cafe_id="12345")

    assert url.startswith("https://m.cafe.naver.com/ca-fe/web/cafes/12345/search/articles?")
    assert "query=" in url


def test_detects_login_gate_from_snapshot():
    gate = ncc._detect_gate("네이버 로그인 후 이용해 주세요")

    assert gate == {
        "status": "login_required",
        "message": "Naver login is required before continuing.",
    }


def test_plain_login_link_is_not_treated_as_gate():
    assert ncc._detect_gate("검색 결과\n로그인\n카페글 보기") is None


def test_extracts_and_limits_naver_cafe_article_links():
    links = [
        {"text": "first", "href": "https://cafe.naver.com/test/1"},
        {"text": "dup", "href": "https://cafe.naver.com/test/1"},
        {"title": "mobile", "url": "https://m.cafe.naver.com/ca-fe/web/cafes/1/articles/2"},
        {"text": "ignore", "url": "https://example.com/not-cafe"},
    ]

    assert ncc._extract_article_links(links, max_results=1) == [
        {"title": "first", "url": "https://cafe.naver.com/test/1"}
    ]


def test_search_flow_stops_before_reading_when_login_required():
    with (
        patch("tools.naver_cafe_camofox.check_camofox_available", return_value=True),
        patch("tools.naver_cafe_camofox._navigate_collect", return_value={
            "url": "https://search.naver.com/search.naver?where=article",
            "snapshot": "로그인 후 이용해 주세요",
            "links": [],
        }),
        patch("tools.naver_cafe_camofox.time.sleep") as sleep,
    ):
        result = json.loads(ncc.naver_cafe_keyword_flow("키워드", read_first=True))

    assert result["success"] is False
    assert result["status"] == "login_required"
    assert result["results"] == []
    sleep.assert_not_called()


def test_search_flow_reads_first_result_with_delay():
    calls = []

    def fake_collect(url, task_id):
        calls.append(url)
        if len(calls) == 1:
            return {
                "url": url,
                "snapshot": "검색 결과",
                "links": [
                    {
                        "text": "첫 글",
                        "url": "https://m.cafe.naver.com/ca-fe/web/cafes/1/articles/2",
                    }
                ],
            }
        return {
            "url": url,
            "snapshot": "본문 내용입니다.",
            "links": [],
        }

    with (
        patch("tools.naver_cafe_camofox.check_camofox_available", return_value=True),
        patch("tools.naver_cafe_camofox._navigate_collect", side_effect=fake_collect),
        patch("tools.naver_cafe_camofox.time.sleep") as sleep,
    ):
        result = json.loads(
            ncc.naver_cafe_keyword_flow("키워드", max_results=3, read_first=True, min_delay_seconds=4)
        )

    assert result["success"] is True
    assert result["status"] == "read"
    assert result["results"] == [
        {"title": "첫 글", "url": "https://m.cafe.naver.com/ca-fe/web/cafes/1/articles/2"}
    ]
    assert result["post"]["excerpt"] == "본문 내용입니다."
    assert calls[1] == "https://m.cafe.naver.com/ca-fe/web/cafes/1/articles/2"
    sleep.assert_called_once_with(4)


def test_builds_cafe_tab_search_url_when_no_cafe_id():
    # The general (no cafe_id) keyword search must hit the Cafe vertical tab.
    # `where=article` does not surface direct cafe article links in the DOM,
    # so extraction always returned zero results.
    url = ncc._build_search_url("맛집")

    assert url.startswith("https://search.naver.com/search.naver?")
    assert "ssc=tab.cafe.all" in url
    assert "where=article" not in url
    assert "query=" in url


def test_navigate_collect_requests_links_beyond_default_page():
    # Naver search pages front-load ~50 chrome/nav links before cafe results.
    # The links fetch must request more than the server's default page size,
    # otherwise the cafe article links are truncated away.
    captured = {}

    def fake_get(path, params=None, timeout=None):
        captured.setdefault(path, params or {})
        if path.endswith("/snapshot"):
            return {"url": "https://x", "snapshot": "s"}
        return {"links": []}

    with (
        patch("tools.naver_cafe_camofox._get_session", return_value={"tab_id": "t1", "user_id": "u1"}),
        patch("tools.naver_cafe_camofox._post"),
        patch("tools.naver_cafe_camofox._get", side_effect=fake_get),
    ):
        ncc._navigate_collect(
            "https://search.naver.com/search.naver?ssc=tab.cafe.all&query=x", None
        )

    assert captured["/tabs/t1/links"].get("limit", 0) >= 100


def test_extracts_cafe_search_tab_url_with_art_token():
    # Cafe-tab results use the `cafe.naver.com/<name>/<id>?art=<jwt>` format.
    links = [
        {"text": "히로시마 맛집", "url": "https://cafe.naver.com/jpnstory/4298673?art=eyJhbGci"}
    ]

    assert ncc._extract_article_links(links, max_results=3) == [
        {"title": "히로시마 맛집", "url": "https://cafe.naver.com/jpnstory/4298673?art=eyJhbGci"}
    ]

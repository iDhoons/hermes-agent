"""Conservative Naver Cafe keyword flow over the local Camofox backend.

This module intentionally does not accept raw cookies or API keys. Auth must
come from the local Camofox profile, a visible browser login, or a cookie file
imported by the Camofox server from disk.
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode, urlparse

import requests

from tools.browser_camofox import (
    _DEFAULT_TIMEOUT,
    _ensure_tab,
    _get_session,
    _get,
    _post,
    check_camofox_available,
    get_vnc_url,
    is_camofox_mode,
)
from tools.registry import registry

_MAX_RESULTS = 5
_MIN_READ_DELAY_SECONDS = 3
_MAX_EXCERPT_CHARS = 4000
# Naver search pages front-load ~50 chrome/nav links before cafe results, so the
# links fetch must request well past the server's default page size.
_LINKS_FETCH_LIMIT = 300


def _clamp_result_limit(value: Any) -> int:
    try:
        limit = int(value)
    except (TypeError, ValueError):
        limit = 3
    return max(1, min(_MAX_RESULTS, limit))


def _clamp_delay_seconds(value: Any) -> int:
    try:
        delay = int(value)
    except (TypeError, ValueError):
        delay = _MIN_READ_DELAY_SECONDS
    return max(_MIN_READ_DELAY_SECONDS, min(30, delay))


def _build_search_url(keyword: str, cafe_id: str = "") -> str:
    keyword = keyword.strip()
    cafe_id = str(cafe_id or "").strip()
    if cafe_id:
        query = urlencode({"query": keyword})
        return f"https://m.cafe.naver.com/ca-fe/web/cafes/{cafe_id}/search/articles?{query}"
    query = urlencode({"ssc": "tab.cafe.all", "query": keyword})
    return f"https://search.naver.com/search.naver?{query}"


def _detect_gate(snapshot: str) -> Optional[Dict[str, str]]:
    text = (snapshot or "").lower()
    if any(token in text for token in ("captcha", "보안문자", "자동입력", "로봇이", "robot")):
        return {
            "status": "verification_required",
            "message": "A CAPTCHA or verification screen is present; manual action is required.",
        }
    if any(
        token in text
        for token in (
            "로그인 후",
            "로그인이 필요",
            "로그인해야",
            "먼저 로그인",
            "login required",
            "please log in",
            "sign in required",
        )
    ):
        return {
            "status": "login_required",
            "message": "Naver login is required before continuing.",
        }
    if "등급 이상의 멤버만 볼 수 있" in text or "등급 이상만 이용" in text:
        return {
            "status": "grade_required",
            "message": "This post requires a higher cafe membership grade.",
        }
    if any(p in text for p in ("카페에 가입하면", "멤버공개", "접근할 수 없습니다",
                               "멤버에게만 공개", "멤버만 볼 수 있")):
        return {
            "status": "permission_required",
            "message": "The cafe post requires membership or is member-only.",
        }
    return None


def _is_naver_cafe_article_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    if host not in {"cafe.naver.com", "m.cafe.naver.com"}:
        return False
    lowered = url.lower()
    if any(blocked in lowered for blocked in ("/login", "/join", "/member", "/manage")):
        return False
    path_parts = [part for part in parsed.path.split("/") if part]
    if host == "cafe.naver.com" and len(path_parts) >= 2 and path_parts[1].isdigit():
        return True
    return any(marker in lowered for marker in ("articleread", "/articles/", "articleid=", "/ca-fe/"))


def _extract_article_links(raw_links: Any, max_results: int) -> List[Dict[str, str]]:
    links = raw_links if isinstance(raw_links, list) else []
    results: List[Dict[str, str]] = []
    seen = set()
    for link in links:
        if not isinstance(link, dict):
            continue
        url = str(link.get("url") or link.get("href") or "").strip()
        if not url or url in seen or not _is_naver_cafe_article_url(url):
            continue
        title = str(link.get("title") or link.get("text") or link.get("ariaLabel") or url).strip()
        results.append({"title": title[:200], "url": url})
        seen.add(url)
        if len(results) >= max_results:
            break
    return results


_SLUG_TO_CLUBID = {
    "0404ab":     "18600855",
    "kimyoooo":   "11289639",
    "overseer":   "23700418",
    "jaengid":    "21776715",
    "pcarpenter": "17593353",
}


def _to_mobile_url(url: str) -> str:
    """PC cafe.naver.com/<slug>/<id> → mobile /ca-fe/web/cafes/<clubid>/articles/<id>."""
    import re as _re
    m = _re.match(r'https?://cafe\.naver\.com/([A-Za-z0-9_]+)/(\d+)', url)
    if m:
        slug, article_id = m.group(1), m.group(2)
        club_id = _SLUG_TO_CLUBID.get(slug, slug)
        return f"https://m.cafe.naver.com/ca-fe/web/cafes/{club_id}/articles/{article_id}"
    return url


def _strip_cafe_chrome(snapshot: str) -> str:
    """Remove nav/sidebar chrome before the article body (heading level=2 = post title)."""
    import re as _re
    m = _re.search(r'heading "[^"]+" \[level=2\]', snapshot)
    if m:
        return snapshot[m.start():]
    return snapshot


def _snapshot_excerpt(snapshot: str) -> str:
    return _strip_cafe_chrome((snapshot or "").strip())[:_MAX_EXCERPT_CHARS]


def _navigate_collect(url: str, task_id: Optional[str]) -> Dict[str, Any]:
    session = _get_session(task_id)
    fetch_url = _to_mobile_url(url)
    if session.get("tab_id"):
        _post(
            f"/tabs/{session['tab_id']}/navigate",
            {"userId": session["user_id"], "url": fetch_url},
            timeout=60,
        )
    else:
        session = _ensure_tab(task_id, fetch_url)

    tab_id = session["tab_id"]
    user_id = session["user_id"]
    snapshot_data = _get(
        f"/tabs/{tab_id}/snapshot",
        params={"userId": user_id},
        timeout=_DEFAULT_TIMEOUT,
    )
    try:
        links_data = _get(
            f"/tabs/{tab_id}/links",
            params={"userId": user_id, "limit": _LINKS_FETCH_LIMIT},
            timeout=_DEFAULT_TIMEOUT,
        )
    except Exception:
        links_data = {}
    return {
        "url": snapshot_data.get("url") or url,
        "snapshot": snapshot_data.get("snapshot", ""),
        "links": links_data.get("links", []),
    }


def naver_cafe_keyword_flow(
    keyword: str,
    cafe_id: str = "",
    max_results: int = 3,
    read_first: bool = False,
    article_url: str = "",
    min_delay_seconds: int = _MIN_READ_DELAY_SECONDS,
    task_id: Optional[str] = None,
) -> str:
    """Search Naver Cafe articles and optionally read one authorized post."""
    keyword = str(keyword or "").strip()
    article_url = str(article_url or "").strip()
    limit = _clamp_result_limit(max_results)
    delay = _clamp_delay_seconds(min_delay_seconds)

    if not keyword and not article_url:
        return json.dumps({
            "success": False,
            "status": "invalid_request",
            "error": "keyword or article_url is required.",
        })
    if article_url and not _is_naver_cafe_article_url(article_url):
        return json.dumps({
            "success": False,
            "status": "invalid_request",
            "error": "article_url must be a Naver Cafe article URL.",
        })
    if not check_camofox_available():
        return json.dumps({
            "success": False,
            "status": "camofox_unavailable",
            "error": "Camofox is not reachable. Start the local Camofox server first.",
        })

    try:
        search_url = _build_search_url(keyword, cafe_id) if keyword else ""
        search_state = {"url": search_url, "snapshot": "", "links": []}
        results: List[Dict[str, str]] = []
        if search_url:
            search_state = _navigate_collect(search_url, task_id)
            gate = _detect_gate(search_state.get("snapshot", ""))
            if gate:
                return json.dumps({
                    "success": False,
                    **gate,
                    "search_url": search_state["url"],
                    "results": [],
                    "vnc_url": get_vnc_url(),
                })
            results = _extract_article_links(search_state.get("links", []), limit)

        target_url = article_url or (results[0]["url"] if read_first and results else "")
        if not target_url:
            return json.dumps({
                "success": True,
                "status": "searched",
                "search_url": search_state["url"],
                "results": results,
                "result_limit": limit,
                "vnc_url": get_vnc_url(),
            })

        time.sleep(delay)
        post_state = _navigate_collect(target_url, task_id)
        gate = _detect_gate(post_state.get("snapshot", ""))
        if gate:
            return json.dumps({
                "success": False,
                **gate,
                "search_url": search_state["url"],
                "results": results,
                "post_url": post_state["url"],
                "vnc_url": get_vnc_url(),
            })

        return json.dumps({
            "success": True,
            "status": "read",
            "search_url": search_state["url"],
            "results": results,
            "post": {
                "url": post_state["url"],
                "excerpt": _snapshot_excerpt(post_state.get("snapshot", "")),
            },
            "result_limit": limit,
            "read_delay_seconds": delay,
            "vnc_url": get_vnc_url(),
        })
    except requests.ConnectionError:
        return json.dumps({
            "success": False,
            "status": "camofox_unavailable",
            "error": "Camofox connection failed during the Naver Cafe flow.",
        })
    except Exception as exc:
        return json.dumps({
            "success": False,
            "status": "error",
            "error": str(exc),
        })


_NAVER_CAFE_SCHEMA = {
    "name": "naver_cafe_keyword_flow",
    "description": (
        "Prototype a conservative authorized Naver Cafe keyword search flow via local Camofox. "
        "Uses the current local browser session only; do not pass cookies or API keys. "
        "Stops on login, CAPTCHA, or permission gates. At most 5 results are returned, "
        "and optional reading opens only one post."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "keyword": {
                "type": "string",
                "description": "Keyword to search in Naver Cafe articles.",
            },
            "cafe_id": {
                "type": "string",
                "description": "Optional Naver Cafe numeric club ID for cafe-scoped mobile search.",
            },
            "max_results": {
                "type": "integer",
                "minimum": 1,
                "maximum": 5,
                "default": 3,
                "description": "Maximum article links to return; clamped to 1-5.",
            },
            "read_first": {
                "type": "boolean",
                "default": False,
                "description": "If true, open only the first discovered article after a delay.",
            },
            "article_url": {
                "type": "string",
                "description": "Optional explicit Naver Cafe article URL to read instead of first search result.",
            },
            "min_delay_seconds": {
                "type": "integer",
                "minimum": 3,
                "maximum": 30,
                "default": _MIN_READ_DELAY_SECONDS,
                "description": "Delay before opening a post; clamped to 3-30 seconds.",
            },
        },
        "required": [],
    },
}


registry.register(
    name="naver_cafe_keyword_flow",
    toolset="browser",
    schema=_NAVER_CAFE_SCHEMA,
    handler=lambda args, **kw: naver_cafe_keyword_flow(
        keyword=args.get("keyword", ""),
        cafe_id=args.get("cafe_id", ""),
        max_results=args.get("max_results", 3),
        read_first=args.get("read_first", False),
        article_url=args.get("article_url", ""),
        min_delay_seconds=args.get("min_delay_seconds", _MIN_READ_DELAY_SECONDS),
        task_id=kw.get("task_id"),
    ),
    check_fn=is_camofox_mode,
    emoji="🌐",
)

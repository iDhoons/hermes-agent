"""Shared helpers for the /ingest slash command.

/ingest is intentionally a prompt-building command, not a direct Obsidian
writer.  The agent still performs retrieval, note edits, and verification with
normal tools so existing safety rules (vault rules, approvals, and tests) apply.
"""

from __future__ import annotations

from textwrap import dedent

INGEST_USAGE = "Usage: /ingest <URL, file path, or source text>"


def build_ingest_prompt(source: str) -> str:
    """Return the agent prompt for an Obsidian-style source ingest.

    Blank input returns an empty string so callers can show ``INGEST_USAGE``
    without duplicating validation logic.
    """

    source = (source or "").strip()
    if not source:
        return ""

    return dedent(
        f"""
        Obsidian 자료 인제스트 작업을 수행해줘.

        [자료]
        {source}

        [워크플로]
        1. 먼저 현재 시간(KST)을 확인한다.
        2. URL/파일/텍스트 유형을 판단하고, 필요한 도구로 원문을 조회·추출한다.
        3. vault 규칙을 따른다: Journey는 raw, Inbox는 미검증, Wiki는 검증지식이다. 분류가 애매하면 Inbox/에 둔다.
        4. 자료노트는 프론트매터를 포함하고, TL;DR / 핵심 포인트 / 근거·데이터 / 액션 아이템 / 연결 노트 / 출처 구조로 작성한다.
        5. 원본 자료(Resources/)는 임의 수정하지 않는다. 기존 노트 구조 변경·삭제도 사용자 확인 없이는 하지 않는다.
        6. 실행으로 이어질 내용은 바로 실행하지 말고 "적용 후보"로 분리해 우선순위와 승인 필요 여부를 표시한다.
        7. 완료 후 한국어로 저장 경로, 한 줄 요약, 다음 행동을 보고한다.
        """
    ).strip()

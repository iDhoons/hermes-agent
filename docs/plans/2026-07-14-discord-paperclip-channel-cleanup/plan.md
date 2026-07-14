# Discord Paperclip 직원 채널 정리 계획

## Context

Paperclip 에이전트 조직이 은퇴(서비스 퇴역 2026-07-10, 공식 확정 2026-07-13)하고 backlog가 Hermes Kanban으로 이관됐지만, Discord "dh Studio" 서버에는 Paperclip 직원별 채널들이 그대로 남아 있다. 사용자가 오늘(2026-07-14 11:10 KST) #비서실에서 "알림 채널 통합 + Paperclip 비운영 논의"를 요청했고, Hermes는 4단계 안을 제시한 뒤 승인 대기 중이다(아직 아무 변경 없음, 칸반 카드 없음).

이 계획은 그중 **명확히 결정된 부분 — Paperclip 직원 채널 완전 삭제와 관련 잔재 정리** — 를 이 세션에서 실행한다. 알림 채널 통합(인프라-알림/운영-위생/개발-품질/오류 등)은 #비서실에서 Hermes와 진행 중인 논의에 맡기고 여기서는 건드리지 않는다.

**사용자 결정사항:** 완전 삭제(Archive 아님) / 봇으로 자동 삭제(discord_tool에 delete_channel 액션 추가) / 범위 = 직원 채널 10개 + 업무-접수 + 로컬 잔재.

## 핵심 제약 (조사로 확인됨)

- Hermes `tools/discord_tool.py`에는 채널 삭제 액션이 없다 (`_ACTIONS`, discord_tool.py:612-628). `_discord_request` 헬퍼(discord_tool.py:62)는 DELETE를 이미 지원.
- 채널 allowlist는 `~/.hermes/.env`의 `DISCORD_ALLOWED_CHANNELS`(런타임 최우선)와 `~/.hermes/config.yaml:437` `discord.allowed_channels`(fallback)에 있다.
- `hermes-discord-allowlist-sync.timer`(6시간 주기)가 `channel_directory.json` 기준으로 allowlist를 **add-only** 재기록한다 → 설정에서만 지우면 재추가됨. **반드시 Discord에서 채널을 먼저 삭제**한 뒤 설정을 정리해야 한다.
- `channel_directory.json`은 게이트웨이가 라이브 길드에서 자동 재생성 — 수동 편집 불필요.
- 봇 토큰은 `~/.hermes/.env`의 `DISCORD_BOT_TOKEN` (값 출력 금지). 채널 삭제에는 봇의 `MANAGE_CHANNELS` 권한 필요 — 사전 확인, 없으면 수동 삭제로 전환.

## 삭제 대상 채널 (11개)

| 채널 | ID |
|---|---|
| 조사-기술동향-조사원 | 1507899411815206942 |
| 운영-문서-감시원 | 1507899416256974938 |
| 기술-리스크-검토자 | 1507899418568036462 |
| 운영-인프라-점검원 | 1507899420807659710 |
| 운영-진행-점검원 | 1507899423051485356 |
| 운영-커버리지-감사관 | 1507899425824051240 |
| 조사-조율자 | 1507899428118204466 |
| 조사-제품수요-검증자 | 1511579831131701461 |
| 조사-제조사-관점-분석관 | 1514283005081030858 |
| 조사-심층-조사원 | (allowlist에 없음 — 라이브 조회로 ID 확정) |
| 업무-접수 (Paperclip 접수 창구) | 1507899404466524260 |

유지: 비서실, 승인, 일일-보고, journey, 복용-체크, 한줄-기준, 오류, 비용, 개발실, 인프라-알림, 시장-조사, 개발-동향, 회의실, 조사실, hermes, 운영-위생, 개발-품질 (마지막 두 개 포함 알림 채널들은 Hermes 통합 논의 대상).

## 실행 단계

### 0. 준비
- 이 플랜을 보이는 위치로 저장: `/home/idhoons/hermes/docs/plans/2026-07-14-discord-paperclip-channel-cleanup/plan.md` (+ `_status.open.md`).
- 라이브 확인: `list_channels`(기존 discord_tool 액션 또는 REST 직접 호출)로 dh Studio 채널 목록을 받아 위 11개의 존재와 ID를 확정하고, 조사-심층-조사원 ID를 채운다.
- 봇 권한 확인: 봇 멤버의 role 권한을 계산해 `MANAGE_CHANNELS` 보유 여부 확인. **없으면**: 사용자에게 Discord UI에서 봇 역할에 권한 부여 또는 수동 삭제 중 선택 요청.

### 1. 코드: delete_channel 액션 추가 — `tools/discord_tool.py`
- `_ACTIONS`에 `delete_channel` 등록, 기존 액션 패턴을 따라 구현 (`_discord_request("DELETE", f"/channels/{channel_id}", token)` — delete_message discord_tool.py:556 스타일 참조). 403은 기존 `_enrich_403`이 처리.
- 기존 discord_tool 테스트 파일이 있으면 같은 패턴으로 delete_channel 테스트 추가, 없으면 최소 단위 테스트 1개.
- 테스트 실행은 `.venv`(python 3.12) 사용 (uv 3.11 `venv`는 pytest 플러그인 없어 가짜 실패 — 프로젝트 메모리).
- 참고: `config.yaml`의 `discord.server_actions: ''`는 전체 액션 노출 의미 → 이 변경 후 Hermes 런타임도 채널 삭제 능력을 갖게 됨 (사용자가 의도적으로 선택).

### 2. 채널 삭제 (비가역 — 실행 직전 최종 게이트)
- 삭제 실행 전, 라이브 조회로 확정된 **최종 채널 목록(이름+ID)을 사용자에게 보여주고 명시 승인**을 받는다.
- 승인 후 새 delete_channel 경로로 11개 채널 일괄 삭제 (각 호출 결과 기록, 실패 채널은 개별 보고).

### 3. 설정 정리 (백업 우선)
- `~/.hermes/.env`와 `~/.hermes/config.yaml`을 타임스탬프 백업 후:
  - `.env` `DISCORD_ALLOWED_CHANNELS`에서 삭제된 채널 ID 제거
  - `config.yaml` `discord.allowed_channels`(:437)에서 동일 ID 제거
  - `free_response_channels`/`no_thread_channels`에는 직원 채널 없음 — 변경 불필요
- `systemctl --user restart hermes-gateway.service` (1회 재시작으로 env 재로드 + channel_directory.json 재생성 + 새 tool 코드 로드). 게이트웨이가 repo 코드를 직접 로드하는지 ExecStart 경로를 먼저 확인.

### 4. 로컬 잔재 정리
- `~/.config/systemd/user.control/paperclip.service.d/` (존재하지 않는 서비스의 drop-in) 삭제 + `systemctl --user daemon-reload`.
- `~/.hermes/skills/devops/paperclip*` 및 `~/.hermes/profiles/*/skills/devops/paperclip*` 스킬 디렉토리 → 참조 여부 grep 확인 후 기존 아카이브 위치 `~/.hermes/skills/.archive/`로 이동(삭제 아님 — 레퍼런스 문서 보존).

### 5. Hermes 대화 정합성 (drift 방지)
- Hermes는 #비서실에서 더 넓은 4단계 안의 `a` 승인을 기다리는 중. 이 세션이 직원 채널 삭제를 완료하면, 사용자가 #비서실에 "직원 채널 삭제는 로컬에서 완료됨, 알림 통합 논의는 계속" 취지로 답할 수 있도록 **제안 답장 문구를 최종 보고에 포함**한다 (무턱대고 `a` 승인하면 중복 실행 위험).
- Hermes 장기 메모리(`~/.hermes/memories/MEMORY.md`)에는 Paperclip 비운영 원칙이 이미 기록돼 있어 추가 변경 불필요.

## 검증

- `list_channels` 재조회: 삭제된 11개 채널이 길드에서 사라졌는지 확인.
- `~/.hermes/channel_directory.json`에서 삭제 ID grep = 0건 (게이트웨이 재시작 후).
- `~/.hermes/scripts/hermes_discord_allowlist_sync.py` 1회 수동 실행 후 `.env`/`config.yaml` allowlist에 삭제 ID가 재추가되지 않았는지 diff 확인.
- `systemctl --user is-active hermes-gateway` = active, 저널에 에러 없음.
- discord_tool 테스트: `.venv` python으로 pytest 통과.
- 사용자 최종 확인: #비서실에서 Hermes 응답 정상 (외부 전송이므로 사용자가 직접).

## 위험 / 남는 것

- 채널 삭제는 비가역 (Discord 메시지 기록 소실). 대상은 봇 알림 위주 채널이며 Paperclip 퇴역 아카이브(3.1GB)는 별도 보존 — 그래도 2단계 최종 게이트에서 목록 재확인.
- 봇에 MANAGE_CHANNELS 권한이 없을 수 있음 → 권한 부여 또는 수동 삭제 fallback.
- 삭제된 채널 하위 스레드(handoff 알림 스레드 등)도 함께 사라짐 — 정상 동작.
- repo 코드 변경(delete_channel)은 현재 브랜치 `runtime/main-sync-20260706b`에 작업, 커밋/푸시는 사용자 지시 시.

"""tmux-backed assistant jobs for the messaging gateway."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shlex
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

from hermes_constants import get_hermes_home
from utils import atomic_json_write


DEFAULT_TMUX_SESSION = "hermes"
CODEX_COMMAND = "cx"
CLAUDE_COMMAND = "claude"
CLAUDE_CODE_WRAPPER = "ccd"

_REPORT_RE = re.compile(
    r"<HERMES_REPORT>\s*(.*?)\s*</HERMES_REPORT>",
    re.IGNORECASE | re.DOTALL,
)
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_SLUG_RE = re.compile(r"[^a-z0-9_-]+")


@dataclass
class TmuxJobRecord:
    id: str
    agent: str
    prompt: str
    pane_id: str
    window_name: str
    tmux_session: str
    workdir: str
    platform: str
    chat_id: str
    effort: str = ""
    model: str = ""
    thread_id: str = ""
    chat_type: str = ""
    user_id: str = ""
    user_name: str = ""
    reply_to_message_id: str = ""
    status: str = "run"
    last_status: str = "run"
    last_status_marker: str = ""
    last_notified_status: str = ""
    last_notified_marker: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TmuxJobRecord":
        fields = cls.__dataclass_fields__
        clean = {key: data.get(key) for key in fields if key in data}
        return cls(**clean)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TmuxJobSnapshot:
    status: str
    marker: str
    pane_tail: str
    summary: str


@dataclass(frozen=True)
class TmuxJobStatusChange:
    record: TmuxJobRecord
    previous_status: str
    snapshot: TmuxJobSnapshot


@dataclass(frozen=True)
class TmuxStartSpec:
    agent: str
    prompt: str
    effort: str = ""
    model: str = ""


class TmuxJobRegistry:
    """Persist and supervise Hermes-owned tmux jobs."""

    def __init__(
        self,
        home: Path | None = None,
        *,
        tmux_session: str = DEFAULT_TMUX_SESSION,
    ) -> None:
        self.home = Path(home) if home is not None else get_hermes_home()
        self.tmux_session = tmux_session
        self.state_dir = self.home / "gateway" / "tmux"
        self.state_path = self.state_dir / "jobs.json"

    def load(self) -> list[TmuxJobRecord]:
        if not self.state_path.exists():
            return []
        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception:
            return []
        items = raw.get("jobs", raw) if isinstance(raw, dict) else raw
        if not isinstance(items, list):
            return []
        records: list[TmuxJobRecord] = []
        for item in items:
            if isinstance(item, dict):
                try:
                    records.append(TmuxJobRecord.from_dict(item))
                except TypeError:
                    continue
        return records

    def save_all(self, records: Iterable[TmuxJobRecord]) -> None:
        payload = {
            "schema_version": 1,
            "updated_at": time.time(),
            "jobs": [record.to_dict() for record in records],
        }
        atomic_json_write(self.state_path, payload, indent=2)

    def save(self, record: TmuxJobRecord) -> None:
        records = self.load()
        for idx, existing in enumerate(records):
            if existing.id == record.id:
                records[idx] = record
                break
        else:
            records.append(record)
        self.save_all(records)

    def find(self, target: str) -> TmuxJobRecord | None:
        target = target.strip()
        if not target:
            return None
        for record in self.load():
            if target in {record.id, record.pane_id}:
                return record
        if target.startswith("%"):
            return None
        matches = [
            record for record in self.load()
            if record.id.startswith(target) or record.window_name == target
        ]
        return matches[0] if len(matches) == 1 else None

    def start_job(
        self,
        *,
        agent: str,
        prompt: str,
        workdir: str,
        platform: str,
        chat_id: str,
        effort: str = "",
        model: str = "",
        thread_id: str = "",
        chat_type: str = "",
        user_id: str = "",
        user_name: str = "",
        reply_to_message_id: str = "",
        send_initial: bool = True,
    ) -> TmuxJobRecord:
        agent = normalize_agent(agent)
        effort = normalize_effort(effort)
        job_id = self._new_job_id()
        window_name = self._window_name(agent, job_id, prompt)
        workdir = str(Path(os.path.expanduser(workdir or "~")).resolve())
        Path(workdir).mkdir(parents=True, exist_ok=True)

        pane_id = self._create_window(
            window_name=window_name,
            workdir=workdir,
            agent=agent,
            effort=effort,
            model=model,
        )
        record = TmuxJobRecord(
            id=job_id,
            agent=agent,
            prompt=prompt,
            pane_id=pane_id,
            window_name=window_name,
            tmux_session=self.tmux_session,
            workdir=workdir,
            platform=platform,
            chat_id=str(chat_id or ""),
            effort=effort,
            model=str(model or ""),
            thread_id=str(thread_id or ""),
            chat_type=str(chat_type or ""),
            user_id=str(user_id or ""),
            user_name=str(user_name or ""),
            reply_to_message_id=str(reply_to_message_id or ""),
        )
        self._tag_pane(record)
        self.save(record)

        if send_initial and prompt.strip():
            time.sleep(0.8)
            self.send(record, build_worker_prompt(prompt))
        return record

    def send(self, record: TmuxJobRecord, text: str) -> None:
        self._tmux(["load-buffer", "-b", record.id, "-"], input_text=text)
        self._tmux(["paste-buffer", "-b", record.id, "-t", record.pane_id])
        self._tmux(["send-keys", "-t", record.pane_id, "Enter"])

    def read_tail(self, target: str, *, lines: int = 80) -> str:
        record = self.find(target)
        pane_id = record.pane_id if record else target
        return self._capture_pane(pane_id, lines=lines)

    def poll(self, record: TmuxJobRecord) -> TmuxJobSnapshot:
        if not self._pane_exists(record.pane_id):
            tail = ""
            return TmuxJobSnapshot(
                status="dead",
                marker="pane-missing",
                pane_tail=tail,
                summary="tmux pane is no longer available.",
            )

        self._sync_pulse(record.agent)
        reason = self._tmux(
            ["show-option", "-pqv", "-t", record.pane_id, "@pulse_reason"],
            check=False,
        ).stdout.strip()
        source = self._tmux(
            ["show-option", "-pqv", "-t", record.pane_id, "@pulse_source"],
            check=False,
        ).stdout.strip()
        tail = self._capture_pane(record.pane_id, lines=100)
        status = classify_tmux_status(
            agent=record.agent,
            pulse_source=source,
            pulse_reason=reason,
            pane_tail=tail,
            previous_status=record.status,
        )
        marker = self._status_marker(status, reason, tail)
        return TmuxJobSnapshot(
            status=status,
            marker=marker,
            pane_tail=tail,
            summary=summarize_pane_tail(tail),
        )

    def poll_all(self) -> list[TmuxJobStatusChange]:
        records = self.load()
        changes: list[TmuxJobStatusChange] = []
        for record in records:
            previous = record.status
            snapshot = self.poll(record)
            record.last_status = previous
            record.status = snapshot.status
            record.last_status_marker = snapshot.marker
            record.updated_at = time.time()
            if snapshot.status != previous:
                changes.append(TmuxJobStatusChange(record, previous, snapshot))
        if records:
            self.save_all(records)
        return changes

    def mark_notified(self, record_id: str, status: str) -> None:
        records = self.load()
        for record in records:
            if record.id == record_id:
                record.last_notified_status = status
                record.last_notified_marker = record.last_status_marker
                record.updated_at = time.time()
                break
        self.save_all(records)

    def _new_job_id(self) -> str:
        stamp = time.strftime("%Y%m%d%H%M%S")
        return f"tmux_{stamp}_{secrets.token_hex(3)}"

    def _window_name(self, agent: str, job_id: str, prompt: str) -> str:
        slug = _SLUG_RE.sub("-", prompt.lower()).strip("-")[:22] or "job"
        prefix = "cx" if agent == "codex" else "claude"
        return f"{prefix}-{job_id[-6:]}-{slug}"[:48]

    def _create_window(
        self,
        *,
        window_name: str,
        workdir: str,
        agent: str,
        effort: str = "",
        model: str = "",
    ) -> str:
        command = shlex.join(agent_command_args(agent, effort=effort, model=model))
        has_session = self._tmux(["has-session", "-t", self.tmux_session], check=False)
        if has_session.returncode != 0:
            self._tmux(
                [
                    "new-session",
                    "-d",
                    "-s",
                    self.tmux_session,
                    "-n",
                    window_name,
                    "-c",
                    workdir,
                    command,
                ]
            )
            result = self._tmux(
                [
                    "display-message",
                    "-p",
                    "-t",
                    f"{self.tmux_session}:{window_name}.0",
                    "#{pane_id}",
                ]
            )
            return result.stdout.strip()

        result = self._tmux(
            [
                "new-window",
                "-P",
                "-F",
                "#{pane_id}",
                "-t",
                f"{self.tmux_session}:",
                "-n",
                window_name,
                "-c",
                workdir,
                command,
            ]
        )
        return result.stdout.strip()

    def _tag_pane(self, record: TmuxJobRecord) -> None:
        tags = {
            "@hermes_tmux_job_id": record.id,
            "@hermes_tmux_agent": record.agent,
            "@hermes_tmux_chat_id": record.chat_id,
        }
        for key, value in tags.items():
            self._tmux(
                ["set-option", "-p", "-t", record.pane_id, key, value],
                check=False,
            )

    def _pane_exists(self, pane_id: str) -> bool:
        result = self._tmux(
            ["display-message", "-p", "-t", pane_id, "#{pane_id}"],
            check=False,
        )
        return result.returncode == 0 and result.stdout.strip() == pane_id

    def _capture_pane(self, pane_id: str, *, lines: int = 80) -> str:
        result = self._tmux(
            ["capture-pane", "-t", pane_id, "-p", "-S", f"-{lines}"],
            check=False,
        )
        return strip_ansi(result.stdout or "")

    def _sync_pulse(self, agent: str) -> None:
        script = Path.home() / ".tmux" / "scripts" / (
            "codex-pulse-sync.sh" if agent == "codex" else "claude-pulse-sync.sh"
        )
        if not script.exists():
            return
        try:
            subprocess.run([str(script), "--force"], check=False, timeout=4)
        except (OSError, subprocess.TimeoutExpired):
            return

    def _status_marker(self, status: str, pulse_reason: str, tail: str) -> str:
        if pulse_reason:
            return f"{status}:{pulse_reason}"
        digest = hashlib.sha256(tail[-2000:].encode("utf-8", "ignore")).hexdigest()[:16]
        return f"{status}:{digest}"

    def _tmux(
        self,
        args: list[str],
        *,
        input_text: str | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            ["tmux", *args],
            input=input_text,
            text=True,
            capture_output=True,
            check=False,
            timeout=8,
        )
        if check and result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "tmux command failed")
        return result


def normalize_agent(agent: str | None) -> str:
    value = (agent or "codex").strip().lower()
    if value in {"codex", "cx"}:
        return "codex"
    if value in {"claude", "cc"}:
        return "claude"
    raise ValueError("agent must be codex or claude")


def normalize_effort(effort: str | None) -> str:
    value = (effort or "").strip().lower()
    if not value:
        return ""
    if value not in {"low", "medium", "high", "xhigh", "max"}:
        raise ValueError("effort must be low, medium, high, xhigh, or max")
    return value


def agent_command_args(agent: str, *, effort: str = "", model: str = "") -> list[str]:
    agent = normalize_agent(agent)
    effort = normalize_effort(effort)
    if agent == "codex":
        args = [CODEX_COMMAND]
    elif shutil.which(CLAUDE_CODE_WRAPPER):
        args = [CLAUDE_CODE_WRAPPER]
    else:
        args = [CLAUDE_COMMAND, "--permission-mode", "auto"]
    if model:
        args.extend(["--model", model])
    if effort:
        if agent == "claude":
            args.extend(["--effort", effort])
        else:
            if effort == "max":
                raise ValueError("Codex effort supports low, medium, high, or xhigh")
            args.extend(["-c", f'model_reasoning_effort="{effort}"'])
    return args


def parse_tmux_command_args(raw: str) -> tuple[str, str]:
    """Return (subcommand, remainder) for /tmux."""
    raw = (raw or "").strip()
    if not raw:
        return "help", ""
    try:
        parts = shlex.split(raw)
    except ValueError:
        return "start", raw
    if not parts:
        return "help", ""
    if parts[0] in {"list", "read", "send", "status", "help"}:
        return parts[0], raw[len(parts[0]):].strip()
    return "start", raw


def parse_start_args(raw: str, default_agent: str = "codex") -> TmuxStartSpec:
    """Parse tmux launch args."""
    try:
        parts = shlex.split(raw)
    except ValueError:
        return TmuxStartSpec(normalize_agent(default_agent), raw.strip())
    agent = normalize_agent(default_agent)
    effort = ""
    model = ""
    prompt_parts: list[str] = []
    idx = 0
    while idx < len(parts):
        part = parts[idx]
        if part == "--claude":
            agent = "claude"
        elif part == "--codex":
            agent = "codex"
        elif part == "--agent" and idx + 1 < len(parts):
            idx += 1
            agent = normalize_agent(parts[idx])
        elif part == "--effort" and idx + 1 < len(parts):
            idx += 1
            effort = normalize_effort(parts[idx])
        elif part in {"--low", "--medium", "--high", "--xhigh", "--max"}:
            effort = normalize_effort(part[2:])
        elif part in {"--model", "-m"} and idx + 1 < len(parts):
            idx += 1
            model = parts[idx]
        else:
            prompt_parts.extend(parts[idx:])
            break
        idx += 1
    if agent == "codex" and effort == "max":
        raise ValueError("Codex effort supports low, medium, high, or xhigh")
    return TmuxStartSpec(agent, " ".join(prompt_parts).strip(), effort=effort, model=model)


def build_worker_prompt(prompt: str) -> str:
    return (
        f"{prompt.strip()}\n\n"
        "When you need the user, finish your visible response with a short "
        "<HERMES_REPORT> block. When you are done, include the same block.\n"
        "<HERMES_REPORT>\n"
        "status: run | ask | ok | failed\n"
        "summary: one short sentence\n"
        "next: what the user should do, or none\n"
        "</HERMES_REPORT>"
    )


def extract_report_block(text: str) -> str:
    matches = _REPORT_RE.findall(text or "")
    return matches[-1].strip() if matches else ""


def summarize_pane_tail(text: str) -> str:
    report = extract_report_block(text)
    if report:
        return report
    lines = [
        line.strip()
        for line in strip_ansi(text).splitlines()
        if line.strip()
    ]
    if not lines:
        return "(no visible output yet)"
    last_line = lines[-1]
    if len(last_line) > 500:
        last_line = f"{last_line[:500]}..."
    return f"No structured report found. Last visible line: {last_line}"


def classify_tmux_status(
    *,
    agent: str,
    pulse_source: str,
    pulse_reason: str,
    pane_tail: str,
    previous_status: str = "run",
) -> str:
    report = extract_report_block(pane_tail).lower()
    if report:
        if re.search(r"status\s*:\s*(ok|done|complete|completed)", report):
            return "ok"
        if re.search(r"status\s*:\s*(ask|needs_input|input|approval)", report):
            return "ask"
        if re.search(r"status\s*:\s*(failed|error|blocked)", report):
            return "failed"
        if re.search(r"status\s*:\s*(run|running)", report):
            return "run"

    reason = (pulse_reason or "").lower()
    source = (pulse_source or "").lower()
    if source in {agent, "codex", "claude"}:
        if "done" in reason:
            return "ok"
        if "waiting" in reason or "prompt" in reason or "approval" in reason:
            return "ask"
        if "active" in reason or "running" in reason or "spinner" in reason:
            return "run"

    lower_tail = pane_tail.lower()
    if re.search(r"(approval requested|needs your approval|waiting for (approval|input|user))", lower_tail):
        return "ask"
    if "worked for " in lower_tail and previous_status in {"run", "ask"}:
        return "ok"
    if re.search(r"(traceback \(most recent call last\)|command not found|fatal:|uncaught exception)", lower_tail):
        return "failed"
    return previous_status or "run"


def strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text or "")

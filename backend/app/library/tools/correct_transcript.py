"""correct_transcript — misheard words in a recording's automatic transcript, fixed before minutes are written.

Shared library: a system binds its meeting store, its glossary and the agent that writes minutes with
make_transcript_correction(store, glossary_path, agent). Two entry points over one workflow
(../workflows/transcript_correction):

  .prepare(request)  run by the system on a direct request before the Meeting Agent reads it: the transcript
                     lines of the current request are corrected in place and a note says what changed. When the
                     model step fails (in part or whole), those lines keep the fixed replacements only, and the
                     note says so and asks the agent to call the tool once before writing the minutes.
  .tool              agent-visible correct_transcript(transcript_id): the same correction of the transcript the
                     speech service kept, for that retry.

The prepare step reports as tool_start / tool_end of correct_transcript (args.auto = true) like a call the
agent made, so the chat and the monitors show it without a new event type. TRANSCRIPT_CORRECTION=0 turns
the automatic step off (the tool stays).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import BaseTool, tool

from ...runtime.agent_core import message_text
from ...runtime.events import emit
from ...runtime.llm import get_chat_model
from ..workflows.compliance import clock
from ..workflows.meeting_store import MeetingStore
from ..workflows.transcript_correction import (
    CHECKIN, TRANSCRIPT_ID, Correction, Glossary, correct_lines, correct_text, known_names, split,
)
from ..workflows.transcripts import TranscriptUnavailable, fetch_transcript

log = logging.getLogger(__name__)

TOOL_NAME = "correct_transcript"
CURRENT = "[현재 요청]\n"  # the gateway puts earlier turns before this; only the current request is corrected


def enabled() -> bool:
    return os.environ.get("TRANSCRIPT_CORRECTION", "1").strip().lower() not in ("0", "off", "false", "no")


def ask_model(system: str, user: str) -> str:
    return message_text(get_chat_model().invoke([SystemMessage(system), HumanMessage(user)]))


def note(result: Correction, transcript_id: str | None) -> str:
    """What the agent reads after the corrected transcript."""
    if not result.failed:
        return f"[자동 교정] 녹음 변환본의 잘못 들린 말을 고쳤습니다 — {result.summary()}. 원래 변환본은 그대로 보관되어 있습니다."
    retry = (f'회의록을 쓰기 전에 {TOOL_NAME}(transcript_id="{transcript_id}")를 한 번 호출해 돌려받은 lines로 쓰고, '
             "그것도 실패하면 이 변환본으로 쓴다." if transcript_id else "다시 교정할 수 없으니 이 변환본으로 쓴다.")
    return (f"[자동 교정 실패] 녹음 변환본의 모델 교정이 끝나지 않았습니다 — {result.summary()}. "
            f"고정 치환만 적용된 줄이 있습니다. {retry} 답변에서 교정하지 못한 부분이 있다고 밝힌다.")


@dataclass
class TranscriptCorrection:
    store: MeetingStore
    glossary_path: Path
    agent: str  # the agent that writes minutes (the prepare step reports as its tool call)
    tool: BaseTool | None = None

    def glossary(self) -> Glossary:
        return Glossary.load(self.glossary_path)

    def names(self, checkin_code: str | None) -> list[str]:
        try:
            with self.store.connect() as conn:
                return known_names(conn, checkin_code)
        except Exception:  # no store: the model corrects with the terms only
            log.warning("transcript correction: no names", exc_info=True)
            return []

    def prepare(self, request: str) -> str:
        """The request with the transcript in its current part corrected and a note appended (unchanged without one)."""
        if not enabled():
            return request
        head, sep, current = request.rpartition(CURRENT)
        target = current if sep else request
        _, at = split(target)
        if not at:
            return request
        found = TRANSCRIPT_ID.search(target)
        transcript_id = found.group(1) if found else None
        code = CHECKIN.search(target)
        emit({"type": "tool_start", "agent": self.agent, "tool": TOOL_NAME,
              "args": {"transcript_id": transcript_id, "lines": len(at), "auto": True}})
        glossary, names = self.glossary(), self.names(code.group(1) if code else None)
        try:
            fixed, result = correct_text(target, glossary, names, ask_model)
        except Exception as e:  # a bug, not a model failure (those are per chunk): keep the replacements
            log.exception("transcript correction failed")
            fixed, result = correct_text(target, glossary, names, None)
            result.failed.append(f"전체: {type(e).__name__}")
        emit({"type": "tool_end", "agent": self.agent, "tool": TOOL_NAME,
              "result": {"auto": True, "summary": result.summary(), "changed": result.changed, "failed": result.failed}})
        corrected = f"{fixed}\n\n{note(result, transcript_id)}"
        return f"{head}{sep}{corrected}" if sep else corrected

    def correct(self, transcript_id: str) -> dict[str, Any]:
        try:
            data = fetch_transcript(transcript_id)
        except TranscriptUnavailable as e:
            return {"error": str(e)}
        lines = [f"[{clock(t['start'])}] {t['speaker']}: {t['text']}" for t in data.get("turns") or [] if t.get("text")]
        result = correct_lines(lines, self.glossary(), self.names(None), ask_model)
        out: dict[str, Any] = {"transcript_id": data.get("id", transcript_id), "summary": result.summary(),
                               "lines": "\n".join(result.lines)}
        if result.failed:
            out["failed"] = result.failed
        return out


def make_transcript_correction(store: MeetingStore, glossary_path: Path, agent: str) -> TranscriptCorrection:
    """The prepare step and the Tool, bound to one system's meeting store and glossary."""
    fixer = TranscriptCorrection(store, glossary_path, agent)

    @tool(TOOL_NAME)
    def correct_transcript(transcript_id: str) -> dict[str, Any]:
        """녹음 자동 변환본의 잘못 들린 말(용어 · 이름)과 띄어쓰기를 고친 변환본 줄(lines)을 돌려준다.
    요청에 "[자동 교정 실패]"가 있을 때 회의록을 쓰기 전에 한 번 부른다. 문장은 바꾸지 않으며 원래 변환본은 그대로 보관된다.

    Args:
        transcript_id: 변환본 첫 줄의 id (예: TR-20261103-1A2B3C).
    """
        return fixer.correct(transcript_id)

    fixer.tool = correct_transcript
    return fixer

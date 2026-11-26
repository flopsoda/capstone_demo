"""Correcting a recording's automatic transcript before the Meeting Agent writes minutes from it.

Speech-to-text mishears domain words and names ("에젠드" for 에이전트) and breaks Korean spacing. The
lines of a transcript ("[mm:ss] 화자 A: …") are corrected in two layers:

  1. replacements — the system's fixed list of words the recognizer is known to get wrong, applied as
     plain substitutions (cannot fail);
  2. the model — chunks of CHUNK_LINES lines, with a few lines of context on either side and the
     system's terms and the known names (directory, the meeting's check-in), asked to fix misheard
     words and spacing only, answering with the changed lines as JSON.

A model edit that rewrites the line instead of fixing words (too little left of the original, or
the length changes too much) is rejected and the line stays as it was: a transcript is a record,
and a wrong fix is worse than a misheard word. A chunk whose call fails or whose answer cannot be
read keeps its replacements-only text and is reported in `failed`. The speaker labels and times are
never sent for editing. The original transcript stays with the speech service (and is copied into
the meeting store unchanged when the minutes are saved).

Not agent-visible; no LangChain dependency — the caller passes `ask(system, user) -> text`.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path

import psycopg

from .attendance import CODE as CHECKIN_CODE

LINE = re.compile(r"^(\[(?:\d{1,2}:)?\d{1,2}:\d{2}\] [^:\n]{1,60}: )(.*)$")  # "[03:15] 화자 A: " + what was said
TRANSCRIPT_ID = re.compile(r"\[transcript_id:\s*(TR-\d{8}-[0-9A-F]{6})\]")
CHECKIN = re.compile(r"출석 확인:\s*(CHK-[A-Z2-9]{6})")
CHUNK_LINES = 30
CONTEXT_LINES = 3
CALL_TIMEOUT_SECONDS = 120.0
MIN_KEPT = 0.6  # share of the original characters (spaces ignored) a corrected line must keep
MAX_LENGTH_CHANGE = 0.3  # a corrected line may be at most this much shorter or longer (spaces ignored)
MAX_NAMES = 200
EXAMPLES = 5  # changes quoted back to the agent and the user

Ask = Callable[[str, str], str]  # (system prompt, user prompt) -> the model's text
Progress = Callable[[int, int], None]  # (chunks done, chunks)

SYSTEM_PROMPT = """너는 회의 녹음 자동 변환본(음성 인식 결과)의 교정자다. 잘못 들린 단어와 띄어쓰기만 고친다.
규칙:
- 문장을 다시 쓰거나 다듬거나 요약하지 않는다. 말한 내용을 더하거나 빼지 않는다. 말버릇 · 반복 · 비문도 그대로 둔다.
- 용어 목록이나 이름 목록의 말이 잘못 들린 것이 분명할 때만 그 표기로 고친다. 확실하지 않으면 그대로 둔다.
- 숫자 · 날짜 · 금액은 들린 그대로 둔다.
- "고칠 줄"만 고친다. 앞뒤 맥락 줄은 읽기만 한다.
- 답은 JSON 배열 하나만 쓴다: 고친 줄만 [{"n": 줄 번호, "text": "고친 발언"}]. 고칠 줄이 없으면 []. 다른 말은 쓰지 않는다."""


class CorrectionError(RuntimeError):
    pass


@dataclass(frozen=True)
class Glossary:
    terms: tuple[str, ...] = ()
    replacements: tuple[tuple[str, str], ...] = ()  # (misheard, correct), longest first

    @classmethod
    def load(cls, path: Path) -> Glossary:
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        pairs = sorted(((str(k), str(v)) for k, v in (data.get("replacements") or {}).items() if k and k != v),
                       key=lambda kv: -len(kv[0]))
        return cls(tuple(str(t) for t in data.get("terms") or []), tuple(pairs))


@dataclass
class Correction:
    lines: list[str]  # every transcript line, corrected ("[mm:ss] 화자 A: …")
    changes: list[tuple[int, str, str]] = field(default_factory=list)  # (line number from 1, before, after) per word
    changed: int = 0  # lines that differ from the original
    by_replacement: int = 0  # lines changed by the fixed replacements
    by_model: int = 0  # lines the model changed
    rejected: int = 0  # model edits refused as rewrites
    failed: list[str] = field(default_factory=list)  # "줄 31–60: 원인" per chunk the model could not do

    def summary(self) -> str:
        """One Korean line for the agent and the user."""
        out = f"변환본 {len(self.lines)}줄 중 {self.changed}줄 교정"
        if self.changes:
            out += " (예: " + ", ".join(f"{b}→{a}" for _, b, a in self.changes[:EXAMPLES]) + ")"
        if self.rejected:
            out += f", 문장을 바꾼 교정 {self.rejected}건은 버림"
        if self.failed:
            out += f", 모델 교정 실패: {'; '.join(self.failed)}"
        return out


def split(text: str) -> tuple[list[str], list[int]]:
    """All lines of `text`, and the indexes of those that are transcript lines."""
    rows = text.split("\n")
    return rows, [i for i, row in enumerate(rows) if LINE.match(row)]


def _plain(s: str) -> str:
    return re.sub(r"\s+", "", s)


def is_word_fix(before: str, after: str) -> bool:
    """A correction that fixes words and spacing, not one that rewrites what was said."""
    a, b = _plain(before), _plain(after)
    if not a:
        return a == b
    if abs(len(b) - len(a)) > MAX_LENGTH_CHANGE * len(a):
        return False
    return SequenceMatcher(None, a, b, autojunk=False).ratio() >= MIN_KEPT


def word_changes(before: str, after: str) -> list[tuple[str, str]]:
    """The words that changed, for showing what was corrected (a spacing-only fix is not listed)."""
    a, b = before.split(), after.split()
    out = []
    for op, i1, i2, j1, j2 in SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op != "equal":
            old, new = " ".join(a[i1:i2]), " ".join(b[j1:j2])
            if _plain(old) != _plain(new):
                out.append((old or "∅", new or "∅"))
    return out


def echoed_label(text: str, prefix: str) -> str:
    """A model may copy the line's "[n] 화자 A:" in front of the corrected words: drop it."""
    text = re.sub(r"^\[\d+\]\s*", "", text)
    label = prefix.split("] ", 1)[-1].strip()  # "화자 A:"
    return text[len(label):].lstrip() if label and text.startswith(label) else text


def replace_known(body: str, glossary: Glossary) -> str:
    for wrong, right in glossary.replacements:
        body = body.replace(wrong, right)
    return body


def parse_answer(text: str, first: int, last: int) -> dict[int, str]:
    """The model's JSON array → {line number: corrected text} for lines first..last."""
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end < start:
        raise CorrectionError("답에 JSON 배열이 없습니다")
    try:
        items = json.loads(text[start:end + 1])
    except json.JSONDecodeError as e:
        raise CorrectionError(f"JSON을 읽을 수 없습니다 ({e.msg})") from e
    if not isinstance(items, list):
        raise CorrectionError("답이 배열이 아닙니다")
    out: dict[int, str] = {}
    for item in items:
        if isinstance(item, dict) and isinstance(item.get("n"), int) and isinstance(item.get("text"), str):
            if first <= item["n"] <= last:
                out[item["n"]] = item["text"].strip()
    return out


def _prompt(bodies: list[str], prefixes: list[str], lo: int, hi: int, glossary: Glossary, names: list[str]) -> str:
    def rows(a: int, b: int) -> str:
        return "\n".join(f"[{n + 1}] {prefixes[n].split('] ', 1)[-1]}{bodies[n]}" for n in range(a, b))  # "[n] 화자 A: …"

    parts = []
    if glossary.terms:
        parts.append("용어: " + ", ".join(glossary.terms))
    if names:
        parts.append("이름: " + ", ".join(names))
    if lo > 0:
        parts.append("앞 맥락 (고치지 말 것):\n" + rows(max(0, lo - CONTEXT_LINES), lo))
    parts.append(f"고칠 줄 ({lo + 1}–{hi}):\n" + rows(lo, hi))
    if hi < len(bodies):
        parts.append("뒤 맥락 (고치지 말 것):\n" + rows(hi, min(len(bodies), hi + CONTEXT_LINES)))
    return "\n\n".join(parts)


def _call(ask: Ask, system: str, user: str, timeout: float) -> str:
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        return pool.submit(ask, system, user).result(timeout=timeout)
    except FutureTimeout as e:
        raise CorrectionError(f"{timeout:.0f}초 안에 답이 없습니다") from e
    finally:
        pool.shutdown(wait=False)  # a call that timed out is left to finish on its own


def correct_lines(lines: list[str], glossary: Glossary, names: list[str], ask: Ask | None, *,
                  progress: Progress | None = None, timeout: float = CALL_TIMEOUT_SECONDS) -> Correction:
    """`lines` are transcript lines ("[mm:ss] 화자 A: …"). `ask` None: the fixed replacements only."""
    parsed = [LINE.match(line) for line in lines]
    prefixes = [m.group(1) if m else "" for m in parsed]
    original = [m.group(2) if m else line for m, line in zip(parsed, lines)]
    bodies = [replace_known(b, glossary) for b in original]
    result = Correction(lines=[])
    result.by_replacement = sum(1 for a, b in zip(original, bodies) if a != b)
    by_model: set[int] = set()
    if ask is not None:
        chunks = [(lo, min(lo + CHUNK_LINES, len(bodies))) for lo in range(0, len(bodies), CHUNK_LINES)]
        for done, (lo, hi) in enumerate(chunks):
            if progress:
                progress(done, len(chunks))
            try:
                answer = _call(ask, SYSTEM_PROMPT, _prompt(bodies, prefixes, lo, hi, glossary, names), timeout)
                fixes = parse_answer(answer, lo + 1, hi)
            except Exception as e:  # any failure of one chunk keeps that chunk as it is
                result.failed.append(f"줄 {lo + 1}–{hi}: {e if isinstance(e, CorrectionError) else type(e).__name__}")
                continue
            for n, text in fixes.items():
                i = n - 1
                text = echoed_label(text, prefixes[i])
                if text == bodies[i]:
                    continue
                if not is_word_fix(bodies[i], text):
                    result.rejected += 1
                    continue
                bodies[i] = text
                by_model.add(i)
        if progress:
            progress(len(chunks), len(chunks))
    result.by_model = len(by_model)
    result.changed = sum(1 for a, b in zip(original, bodies) if a != b)
    for i, (before, after) in enumerate(zip(original, bodies)):
        result.changes += [(i + 1, b, a) for b, a in word_changes(before, after)]
    result.lines = [p + b for p, b in zip(prefixes, bodies)]
    return result


def correct_text(text: str, glossary: Glossary, names: list[str], ask: Ask | None, **kw) -> tuple[str, Correction] | None:
    """`text` with its transcript lines corrected in place (None when it has no transcript lines)."""
    rows, at = split(text)
    if not at:
        return None
    result = correct_lines([rows[i] for i in at], glossary, names, ask, **kw)
    for i, line in zip(at, result.lines):
        rows[i] = line
    return "\n".join(rows), result


def known_names(conn: psycopg.Connection, checkin_code: str | None = None) -> list[str]:
    """Names the transcript may contain: who checked in to this recording's meeting, then the directory."""
    names: list[str] = []
    if checkin_code and CHECKIN_CODE.match(checkin_code):
        names += [r["name"] for r in conn.execute(
            """SELECT a.name FROM checkin_attendees a JOIN checkin_sessions s ON s.id = a.session_id
               WHERE s.code = %s ORDER BY a.checked_in_at""", (checkin_code,)).fetchall() if r["name"]]
    names += [r["name"] for r in conn.execute("SELECT name FROM staff_directory ORDER BY name LIMIT %s", (MAX_NAMES,))]
    return list(dict.fromkeys(n.strip() for n in names if n and n.strip()))[:MAX_NAMES]

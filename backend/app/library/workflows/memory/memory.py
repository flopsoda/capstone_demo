"""SuperAgentMemory — a Super Agent's long-term memory of its direct conversations (runtime.memory.ConversationMemory).

    remember   after a direct run: the user's current request, the answer and what was handed off, kept once per
               run_id under the user's key; split into chunks and embedded (the configured provider, embedding.py).
    recall     before a direct run: that user's past exchanges closest to the current request — by meaning
               (pgvector) first, by words (pg_trgm) as a helper, fused by rank (RRF) — oldest first with their time, as
               one paragraph for the system context. Exchanges the request already carries (the gateway's [이전 대화])
               are left out, so the recent turns are not given twice.

Whose memory: the principal's email (the gateway signs it). A run without one is not remembered, except for a
local demo that names a user (MEMORY_ANONYMOUS_USER). A2A runs are neither recalled nor remembered: they are another
system's request, not a conversation with a user. MEMORY_ENABLED=0 turns the memory off.
Kept for good: nothing is pruned or merged (summaries, extracted preferences and instruction lifetimes are later work).
"""

from __future__ import annotations

import logging
import os
from typing import Any

from ....runtime.events import RunContext
from ....runtime.memory import Recall
from ..meeting_store import KST
from ..news.embed import EmbedUnavailable
from ..news.store import squeeze
from .embedding import MemoryEmbedder, memory_embedder
from .store import MemoryStore

log = logging.getLogger("agents")

HISTORY_MARK, CURRENT_MARK = "[이전 대화]", "[현재 요청]"
MAX_TURNS = 4            # past exchanges given to the model
CANDIDATES = 12          # chunks each search returns before fusion
CHUNK_CHARS = 1200       # answer characters per chunk
PROMPT_CHARS = 400       # answer characters of a recalled exchange in the prompt
SHORT_REQUEST = 15       # a request this short ("그거 다시") is searched with the user's previous line too
# A meaning hit: cosine at least the floor (MEMORY_MIN_SIMILARITY) and within MARGIN of the best hit. Measured with
# text-embedding-3-small on Korean exchanges: related 0.40–0.56, a related one in other words 0.23–0.29, unrelated
# up to 0.32 — a floor alone either drops the second kind or lets the third in; the margin keeps the closest group.
DEFAULT_MIN_SIMILARITY = 0.2
MARGIN = 0.05
WORD_FLOOR = 0.3         # pg_trgm word_similarity floor of a word hit
RRF_K = 60

GUIDE = ("[관련 과거 대화] 이 사용자와 예전에 나눈 대화 중 지금 요청과 관련 있어 보이는 기록이다 (오래된 것부터, 한국 시간).\n"
         "- 사용자의 이전 지시 · 선호 · 맡긴 업무를 이해하는 데 쓴다. 회사 데이터의 근거로 쓰지 않는다: 지금의 사실이 필요하면 담당 에이전트에게 다시 확인한다.\n"
         "- 지금 요청과 다르면 지금 요청을 따르고, 과거 지시끼리 다르면 더 최근 것을 따른다.\n"
         "- 관련이 없으면 무시한다. 위임 · A2A 요청 문장에는 필요한 내용(사용자가 원한 관점 · 형식 · 조건 등)을 풀어 쓴다.")


def enabled() -> bool:
    return os.environ.get("MEMORY_ENABLED", "1").strip().lower() not in ("0", "off", "false", "no")


def min_similarity() -> float:
    try:
        return float(os.environ.get("MEMORY_MIN_SIMILARITY", "") or DEFAULT_MIN_SIMILARITY)
    except ValueError:
        return DEFAULT_MIN_SIMILARITY


def split_request(request: str) -> tuple[str, str]:
    """(earlier turns the gateway attached, the current request)."""
    if CURRENT_MARK in request:
        head, current = request.rsplit(CURRENT_MARK, 1)
        return head.replace(HISTORY_MARK, "", 1).strip(), current.strip()
    return "", request.strip()


def chunks(user_text: str, response: str, handoffs: list[dict[str, Any]]) -> list[str]:
    work = "; ".join(f"{h['tool']}: {(h.get('task') or h.get('request') or '')[:200]}" for h in handoffs)
    parts = [response[i:i + CHUNK_CHARS] for i in range(0, len(response), CHUNK_CHARS)] or [""]
    out = []
    for n, part in enumerate(parts):
        text = f"사용자: {user_text}\nAI{' (계속)' if n else ''}: {part}"
        if n == 0 and work:
            text += f"\n맡긴 일: {work}"
        out.append(text)
    return out


def _clip(text: str, limit: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[:limit] + "…"


class SuperAgentMemory:
    def __init__(self, store: MemoryStore, embedder: MemoryEmbedder | None = None):
        self.store = store
        self._embedder = embedder  # None: the configured provider, read when used

    def embedder(self) -> MemoryEmbedder | None:
        return self._embedder if self._embedder is not None else memory_embedder()

    def user_key(self, run: RunContext) -> str | None:
        if run.principal is not None:
            return run.principal.email.strip().lower()
        return os.environ.get("MEMORY_ANONYMOUS_USER", "").strip().lower() or None

    def applies(self, run: RunContext) -> bool:
        configured = bool(self.store.meeting_store._dsn or os.environ.get("MEETINGS_DATABASE_URL"))
        return enabled() and configured and run.origin != "a2a" and self.user_key(run) is not None

    # ── remember ─────────────────────────────────────────────────────────────

    def remember(self, run: RunContext, request: str, response: str, handoffs: list[dict[str, Any]]) -> None:
        _, current = split_request(request)
        pending = self.store.save_turn(run_id=run.run_id, agent=run.root_agent, user_key=self.user_key(run) or "",
                                       user_text=current, response=response, handoffs=handoffs,
                                       chunks=chunks(current, response, handoffs))
        embedder = self.embedder()
        if not pending or embedder is None:
            return
        try:
            vectors = embedder.embed_documents([text for _, text in pending])
        except EmbedUnavailable as e:  # kept without a vector: found by words until memory.reembed adds one
            log.warning("conversation memory: not embedded (%s)", e)
            return
        self.store.save_embeddings(embedder.name, zip((chunk_id for chunk_id, _ in pending), vectors))

    # ── recall ───────────────────────────────────────────────────────────────

    def recall(self, run: RunContext, request: str) -> Recall:
        user = self.user_key(run) or ""
        history, current = split_request(request)
        query = current
        if len(current) < SHORT_REQUEST and history:
            said = [line.split(":", 1)[1].strip() for line in history.splitlines() if line.startswith("사용자:")]
            if said:
                query = f"{said[-1]}\n{current}"
        search: dict[str, Any] = {"mode": "keyword"}
        ranked: dict[int, dict[str, Any]] = {}

        def add(rows: list[dict[str, Any]], how: str) -> None:
            seen = set()
            for row in rows:
                if row["turn_id"] in seen:
                    continue
                seen.add(row["turn_id"])
                hit = ranked.setdefault(row["turn_id"], {**row, "score": 0.0, "matched_by": [], "similarity": None})
                hit["score"] += 1 / (RRF_K + len(seen))
                hit["matched_by"].append(how)
                if how == "meaning":
                    hit["similarity"] = round(float(row["similarity"]), 3)

        recent, skipped = squeeze(history), set()

        def fresh(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
            """Leaves out the exchanges the request already carries (the gateway's recent turns)."""
            out = []
            for row in rows:
                said = squeeze(row["user_text"])
                if len(said) >= 4 and said[:300] in recent:
                    skipped.add(row["turn_id"])
                else:
                    out.append(row)
            return out

        embedder = self.embedder()
        if embedder is None:
            search["note"] = "임베딩 모델이 설정되지 않아 낱말로만 찾았습니다."
        else:
            try:
                vector = embedder.embed_query(query)
                rows = fresh(self.store.by_meaning(user, embedder.name, vector, limit=CANDIDATES, floor=min_similarity()))
                best = max((float(r["similarity"]) for r in rows), default=0.0)
                add([r for r in rows if float(r["similarity"]) >= best - MARGIN], "meaning")
                search = {"mode": "meaning+keyword", "model": embedder.name, "min_similarity": min_similarity(), "margin": MARGIN}
            except EmbedUnavailable as e:
                search = {"mode": "keyword", "error": str(e)}
        add(fresh(self.store.by_words(user, query, limit=CANDIDATES, floor=WORD_FLOOR)), "keyword")
        top = sorted(ranked.values(), key=lambda h: (-h["score"], -h["created_at"].timestamp()))[:MAX_TURNS]
        top.sort(key=lambda h: h["created_at"])
        memories = [{"turn": h["turn_id"], "at": h["created_at"].astimezone(KST).strftime("%Y-%m-%d %H:%M"),
                     "request": h["user_text"], "response": _clip(h["response"], PROMPT_CHARS),
                     "handoffs": [f"{x['tool']}: {_clip(x.get('task') or x.get('request') or '', 120)}" for x in h["handoffs"]],
                     "similarity": h["similarity"], "matched_by": h["matched_by"]} for h in top]
        context = ""
        if memories:
            lines = [GUIDE]
            for n, m in enumerate(memories, 1):
                lines.append(f"{n}) {m['at']} 사용자: {m['request']}")
                lines.append(f"   응답 요지: {m['response']}")
                if m["handoffs"]:
                    lines.append(f"   맡긴 일: {' / '.join(m['handoffs'])}")
            context = "\n".join(lines)
        return Recall(context, {"query": query, "search": search, "memories": memories, "skipped_recent": len(skipped)})

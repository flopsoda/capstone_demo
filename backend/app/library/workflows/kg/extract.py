"""News relation extraction: one news event (its articles) → the entities it names and the relations it states.

One model call per event (the event already groups the articles that report one matter). The answer is checked
field by field like the news analysis (analysis.py): an entity without a name, a relation whose ends are not among
the entities, an article id the event does not have — each is dropped, the rest is kept. The relation word the
model chose and the article's own expression are both kept, so a better relation resolver can re-read them.
Articles under an "AI 학습·활용 금지" notice give the model only their lead, as everywhere else.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from .resolve import PREDICATES, STATUSES, entity_type

EXTRACTOR = "news-rel-v2"  # v2: the names the articles are linked to go into the prompt (one entity per thing)
LEAD_CHARS = 400
NOTICE_CHARS = 200
MAX_ENTITIES = 15
MAX_RELATIONS = 15
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

SYSTEM = ("너는 기업 뉴스에서 기업 · 기관 · 인물 · 제품 사이의 관계를 뽑는 분석가다. 기사에 적힌 사실만 쓰고 추측하지 않는다. "
          "JSON 객체 하나로만 답한다.")


class ExtractionError(Exception):
    pass


def article_text(a: dict[str, Any]) -> str:
    body = a.get("body") or ""
    if body:
        return body[:NOTICE_CHARS] if a.get("usage_notice") else body[:LEAD_CHARS]
    return (a.get("summary") or "")[:LEAD_CHARS]


def linked_names(articles: list[dict[str, Any]]) -> list[str]:
    """Names the stores already link to the articles (the data file's companies, the analyses' entities)."""
    return sorted({n for a in articles for n in (*(a.get("seeded_names") or []), *(a.get("entities") or [])) if n})


def input_hash(event: dict[str, Any], articles: list[dict[str, Any]]) -> str:
    """What the model would read: the same input is never sent twice (kg_extractions)."""
    payload = [EXTRACTOR, event.get("title"), linked_names(articles),
               [(a["id"], a.get("date"), a.get("title"), article_text(a), a.get("key_facts") or []) for a in articles]]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()[:32]


def prompt(event: dict[str, Any], articles: list[dict[str, Any]]) -> str:
    lines = [f"뉴스 사건: {event.get('title')}", "", "기사:"]
    for a in articles:
        lines.append(f"[{a['id']}] {a.get('date')} {a.get('outlet')}: {a.get('title')}")
        text = article_text(a)
        if text:
            lines.append(f"  내용: {text}")
        if a.get("key_facts"):
            lines.append(f"  핵심 사실: {'; '.join(a['key_facts'][:6])}")
    if names := linked_names(articles):
        lines += ["", f"기사에 연결된 이름: {', '.join(names)}"]
    lines += ["", "관계 종류 (predicate):", *(f"- {k}: {v}" for k, v in PREDICATES.items()), "",
              '답 형식: {"entities": [{"name": "기사에 쓰인 이름", "type": "organization | person | product | other", '
              '"aliases": ["기사에 함께 쓰인 같은 대상의 다른 표기 (예: 영문명)"]}], '
              '"relations": [{"subject": "entities의 name", "predicate": "위 관계 종류 중 하나", "object": "entities의 name", '
              '"expression": "기사의 원래 표현 (짧게)", "status": "active | ended | planned | unknown", "date": "YYYY-MM-DD", '
              '"article_ids": ["근거 기사 id"], "evidence": "근거 문장 (기사 그대로, 80자 이내)"}]}',
              "규칙:",
              "- entities에는 기사에 이름이 나온 기업 · 기관 · 인물 · 제품만 넣는다. '국내 가전사', '업계' 같은 일반 명사는 넣지 않는다.",
              "- 같은 대상의 한글 · 영문 표기는 entity 하나로 쓰고 다른 표기는 aliases에 넣는다. '기사에 연결된 이름'에 같은 대상의 "
              "다른 표기가 있으면(예: 한글명과 영문명) 그 표기를 모두 aliases에 넣는다.",
              "- 관계는 기사가 사실로 전하는 것만 쓴다. 전망 · 추측은 쓰지 않고, 검토 · 계획 단계면 status를 planned로 한다.",
              "- status: 관계가 지금 유효하면 active, 중단 · 해지 · 철회 · 종료되었으면 ended.",
              "- 상태가 기사마다 다르면(예: 공급 중 → 공급 중단) 상태마다 관계를 따로 쓰고 date에 그 기사의 날짜를 쓴다.",
              "- 관계의 방향은 관계 종류 설명의 화살표를 따른다 (예: 공급사 → 고객사)."]
    return "\n".join(lines)


def _json(text: str) -> Any:
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ExtractionError("답에 JSON 객체가 없습니다")
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError as e:
        raise ExtractionError(f"JSON을 읽을 수 없습니다 ({e.msg})") from e


def _str(value: Any, limit: int) -> str:
    return str(value).strip()[:limit] if isinstance(value, (str, int, float)) else ""


def parse(text: str, article_ids: list[str]) -> dict[str, Any]:
    """The model's answer → {"entities": [...], "relations": [...]} with only what checks out."""
    data = _json(text)
    if not isinstance(data, dict):
        raise ExtractionError("답이 JSON 객체가 아닙니다")
    entities, names = [], {}
    for e in data.get("entities") or []:
        if not isinstance(e, dict) or not (name := _str(e.get("name"), 60)):
            continue
        aliases = [a for a in (_str(x, 60) for x in e.get("aliases") or [] if isinstance(e.get("aliases"), list)) if a and a != name][:5]
        if name in names:
            continue
        names[name] = len(entities)
        entities.append({"name": name, "type": entity_type(e.get("type")) or "other", "aliases": aliases})
        for a in aliases:
            names.setdefault(a, names[name])
        if len(entities) >= MAX_ENTITIES:
            break
    allowed = set(article_ids)
    relations = []
    for r in data.get("relations") or []:
        if not isinstance(r, dict):
            continue
        s, o = _str(r.get("subject"), 60), _str(r.get("object"), 60)
        if s not in names or o not in names or names[s] == names[o]:
            continue
        status = _str(r.get("status"), 10).lower()
        date = _str(r.get("date"), 10)
        ids = [i for i in (r.get("article_ids") or []) if isinstance(i, str) and i in allowed] if isinstance(r.get("article_ids"), list) else []
        relations.append({"subject": entities[names[s]]["name"], "object": entities[names[o]]["name"],
                          "predicate": _str(r.get("predicate"), 40) or "OTHER", "expression": _str(r.get("expression"), 80),
                          "status": status if status in STATUSES else "unknown", "date": date if _DATE.match(date) else None,
                          "article_ids": ids, "evidence": _str(r.get("evidence"), 160)})
        if len(relations) >= MAX_RELATIONS:
            break
    return {"entities": entities, "relations": relations}

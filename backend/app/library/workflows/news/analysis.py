"""Analysis of news toward one target (the subject of a request), kept as separate questions:

    per article  stance        how this article reports the target: favorable | critical | neutral |
                               unknown — judged only from its body (or the lead under an AI-use notice);
                               a description or a headline alone is "unknown", never a guess
                 article_type  report | opinion | press_release | wire_copy
                 risk          a risk to the target the article reports (category + level) or none —
                               independent of stance: a favorable article can still report a risk
                 key_facts     facts the article states
    per event    summary, key_facts (each with the article ids it rests on), impact on the target
                 (positive | negative | mixed | neutral | unknown), risk, implication for management
    per outlet   tone          aggregated from its articles' stances (wire copies and unknowns left out)

An LLM answers when one is configured; every field has a rule value first, and a model answer
that does not fit the schema is dropped field by field. Article analyses are versioned
(ARTICLE_VERSION) so a live store can reuse them across requests.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from .cluster import wire_agency
from .labels import CATEGORY_LABELS, CHECK_POINTS, LEVELS, article_type, parse_json_array, parse_json_object, rule_risk

LLM = Callable[[str, str], str]

ARTICLE_VERSION = "article-v1"
EVENT_VERSION = "event-v1"
STANCES = ("favorable", "critical", "neutral", "unknown")
IMPACTS = ("positive", "negative", "mixed", "neutral", "unknown")
TYPES = ("report", "opinion", "press_release", "wire_copy")
BATCH = 8
PARALLEL = 3
TONE_MIN_ARTICLES = 2
TONE_THRESHOLD = 0.34
_SENTENCE = re.compile(r"(?<=[.!?다])\s+")


def _facts(text: str, limit: int = 2) -> list[str]:
    return [s.strip()[:90] for s in _SENTENCE.split(text or "") if len(s.strip()) >= 12][:limit]


def _risk(category: Any, level: Any) -> dict[str, str] | None:
    if category in CATEGORY_LABELS and level in LEVELS:
        return {"category": category, "label": CATEGORY_LABELS[category], "level": level}
    return None


# ── per article ─────────────────────────────────────────────────────────────

def rule_article(a: dict[str, Any], target: str) -> dict[str, Any]:
    """a: {id, title, outlet, text, basis}. Stance needs a reader: the rules leave it unknown."""
    named = any(t in a["title"].lower() for t in target.split()) if target else False
    agency = wire_agency(a.get("text", ""))
    kind = "wire_copy" if agency and agency not in a.get("outlet", "") else article_type(a["title"])
    return {"article_id": a["id"], "stance": "unknown", "article_type": kind,
            "risk": rule_risk(f"{a['title']} {a.get('text', '')}", a["title"]) if named else None,
            "key_facts": _facts(a.get("text", "")) or [a["title"]], "entities": [], "basis": a["basis"], "method": "rule"}


def fixture_article(a: dict[str, Any], hint: dict[str, Any]) -> dict[str, Any]:
    """The demo fixture labels its articles: a risk article reports against the company, one without a
    risk is good news (unless it says otherwise)."""
    risk = hint.get("risk")
    stance = "critical" if risk else {"positive": "favorable", "negative": "critical", "neutral": "neutral"}.get(
        hint.get("sentiment", "positive"), "neutral")
    return {"article_id": a["id"], "stance": stance, "article_type": article_type(a["title"]),
            "risk": risk, "key_facts": hint.get("key_facts") or [a["title"]], "entities": hint.get("companies", []),
            "basis": "fixture", "method": "fixture"}


ARTICLE_SYSTEM = """너는 기사 한 건씩을 분석하는 뉴스 분석가다. 분석 대상(target)에 대해 기사들을 읽고 JSON 배열만 출력한다. 설명은 쓰지 않는다.
각 원소: {{"id": 기사 id,
 "stance": 이 기사가 대상을 어떻게 보도하는지 "favorable"(우호적) | "critical"(비판적) | "neutral"(중립·사실 전달) | "unknown"(판단 불가),
 "article_type": "report"(스트레이트) | "opinion"(칼럼·사설·기고) | "press_release"(보도자료성) | "wire_copy"(통신사 기사 전재),
 "risk": 기사가 대상에게 리스크를 보도하면 {{"category": {categories} 중 하나, "level": "HIGH" | "MEDIUM" | "LOW"}}, 아니면 null,
 "key_facts": 기사에 실제로 적힌 사실 1~3개 (각 60자 이내),
 "entities": 기사가 다루는 주요 기업·기관·인물 이름 최대 5개}}
- stance는 사건이 좋은지 나쁜지가 아니라 기사의 보도 관점이다. 같은 사건도 매체마다 다를 수 있다.
- basis가 "snippet"이나 "title"인 기사는 stance를 "unknown"으로 둔다.
- risk는 stance와 별개다. 대상과 관계없는 리스크(다른 회사의 일)는 null.
- 기사에 없는 사실을 만들지 않는다. 추측성 보도와 칼럼의 risk는 LOW.
- 모든 문장은 한국어로 쓴다 (고유명사·약어는 그대로)."""


def _article_rows(batch: list[dict[str, Any]], target: str, llm: LLM) -> list[dict]:
    payload = [{"id": a["id"], "title": a["title"], "outlet": a["outlet"], "basis": a["basis"], "text": a["text"]} for a in batch]
    system = ARTICLE_SYSTEM.format(categories=", ".join(f'"{k}"' for k in CATEGORY_LABELS))
    try:
        answer = llm(system, f"분석 대상: {target}\n\n기사:\n{json.dumps(payload, ensure_ascii=False)}")
    except Exception:  # network, quota, timeout: this batch keeps the rules
        return []
    return [r for r in parse_json_array(answer) if isinstance(r, dict)]


def analyze_articles(articles: list[dict[str, Any]], target: str, llm: LLM | None) -> dict[str, dict[str, Any]]:
    """article id → analysis. Articles: {id, title, outlet, text, basis}."""
    out = {a["id"]: rule_article(a, target) for a in articles}
    if not (llm and articles):
        return out
    batches = [articles[i : i + BATCH] for i in range(0, len(articles), BATCH)]
    with ThreadPoolExecutor(max_workers=PARALLEL) as pool:
        answers = list(pool.map(lambda b: _article_rows(b, target, llm), batches))
    by_id = {a["id"]: a for a in articles}
    for rows in answers:
        for row in rows:
            a = by_id.get(row.get("id"))
            if a is None:
                continue
            r = out[a["id"]]
            if row.get("stance") in STANCES:
                r["stance"] = row["stance"] if a["basis"] in ("body", "lead") else "unknown"
            if row.get("article_type") in TYPES:
                r["article_type"] = row["article_type"]
            if "risk" in row:
                r["risk"] = _risk((row["risk"] or {}).get("category"), (row["risk"] or {}).get("level")) if isinstance(row["risk"], dict) else None
            facts = [str(f).strip()[:90] for f in row.get("key_facts") or [] if str(f).strip()][:3]
            if facts:
                r["key_facts"] = facts
            if isinstance(row.get("entities"), list):
                r["entities"] = [str(e).strip()[:40] for e in row["entities"] if str(e).strip()][:5]
            r["method"] = "llm"
    return out


def analyze_with_store(articles: list[dict[str, Any]], target: str, llm: LLM | None, store: Any | None) -> dict[str, dict[str, Any]]:
    """analyze_articles, reusing the store's analyses of the same target and version and saving new ones."""
    out: dict[str, dict[str, Any]] = {}
    todo = articles
    if store is not None and todo:
        for aid, row in store.article_analyses([a["id"] for a in todo], target, ARTICLE_VERSION).items():
            out[aid] = {"article_id": aid, "stance": row["stance"], "article_type": row["article_type"],
                        "risk": _risk(row["risk_category"], row["risk_level"]), "key_facts": row["key_facts"],
                        "entities": row["entities"], "basis": row["basis"], "method": row["method"], "cached": True}
        todo = [a for a in todo if a["id"] not in out]
    fresh = analyze_articles(todo, target, llm)
    out.update(fresh)
    if store is not None and fresh:
        store.save_article_analyses({
            "article_id": aid, "target": target, "analyzer_version": ARTICLE_VERSION, "stance": r["stance"],
            "article_type": r["article_type"], "risk_category": (r["risk"] or {}).get("category"),
            "risk_level": (r["risk"] or {}).get("level"), "key_facts": r["key_facts"], "entities": r["entities"],
            "basis": r["basis"], "method": r["method"]} for aid, r in fresh.items())
    return out


# ── per outlet ──────────────────────────────────────────────────────────────

def outlet_tone(rows: list[tuple[str, str, bool]], limit: int = 8) -> list[dict[str, Any]]:
    """rows: (outlet, stance, is_copy). Per outlet, how its own articles report the target: 우호 / 비우호 /
    중립, 판단 보류 under two judged articles. Most critical first."""
    counts: dict[str, dict[str, int]] = {}
    for outlet, stance, copy in rows:
        if copy:
            continue
        c = counts.setdefault(outlet, {"favorable": 0, "critical": 0, "neutral": 0, "unknown": 0})
        c[stance if stance in c else "unknown"] += 1
    out = []
    for outlet, c in counts.items():
        judged = c["favorable"] + c["critical"] + c["neutral"]
        score = (c["favorable"] - c["critical"]) / judged if judged else 0.0
        tone = ("판단 보류" if judged < TONE_MIN_ARTICLES else
                "우호" if score >= TONE_THRESHOLD else "비우호" if score <= -TONE_THRESHOLD else "중립")
        out.append({"outlet": outlet, **c, "judged": judged, "tone": tone})
    out.sort(key=lambda r: (-r["critical"], -r["judged"], r["outlet"]))
    return out[:limit]


def stance_by_outlet(members: list[dict[str, Any]], analyses: dict[str, dict[str, Any]]) -> tuple[list[dict[str, Any]], bool]:
    """Within one event: each outlet's stance(s) and whether outlets reported it in opposite ways."""
    rows = [{"outlet": a["outlet"], "article_id": a["id"], "stance": analyses[a["id"]]["stance"]}
            for a in members if a["id"] in analyses and not a.get("syndicated_from")]
    stances = {r["stance"] for r in rows}
    return rows, {"favorable", "critical"} <= stances


# ── per event ───────────────────────────────────────────────────────────────

EVENT_SYSTEM = """너는 경영진에게 뉴스를 보고하는 분석가다. 사건(topic)별로 묶인 기사와 기사별 분석을 읽고 JSON 객체 하나만 출력한다. 설명 문장은 쓰지 않는다.
{{"topics": [{{"id": 사건 id, "summary": 무슨 일인지 1~2문장,
              "key_facts": [{{"text": 핵심 사실, "article_ids": [근거 기사 id]}}] 1~3개,
              "impact": 이 사건이 분석 대상에게 주는 영향 "positive" | "negative" | "mixed" | "neutral" | "unknown",
              "importance": "high" | "medium" | "low",
              "implication": 대상의 경영 관점에서 이 사건이 뜻하는 것 1문장{risk_fields}}}],
 "trend": 전체 사건에서 보이는 흐름 2~3문장,
 "so_what": 요청 목적({purpose}) 관점에서 경영진이 알아야 할 시사점 2~3문장}}
- impact는 사건 자체가 대상에게 좋은지 나쁜지다. 기사의 보도 관점(stance)과 다를 수 있다.
- key_facts의 article_ids는 주어진 기사 id만 쓴다. 기사에 없는 사실이나 수치를 만들지 않는다.
- 기사 text는 본문 일부(basis "body"), 본문 앞부분("lead"), 요약("snippet"), 제목("title") 중 하나다. 근거가 약한 사건은 단정하지 않는다.
- 모든 문장은 한국어로만 쓴다 (중국어·일본어 한자 표현, 영어 문장 금지. 고유명사와 약어는 그대로)."""

RISK_FIELDS = (',\n              "risk": 대상에 대한 리스크가 아니면 null, 리스크면 {"category": '
               + " | ".join(f'"{k}"' for k in CATEGORY_LABELS) + ', "level": "HIGH" | "MEDIUM" | "LOW"}')


def rule_event(t: dict[str, Any], analyses: dict[str, dict[str, Any]], purpose: str) -> None:
    """Rule values for one event from its articles' analyses (fills t in place)."""
    members = [analyses[a["id"]] for a in t["articles"] if a["id"] in analyses]
    lead = t["articles"][0]
    facts: list[dict[str, Any]] = []
    for a in t["articles"]:
        for f in (analyses.get(a["id"]) or {}).get("key_facts", [])[:1]:
            if f not in [x["text"] for x in facts]:
                facts.append({"text": f, "article_ids": [a["id"]]})
    risks = [m["risk"] for m in members if m.get("risk")]
    risk = min(risks, key=lambda r: LEVELS.index(r["level"])) if risks else None
    stances = {m["stance"] for m in members}
    if risk:
        impact = "negative"
    elif "favorable" in stances and "critical" in stances:
        impact = "mixed"
    elif "favorable" in stances:
        impact = "positive"
    elif "critical" in stances:
        impact = "negative"
    else:
        impact = "unknown" if stances <= {"unknown"} else "neutral"
    t.update(summary=lead["title"], evidence_facts=facts[:3] or [{"text": lead["title"], "article_ids": [lead["id"]]}],
             impact=impact, implication="")
    if purpose == "risk":
        t["risk"] = risk
        t["check_points"] = list(CHECK_POINTS.get(risk["category"], ())) if risk else []


def analyze_events(topics: list[dict[str, Any]], analyses: dict[str, dict[str, Any]], query: str, purpose: str,
                   llm: LLM | None) -> tuple[str, str, str]:
    """Fills each topic's summary, evidence_facts, impact, implication (and risk) in place. Returns (trend, so_what, method)."""
    for t in topics:
        rule_event(t, analyses, purpose)
    if not (llm and topics):
        return "", "", "rule"
    payload = [{"id": t["id"], "articles": [
        {"id": a["id"], "title": a["title"], "outlet": a["outlet"], "date": a["date"], "basis": a["basis"], "text": a["text"],
         "stance": (analyses.get(a["id"]) or {}).get("stance", "unknown")} for a in t["articles"][:5]]} for t in topics]
    system = EVENT_SYSTEM.format(purpose="리스크 점검" if purpose == "risk" else "일반 동향 파악",
                                 risk_fields=RISK_FIELDS if purpose == "risk" else "")
    try:
        answer = parse_json_object(llm(system, f"분석 대상: {query}\n\n사건별 기사:\n{json.dumps(payload, ensure_ascii=False)}"))
    except Exception:  # network, quota, timeout: the rule values stand
        answer = None
    if not answer:
        return "", "", "rule"
    by_id = {t["id"]: t for t in topics}
    for row in answer.get("topics") or []:
        t = by_id.get(row.get("id")) if isinstance(row, dict) else None
        if not t:
            continue
        known = {a["id"] for a in t["articles"]}
        if isinstance(row.get("summary"), str) and row["summary"].strip():
            t["summary"] = row["summary"].strip()
        facts = []
        for f in row.get("key_facts") or []:
            if isinstance(f, dict) and str(f.get("text", "")).strip():
                ids = [i for i in f.get("article_ids") or [] if i in known]
                facts.append({"text": str(f["text"]).strip(), "article_ids": ids or [t["articles"][0]["id"]]})
            elif isinstance(f, str) and f.strip():
                facts.append({"text": f.strip(), "article_ids": [t["articles"][0]["id"]]})
        if facts:
            t["evidence_facts"] = facts[:3]
        if row.get("impact") in IMPACTS:
            t["impact"] = row["impact"]
        if row.get("importance") in ("high", "medium", "low"):
            t["importance_llm"] = row["importance"]
        if isinstance(row.get("implication"), str):
            t["implication"] = row["implication"].strip()
        if purpose == "risk" and "risk" in row:
            r = row["risk"]
            risk = _risk(r.get("category"), r.get("level")) if isinstance(r, dict) else None
            t["risk"], t["check_points"] = risk, list(CHECK_POINTS.get(risk["category"], ())) if risk else []
    trend = answer.get("trend") if isinstance(answer.get("trend"), str) else ""
    so_what = answer.get("so_what") if isinstance(answer.get("so_what"), str) else ""
    return trend.strip(), so_what.strip(), "llm"


SENTIMENT_OF_IMPACT = {"positive": "positive", "negative": "negative", "mixed": "neutral", "neutral": "neutral", "unknown": "neutral"}

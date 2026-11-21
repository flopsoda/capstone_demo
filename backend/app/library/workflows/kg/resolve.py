"""Raw layer → resolved layer: which names are one entity, which claims are one fact, and where each fact stands.

Rule based (VERSION "rules-v1"). Everything here is a pure function of the raw layer, so a better algorithm
(embedding / LLM entity resolution, relation clustering, cross-event fact matching, contradiction models) replaces
these functions and KGStore.rebuild(resolver=…) re-resolves the whole graph without calling a model again.
A resolver is any object with the four functions below (this module is the default one):

    resolve_entities(mentions, seeds) -> Entities       one node per real-world thing
    normalize_predicate(raw) -> (predicate, swapped)    one name per relation meaning
    instance_key(claim) -> str                          which occurrence a claim is about (same SPO ≠ same fact)
    reconcile(predicate, fact_class, claims) -> dict    status and validity from the claims behind a fact
    build_facts(claims, endpoint, resolver) -> facts    the grouping that uses them (shared, not usually replaced)
"""

from __future__ import annotations

import hashlib
import re
import sys
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from ..news.store import squeeze

VERSION = "rules-v1"

# Relations a news text may state (the extraction prompt lists them; anything else is OTHER with its own words kept).
PREDICATES: dict[str, str] = {
    "ACQUIRES": "인수 · 합병 (인수하는 쪽 → 인수 대상)",
    "INVESTS_IN": "지분 투자 · 출자 (투자자 → 투자 대상)",
    "SUPPLIES_TO": "공급 · 납품 계약 (공급사 → 고객사)",
    "PARTNERS_WITH": "제휴 · 협력 · 공동 개발",
    "COMPETES_WITH": "경쟁 관계",
    "SUES": "소송 · 가처분 · 분쟁 제기 (제기한 쪽 → 상대)",
    "REGULATES": "규제 · 제재 · 조사 (기관 → 대상)",
    "EXECUTIVE_OF": "임원 · 대표 · 핵심 인력 (사람 → 조직)",
    "ADOPTS": "제품 · 기술 채택 (채택한 쪽 → 제품 또는 그 공급사)",
    "OTHER": "위 어디에도 맞지 않는 관계 (expression에 원문 표현)",
}
# Other names the extractor or a source may use for the same relation. Inverse names swap subject and object.
SYNONYMS: dict[str, str] = {
    "ACQUIRE": "ACQUIRES", "MERGES_WITH": "ACQUIRES", "인수": "ACQUIRES", "합병": "ACQUIRES",
    "INVEST": "INVESTS_IN", "INVESTS": "INVESTS_IN", "투자": "INVESTS_IN", "출자": "INVESTS_IN",
    "SUPPLIES": "SUPPLIES_TO", "SUPPLY": "SUPPLIES_TO", "공급": "SUPPLIES_TO", "납품": "SUPPLIES_TO",
    "PARTNERS": "PARTNERS_WITH", "PARTNER": "PARTNERS_WITH", "COOPERATES_WITH": "PARTNERS_WITH", "제휴": "PARTNERS_WITH", "협력": "PARTNERS_WITH",
    "COMPETES": "COMPETES_WITH", "경쟁": "COMPETES_WITH",
    "SUE": "SUES", "LITIGATES": "SUES", "소송": "SUES", "특허침해": "SUES", "가처분": "SUES",
    "REGULATE": "REGULATES", "SANCTIONS": "REGULATES", "규제": "REGULATES", "제재": "REGULATES",
    "CEO_OF": "EXECUTIVE_OF", "CTO_OF": "EXECUTIVE_OF", "WORKS_FOR": "EXECUTIVE_OF", "임원": "EXECUTIVE_OF",
    "ADOPT": "ADOPTS", "USES": "ADOPTS", "채택": "ADOPTS",
}
INVERSES: dict[str, str] = {"CUSTOMER_OF": "SUPPLIES_TO", "BUYS_FROM": "SUPPLIES_TO", "ACQUIRED_BY": "ACQUIRES",
                            "SUED_BY": "SUES", "REGULATED_BY": "REGULATES", "EMPLOYS": "EXECUTIVE_OF"}
# What a source says about a relation's state (extraction "status"). Only "ended" closes a fact.
STATUSES = ("active", "ended", "planned", "unknown")

TYPE_PREFIX = {"organization": "org", "person": "person", "org_unit": "unit", "product": "product", "other": "other"}
_TYPE_ALIASES = {"company": "organization", "org": "organization", "organization": "organization", "institution": "organization",
                 "government": "organization", "agency": "organization", "person": "person", "people": "person",
                 "product": "product", "technology": "product", "tech": "product", "org_unit": "org_unit"}
_SUFFIX = re.compile(r"주식회사|\(주\)|㈜|\b(?:inc|corp|corporation|co|ltd|llc|plc)\b\.?", re.IGNORECASE)


def entity_type(guess: str | None) -> str | None:
    """organization | person | product | org_unit, or None when the source did not say."""
    return _TYPE_ALIASES.get((guess or "").strip().lower())


def normalize(name: str) -> str:
    """The key two names of one thing share: no legal-form words, spacing or joining marks, lower case."""
    return squeeze(_SUFFIX.sub(" ", name or ""))


def stable_id(*parts: Any) -> str:
    return hashlib.sha256("\x1f".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:24]


# ---------------------------------------------------------------------------
# Entities
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Seed:
    """An entity known before any text is read: a company of the system's data file, a person or a unit of its
    meeting directory. A seed keeps its id; mentions that name it link to it."""

    id: str
    type: str
    label: str
    aliases: tuple[str, ...] = ()


@dataclass
class Entities:
    nodes: dict[str, dict[str, Any]] = field(default_factory=dict)   # id → {type, label, aliases: set}
    links: dict[str, tuple[str, str]] = field(default_factory=dict)  # mention id → (node id, method)


class _Union:
    def __init__(self) -> None:
        self.parent: dict[str, str] = {}
        self.type: dict[str, str | None] = {}
        self.seed: dict[str, str | None] = {}

    def add(self, key: str, type_: str | None, seed: str | None = None) -> str:
        if key not in self.parent:
            self.parent[key], self.type[key], self.seed[key] = key, type_, seed
        root = self.find(key)
        self.type[root] = self.type[root] or type_
        self.seed[root] = self.seed[root] or seed
        return root

    def find(self, key: str) -> str:
        while self.parent[key] != key:
            self.parent[key] = self.parent[self.parent[key]]
            key = self.parent[key]
        return key

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
        ta, tb, sa, sb = self.type[ra], self.type[rb], self.seed[ra], self.seed[rb]
        if (ta and tb and ta != tb) or (sa and sb and sa != sb):
            return  # a person and a company of one name, or two known entities sharing an alias: keep apart
        self.parent[rb] = ra
        self.type[ra], self.seed[ra] = ta or tb, sa or sb


def resolve_entities(mentions: Iterable[dict[str, Any]], seeds: Iterable[Seed]) -> Entities:
    """Names connect when they are the same after normalize() or one source gives them for one thing (aliases).
    A typed name never joins a different type; two seeds never merge. Untyped names (an analysis' entity list)
    join whatever their name already is."""
    uf = _Union()
    seeds = list(seeds)
    for s in seeds:
        keys = [k for k in {normalize(a) for a in (s.label, *s.aliases)} if k]
        for k in keys:
            uf.add(k, s.type, s.id)
        for k in keys[1:]:
            uf.union(keys[0], k)
    mentions = sorted(mentions, key=lambda m: m["id"])
    keys_of: dict[str, list[str]] = {}
    for m in mentions:
        type_ = entity_type(m.get("type_guess"))
        keys = [k for k in dict.fromkeys(normalize(n) for n in (m["surface"], *(m.get("aliases") or []))) if k]
        for k in keys:
            uf.add(k, type_)
        for k in keys[1:]:
            uf.union(keys[0], k)
        keys_of[m["id"]] = keys
    # One node per component: the seed's id and label, else the name used most (shortest, then alphabetical).
    members: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for m in mentions:
        if keys_of[m["id"]]:
            members[uf.find(keys_of[m["id"]][0])].append(m)
    by_seed = {s.id: s for s in seeds}
    out = Entities()
    roots = {uf.find(k) for k in uf.parent}
    for root in sorted(roots):
        seed = by_seed.get(uf.seed[root] or "")
        ms = members.get(root, [])
        if seed is None and not ms:
            continue
        type_ = seed.type if seed else (uf.type[root] or "other")
        if seed:
            node_id, label = seed.id, seed.label
        else:
            counts = Counter(m["surface"].strip() for m in ms)
            label = sorted(counts, key=lambda s: (-counts[s], len(s), s))[0]
            node_id = f"{TYPE_PREFIX.get(type_, 'other')}:{normalize(label)}"
        names = {*(seed.aliases if seed else ()), *([seed.label] if seed else [])}
        for m in ms:
            names.update([m["surface"].strip(), *(a.strip() for a in m.get("aliases") or [])])
        node = out.nodes.setdefault(node_id, {"type": type_, "label": label, "aliases": set()})
        node["aliases"].update(n for n in names if n and normalize(n))
        distinct = {k for m in ms for k in keys_of[m["id"]]}
        for m in ms:
            out.links[m["id"]] = (node_id, "seed" if seed else ("alias" if len(distinct) > 1 or len(ms) > 1 else "new"))
    return out


# ---------------------------------------------------------------------------
# Relations and facts
# ---------------------------------------------------------------------------


def normalize_predicate(raw: str) -> tuple[str, bool]:
    """(canonical predicate, whether subject and object swap). Unknown relation words stay as OTHER."""
    word = (raw or "").strip()
    key = word.upper().replace(" ", "_")
    if key in INVERSES:
        return INVERSES[key], True
    if key in PREDICATES or key in STRUCTURAL:
        return key, False
    if key in SYNONYMS or word in SYNONYMS:
        return SYNONYMS.get(key) or SYNONYMS[word], False
    return "OTHER", False


# Relations the projections write from records (the meeting DB, article ↔ event). Not in the extraction vocabulary.
STRUCTURAL = {
    "DECIDED_IN": "결정 → 회의", "SUPERSEDES": "결정 → 바뀐 이전 결정", "RESOLVES": "결정 → 해결한 쟁점",
    "RESTS_ON": "결정 → 전제 · 조건", "RAISED_IN": "할 일 → 회의", "FOLLOWS_UP": "할 일 → 결정",
    "ASSIGNED_TO": "할 일 → 담당", "STATED_BY": "결정 · 전제 → 말한 사람", "ATTENDED": "사람 → 회의",
    "CONCERNS": "회의 기록 → 다루는 기업 · 사람", "PART_OF": "기사 → 뉴스 사건", "MENTIONS": "기사 · 사건 → 다루는 기업 · 사람",
    "AFFECTS": "기사 → 결정의 전제 (판정된 영향)",
}


def instance_key(claim: dict[str, Any]) -> str:
    """Which occurrence a claim is about. Reported relations are kept per news event: the articles of one event
    are one fact; another event between the same parties (a second lawsuit, a new contract) is another fact.
    Records are their own occurrence."""
    return str(claim.get("event_ref") or "") if claim["claim_class"] == "reported" else ""


def _date(value: Any) -> str | None:
    return str(value)[:10] if value else None


def reconcile(predicate: str, fact_class: str, claims: list[dict[str, Any]]) -> dict[str, Any]:
    """Status and validity of one fact from the claims behind it (oldest first)."""
    claims = sorted(claims, key=lambda c: (_date(c.get("stated_at")) or "", c["id"]))
    dates = [d for d in (_date(c.get("stated_at")) for c in claims) if d]
    first, last = (dates[0], dates[-1]) if dates else (None, None)
    latest_q = claims[-1].get("qualifiers") or {}
    out: dict[str, Any] = {"status": "active", "valid_from": first, "valid_to": None, "first_seen": first, "last_seen": last, "attrs": {}}
    if fact_class == "reported":
        timeline = [{"date": _date(c.get("stated_at")), "status": (c.get("qualifiers") or {}).get("status", "unknown"),
                     "expression": (c.get("qualifiers") or {}).get("expression")} for c in claims]
        expressions = sorted({t["expression"] for t in timeline if t["expression"]})
        at_last = {t["status"] for t in timeline if t["date"] == last} - {"unknown"}
        out["attrs"] = {"timeline": timeline, "expressions": expressions, "latest_status": timeline[-1]["status"]}
        if "ended" in at_last and at_last & {"active", "planned"}:
            out["status"] = "contradicted"  # the latest reports disagree: leave it to a person (or a better resolver)
        elif "ended" in at_last:
            out["status"], out["valid_to"] = "ended", last
            started = [t["date"] for t in timeline if t["status"] in ("active", "planned") and t["date"]]
            out["valid_from"] = started[0] if started else None
    elif predicate == "ASSIGNED_TO":
        out["attrs"] = {"item_status": latest_q.get("status"), "due": latest_q.get("due")}
        if latest_q.get("status") in ("done", "cancelled"):
            out["status"], out["valid_to"] = "ended", _date(latest_q.get("completed_at")) or last
    elif predicate == "AFFECTS":
        out["attrs"] = {k: latest_q.get(k) for k in ("effect", "judged", "reason", "signal_status", "signal_id")}
        if latest_q.get("signal_status") == "dismissed":
            out["status"] = "dismissed"
    else:
        out["attrs"] = dict(latest_q)
    return out


def build_facts(claims: Iterable[dict[str, Any]], endpoint: Callable[[str], str | None], resolver: Any = None) -> list[dict[str, Any]]:
    """Groups claims into facts. `endpoint(key)` is the node a claim's subject / object names (None: unresolved,
    the claim waits). A later decision that supersedes an earlier one ends the earlier one's facts."""
    r = resolver or sys.modules[__name__]
    groups: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    classes: dict[tuple[str, str, str, str], str] = {}
    for c in claims:
        predicate, swapped = r.normalize_predicate(c["predicate_raw"])
        s, o = endpoint(c["subject"]), endpoint(c["object"])
        if swapped:
            s, o = o, s
        if not s or not o or s == o:
            continue
        key = (s, predicate, o, r.instance_key(c))
        groups[key].append(c)
        classes[key] = c["claim_class"]
    facts = []
    for (s, p, o, ik), group in groups.items():
        state = r.reconcile(p, classes[(s, p, o, ik)], group)
        facts.append({"id": stable_id(s, p, o, ik), "subject": s, "predicate": p, "object": o, "instance_key": ik,
                      "fact_class": classes[(s, p, o, ik)], **state, "claims": sorted(c["id"] for c in group)})
    decided = {f["subject"]: f for f in facts if f["predicate"] == "DECIDED_IN"}
    for f in facts:
        if f["predicate"] == "SUPERSEDES" and f["object"] in decided and f["status"] == "active":
            old = decided[f["object"]]
            old["status"], old["valid_to"] = "superseded", (decided.get(f["subject"]) or f)["valid_from"]
            old["attrs"] = {**old["attrs"], "superseded_by": f["subject"]}
    return facts

"""record_advisory_opinion · lookup_advisory_reviews — Tool Capability factories of the Advisory Agent's judgements.

record_advisory_opinion keeps the model's judgement once it passes the checks of workflows/advisory/opinion.py (cited
ids read in this run, numbers that match the evidence, unmet meeting criteria addressed, …); a failed check comes back
as problems for the model to fix. The kept record carries a copy of its evidence, so a report or a later re-evaluation
reads exactly what the judgement stood on. A report is a reference (artifact) to that record: the PDF is drawn from it
when someone opens it (workflows/advisory/report.py), through the system's report endpoint and its access rule.

A system binds them to its AdvisoryStore.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from langchain_core.tools import BaseTool, tool
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator

from ...runtime import evidence
from ...runtime.events import current_run
from ..workflows.advisory import opinion as op
from ..workflows.advisory.store import AdvisoryStore
from ..workflows.meeting_store import StoreUnavailable
from .meeting_access import current_viewer, plain

log = logging.getLogger("advisory")

RECORD_TOOL = "record_advisory_opinion"
LOOKUP_TOOL = "lookup_advisory_reviews"
DETAILS_TOOL = "add_advisory_details"
Recommendation = Literal["PROCEED", "CONDITIONAL_PROCEED", "DO_NOT_PROCEED"]


def _upper(value: Any) -> Any:
    return value.strip().upper() if isinstance(value, str) else value


_STANCES = {"진행": "PROCEED", "찬성": "PROCEED", "조건부 진행": "CONDITIONAL_PROCEED", "조건부": "CONDITIONAL_PROCEED",
            "보류": "DO_NOT_PROCEED", "반대": "DO_NOT_PROCEED", "CONDITIONAL": "CONDITIONAL_PROCEED", "DO NOT PROCEED": "DO_NOT_PROCEED"}


def _recommendation(value: Any) -> Any:
    """The three codes as a model also writes them (Korean labels, spaces, lower case)."""
    if not isinstance(value, str):
        return value
    v = value.strip()
    code = _STANCES.get(v) or _STANCES.get(v.upper()) or v.upper().replace(" ", "_").replace("-", "_")
    return code if code in op.RECOMMENDATIONS else v  # a perspective's stance may stay in its own words


def _ids(value: Any) -> Any:
    """Evidence ids as a list, also when a model writes them as one string."""
    if isinstance(value, str):
        return [x for x in (t.strip() for t in value.replace(",", " ").split()) if x]
    return value


class Claim(BaseModel):
    statement: str = Field(description="판단 문장 (한국어). 수치는 근거에 있는 값 그대로")
    evidence: list[str] = Field(default_factory=list, description="근거 id: 회의 결정 ref, 기사 id, run_mna_review 결과의 id 등")

    _evidence = field_validator("evidence", mode="before")(lambda v: _ids(v))

    @model_validator(mode="before")
    @classmethod
    def _sentence(cls, data: Any) -> Any:
        return {"statement": data} if isinstance(data, str) else data


class PersonaView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    persona: Literal["CEO", "CFO", "CTO"] = Field(validation_alias=AliasChoices("persona", "lens", "role"))
    stance: str = Field(validation_alias=AliasChoices("stance", "recommendation", "position"),
                        description="이 관점의 입장: PROCEED | CONDITIONAL_PROCEED | DO_NOT_PROCEED (또는 짧은 입장 표현)")
    judgement: str = Field(validation_alias=AliasChoices("judgement", "judgment", "opinion", "assessment"),
                           description="이 관점의 판단 (2~4문장). 그 관점의 기준으로 쓴다")
    evidence: list[str] = Field(default_factory=list, description="이 관점이 근거로 삼은 id (persona_lenses의 그 관점 수치 포함)")
    key_points: list[Claim] = Field(default_factory=list, description="이 관점의 핵심 근거 (각각 근거 id)")
    deal_breakers: str | None = Field(default=None, description="이 관점에서 거래를 막는 조건")
    would_change_if: str | None = Field(default=None, description="이 관점의 판단이 바뀔 조건")

    _persona = field_validator("persona", mode="before")(lambda v: _upper(v))
    _stance = field_validator("stance", mode="before")(lambda v: _recommendation(v))
    _evidence = field_validator("evidence", mode="before")(lambda v: _ids(v))


class Risk(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, description="위험 이름")
    level: Literal["HIGH", "MEDIUM", "LOW"] = Field(description="이 위험의 수준 (경영자문 에이전트의 판단)")
    assessment: str = Field(validation_alias=AliasChoices("assessment", "description", "detail"), description="위험의 내용과 영향")
    evidence: list[str] = Field(default_factory=list)
    response: str | None = Field(default=None, validation_alias=AliasChoices("response", "mitigation"),
                                 description="대응 (조건 · 실사 · 거래 구조) 또는 감수하는 이유")

    _level = field_validator("level", mode="before")(lambda v: _upper(v))
    _evidence = field_validator("evidence", mode="before")(lambda v: _ids(v))

    @model_validator(mode="after")
    def _named(self) -> "Risk":
        if not self.title:
            self.title = self.assessment.split(",")[0][:40]
        return self


class Condition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    condition: str = Field(description="거래를 진행하려면 충족해야 할 조건")
    addresses: str = Field(default="", description="이 조건이 다루는 위험")
    evidence: list[str] = Field(default_factory=list)

    _evidence = field_validator("evidence", mode="before")(lambda v: _ids(v))

    @model_validator(mode="before")
    @classmethod
    def _sentence(cls, data: Any) -> Any:
        return {"condition": data} if isinstance(data, str) else data


_SECTION_NAMES = {"financial": ("financial", "financials", "finance"), "valuation": ("valuation", "enterprise_value", "value", "price"),
                  "commercial": ("commercial", "customers", "customer", "contracts"), "technology": ("technology", "tech", "people", "ip")}


class Sections(BaseModel):
    model_config = ConfigDict(extra="forbid")

    financial: str | None = Field(default=None, description="재무 실적 · 현금흐름 판단 (보고서 '재무' 장)")
    valuation: str | None = Field(default=None, description="기업가치 · 가격 판단 (보고서 '기업가치와 가격' 장)")
    commercial: str | None = Field(default=None, description="고객 · 계약 · 발주 판단 (보고서 '고객 · 계약' 장)")
    technology: str | None = Field(default=None, description="기술 · 특허 · 인력 · 통합 판단 (보고서 '기술 · 인력' 장)")

    @model_validator(mode="before")
    @classmethod
    def _common_names(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        out: dict[str, Any] = {}
        for key, value in data.items():
            name = next((k for k, names in _SECTION_NAMES.items() if key in names), key)
            out[name] = f"{out[name]} {value}".strip() if name in out and value else value
        return out


class RecordArgs(BaseModel):
    """The core of a judgement. The three executive views, risks and report paragraphs come in a second, smaller call
    (add_advisory_details): the configured model drops one very long tool call, two shorter ones go through."""

    company: str = Field(description="인수 대상 기업명.")
    evaluation_type: Literal["initial", "reevaluation", "what_if"] = Field(
        description="initial(최초 평가) | reevaluation(새 사건을 반영한 재평가) | what_if(조건을 바꾼 평가).")
    price_trillion_krw: float = Field(description="이 판단이 평가한 거래 가격, 조 원: 최초 요구가 · 제안가 · 사용자가 말한 가격 (run_mna_review로 계산한 가격). "
                                                  "DCF 가치나 권고하는 가격이 아니다 — 받아들일 만한 가격은 conditions에 쓴다.")
    recommendation: Recommendation = Field(
        description="최종 권고 — PROCEED(진행) | CONDITIONAL_PROCEED(조건부 진행) | DO_NOT_PROCEED(보류). 경영자문 에이전트의 판단.")
    headline: str = Field(description="한 줄 결론.")
    summary: str = Field(description="종합 평가 (3~5문장): 추진 이유, 핵심 위험, 세 관점의 종합, 우선 충족할 조건.")
    rationale: list[Claim] = Field(description="핵심 판단 근거 3~5개 (각각 statement와 근거 id 목록 evidence).")
    conditions: list[Condition] | None = Field(default=None, description="권고 조건: condition, addresses(다루는 위험), evidence.")
    changes_from_prior: str | None = Field(default=None, description="재평가 · What-if에서 이전 판단 대비 달라진 근거 · 수치 · 결론 (또는 유지한 이유).")
    prior_id: str | None = Field(default=None, description="다시 본 이전 판단의 id (lookup_advisory_reviews의 review_id).")
    report: bool = Field(default=False, description="투자심의 보고서(PDF)를 제공할지. 종합 평가 · 재평가 결과를 보고하거나 사용자가 보고서를 원하면 true.")

    _recommendation = field_validator("recommendation", mode="before")(lambda v: _recommendation(v))


class DetailsArgs(BaseModel):
    review_id: str = Field(description="이번 실행에서 record_advisory_opinion이 돌려준 review_id.")
    personas: list[PersonaView] = Field(description="CEO · CFO · CTO 관점 각 하나: persona, stance, judgement(1~2문장), evidence.")
    risks: list[Risk] | None = Field(default=None, description="주요 위험 3~5개: title, level(HIGH|MEDIUM|LOW), assessment, evidence, response.")
    uncertainties: list[str] | None = Field(default=None, description="정보 부족 · 불확실한 점.")
    sections: Sections | None = Field(default=None, description="보고서 장별 판단 문단(각 1~2문장): financial · valuation · commercial · technology.")

    @field_validator("uncertainties", mode="before")
    @classmethod
    def _lines(cls, value: Any) -> Any:
        return [x.strip(" -·") for x in value.splitlines() if x.strip(" -·")] if isinstance(value, str) else value

    @field_validator("personas", mode="before")
    @classmethod
    def _keyed_views(cls, value: Any) -> Any:
        """{"CEO": "…", "CFO": {...}} → one view per perspective (the persona from the key)."""
        if isinstance(value, dict):
            return [{"persona": k, **(v if isinstance(v, dict) else {"judgement": v})} for k, v in value.items()]
        return value


NEXT_STEP = "add_advisory_details(review_id=…)로 CEO · CFO · CTO 관점, 위험, 불확실성, 보고서 장별 문단을 더한다"


def unrecorded_judgement(messages: list[Any]) -> str | None:
    """For the advisory agent's answer: a judgement record that was refused and never fixed in this run."""
    refused = accepted = False
    for m in messages:
        if getattr(m, "name", None) not in (RECORD_TOOL, DETAILS_TOOL):
            continue
        content = str(getattr(m, "content", ""))
        if '"accepted": true' in content:
            accepted, refused = True, False
        elif '"accepted": false' in content or "인자가 Tool 정의와 맞지 않아" in content:
            refused = True
    if refused and not accepted:
        return ("판단 기록이 거부된 채 끝나려 한다. 받은 problems(또는 인자 오류)만 고쳐 다시 기록하라. "
                "고칠 수 없으면 기록하지 못한 이유를 밝히고, 그 결론을 확정된 판단으로 보고하지 마라.")
    return None


def _system() -> str:
    run = current_run()
    return run.system if run is not None and run.system else ""


def artifact(review: dict[str, Any], system: str) -> dict[str, Any]:
    """A reference to a kept judgement's report: what the user's screen needs to show and fetch it (no file content)."""
    kind = op.EVALUATION_TYPES.get(review["evaluation_type"], "평가")
    return {"kind": "report", "id": review["code"], "system": system, "media_type": "application/pdf",
            "title": f"{review['company']} 인수 투자심의 보고서 ({kind})",
            "filename": f"{review['company']}_투자심의보고서_{review['code']}.pdf",
            "recommendation": review["recommendation"], "recommendation_label": op.RECOMMENDATIONS.get(review["recommendation"]),
            "evaluation_type": review["evaluation_type"], "classification": review["classification"], "created_at": review["created_at"]}


def _figure(review: dict[str, Any], key: str, scenario: bool = False) -> str | None:
    for a in review["evidence"].get("analyses") or []:
        for i in a.get("items") or []:
            if (".S" in i["id"]) == scenario and i["id"].endswith("." + key):
                return f"{i['display']} [{review['code']}:{i['id']}]"
    return None


def summary(review: dict[str, Any]) -> dict[str, Any]:
    o = review["opinion"]
    records = [{"id": f"{review['code']}:{i['id']}", "kind": i.get("kind"), "title": i.get("title") or i.get("text") or i.get("label"),
                "date": i.get("date")} for i in review["evidence"]["items"].values() if i.get("kind") not in ("calc", "fact", "check")][:15]
    return {"review_id": review["code"], "created_at": review["created_at"], "evidence_as_of": review["evidence_as_of"],
            "evaluation_type": op.EVALUATION_TYPES.get(review["evaluation_type"]), "recommendation": review["recommendation"],
            "recommendation_label": op.RECOMMENDATIONS.get(review["recommendation"]), "price_trillion_krw": review["price_trillion_krw"],
            "headline": o.get("headline"), "summary": o.get("summary"), "conditions": [c.get("condition") for c in o.get("conditions") or []],
            "risks": [f"{r.get('title')} ({r.get('level')})" for r in o.get("risks") or []],
            "key_figures": [f for f in (_figure(review, "dcf.equity_value"), _figure(review, "price.premium_vs_dcf"),
                                        _figure(review, "dcf.equity_value", scenario=True)) if f],
            "records": records, "prior_review": review["prior_code"]}


def _remember(review: dict[str, Any]) -> None:
    """A looked-up judgement and its evidence join this run's ledger under its code, so a re-evaluation can cite them."""
    o = review["opinion"]
    evidence.add({"id": review["code"], "kind": "advisory_review", "title": f"{review['company']} {op.EVALUATION_TYPES.get(review['evaluation_type'])}",
                  "date": review["created_at"][:10], "text": o.get("headline"), "source": LOOKUP_TOOL})
    evidence.add_many({**i, "id": f"{review['code']}:{i['id']}", "source": LOOKUP_TOOL} for i in review["evidence"]["items"].values())


def _check_render(review: dict[str, Any]) -> str | None:
    from ..workflows.advisory import report  # fpdf is imported lazily there

    if not report.fonts_available():
        return "PDF 한글 글꼴이 없는 환경이라 보고서를 열 때 오류가 납니다 (REPORT_FONT_DIR)."
    try:
        report.render(review)
    except Exception as e:  # the judgement is kept either way; the reference says the document failed
        log.exception("could not render the report of %s", review["code"])
        return f"보고서 생성 실패: {type(e).__name__}"
    return None


def make_advisory_review_tools(store: AdvisoryStore) -> tuple[BaseTool, BaseTool, BaseTool]:
    def _accepted(saved: dict[str, Any]) -> dict[str, Any]:
        return {"accepted": True, "review_id": saved["code"], "company": saved["company"],
                "recommendation": saved["recommendation"], "recommendation_label": op.RECOMMENDATIONS[saved["recommendation"]],
                "evaluation_type": op.EVALUATION_TYPES[saved["evaluation_type"]], "classification": saved["classification"],
                "evidence_as_of": saved["evidence_as_of"], "prior_review": saved["prior_code"]}

    def _refused(problems: list[str]) -> dict[str, Any]:
        return {"accepted": False, "problems": problems,
                "hint": "아직 기록되지 않았다. 판단은 그대로 두고 problems만 고쳐 다시 호출한다 (필요한 수치는 run_mna_review로 계산). "
                        "기록되지 않은 판단은 확정된 결론으로 보고하지 않는다."}

    def _with_report(out: dict[str, Any], saved: dict[str, Any]) -> dict[str, Any]:
        ref = artifact(saved, _system())
        problem = _check_render(saved)
        if problem:
            ref["warning"] = problem
        return {**out, "artifacts": [ref]}

    @tool(RECORD_TOOL, args_schema=RecordArgs)
    def record_advisory_opinion(
        company: str,
        evaluation_type: Literal["initial", "reevaluation", "what_if"],
        price_trillion_krw: float,
        recommendation: Recommendation,
        headline: str,
        summary: str,
        rationale: list[Claim],
        conditions: list[Condition] | None = None,
        changes_from_prior: str | None = None,
        prior_id: str | None = None,
        report: bool = False,
    ) -> dict[str, Any]:
        """경영자문 에이전트가 내린 인수 판단의 핵심(권고 · 결론 · 근거 · 조건)을 검증한 뒤 근거 사본과 함께 저장한다. 검증은 판단을 바꾸지 않는다:
        인용한 근거 id가 이번 실행에서 받은 결과에 있는지, 적은 금액(조 · 억)과 %가 근거의 수치와 맞는지, 충족하지 못한 회의 기준을 다뤘는지,
        평가 가격을 계산했는지, 조건부 진행의 조건, 재평가의 이전 판단과 변경 설명을 확인한다. 통과하지 못하면 accepted=false와 problems를
        돌려주니 고쳐서 다시 호출한다. 통과하면 review_id를 돌려준다 — 종합 평가 · 재평가 · 보고서는 이어서 add_advisory_details로
        CEO · CFO · CTO 관점과 위험 · 장별 문단을 더한다 (report=true면 그때 보고서 참조가 나온다)."""
        ledger = evidence.current()
        if ledger is None:
            return _refused(["근거 장부가 없는 실행입니다."])
        record = {k: plain(v) for k, v in dict(
            company=company, evaluation_type=evaluation_type, price_trillion_krw=price_trillion_krw, recommendation=recommendation,
            headline=headline, summary=summary, rationale=rationale, conditions=conditions, changes_from_prior=changes_from_prior,
            prior_id=prior_id, report=report,
        ).items() if v is not None}
        viewer = current_viewer()
        try:
            prior = store.get(viewer, prior_id) if prior_id else None
            problems = op.check(record, ledger, prior=prior)
            if problems:
                return _refused(problems)
            snap = op.snapshot(record, ledger)
            company_id = next((a.get("company_id") for a in snap["analyses"] if a.get("company_id")), None)
            saved = store.save(record, snap, viewer=viewer, company_id=company_id)
        except StoreUnavailable as e:
            return _refused([f"판단 저장소를 쓸 수 없습니다: {e}"])
        # this run may add the second part to it (not to a judgement read from the store)
        evidence.add({"id": saved["code"], "kind": "advisory_review", "title": f"{saved['company']} {op.EVALUATION_TYPES[saved['evaluation_type']]}",
                      "date": saved["created_at"][:10], "text": headline, "source": RECORD_TOOL, "editable": True})
        return {**_accepted(saved), "next": NEXT_STEP}

    @tool(DETAILS_TOOL, args_schema=DetailsArgs)
    def add_advisory_details(review_id: str, personas: list[PersonaView], risks: list[Risk] | None = None,
                             uncertainties: list[str] | None = None, sections: Sections | None = None) -> dict[str, Any]:
        """이번 실행에서 기록한 판단(review_id)에 CEO · CFO · CTO 관점, 주요 위험, 불확실성, 보고서 장별 문단을 더한다.
        같은 검증(근거 id · 수치 · 관점마다 그 관점의 수치 인용)을 거치고, 통과하면 저장된 판단에 합친다. 판단 기록 때 report=true였으면
        투자심의 보고서(PDF) 참조(artifacts)를 돌려준다."""
        ledger = evidence.current()
        mine = ledger.get(review_id) if ledger is not None else None
        if not mine or not mine.get("editable"):
            return _refused([f"'{review_id}'는 이번 실행에서 기록한 판단이 아닙니다. record_advisory_opinion이 돌려준 review_id를 쓰세요."])
        details = {k: plain(v) for k, v in dict(personas=personas, risks=risks, uncertainties=uncertainties, sections=sections).items()
                   if v is not None}
        try:
            core = store.get_unchecked(review_id)
            problems = op.check_details(details, ledger, core["opinion"])
            if problems:
                return _refused(problems)
            saved = store.add_details(review_id, details, op.snapshot(details, ledger))
        except StoreUnavailable as e:
            return _refused([f"판단 저장소를 쓸 수 없습니다: {e}"])
        out = _accepted(saved)
        return _with_report(out, saved) if saved["opinion"].get("report") else out

    @tool(LOOKUP_TOOL)
    def lookup_advisory_reviews(company: str | None = None, review_id: str | None = None, report: bool = False) -> dict[str, Any]:
        """저장된 경영자문 판단(이전 평가)을 조회한다: 권고, 결론, 조건, 위험, 핵심 수치, 근거 기록과 각 id.
        재평가 전에 이전 판단을 확인하거나, 사용자가 지난 평가를 물을 때 쓴다. 조회한 판단의 근거는 '판단id:근거id' 형태로 인용할 수 있다.
        report=true면 그 판단(review_id가 없으면 가장 최근 판단)의 투자심의 보고서(PDF) 참조를 돌려준다.

        Args:
            company: 기업명 (최근 판단 순, 최대 5건).
            review_id: 특정 판단 id (예: ADV-2026-11-12-01).
            report: 보고서 참조가 필요하면 true.
        """
        if not company and not review_id:
            return {"error": "company 또는 review_id가 필요합니다."}
        viewer = current_viewer()
        try:
            if review_id:
                found = store.get(viewer, review_id)
                reviews = [found] if found else []
            else:
                reviews = store.history(viewer, company or "")
        except StoreUnavailable as e:
            return {"error": f"판단 저장소를 쓸 수 없습니다: {e}"}
        if not reviews:
            return {"found": False, "reviews": [], "message": "저장된 판단이 없거나 열람 권한이 없습니다."}
        for r in reviews:
            _remember(r)
        out: dict[str, Any] = {"found": True, "reviews": [summary(r) for r in reviews]}
        if report:
            out = _with_report(out, reviews[0])
        return out

    for t in (record_advisory_opinion, add_advisory_details):  # an answer must not leave a refused record unfixed (agent_core.answer_checks)
        t.metadata = {**(t.metadata or {}), "answer_check": unrecorded_judgement}
    return record_advisory_opinion, add_advisory_details, lookup_advisory_reviews

"""Risk taxonomy and the keyword rules every news analysis falls back to.

General categories apply to any company or topic; the decision checklists (CHECK_POINTS) are
questions a reader should ask about each kind of risk — an M&A or investment review uses them
as one lens, nothing in the taxonomy assumes a deal.

The rules are the fallback when no model is configured or its answer is unusable: they read the
headline and text, so they are cheap and explainable but coarse.
"""

from __future__ import annotations

import json
from typing import Any

CATEGORY_LABELS = {
    "customer_concentration": "고객 집중도",
    "legal_dispute": "법적 분쟁",
    "key_personnel": "핵심인력",
    "labor_dispute": "노사 분쟁",
    "regulatory": "규제·제재",
    "market_reaction": "주가·주주 반응",
    "operations": "생산·공급 차질",
    "financial": "재무·실적",
    "governance": "지배구조·경영권",
    "security_incident": "보안·안전 사고",
}

# Cues in an article's own text (a user's request is matched by RISK_CATEGORY_KEYWORDS).
ARTICLE_CUES: dict[str, tuple[str, ...]] = {
    "customer_concentration": ("최대 고객", "매출 비중", "고객 집중", "고객사 이탈", "공급업체 다변화", "주문 축소", "물량 축소", "거래 중단"),
    "legal_dispute": ("소송", "특허침해", "특허 침해", "가처분", "제소", "피소", "분쟁", "판결"),
    "key_personnel": ("사임", "퇴사", "이직", "핵심 인력", "핵심인력", "연구진 이탈", "인력 이탈", "경영진 교체", "퇴진"),
    "labor_dispute": ("노조", "파업", "임금 협상", "임금협상", "쟁의", "노사", "성과급 갈등", "성과급 논란", "성과급 반발"),
    "regulatory": ("규제", "제재", "과징금", "공정위", "금산법", "당국 조사", "수출 통제", "수출통제", "인허가"),
    "market_reaction": ("주가 하락", "주가 급락", "급락", "흘러내", "빠졌", "등 돌", "약세", "실망", "주주 불만", "매도세"),
    "operations": ("감산", "생산 차질", "생산 중단", "가동 중단", "공급 부족", "수율 문제", "리콜", "가동률 하락"),
    "financial": ("적자 전환", "영업손실", "순손실", "실적 부진", "어닝 쇼크", "신용등급 강등", "유동성 위기", "부채비율", "적자"),
    "governance": ("경영권 분쟁", "횡령", "배임", "지배구조", "오너 리스크", "압수수색", "검찰 수사"),
    "security_incident": ("해킹", "개인정보 유출", "정보 유출", "랜섬웨어", "화재", "폭발", "사망 사고", "중대재해"),
}
# A non-risk article counts as good news only with one of these; otherwise it is neutral.
POSITIVE_CUES = ("수주", "흑자", "호실적", "최대 실적", "역대 최대", "신기록", "돌파", "증가", "1위", "연속", "채택", "출시",
                 "투자 유치", "승소", "인증", "급등", "공급 계약", "배당")
# Opinion pieces argue rather than report an event: at most LOW.
OPINION_CUES = ("칼럼", "사설", "기고", "시론", "오피니언", "[기자수첩]", "[데스크", "[논단", "[시각", "[기자의 눈")
PRESS_RELEASE_CUES = ("보도자료", "[알림]", "[게시판]", "[인사]", "[부고]")
HIGH_CUES = ("대규모", "전면", "중단", "판매금지", "수입 금지", "형사", "기소", "패소", "잇단", "대거", "최대 규모")
LOW_CUES = ("가능성", "검토", "전망", "우려", "관측")

# Risk category id → keywords that select it from a natural-language request.
# Matching is case-insensitive substring, so "고객" also covers "주요 고객사".
RISK_CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "customer_concentration": ("고객", "매출 의존", "매출의존", "집중도", "customer", "concentration"),
    "legal_dispute": ("법적", "소송", "특허", "분쟁", "legal", "lawsuit", "litigation", "patent"),
    "key_personnel": ("핵심인력", "핵심 인력", "인력", "창업자", "cto", "이탈", "retention", "personnel", "talent"),
    "labor_dispute": ("노사", "노조", "파업", "labor", "strike", "union"),
    "regulatory": ("규제", "제재", "당국", "법안", "regulation", "sanction"),
    "market_reaction": ("주가", "주주", "투자자", "시장 반응", "stock", "shareholder"),
    "operations": ("생산", "공급망", "감산", "수율", "공장", "production", "supply chain"),
    "financial": ("실적", "재무", "적자", "부채", "신용", "financial", "earnings"),
    "governance": ("지배구조", "경영권", "횡령", "배임", "governance"),
    "security_incident": ("해킹", "유출", "보안", "사고", "security", "breach"),
}

# What a decision maker should confirm for each kind of risk: questions, not findings.
CHECK_POINTS: dict[str, tuple[str, ...]] = {
    "customer_concentration": (
        "최대 고객의 매출 비중과 공급계약 잔여 기간",
        "고객 다변화 계획과 신규 고객 확보 현황",
        "최대 고객 이탈 시 매출·가치 하락 폭",
    ),
    "legal_dispute": (
        "분쟁 상대와 쟁점, 지금 어느 단계인지 (제기·심리·판결)",
        "예상 배상액과 판매금지(가처분) 등 사업에 미칠 영향",
        "거래 계약의 진술·보장, 손해배상 조항 반영 여부",
    ),
    "key_personnel": (
        "핵심 인력 명단과 잔류 조건(리텐션 보상, 스톡옵션 행사 시점)",
        "통합 후 조직 운영·연구 자율성 보장 방안",
        "핵심 인력 이탈 시 기술 이전·개발 일정 영향",
    ),
    "labor_dispute": ("쟁의 일정과 핵심 쟁점", "생산·납기에 미치는 영향 범위", "노사 협의 대응 계획"),
    "regulatory": ("적용되는 규제·법 조항과 시행 시점", "사업 계획·주주환원에 미치는 영향 범위", "당국·국회 논의 동향과 대응 창구"),
    "market_reaction": ("주가 하락·비판의 직접 원인 (실적·주주환원·업황)", "주요 주주·외국인 투자자의 반응", "IR·주주 소통 계획"),
    "operations": ("영향받는 제품·생산 라인과 기간", "납기·매출에 미치는 규모", "대체 생산·공급처 확보 여부"),
    "financial": ("실적 변동의 원인이 일회성인지 구조적인지", "현금흐름·차입 여력과 만기 구조", "가이던스·배당 정책 변화"),
    "governance": ("관련 인물·지분 구조와 의사결정 영향", "수사·소송 진행 단계와 경영 공백 가능성", "이사회·감사 기능의 대응"),
    "security_incident": ("피해 범위(고객·데이터·설비)와 복구 상황", "규제 보고·과징금 등 법적 책임", "재발 방지 대책과 비용"),
}

LEVELS = ("HIGH", "MEDIUM", "LOW")


def rule_risk(text: str, title: str) -> dict[str, Any] | None:
    """The category with the most cues in `text`; HIGH / LOW from strong / hedging words; opinion ≤ LOW."""
    hits = {cat: sum(c in text for c in cues) for cat, cues in ARTICLE_CUES.items()}
    category = max(hits, key=hits.get)
    if not hits[category]:
        return None
    if any(c in title for c in OPINION_CUES):
        level = "LOW"
    else:
        level = "HIGH" if any(c in text for c in HIGH_CUES) else "LOW" if any(c in text for c in LOW_CUES) else "MEDIUM"
    return {"category": category, "label": CATEGORY_LABELS[category], "level": level}


def rule_sentiment(text: str) -> str:
    if any(c in text for cues in ARTICLE_CUES.values() for c in cues):
        return "negative"
    return "positive" if any(c in text for c in POSITIVE_CUES) else "neutral"


def article_type(title: str, outlet_count_of_body: int = 1) -> str:
    """report | opinion | press_release (wire_copy is decided from duplicates, not the headline)."""
    if any(c in title for c in OPINION_CUES):
        return "opinion"
    if any(c in title for c in PRESS_RELEASE_CUES):
        return "press_release"
    return "report"


def requested_categories(query: str, topics: list[str]) -> set[str]:
    """Risk categories a request mentions. Empty means "no restriction"."""
    text = f"{query} {' '.join(topics)}".lower()
    return {cat for cat, keys in RISK_CATEGORY_KEYWORDS.items() if any(k in text for k in keys)}


def parse_json_object(text: str) -> dict | None:
    if not text:
        return None
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        value = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def parse_json_array(text: str) -> list:
    start, end = (text or "").find("["), (text or "").rfind("]")
    if start == -1 or end <= start:
        return []
    try:
        value = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return []
    return value if isinstance(value, list) else []

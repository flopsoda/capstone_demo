
# 05. M&A 경영진 종합 평가 및 보고서 생성 기능 개발

- **개발 주제:** CEO·CFO·CTO 관점 통합 및 M&A 경영진 종합 보고서 생성
- **개발 목적:** 개별 Persona 중심의 M&A 평가를 통합하여 경영진의 의사결정에 필요한 위험, 기회, 판단 근거 및 인수 조건 제공
- **주요 개발 내용:** 경영진 종합 평가 Workflow 구현, 공통 위험·인수 기회 통합, 최종 의사결정 근거 생성, 종합 보고서 생성 및 기존 Tool 연동
- **진행 시기:** 2026년 11월 초 (04 문서 작성 이후)

## 1. 개발 목적

기존 경영자문 Agent는 CEO, CFO, CTO 관점별 M&A 평가와 What-if 시나리오 분석을 지원하였다.

그러나 각 Persona의 평가 결과를 개별적으로 제공하는 구조이므로, 경영진 전체의 판단 근거와 주요 위험을 하나의 종합 보고서로 확인하는 기능이 필요하였다.

이번 개발에서는 기존 평가 함수를 재사용하여 세 관점의 분석을 통합하고, 최종 인수 권고와 선행 조건을 함께 제공하도록 확장하였다.

주요 개발 목표는 다음과 같다.

1. CEO·CFO·CTO 평가 결과 통합
2. 중복 위험 및 긍정 요인 정리
3. 위험 등급에 따른 주요 위험 우선순위 구성
4. 최종 권고와 의사결정 근거 제공
5. 경영진 종합 보고서 생성
6. 기존 M&A Tool 및 Agent 구조와의 호환성 유지

## 2. 수정 및 추가 파일

| 파일 | 개발 내용 |
|---|---|
| `backend/app/library/workflows/mna_executive_summary.py` | 종합 평가 Workflow 및 보고서 생성 |
| `backend/app/library/tools/run_mna_review.py` | `persona="ALL"` 종합 평가 요청 연결 |
| `backend/app/systems/k_electronics/agents/advisory.py` | 종합 평가 결과 기반 LLM 보고서 처리 |
| `backend/tests/test_mna_executive_summary.py` | 종합 평가 및 보고서 검증 테스트 |

기존 `review_acquisition()`과 M&A 평가 데이터를 재사용하였으며, 별도의 평가 엔진을 새로 구현하지 않았다.

## 3. 경영진 종합 평가 Workflow 구현

### 3.1. CEO·CFO·CTO 평가 결과 통합

**파일:** `mna_executive_summary.py`

**함수:** `review_executive_summary()`

기존 `review_acquisition()`을 CEO, CFO, CTO 관점으로 각각 실행하고 결과를 통합하도록 구현하였다.

```python
reviews = {
    persona: review_acquisition(
        query=query,
        company=company,
        persona=persona,
        context=context,
        path=path,
    )
    for persona in ("CEO", "CFO", "CTO")
}
```

각 Persona는 기존 평가 기준을 유지한다.

| Persona | 주요 평가 요소 |
|---|---|
| CEO | 전략 적합성, 사업 성장성, 시장 진입 효과 |
| CFO | 인수가격, 수익성, 고객 집중도, 재무 위험 |
| CTO | 기술 적합성, 핵심인력, 특허 위험, 기술 통합 |

개별 분석 결과는 `executive_reviews`에 저장하며, Persona별 평가 항목과 긍정·우려 요인을 구분하여 제공한다.

이를 통해 한 번의 종합 평가 요청으로 세 관점의 분석 내용을 확인할 수 있도록 개선하였다.

### 3.2. 주요 위험 통합

**함수:** `extract_key_risks()`

세 Persona에서 `verdict="concern"`으로 평가한 항목을 추출하고, `criterion` 기준으로 중복을 제거하도록 구현하였다.

주요 처리 내용은 다음과 같다.

- 동일한 위험 항목의 중복 제거
- 해당 위험을 평가한 Persona 기록
- 위험 등급이 서로 다르면 높은 등급 우선 반영
- `HIGH → MEDIUM → LOW` 순서로 정렬

이를 통해 경영진 전체가 공통으로 고려해야 할 위험을 우선순위에 따라 확인할 수 있도록 하였다.

### 3.3. 주요 인수 기회 통합

**함수:** `extract_key_opportunities()`

각 Persona의 `verdict="positive"` 평가 항목을 수집하여 중복 없이 통합하도록 구현하였다.

전략적 적합성, 기술 경쟁력, 시장 진입 기간 단축, 사업 성장성 등 긍정 요인을 정리하고, 해당 요인을 평가한 Persona 정보를 함께 제공한다.

### 3.4. 최종 의사결정 근거 생성

**함수:** `build_decision_rationale()`

종합 평가 결과를 바탕으로 다음 정보를 구조화하였다.

| 반환 항목 | 내용 |
|---|---|
| `recommendation` | 최종 인수 권고 |
| `positive_factors` | 주요 긍정 요인 |
| `high_risks` | HIGH 등급 위험 |
| `required_conditions` | 인수 진행 조건 |
| `summary` | 최종 판단 근거 요약 |

최종 권고는 기존 M&A 평가 Workflow의 판정 결과를 사용한다.

따라서 LLM이 임의로 인수 진행 여부를 결정하는 것이 아니라, 기존 평가 결과를 바탕으로 경영진에게 판단 근거를 제공하는 구조이다.

## 4. 경영진 종합 보고서 생성

### 4.1. 보고서 생성 구조

**함수:** `review_executive_summary()`

종합 평가 결과에 `verified_report` 필드를 추가하여 구조화된 데이터를 기반으로 기본 보고서를 생성하도록 구현하였다.

보고서는 다음 항목으로 구성된다.

1. 기업 개요
2. 재무 및 기업가치
3. 주요 고객 및 기술
4. CEO·CFO·CTO 관점별 평가
5. 핵심 위험
6. 인수 기회
7. 인수 조건
8. 최종 권고

기본 보고서는 기존 Workflow 결과에 저장된 수치와 평가 내용을 이용하여 생성한다.

현재 Agent의 최종 자연어 보고서는 LLM이 작성하도록 처리하고 있으며, `verified_report`는 구조화된 기준 보고서로 유지된다.

### 4.2. 기업별 평가 결과 반영

뉴로엣지의 주요 입력 정보는 다음과 같다.

| 항목 | 값 |
|---|---|
| 주요 사업 | AI NPU 설계 |
| 연매출 | 4,200억 원 |
| 3년 매출 CAGR | 31% |
| 영업이익률 | -8.2% |
| 인수 요구가격 | 2.8조 원 |
| 추정 적정가치 | 2.0~2.3조 원 |
| 최대 고객 | 알파모바일 |
| 최대 고객 매출 비중 | 46% |

종합 보고서에서는 이러한 기업 정보를 기반으로 Persona별 판단 근거를 제시한다.

예를 들어 CEO는 기술 확보와 시장 진입 효과를, CFO는 인수가격과 재무 위험을, CTO는 특허소송과 핵심인력 유지 문제를 중심으로 분석한다.

### 4.3. 기존 M&A Tool 연동

**파일:** `run_mna_review.py`

기존 `run_mna_review` Tool에서 `persona="ALL"`을 종합 평가 요청으로 처리하도록 연결하였다.

```python
if persona.upper() == "ALL":
    return review_executive_summary(
        query=query,
        company=company,
        context=context,
        path=path,
    )
```

실제 구현에서는 What-if 변경 인자가 동시에 전달되는지 먼저 확인한다.

| 입력 조건 | 실행 기능 |
|---|---|
| `CEO`, `CFO`, `CTO` | 개별 M&A 평가 |
| 개별 Persona + 변경 조건 | What-if 분석 |
| `ALL` | 경영진 종합 평가 |

현재 `ALL`과 What-if 변경 조건을 동시에 처리하는 기능은 지원하지 않는다.

기존 Tool 이름과 필수 입력 구조를 유지하여 기존 Agent 실행 방식과의 호환성을 확보하였다.

## 5. 종합 평가 결과

뉴로엣지 종합 평가에서 주요 위험과 인수 기회가 다음과 같이 정리되었다.

**주요 긍정 요인**

- AI NPU 설계 기술 확보에 따른 전략적 적합성
- 자체 개발 대비 시장 진입 기간 단축 가능성
- 매출 성장성 및 사업 추진 가치
- 핵심 기술 확보를 통한 사업 경쟁력 강화

**주요 위험**

- 인수가격의 적정가치 상단 초과
- 주요 고객에 대한 높은 매출 의존도
- 핵심 설계팀 이탈 위험
- 영업적자 및 추가 자금 소요 가능성
- 특허소송 잠재손실 미확정

**최종 인수 권고**

`CONDITIONAL_PROCEED`

**인수 진행 조건**

1. 인수 협상 기준가격 2.3조 원 이하
2. 알파모바일 계약 지속 여부 검증
3. 코어NPU 특허소송 잠재손실 검증
4. 창업자 CTO 중심의 핵심 설계팀 retention 조건 확보

종합 평가에서는 긍정 요인이 존재하더라도 주요 위험이 해소되지 않았으므로 조건부 진행 권고를 유지하였다.

## 6. 자동 테스트

**파일:** `test_mna_executive_summary.py`

종합 평가 구현 이후 다음 항목을 중심으로 테스트를 추가하였다.

- CEO·CFO·CTO 평가 결과 포함 여부
- 개별 평가와 종합 평가의 권고 일관성
- 공통 위험 중복 제거
- 위험 등급 우선순위 처리
- 인수 기회 통합
- 최종 판단 근거 생성
- 종합 보고서 필수 내용 포함
- 미등록 기업 및 잘못된 입력 처리

주요 테스트 함수:

- `test_executive_summary_contains_all_personas()`
- `test_executive_summary_matches_individual_recommendations()`
- `test_executive_summary_key_risks()`
- `test_executive_summary_key_opportunities()`
- `test_verified_report_includes_three_executive_views()`
- `test_invalid_price_returns_tool_error()`

본 단계에서 종합 평가 Workflow와 기본 보고서 생성 기능을 구현하였다.

이후 추가된 LLM 보고서 검증 및 재작성 기능은 06 문서에 별도로 기록한다.

## 7. 개발 결과

이번 개발을 통해 개별 Persona 기반 M&A 평가 기능을 CEO·CFO·CTO 통합 경영진 종합 평가로 확장하였다.

**주요 성과**

- 기존 평가 Workflow 재사용
- 세 Persona의 평가 결과 통합
- 주요 위험 및 인수 기회 중복 제거
- 최종 의사결정 근거 구조화
- 경영진 종합 보고서 생성
- 기존 `run_mna_review` Tool과 연동

새로운 평가 엔진을 추가하지 않고 기존 기업 데이터와 평가 기준을 활용하여, 경영진 의사결정에 필요한 정보를 하나의 보고서로 제공할 수 있도록 개선하였다.

**검증 범위:** 종합 평가 함수 및 Tool 구현 확인. 이후 수행한 실제 LLM 응답 검증과 최종 실행 결과는 07 문서에서 별도로 정리한다.

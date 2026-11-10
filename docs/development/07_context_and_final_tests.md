
# 07. M&A 회의 맥락 반영 및 경영자문 Agent 최종 실행 검증

- **개발 주제:** What-if 회의 맥락 적용 개선 및 경영자문 Agent 최종 실행 테스트
- **개발 목적:** 회의에서 확정된 판단 기준을 변경 전후 평가에 반영하고, 실제 LLM 및 Super Agent 환경에서 주요 기능 검증
- **주요 개발 내용:** What-if context 전달 개선, 회의 기준·위험 반영, 실제 Ollama 보고서 검증, K전자 Super Agent 위임 테스트
- **검증 시기:** 2026년 11월 초, 11월 9일 최종 테스트

## 1. 개발 및 검증 목적

기존 경영자문 Agent는 자연어 질문에서 회의 맥락을 추출하여 `run_mna_review` Tool에 전달할 수 있었다.

그러나 초기 What-if 분석에서는 사용자가 변경한 인수가격 등의 시나리오 조건은 평가에 반영되었지만, 회의에서 확정된 가격 상한이나 확인된 위험이 변경 전후 평가에 일관되게 적용되는지 추가 확인이 필요하였다.

이번 작업에서는 회의 맥락을 What-if 평가 Workflow에 반영하도록 수정하고, 실제 Ollama 환경에서 최종 보고서까지 정상적으로 전달되는지 검증하였다.

또한 경영자문 Agent의 개별 분석, 종합 보고서, What-if, 예외 처리 및 K전자 Super Agent 위임 기능을 직접 실행하여 확인하였다.

## 2. 수정 및 관련 파일

| 파일 | 개발 내용 |
|---|---|
| `backend/app/library/workflows/mna_scenario.py` | What-if 변경 전후 평가에 회의 맥락 적용 |
| `backend/app/library/tools/run_mna_review.py` | What-if context 전달 및 변경 후 평가 근거 생성 |
| `backend/tests/test_mna_scenario.py` | 회의 상한·고객 집중·특허 위험 맥락 테스트 |
| `backend/app/systems/k_electronics/agents/advisory.py` | 실제 경영자문 Agent 실행 및 보고서 검증 |
| `backend/app/systems/k_electronics/agents/k_super.py` | K전자 Super Agent를 통한 위임 실행 확인 |

공용 Super Agent와 A2A 통신 구조는 이번 경영자문 기능 개발에서 수정하지 않았다.

## 3. What-if 회의 맥락 반영 개선

### 3.1. 기존 문제

기존 What-if 비교에서는 변경 전후 평가 시 다음과 같이 빈 맥락을 사용하는 부분이 있었다.

```python
before = evaluate_all(company, {})
after = evaluate_all(scenario_company, {})
```

이 경우 회의에서 정한 가격 상한이나 확인된 위험이 실제 평가 근거에 반영되지 않을 수 있었다.

### 3.2. Workflow 수정

**파일:** `mna_scenario.py`

**함수**

- `compare_mna_scenarios()`
- `compare_persona_scenarios()`

두 함수에 `context`를 선택적 인자로 전달하도록 확장하였다.

기존 `extract_context_signals()`를 재사용해 회의 맥락을 구조화하고, 변경 전후 평가에 동일하게 적용하도록 수정하였다.

```python
context_signals = extract_context_signals(context)

before = evaluate_all(company, context_signals)
after = evaluate_all(scenario_company, context_signals)
```

이를 통해 사용자가 제시한 회의 기준과 위험 요소를 기존 기업 데이터와 변경된 시나리오에 함께 반영할 수 있도록 개선하였다.

### 3.3. Tool 연동 수정

**파일:** `run_mna_review.py`

What-if 호출 시 `compare_persona_scenarios()`에 `context`를 전달하도록 변경하였다.

```python
result = compare_persona_scenarios(
    target,
    persona=persona,
    asking_trillion_krw=asking_trillion_krw,
    operating_margin=operating_margin,
    revenue_share=revenue_share,
    context=context,
)
```

변경 후 평가 항목을 생성할 때에도 다음과 같이 회의 맥락을 적용하였다.

```python
assessments = evaluate_all(
    scenario_company,
    extract_context_signals(context),
)
```

기존 What-if 반환 형식을 유지하면서 평가 근거에 회의 맥락이 포함되도록 개선하였다.

## 4. 회의 맥락 적용 테스트

### 4.1. 테스트 조건

**대상 기업:** 뉴로엣지

**평가 관점:** CFO

**회의에서 확인된 조건**

- 내부 인수가격 상한 2.5조 원 이하
- 알파모바일 고객 집중 위험
- 코어NPU 특허소송 위험

**What-if 변경 조건**

- 기존 인수가격: 2.8조 원
- 변경 인수가격: 2.7조 원
- 추정 적정가치 상단: 2.3조 원

### 4.2. 가격 기준 반영 결과

변경 후 인수가격 2.7조 원은 다음 기준을 모두 초과한다.

| 평가 기준 | 값 | 판단 |
|---|---|---|
| 회의에서 확정한 내부 상한 | 2.5조 원 | 초과 |
| 추정 적정가치 상단 | 2.3조 원 | 초과 |
| 변경 후 인수가격 | 2.7조 원 | 가격 위험 유지 |

Tool의 가격 평가 근거에서 다음 내용을 확인하였다.

```text
요청 맥락의 내부 기준(2.5조 원) 초과
— 가격 타당성 재검토 대상
```

따라서 회의에서 정한 내부 상한이 실제 평가 근거에 반영되는 것을 확인하였다.

### 4.3. 고객 집중 및 특허 위험 테스트

회의 맥락에 고객 집중 위험과 특허소송 위험이 포함된 경우, 관련 평가 근거에 해당 위험이 반영되는지 확인하였다.

**고객 집중 위험**

- 최대 고객: 알파모바일
- 매출 비중: 46%
- 회의에서 확인된 고객 관련 위험을 평가 근거에 반영

**특허소송 위험**

- 코어NPU 관련 특허 분쟁
- 회의에서 확인된 소송 위험을 평가 근거에 반영
- 잠재손실 검증 조건 유지

CFO 및 CTO 관점의 관련 테스트를 추가하여 회의 맥락 전달 여부를 검증하였다.

**관련 테스트 함수**

- `test_what_if_respects_meeting_context()`
- `test_what_if_respects_customer_and_patent_context()`

두 테스트를 포함한 경영자문 관련 자동 테스트가 통과하였다.

## 5. 실제 Ollama Agent 테스트

### 5.1. CFO 개별 평가

**테스트 질문**

> 뉴로엣지 인수 타당성을 CFO 관점에서 검토해줘.

**확인 결과**

- 매출액 4,200억 원 반영
- 영업이익률 -8.2%를 적자로 평가
- 인수가격 2.8조 원 반영
- 적정가치 상단 대비 프리미엄 21.7% 반영
- 알파모바일 매출 비중 46% 반영
- 재무 위험 및 인수 조건 설명
- 최종 권고 `CONDITIONAL_PROCEED`

**판정:** 실제 LLM 개별 보고서의 주요 수치 및 권고 일치.

### 5.2. CEO·CFO·CTO 종합 보고서

**테스트 질문**

> 뉴로엣지 인수 타당성을 CEO, CFO, CTO 관점에서 종합적으로 평가하고 최종 경영진 보고서를 작성해줘.

**확인 결과**

- CEO 전략적 평가 포함
- CFO 재무적 평가 포함
- CTO 기술적 평가 포함
- 핵심 위험 및 위험 등급 구분
- 인수 조건 4개 포함
- 최종 권고 `CONDITIONAL_PROCEED`
- 종합 판단 근거 설명

LLM이 생성한 보고서는 세 관점별 평가와 최종 권고를 포함하였다.

다만 일부 종합 의견과 최종 권고 내용이 반복되는 표현상의 중복은 확인되었다.

**판정:** 실제 LLM 종합 보고서 생성 및 주요 정보 일치.

### 5.3. What-if 가격 변경 테스트

**테스트 조건**

- 변경 전 인수가격: 2.8조 원
- 변경 후 인수가격: 2.1조 원
- 평가 관점: CFO

**확인 결과**

| 항목 | 변경 전 | 변경 후 |
|---|---|---|
| 인수가격 | 2.8조 원 | 2.1조 원 |
| 가격 평가 | HIGH 위험 | 긍정 평가 |
| 고객 집중 위험 | HIGH | HIGH |
| 최종 권고 | 조건부 진행 | 조건부 진행 |

인수가격 인하에 따라 가격 관련 위험이 해소되었으며, 협상 기준가격 조건이 변경 후 인수 조건에서 제거되었다.

그러나 고객 집중, 특허소송 및 핵심인력 위험은 유지되므로 최종 권고는 변경되지 않았다.

**판정:** 변경 전후 평가, 권고 및 인수 조건 비교 정상.

### 5.4. 회의 맥락 포함 What-if 테스트

**테스트 조건**

- 회의 내부 인수가격 상한: 2.5조 원
- 추정 적정가치 상단: 2.3조 원
- 변경 인수가격: 2.7조 원
- 고객 집중 및 특허소송 위험 확인

실제 `collect_events()`로 Agent의 Tool 호출 인자를 확인하였다.

**확인된 주요 인자**

```python
{
    "company": "뉴로엣지",
    "persona": "CFO",
    "asking_trillion_krw": 2.7,
    "context": (
        "회의 상한 2.5조 원 이하, "
        "적정가치 상한 2.3조 원, "
        "알파모바일 고객 집중 위험, "
        "코어NPU 특허소송 위험"
    ),
}
```

위 내용은 실제 호출에서 확인된 핵심 인자를 정리한 것이며, 기타 선택적 인자는 생략하였다.

Tool 결과에서는 다음 내용을 확인하였다.

- 변경 전 인수가격 2.8조 원
- 변경 후 인수가격 2.7조 원
- 변경 전 가격 프리미엄 약 21.7%
- 변경 후 가격 프리미엄 약 17.4%
- 회의 상한 2.5조 원 초과 근거
- 고객 집중 위험 반영
- 최종 권고 `CONDITIONAL_PROCEED`

최종 LLM 보고서에서도 회의 상한 2.5조 원과 적정가치 상한 2.3조 원을 구분하여 설명하였다.

최초 자연어 테스트에서는 회의 상한 2.5조 원이 보고서에 누락된 사례가 있었다.

이후 두 기준을 명시적으로 구분하도록 요청한 재실행에서는 Tool 호출과 보고서 모두 정상적인 결과를 확인하였다.

따라서 이번 실행에서 기능의 정상 동작을 확인했으나, 모든 질문 표현에서 동일한 결과를 보장하는 것은 아니다.

**판정:** 실제 LLM Tool Calling 및 회의 맥락 반영 성공 사례 확인.

### 5.5. 미등록 기업 예외 처리

**테스트 질문**

> 등록되지 않은 기업인 테스트기업XYZ의 인수 타당성을 CFO 관점에서 분석해줘.

**확인 결과**

- 등록되지 않은 기업에 대한 분석 불가능 안내
- 임의의 재무 수치 생성 없음
- 근거 없는 인수 권고 생성 없음
- 추가 자료 제공 안내

**판정:** 테스트한 미등록 기업 요청에서 데이터 부족 안내 정상.

## 6. K전자 Super Agent 위임 테스트

### 6.1. 테스트 목적

경영자문 Agent가 직접 요청받을 때뿐 아니라, K전자 Super Agent의 위임을 통해 호출되는지 확인하였다.

**테스트 질문**

> 뉴로엣지 인수 타당성을 CFO 관점에서 검토해줘.

**실행 함수**

```python
run_k_super(
    "뉴로엣지 인수 타당성을 CFO 관점에서 검토해줘.",
    invocation="direct",
)
```

### 6.2. 확인된 호출 흐름

```text
delegation_start → advisory
tool_start → advisory / run_mna_review
tool_end → advisory / run_mna_review
delegation_end → advisory
```

최종 응답에서는 다음 내용이 확인되었다.

- 조건부 진행 권고
- 가격 프리미엄 21.7%
- 영업이익률 -8.2%
- 고객 매출 비중 46%
- 특허소송 및 핵심인력 위험
- 인수 조건 4개

**판정:** K전자 Super Agent의 경영자문 위임 및 최종 응답 생성 성공.

이번 테스트는 동일한 K전자 Agent System 내부의 위임 기능을 검증한 것이다.

그룹 Super Agent에서 K전자 Super Agent를 HTTP로 호출하는 시스템 간 A2A 통합과는 별개의 검증이다.

## 7. 최종 자동 테스트 결과

### 7.1. 경영자문 관련 테스트

**실행 명령어**

```powershell
python -m pytest tests -v -k "mna or advisory"
```

**결과**

```text
75 passed
```

보고서 검증, What-if 분석, 회의 맥락 반영 및 기존 기능 회귀 테스트가 통과하였다.

### 7.2. 전체 백엔드 테스트

**실행 명령어**

```powershell
python -m pytest tests -q
```

**결과**

```text
127 passed
4 failed
40 skipped
```

실패한 4개 테스트는 모두 `tests/test_group.py`의 그룹 A2A 통신 관련 테스트였다.

주요 오류:

```text
A2A 요청이 거부되었습니다 (HTTP 404)
```

실패 로그에서는 A2A 요청 URL이 빈 문자열로 전달된 사례가 확인되었다.

해당 결과는 그룹 Super Agent와 K전자 시스템 간 A2A 테스트 영역에서 발생한 것으로, 경영자문 관련 테스트 75개 통과 결과와 구분된다.

이후 그룹 A2A 테스트 파일만 실행한 결과에서도 동일한 4개 실패가 재현되었다.

**그룹 A2A 재실행 결과**

```text
4 failed, 6 passed, 2 skipped
```

현재 전체 백엔드 테스트가 모두 통과한 상태는 아니다.

## 8. 현재 개발 상태

| 항목 | 상태 |
|---|---|
| 기존 M&A 평가 로직 개선 | 완료 |
| What-if 시나리오 분석 | 완료 |
| CEO·CFO·CTO 종합 평가 | 완료 |
| LLM 보고서 검증·재작성 | 완료 |
| What-if 회의 맥락 반영 | 구현 및 테스트 완료 |
| 경영자문 자동 테스트 | 75개 통과 |
| 실제 LLM 주요 시나리오 | 정상 동작 사례 확인 |
| K전자 Super Agent 내부 위임 | 확인 |
| 그룹 시스템 간 A2A | 추가 확인 필요 |
| Open WebUI 최종 통합 | 미검증 |

## 9. 개발 결과 및 남은 검증

이번 작업에서는 기존 M&A 평가와 What-if 분석에 회의 맥락을 반영하고, 실제 LLM 환경에서 경영자문 Agent의 주요 기능을 검증하였다.

또한 K전자 Super Agent가 경영자문 Agent를 호출하여 최종 M&A 분석 결과를 반환하는 흐름을 확인하였다.

**주요 성과**

1. What-if 변경 전후 회의 맥락 반영
2. 회의 가격 상한 및 위험 요소 평가 근거 반영
3. CFO 개별 보고서 실행 확인
4. CEO·CFO·CTO 종합 보고서 실행 확인
5. 인수가격 What-if 비교 확인
6. 미등록 기업 예외 처리 확인
7. K전자 Super Agent 내부 위임 확인
8. 경영자문 관련 자동 테스트 75개 통과

**남은 검증**

- 팀 전체 Open WebUI에서 결과 표시 확인
- 그룹 Super Agent와 K전자 시스템 간 A2A 통합 확인
- 다양한 자연어 질문에 대한 LLM 응답 일관성 확인
- 보고서의 파생 수치 및 설명 정확성 추가 점검

## 10. 최종 결론

기존 경영자문 Agent의 M&A 평가 Workflow를 기반으로 What-if, 경영진 종합 분석, LLM 보고서 검증 및 회의 맥락 반영 기능을 확장하였다.

로컬 Python 자동 테스트와 실제 Ollama 실행을 통해 주요 기능이 작동하는 것을 확인하였으며, K전자 Super Agent의 경영자문 위임 기능도 정상적으로 수행되었다.

현재 경영자문 Agent의 계획된 핵심 기능 구현과 주요 단독 실행 검증은 마무리된 상태로 판단한다.

다만 전체 시스템 간 A2A 및 Open WebUI 통합 테스트는 아직 남아 있으므로, 캡스톤 전체 시스템의 최종 검증 완료와는 구분한다.

**검증 범위:** 경영자문 관련 자동 테스트 75개 통과, 실제 Ollama 주요 시나리오 및 K전자 Super Agent 내부 위임 확인. 그룹 A2A와 웹 UI 통합은 미완료.

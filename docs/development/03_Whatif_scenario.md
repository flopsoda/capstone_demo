# 03. M&A What-if 시나리오 분석 기능 개발

- **개발 주제:** M&A 경영자문 Agent의 What-if 시나리오 분석 기능 확장
- **개발 목적:** 사용자 지정 인수 조건에 따른 M&A 타당성 재평가 및 변경 전후 결과 비교
- **주요 개발 내용:** 인수가격·영업이익률·고객 의존도 변경 시나리오 구현, 변경 전후 위험 평가·인수 권고·선행 조건 비교, CEO·CFO·CTO Persona별 결과 정렬, 기존 경영자문 Tool·Skill 확장 및 UI 응답 형식 호환성 개선, 자동 테스트 49개 통과

## 1. 개발 목적

기존 M&A 경영자문 Workflow는 `companies.json`에 저장된 기업 정보를 기준으로 인수 타당성을 평가했다.

따라서 사용자가 인수가격, 영업이익률, 고객 의존도 등의 조건을 변경하더라도 변경된 조건을 적용한 평가 결과와 기존 결과를 비교하기 어려웠다.

이번 개발에서는 기존 M&A 평가 로직을 재사용하면서 다음 기능을 추가했다.

- 사용자가 지정한 인수 조건을 반영하는 What-if 시나리오 분석
- 변경 전후 평가 결과, 위험 수준, 최종 인수 권고 비교
- CEO·CFO·CTO 관점에 따른 변경 항목 정렬
- 변경 전후 인수 진행 조건 비교
- 기존 경영자문 Tool 및 UI 반환 형식과의 호환성 유지

## 2. 수정 및 추가 파일

| 파일 | 수정 목적 |
|---|---|
| `backend/app/library/workflows/mna_scenario.py` | What-if 시나리오 분석 Workflow 신규 구현 |
| `backend/app/library/tools/run_mna_review.py` | 기존 M&A Tool에 What-if 기능 연결 및 UI 호환성 개선 |
| `backend/app/library/skills/mna_feasibility_review.py` | 자연어 변경 조건을 Tool에 전달하도록 호출 지침 확장 |
| `backend/tests/test_mna_scenario.py` | 신규 기능 및 기존 기능 호환성 검증 |

기존 `mna_review.py`의 평가 함수와 `evaluate_all()`, `recommend()`를 재사용했다.

공용 LangGraph 실행 구조, Super Agent, A2A 통신, 프론트엔드 UI 코드는 수정하지 않았다.

## 3. 주요 개발 내용

### 3.1. What-if 시나리오 분석 Workflow 추가

**파일:** `mna_scenario.py`

| 함수 | 기능 |
|---|---|
| `apply_price_scenario()` | 변경된 인수가격을 기업 데이터에 적용 |
| `compare_price_scenarios()` | 변경 전후 가격 적정성 평가 비교 |
| `apply_profitability_scenario()` | 변경된 영업이익률 적용 |
| `apply_customer_scenario()` | 변경된 최대 고객 매출 비중 적용 |
| `compare_mna_scenarios()` | 하나 이상의 조건을 변경하고 전체 M&A 평가 결과 비교 |
| `compare_persona_scenarios()` | CEO·CFO·CTO별 중요 평가 항목 우선 정렬 |

기존 `evaluate_all()`을 통해 변경 전후 평가를 수행하고, `recommend()`로 각각의 최종 인수 권고를 산출하도록 구현했다.

주요 비교 정보는 다음과 같다.

- `assumptions`: 변경 조건
- `changes`: 평가가 변경된 항목과 변경 전후 판단·위험 수준·근거
- `before_recommendation`: 기존 인수 권고
- `after_recommendation`: 변경 후 인수 권고

입력값 유효성 검사를 추가했으며, `deepcopy()`를 사용해 변경된 조건이 원본 기업 데이터에 영향을 주지 않도록 했다.

### 3.2. 인수 진행 조건 비교 기능 추가

**파일:** `mna_scenario.py`

기존 평가에서는 인수 진행에 필요한 조건을 생성했으나, 시나리오 변경으로 어떤 조건이 없어지거나 추가되는지 직접 비교하는 기능은 없었다.

이를 개선하기 위해 다음 반환 항목을 추가했다.

| 항목 | 기능 |
|---|---|
| `before_conditions` | 변경 전 인수 진행 조건 |
| `after_conditions` | 변경 후 인수 진행 조건 |
| `removed_conditions` | 변경으로 제거된 조건 |
| `added_conditions` | 변경으로 새롭게 추가된 조건 |

평가 항목별 `condition`을 수집하고 중복을 제거하도록 구현했다.

예를 들어 인수가격을 낮춰 가격 관련 위험이 완화되면 해당 가격 협상 조건은 제거될 수 있지만, 핵심인력 유지나 고객 계약 검증 조건은 계속 유지될 수 있다.

### 3.3. 기존 M&A Tool 확장

**파일:** `run_mna_review.py`

기존 Tool은 기업명, Persona, 회의·뉴스 맥락 등을 입력받아 일반 M&A 평가를 실행하는 구조였다.

이번에는 기존 Tool에 다음 선택적 파라미터를 추가했다.

| 파라미터 | 의미 |
|---|---|
| `asking_trillion_krw` | 변경할 인수가격(조 원) |
| `operating_margin` | 변경할 영업이익률 |
| `revenue_share` | 변경할 최대 고객 매출 비중 |

**변경된 처리 방식**

- 변경 조건이 없으면 기존 `review_acquisition()` 호출
- 변경 조건이 있으면 `compare_persona_scenarios()` 호출
- What-if 결과에는 `mode="what_if"`를 포함해 일반 평가와 구분

기존 `run_mna_review` Tool 이름과 필수 인자를 유지하여 기존 Agent 호출 방식을 변경하지 않도록 했다.

### 3.4. 경영자문 Skill 지침 확장

**파일:** `mna_feasibility_review.py`

경영자문 Agent가 자연어 질문에서 변경 조건을 파악하고 Tool의 선택적 인자로 전달할 수 있도록 지침을 추가했다.

주요 변경 사항은 다음과 같다.

- 가격은 조 원 단위로 전달
- 영업이익률과 고객 매출 비중은 소수 비율로 변환
- 여러 변경 조건을 하나의 Tool 호출에 전달
- 일반 M&A 평가와 What-if 요청을 구분
- 변경 전후 위험 및 권고 결과를 설명하도록 안내

실제 LLM이 해당 지침에 따라 정확하게 Tool을 호출하는지는 후속 테스트가 필요하다.

### 3.5. 기존 UI 반환 형식 호환성 개선

**파일:** `run_mna_review.py`

**발견한 문제**

기존 프론트엔드의 `src/live/agentEvents.ts`는 `run_mna_review` 결과에서 `inputs`, `assessments`, `recommendation`, `recommendation_label`, `conditions` 등을 사용한다.

그러나 최초 What-if 결과에는 변경 전후 비교 전용 필드만 존재하여, 기존 UI가 결과를 표시할 때 필드 누락으로 오류가 발생할 가능성이 있었다.

**수정 내용**

공용 프론트엔드 파일은 수정하지 않고, `run_mna_review.py`에서 변경 후 기업 데이터를 기반으로 기존 UI가 사용하는 필드를 추가했다.

| 추가 필드 | 반환 내용 |
|---|---|
| `inputs` | 변경된 기업 데이터의 평가 입력 정보 |
| `assessments` | 선택한 Persona의 변경 후 평가 항목 |
| `recommendation` | 변경 후 최종 인수 권고 |
| `recommendation_label` | 권고 결과의 한글 표시명 |
| `conditions` | 변경 후 인수 진행 조건 |

이를 위해 기존 `summarize_inputs()`, `evaluate_all()`, `PERSONAS`, `RECOMMENDATION_LABELS`를 재사용했다.

기존 What-if 비교 정보는 그대로 유지하면서 기존 UI가 요구하는 반환 형식도 제공하도록 확장했다.

현재 기존 UI에는 변경 후 평가 결과를 표시할 수 있도록 데이터를 제공하며, 변경 전후 비교 결과는 별도 필드로 유지한다.

실제 웹 UI 렌더링은 아직 검증하지 않았다.

## 4. 테스트 및 검증

**테스트 파일**
- `tests/test_mna_review_workflow.py`
- `tests/test_mna_scenario.py`

**검증 내용**
- 인수가격, 영업이익률, 고객 의존도 변경
- 복수 조건 동시 적용 및 원본 데이터 보존
- 잘못된 입력값 처리
- 변경 전후 평가·권고·인수 조건 비교
- CEO·CFO·CTO Persona 처리
- 기존 일반 M&A Tool 기능 유지
- What-if Tool 호출 및 반환값 검증
- 기존 UI에서 사용하는 필드 반환 확인

**테스트 실행**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_mna_review_workflow.py tests/test_mna_scenario.py -q
```

**최종 결과: `49 passed`**

기존 평가 및 신규 시나리오 테스트가 모두 통과했다.

추가로 기존 UI가 요구하는 반환 필드를 검증하는 테스트를 작성하여, What-if 분석 시에도 해당 필드가 제공되는 것을 확인했다.

단, 실제 브라우저 렌더링과 LLM Tool Calling은 검증 범위에 포함되지 않았다.

## 5. 개발 결과 및 향후 작업

이번 개발을 통해 기존 고정 데이터 기반 M&A 평가를 사용자 지정 조건 기반의 What-if 분석으로 확장했다.

사용자는 인수가격, 수익성, 고객 의존도를 변경하고, 기존 평가와 변경된 평가의 차이 및 인수 권고 조건의 변화를 비교할 수 있다.

기존 LangGraph 및 Agent 구조를 유지하면서 경영자문 Workflow와 Tool, Skill을 확장했으며, 공용 UI 코드를 수정하지 않고 기존 반환 형식과의 호환성을 개선했다.

**다음 개발 단계**

1. **CEO·CFO·CTO 관점별 평가 결과 정리**
   - 기존 `evaluate_all()`과 Persona별 평가 기준 재사용
   - 각 Persona의 주요 긍정 요인과 위험 요인 추출
   - 세 관점에서 공통으로 확인되는 위험 요인 정리

2. **경영진 종합 평가 결과 생성**
   - Persona별 평가 결과를 하나의 구조화된 결과로 통합
   - 공통 위험 요인과 인수 진행 조건 정리
   - 기존 `recommend()`를 재사용하여 최종 인수 권고 생성

3. **자동 테스트 및 검증**
   - Persona별 평가 결과와 공통 위험 추출 기능 테스트
   - 경영진 종합 평가 및 최종 인수 권고 검증
   - 기존 M&A 평가 및 What-if 테스트 49개 회귀 테스트 실행

본 기능은 변경 조건을 기존 규칙에 적용하여 평가 결과를 비교하는 기능이며, 실제 미래 재무실적을 예측하는 모델은 아니다.

**검증 범위:** 로컬 Python 자동 테스트 49개 통과. 실제 LLM 및 웹 UI 통합 동작은 미검증.


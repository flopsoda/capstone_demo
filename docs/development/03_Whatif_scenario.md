# 03. M&A What-if 시나리오 분석 기능 개발

## 1. 개발 개요

- **개발 주제:** M&A 경영자문 Agent의 What-if 시나리오 분석 기능 확장
- **개발 목적:** 사용자 지정 인수 조건에 따른 M&A 타당성 재평가 및 변경 전후 결과 비교

### 개발 배경

기존 M&A 경영자문 Workflow는 기업 데이터에 저장된 인수가격, 영업이익률, 고객 의존도 등을 기준으로 인수 타당성을 평가했다.

하지만 사용자가 기존 데이터와 다른 인수가격을 제시하더라도 이를 직접 반영하여 재평가하는 기능은 제공하지 않았다.

이에 따라 사용자가 인수가격 등 주요 조건을 직접 변경하고, 기존 평가와 변경된 평가의 차이를 비교할 수 있도록 What-if 시나리오 분석 기능을 개발했다.

## 2. 주요 개발 내용

### 2.1. 인수가격 변경 및 비교 기능

**구현 함수**
- `apply_price_scenario()`
- `compare_price_scenarios()`

사용자가 제시한 새로운 인수가격을 기업 데이터 복사본에 적용하고, 기존 가격과 변경 가격에 대한 평가 결과를 비교하도록 구현했다.

예를 들어 뉴로엣지의 인수가격을 2.8조 원에서 2.2조 원으로 변경하면, 기존의 높은 가격 위험이 완화되는지 확인할 수 있다.

### 2.2. 수익성 및 고객 의존도 변경 기능

**구현 함수**
- `apply_profitability_scenario()`
- `apply_customer_scenario()`

인수가격 외에도 영업이익률과 최대 고객 매출 비중을 변경할 수 있도록 확장했다.

- 영업이익률: -8.2% → +5%
- 최대 고객 매출 비중: 46% → 25%

입력값 검증을 추가하여 허용 범위를 벗어난 수치나 유효하지 않은 값이 전달되면 오류를 발생시키도록 구현했다.

### 2.3. 복수 조건 기반 전체 M&A 평가 비교

**구현 함수**
- `compare_mna_scenarios()`

인수가격, 영업이익률, 고객 의존도를 동시에 변경할 수 있도록 구현했다.

기존 `mna_review.py`의 `evaluate_all()`과 `recommend()`를 재사용하여 변경 전후 전체 평가 결과와 최종 인수 권고를 비교한다.

주요 비교 항목은 다음과 같다.

- 평가 항목별 판단(`verdict`)
- 위험 수준(`level`)
- 판단 근거(`finding`)
- 최종 인수 권고(`recommendation`)

원본 기업 데이터의 변경을 방지하기 위해 `deepcopy()`를 사용했다.

### 2.4. CEO·CFO·CTO 관점별 결과 정렬

**구현 함수**
- `compare_persona_scenarios()`

기존 `PERSONAS` 설정을 재사용하여 경영진 관점에 따라 중요한 평가 항목을 우선 표시하도록 구현했다.

- CEO: 전략적 적합성, 시장 진입 속도, 위험과 기회 균형 등
- CFO: 인수가격, 수익성, 고객 집중도 등
- CTO: 기술 적합성, 핵심인력 유지, 특허 위험 등

동일한 시나리오에는 기존 평가 규칙을 동일하게 적용하며, Persona에 따라 변경 항목의 표시 우선순위를 조정한다.

### 2.5. 변경 전후 인수 권고 조건 비교

기존 What-if 결과에 인수 진행 조건을 비교하는 기능을 추가했다.

**추가 반환 항목**

| 반환값 | 설명 |
|---|---|
| `before_conditions` | 기존 인수 진행 조건 |
| `after_conditions` | 변경 후 인수 진행 조건 |
| `removed_conditions` | 변경 후 제거된 조건 |
| `added_conditions` | 변경 후 새롭게 추가된 조건 |

이를 통해 특정 위험이 개선되었더라도 핵심인력 유지 등의 조건이 여전히 필요한지 확인할 수 있도록 했다.

### 2.6. 경영자문 Agent Tool 연결

**수정 파일:** `backend/app/library/tools/run_mna_review.py`

기존 `run_mna_review` Tool을 확장하여 What-if 분석에 필요한 입력 파라미터를 추가했다.

- `asking_trillion_krw`
- `operating_margin`
- `revenue_share`

조건 변경 요청이 없는 경우 기존 `review_acquisition()`을 실행하고, 변경 조건이 있는 경우 `compare_persona_scenarios()`를 실행하도록 분기했다.

이를 통해 기존 M&A 평가 기능과 새로운 What-if 분석 기능을 동일한 Tool 인터페이스에서 사용할 수 있도록 구성했다.

### 2.7. Agent Skill 지침 개선

**수정 파일:** `backend/app/library/skills/mna_feasibility_review.py`

사용자의 자연어 질문에서 변경 조건을 파악하고 Tool 인자로 전달할 수 있도록 Skill 지침을 확장했다.

주요 내용은 다음과 같다.

- 인수가격 단위는 조 원으로 전달
- 영업이익률과 고객 의존도는 비율로 변환
- 여러 조건을 하나의 Tool 호출에 전달
- 일반 평가와 What-if 분석 요청 구분
- 변경 전후 위험 및 권고 결과 설명

단, 자연어 질문에 대한 실제 Tool Calling 정확도는 추후 LLM 연동 테스트에서 검증할 예정이다.

## 3. 주요 분석 사례

**분석 대상: 뉴로엣지**

| 항목 | 기존 조건 | 변경 조건 |
|---|---|---|
| 인수가격 | 2.8조 원 | 2.2조 원 |
| 영업이익률 | -8.2% | +5% |
| 최대 고객 매출 비중 | 46% | 25% |
| 가격 위험 | HIGH | 완화 |
| 수익성 평가 | concern | positive |
| 고객 집중 위험 | HIGH | MEDIUM |
| 최종 인수 권고 | CONDITIONAL_PROCEED | CONDITIONAL_PROCEED |

인수가격과 재무 조건이 개선되면서 일부 위험 수준은 낮아졌으나, 핵심인력 유지 등 다른 위험이 남아 있어 최종 권고는 조건부 진행으로 유지되었다.

이를 통해 최종 권고의 변화뿐만 아니라 개별 위험 요소의 개선 여부도 확인할 수 있도록 했다.

## 4. 테스트 및 검증

**테스트 파일**
- `backend/tests/test_mna_review_workflow.py`
- `backend/tests/test_mna_scenario.py`

**테스트 내용**
- 인수가격 변경 및 원본 데이터 보존
- 유효하지 않은 입력값 처리
- 영업이익률·고객 의존도 변경
- 복수 조건 동시 적용
- 전체 M&A 평가 및 권고 비교
- CEO·CFO·CTO 관점 처리
- 기존 M&A Tool 호환성
- What-if Tool 호출 및 결과 반환
- 변경 전후 인수 권고 조건 비교

**최종 테스트 결과**

```text
48 passed
```

기존 평가 테스트와 신규 What-if 테스트를 포함하여 총 48개 테스트가 정상 통과했다.

단, 본 테스트는 Python Workflow 및 Tool 호출 검증이며 실제 LLM의 Tool Calling과 UI 동작까지 검증한 것은 아니다.

## 5. 변경 및 추가 파일

| 파일 | 작업 내용 |
|---|---|
| `backend/app/library/workflows/mna_scenario.py` | 신규 What-if 분석 Workflow 구현 |
| `backend/app/library/tools/run_mna_review.py` | 시나리오 분석 Tool 연결 |
| `backend/app/library/skills/mna_feasibility_review.py` | What-if 분석 Skill 지침 확장 |
| `backend/tests/test_mna_scenario.py` | 시나리오 분석 자동 테스트 추가 |

기존 `mna_review.py`의 평가 규칙은 재사용하여 변경에 따른 영향 범위를 최소화했다.

## 6. 남은 작업

1. Ollama Cloud 모델별 Tool Calling 정확도 및 응답 품질 비교
2. 실제 경영자문 Agent 실행 테스트
3. 변경 조건이 자연어에서 정확하게 추출되는지 검증
4. Open WebUI 또는 팀 웹 UI에서 통합 테스트
5. 테스트 결과에 따른 오류 수정
6. 개인 프로젝트의 최종 개발 내용을 팀 작업 브랜치에 반영

## 7. 개발 결과 및 의의

이번 개발을 통해 기존의 고정 데이터 기반 M&A 평가 Workflow를 사용자 지정 조건 기반의 What-if 분석으로 확장했다.

사용자는 인수가격, 영업이익률, 고객 의존도 등의 주요 조건을 변경하여 기존 평가와 새로운 평가의 차이를 비교할 수 있다.

또한 기존 경영자문 Tool 및 Skill에 새로운 분석 기능을 연결하여 향후 자연어 기반 시나리오 분석에 활용할 수 있는 구조를 마련했다.

다만 현재 분석은 사전에 정의한 평가 규칙을 사용하며 실제 미래 실적을 예측하는 모델은 아니다. 실제 AI Agent 및 웹 UI 환경에서의 동작 검증은 후속 개발 과제로 남아 있다.

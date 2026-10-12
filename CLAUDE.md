# CLAUDE.md

Claude 기반 coding agent가 이 repository에서 작업할 때의 지침이다.

## 먼저 읽을 것

작업을 시작하기 전에 반드시 순서대로 읽는다.

1. [`docs/architecture.md`](docs/architecture.md) — 시스템 목적, Agent 구조, 현재 구현 vs 목표 구조, 열린 결정 사항
2. [`AGENTS.md`](AGENTS.md) — 모든 coding agent 공통 규칙 (범위, 경계, backend/frontend 책임, 테스트)

이 문서는 두 문서를 반복하지 않는다. 충돌하면 `docs/architecture.md` → `AGENTS.md` → 이 문서 순으로 우선한다.

## 작업 원칙

- **범위를 먼저 확정한다.** 요청이 어느 Step / 어느 파일에 해당하는지 파악하고, 그 범위 밖은 건드리지 않는다. 범위가 불분명하면 작업 전에 묻는다.
- **기존 구현을 읽고 변경 지점을 찾는다.** 새로 쓰기 전에 같은 역할을 하는 코드(`runtime/agent_core.py`, `runtime/events.py`, `src/live/agentEvents.ts`, `useScenarioRunner.ts`)를 재사용한다.
- **최소 변경을 선호한다.** 요청을 만족하는 가장 작은 diff를 만든다. 지나가는 김에 정리하지 않는다.
- **추상화 / framework를 새로 들이지 않는다.** 현재 stack은 LangGraph + FastAPI + React(Vite)다. dependency 추가는 요청이 있을 때만 한다.
- **architecture decision을 임의로 확정하지 않는다.** `docs/architecture.md` 12장의 열린 결정(다중 위임과 Step 경계, K Super 루프, Capability Registry, 결과 렌더링, 공유 구조, A2A 경계 등)이 작업에 걸리면 한 가지로 가정하고 진행하지 말고 선택지와 추천을 제시한 뒤 결정을 받는다.
- **요청받지 않은 기능을 같이 만들지 않는다.** "다음 Step에 필요할 것 같아서" 미리 넣지 않는다.
- **mock과 real을 구분한다.** 어느 Step이 real인지 `docs/architecture.md` 8장 표로 확인한다. mock 영역을 건드릴 때는 그것이 의도된 임시 상태임을 전제로 한다. real 영역에서는 frontend가 backend 이벤트 없이 상태를 만들지 않도록 한다.
- **Step을 real로 바꾸는 작업은 vertical slice로 한다.** backend Agent + Workflow Tool + 이벤트 + frontend `live` 교체 + 테스트를 한 Step 단위로 끝낸다.

## 변경 후

- 테스트와 빌드를 실행하고 **결과를 그대로 보고한다** (`AGENTS.md` "구현 후" 참고). skip된 테스트, 실행하지 못한 검증은 그렇게 적는다.
- 실행 경로를 바꿨으면 실제로 backend와 frontend를 띄워 확인한다. backend는 `backend/.venv`(Python 3.13)와 `backend/.env`의 `OLLAMA_API_KEY`가 필요하다.
- 구현 상태나 구조가 바뀌었으면 `docs/architecture.md`(구현 상태 표, 차이 표)와 `README.md`(Step 표)를 같은 커밋에서 갱신한다.
- 작업 중 발견한 architecture 문제는 코드로 고치지 말고 보고하거나 `docs/architecture.md` 12장에 추가한다.

## 보고 형식

- 무엇을 바꿨는지, 무엇을 검증했는지, 무엇을 범위에서 뺐는지를 분리해서 짧게 적는다.
- 결정이 필요한 사항은 선택지와 추천을 함께 적는다.

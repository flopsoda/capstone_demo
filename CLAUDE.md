# CLAUDE.md

Claude 기반 coding agent가 이 repository에서 작업할 때의 지침이다.

## 먼저 읽을 것

작업을 시작하기 전에 반드시 순서대로 읽는다.

1. [`docs/architecture.md`](docs/architecture.md) — 시스템 목적, Agent 구조, 현재 구현 vs 목표 구조, 열린 결정 사항
2. [`AGENTS.md`](AGENTS.md) — 모든 coding agent 공통 규칙 (범위, 경계, backend/frontend 책임, 테스트)

이 문서는 두 문서를 반복하지 않는다. 충돌하면 `docs/architecture.md` → `AGENTS.md` → 이 문서 순으로 우선한다.

## 작업 원칙

- **범위를 먼저 확정한다.** 요청이 어느 Step / 어느 파일에 해당하는지 파악하고, 그 범위 밖은 건드리지 않는다. 범위가 불분명하면 작업 전에 묻는다.
- **기존 구현을 읽고 변경 지점을 찾는다.** 새로 쓰기 전에 같은 역할을 하는 코드(`runtime/agent_core.py`, `runtime/events.py`, `library/`의 Skill·Tool factory, `src/live/agentEvents.ts`, `src/state/runState.ts`)를 재사용한다.
- **최소 변경을 선호한다.** 요청을 만족하는 가장 작은 diff를 만든다. 지나가는 김에 정리하지 않는다.
- **추상화 / framework를 새로 들이지 않는다.** 현재 stack은 LangGraph + FastAPI + React(Vite)다. dependency 추가는 요청이 있을 때만 한다.
- **architecture decision을 임의로 확정하지 않는다.** `docs/architecture.md` 12장의 열린 결정(다중 위임과 Step 경계, K Super 루프, Capability Registry, 결과 렌더링, 공유 구조, A2A 경계 등)이 작업에 걸리면 한 가지로 가정하고 진행하지 말고 선택지와 추천을 제시한 뒤 결정을 받는다.
- **요청받지 않은 기능을 같이 만들지 않는다.** "다음 Step에 필요할 것 같아서" 미리 넣지 않는다.
- **frontend가 backend 이벤트 없이 상태를 만들지 않도록 한다.** 남은 고정 문장은 예시 요청뿐이다. 데모 Step은 Demo Monitor의 표시 개념이며 backend · 제품 UI에 넣지 않는다.
- **system 경계를 지킨다.** `runtime/`은 system을 모르고, `library/`는 system을 import하지 않으며, system끼리는 A2A(HTTP)로만 통신한다. 같은 모양의 Capability가 필요하면 라이브러리 factory에 그 system의 데이터를 바인딩한다.
- **기능 추가는 vertical slice로 한다.** backend Agent + Capability + 이벤트 + frontend 반영 + 테스트를 한 기능 단위로 끝내고, 동작 변화 없는 정비는 따로 커밋한다.

## 최종 발표(2026-11-27) 기준 작업 범위

- 발표에서는 Agent를 실시간으로 실행하지 않는다. 업무 시나리오는 모션 코믹으로 연출하고, 기능은 실제 구현된 UI의 스크린샷과 최종 보고서로 설명한다.
- 따라서 실시간 실행을 위한 지연시간 최적화, 결과 재현성 확보, 동기화 타이밍 조정에 개발 시간을 쓰지 않는다. 이런 작업은 요청이 있을 때만 한다.
- 핵심 Agent 기능은 실제로 동작해야 한다. 발표에 쓸 데이터와 결과 화면(스크린샷 · 보고서)은 미리 실행해 준비해도 된다.
- 필요한 합성 데이터는 충분히 보강한다. 다만 데이터를 맞추려고 복잡한 운영 정책이나 임의의 규칙(특정 시나리오만 통과시키는 조건 등)을 코드에 추가하지 않는다.

## 변경 후

- 테스트와 빌드를 실행하고 **결과를 그대로 보고한다** (`AGENTS.md` "구현 후" 참고). skip된 테스트, 실행하지 못한 검증은 그렇게 적는다.
- 실행 경로를 바꿨으면 실제로 backend 둘(`uvicorn app.main:app --port 8000`, `uvicorn app.systems.group.app:app --port 8001`)과 frontend를 띄워 확인한다. backend는 `backend/.venv`(Python 3.13)와 `backend/.env`의 `OLLAMA_API_KEY`가 필요하다.
- 구현 상태나 구조가 바뀌었으면 `docs/architecture.md`(구현 상태 표, 차이 표, 11장 Phase 표)와 `README.md`를 같은 커밋에서 갱신한다.
- 작업 중 발견한 architecture 문제는 코드로 고치지 말고 보고하거나 `docs/architecture.md` 12장에 추가한다.

## 보고 형식

- 무엇을 바꿨는지, 무엇을 검증했는지, 무엇을 범위에서 뺐는지를 분리해서 짧게 적는다.
- 결정이 필요한 사항은 선택지와 추천을 함께 적는다.

# AGENTS.md

이 repository에서 작업하는 **모든 coding agent**가 따라야 할 공통 개발 지침이다.
architecture 자체는 여기서 다시 설명하지 않는다. 먼저 아래 문서를 읽는다.

1. [`docs/architecture.md`](docs/architecture.md) — 무엇을 만들고 있는지, 현재 구현 범위, 목표 구조, 열린 결정 사항
2. [`README.md`](README.md) — 실행 방법, 파일 구조, 이벤트 타입 표

## 한 줄 요약

대기업 경영진용 **Multi-Agent Decision Support System prototype**이다.
같은 플랫폼 runtime 위의 독립된 Agent System 둘(Group `:8001`, K Electronics `:8000`)이 A2A(HTTP)로만 통신한다:
`User → Group Super →(delegation) 그룹 Specialist | →(A2A) K Super →(delegation) Meeting / News / Advisory → Workflow Tool`.
제품 UI는 `/group` · `/k`(탭 4개 직접 채팅 + 협업 세션창), 테스트·발표용 Demo Monitor는 `/monitor`(그래프 · Step 1~8). NeuroEdge M&A demo의 Step 1~8은 전부 real이다. 상태 표는 `docs/architecture.md` 8장·11장.

## 작업 전

- 작업 범위를 먼저 확인한다. "사용자가 요청한 Step / 기능"이 범위다.
- 기존 코드를 먼저 읽는다. 특히 `backend/app/runtime/`, `backend/app/library/`, `backend/app/systems/<system>/`, `src/live/`, `src/state/runState.ts`, `src/platform/`, `src/monitor/`.
- `docs/architecture.md`의 "Current implementation"과 "Target architecture"를 구분해서 이해한다. 목표 구조를 이미 구현된 것처럼 가정하지 않는다.

## 범위 관리

- **요청받은 기능만 구현한다.** 다음 단계에 필요할 것 같은 기능을 임의로 선행 구현하지 않는다.
- 한 기능을 바꿀 때 나머지 system · UI는 그대로 둔다. 동작 변화 없는 정비와 기능 추가를 한 커밋에 섞지 않는다.
- 요청받지 않은 기능, 리팩터링, dependency 추가, 새 framework 도입을 같이 하지 않는다.
- architecture 변경이 필요하다고 판단되면 **조용히 바꾸지 말고 명확히 알린다.** `docs/architecture.md` 12장(열린 결정 사항)에 해당하는 문제라면 결정을 요청한다.

## 지켜야 할 경계

- **Agent / Workflow Tool / Delegation / A2A를 섞지 않는다.**
  - Super Agent는 LLM tool calling으로 전문 Agent에게 자연어로 위임한다. 중앙 Router / Planner 노드를 추가하지 않는다.
  - Specialist Agent는 독립 LangGraph Agent다. 함수 호출로 대체하지 않는다. Super Agent 경로와 사용자 직접 대화 경로가 같은 core를 쓴다.
    새 Specialist는 `runtime/agent_core.py`의 `Specialist` / `build_specialist_graph` / `run_specialist`를, 새 Super Agent는 `SuperAgent` / `build_super_graph` / `run_super`를 쓰고, Agent 파일에는 `AGENT_ID` · `IDENTITY = load_soul(__file__)` · Registry 조회만 두고, 정체성 문장은 `systems/<system>/souls/<모듈 이름>.md`에 쓴다.
  - Specialist에게 Tool 호출을 모든 응답에 강제하지 않는다. Workflow Tool 근거 검사는 delegation 경로(`invocation="delegation"`)에만 있고, 직접 대화(`"direct"`)에서는 모델이 Skill 지침에 따라 결정한다.
  - Workflow Tool은 Agent가 아니다. 내부의 결정적 절차를 Agent-visible Tool 여러 개로 쪼개지 않는다.
  - K Super → Specialist는 **delegation**(`delegation_start/end`), Group Super ⇅ K Super는 **A2A**다. Specialist 호출을 A2A라고 부르지 않는다.
  - Specialist가 다른 Specialist에게 위임하는 것은 경영자문 → 미팅 · 뉴스의 **조회 전용** 위임(`ask_*_agent`, `make_delegate_tool(read_only=True)`)뿐이다. 미팅 · 뉴스 Agent에는 위임 Tool을 주지 않는다 (순환 금지, `tests/test_capabilities.py`가 위임 그래프를 검사).
- **Agent state는 message history 중심이다.** `MessagesState`를 쓰고, `meeting_result` 같은 Agent별 결과 field를 추가하지 않는다. 공유 구조가 꼭 필요하면 범용 구조를 제안하고 결정을 받는다.
- **Agent 경계를 유지한다.** 하위 Agent의 내부 메시지 / tool call을 상위 Agent history에 병합하지 않는다. 최종 결과만 전달한다. A2A도 같다: 상대 system의 lifecycle은 관찰용으로 relay(`relayed: true`)만 하고 Agent에게는 최종 응답 텍스트만 준다.
- **Workflow Tool이 데이터 경계다.** Agent가 fixture / DB를 직접 읽지 않는다. 사용자별 접근(등급 · 작성자)은 Tool이 run의 principal로 정하며, 모델 인자로 받지 않는다.
- **Agent system 경계를 지킨다.** `runtime/`은 `library/`·`systems/`를, `library/`는 `systems/`를, 한 system은 다른 system을 import하지 않는다 (`tests/test_boundaries.py`). system 간 업무는 A2A(HTTP)로만 오간다. 같은 모양의 Workflow / Data가 필요하면 라이브러리 factory에 자기 데이터를 바인딩한다.

## Backend ↔ Frontend

- **Backend 실행이 source of truth다.** Agent 코드는 실제 lifecycle 시점에 `emit()`한다. 요청자는 NDJSON 스트림으로, 관찰자(Demo Monitor 등)는 `GET /api/events`(SSE)로 같은 이벤트를 받는다.
- Frontend는 받은 이벤트만 상태로 바꾼다. Tool 이름, 위임 · A2A 문장, 결과, 응답을 frontend에서 추측하거나 꾸며내지 않는다. Step 번호는 Demo Monitor가 이벤트에서 해석할 뿐 backend · 제품 UI에는 없다.
- 실패 시 mock으로 대체하지 않는다. backend가 지목한 Agent를 error로 표시하고 실행을 멈춘다.
- 제품 UI(`src/platform/`)와 Demo Monitor(`src/monitor/`)의 책임을 섞지 않는다. 그래프 · Step은 Monitor에만 둔다.
- 새 Agent / Tool을 추가할 때 **기존 이벤트 종류와 변환 경로**(`runtime/events.py` → `src/live/api.ts` → `src/live/agentEvents.ts` → `src/state/runState.ts`)를 재사용한다. 새 이벤트 타입이나 별도 채널을 만들기 전에 기존 것으로 표현할 수 없는지 확인한다.
- backend `AGENT_ID`와 frontend `AgentId` 문자열은 같아야 한다. 두 system이 같은 Specialist id를 쓰므로 화면용 id는 `resolveAgent(system, agent)`로 정한다 (Group의 Specialist는 `group_*`).

## Capability 추가 시

- Agent-visible Capability(Skill = 업무 지침, Tool = `@tool`)는 system 중립이면 `backend/app/library/skills/`, `library/tools/`(factory `make_<tool>(path)`, 회의 저장소를 쓰는 Tool은 `make_<tool>(store)`)에, 한 system 전용이면 `systems/<system>/tools/`에 정의하고, 그 system의 `registry.py` `CAPABILITIES`에 `allowed_agents`와 함께 등록한다. **권한은 그곳에만** 적는다.
- Agent 파일은 `skills_for(AGENT_ID)` / `tools_for(AGENT_ID)`로 자기 subset만 받는다. Agent 파일에서 Skill / Tool 모듈을 직접 import하거나 `TOOLS`를 하드코딩하지 않는다.
- Tool의 이름 · description · argument schema는 `@tool` 정의가 source of truth다. Registry나 다른 곳에 중복 정의하지 않는다.
- Tool 내부의 workflow 단계와 helper는 `backend/app/library/workflows/`에 두고 Registry에 등록하지 않는다. workflow는 데이터 store 경로를 `path`로 받는 순수 함수다. Agent가 직접 쓰는 것만 Capability다.
- Skill은 실제 업무 지침이 있을 때만 만든다. Registry를 채우기 위한 빈 Skill이나 wrapper는 만들지 않는다. Agent의 정체성 · 출력 형식 규칙은 Agent의 SOUL 파일(`souls/<모듈 이름>.md`)에 둔다.
- 위임 Tool(`delegate_to_*`)은 대상 Agent의 `run_*`를 호출 시점에 import한다 (순환 import 방지). 위임 이벤트(`delegation_start/end`)는 Tool 안에서 emit한다.

## 보안

- API key, secret을 코드나 Git에 넣지 않는다. `backend/.env`는 gitignore 대상이며 `.env.example`만 커밋한다.
- fixture 데이터는 가상이다. 실제 기업 데이터를 넣지 않는다.

## 구현 후

- 관련 테스트를 실행한다.
  - backend: `cd backend && .venv/bin/python -m pytest` (회의 · 뉴스 저장소 테스트는 `MEETINGS_TEST_DATABASE_URL` — 이름에 test가 든 버릴 Postgres DB — 가 있어야 실행되고 없으면 skip. 테스트는 localhost 밖으로 나갈 수 없고 실제 언론사 피드도 읽지 않으며(`tests/conftest.py`; 뉴스 시나리오 테스트는 `news_db`가 지운 schema에 `data/news.json`이 다시 등록된 저장소를 쓴다), 실제 모델 · 웹을 부르는 테스트는 `network` 마커를 단다. 테스트 실행이 system의 `data/` 폴더에 파일을 남기면 세션이 실패한다. `tests/test_agents.py`는 LLM key — `OLLAMA_API_KEY`, `LLM_PROVIDER=openai`면 `OPENAI_API_KEY` — 가 있어야 실행된다). import 경계는 `tests/test_boundaries.py`가 지킨다.
  - frontend: `npm run build` (tsc 포함), `npm run lint`
- 실제 실행 경로를 바꿨다면 backend 둘(`:8000`, `:8001`) + frontend를 띄워 `/group`에서 예시 요청을 한 번 돌리고 `/monitor`가 8/8까지 가는지 본다.
- 문서는 **적힌 사실이 틀려졌을 때** 코드와 같은 커밋에서 갱신한다. "의미있는 변화"가 아니라 "문서가 거짓이 되었는가"가 기준이다.
  - `docs/architecture.md`: 구현 상태가 바뀜 (8장·10장·11장 표) / Agent·Workflow Tool·endpoint·이벤트 타입 추가 (3장·6장) / 12장의 열린 결정이 내려짐 (항목 제거, 본문 반영) / 새 구조적 문제·미결정 사항 발견 (12장 추가).
  - `README.md`: 구현 상태, 실행 방법, 파일 구조가 바뀜.
  - `AGENTS.md`: 규칙이 깨졌거나 현실과 맞지 않음 / 테스트·실행 명령이 바뀜. 그 외에는 손대지 않는다.
  - `CLAUDE.md`: 작업 방식 자체가 바뀔 때만. 코드 변화로는 갱신하지 않는다.
- 결과를 사실대로 보고한다. 실패한 테스트, 건너뛴 검증, 범위에서 뺀 부분을 명시한다.

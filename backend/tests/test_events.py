import threading

from app.runtime.api import sse_line
from app.runtime.events import AgentError, EventHub, collect_events, emit, new_run, stream_run

RUN = new_run(system="k_electronics", root_agent="k_super", origin="a2a")


def test_collect_events_captures_emitted_events_in_order():
    def work():
        emit({"type": "agent_start", "agent": "x", "request": "r"})
        emit({"type": "agent_end", "agent": "x", "response": "ok"})
        return "ok"

    result, events = collect_events(work)
    assert result == "ok"
    assert [e["type"] for e in events] == ["agent_start", "agent_end"]


def test_emit_outside_a_run_is_a_noop():
    emit({"type": "agent_start", "agent": "x", "request": "r"})  # must not raise


def test_stream_run_ends_with_done():
    def work():
        emit({"type": "agent_start", "agent": "x", "request": "r"})

    assert [e["type"] for e in stream_run(work, run=RUN)] == ["agent_start", "done"]


def test_stream_run_reports_agent_error_with_agent():
    def work():
        emit({"type": "agent_start", "agent": "meeting", "request": "r"})
        raise AgentError("meeting", "boom")

    events = list(stream_run(work, run=RUN))
    assert events[-1]["type"] == "error" and events[-1]["agent"] == "meeting" and events[-1]["message"] == "boom"


def test_stream_run_stamps_every_event_with_the_run_identity():
    def work():
        emit({"type": "agent_start", "agent": "meeting", "request": "r"})
        emit({"type": "tool_start", "agent": "meeting", "tool": "t", "args": {}, "system": "other", "run_id": "theirs"})

    run = new_run(system="k_electronics", root_agent="meeting", origin="direct", trace_id="tr-1")
    events = list(stream_run(work, run=run))
    assert [e["type"] for e in events] == ["agent_start", "tool_start", "done"]
    first, relayed, done = events
    assert (first["run_id"], first["system"], first["origin"], first["trace_id"]) == (run.run_id, "k_electronics", "direct", "tr-1")
    # An event that already carries another system's stamp (relayed) keeps it.
    assert (relayed["run_id"], relayed["system"]) == ("theirs", "other")
    assert done["run_id"] == run.run_id


def test_stream_run_attributes_unlabelled_failures_to_the_root_agent():
    def work():
        raise RuntimeError("network down")

    events = list(stream_run(work, run=new_run(system="k_electronics", root_agent="news")))
    assert events[-1]["type"] == "error" and events[-1]["agent"] == "news" and "network down" in events[-1]["message"]


def test_stream_run_publishes_every_event_to_the_hub_for_observers():
    hub = EventHub()
    seen: list[dict] = []
    ready = threading.Event()

    def observe():
        for event in hub.subscribe(timeout=0.05):
            if event is None:
                if ready.is_set():
                    break
                continue
            seen.append(event)
            if event["type"] in ("done", "error"):
                ready.set()

    t = threading.Thread(target=observe, daemon=True)
    t.start()
    while hub.subscriber_count == 0:
        pass

    def work():
        emit({"type": "agent_start", "agent": "news", "request": "r"})
        emit({"type": "agent_end", "agent": "news", "response": "ok"})

    run = new_run(system="k_electronics", root_agent="news", origin="a2a", caller="group")
    requester = list(stream_run(work, run=run, hub=hub))
    t.join(timeout=2)
    assert [e["type"] for e in requester] == ["agent_start", "agent_end", "done"]
    # The observer saw the same events, in order, with the run stamp (incl. origin / caller).
    assert seen == requester
    assert all(e["run_id"] == run.run_id and e["origin"] == "a2a" and e["caller"] == "group" for e in seen)
    assert hub.subscriber_count == 0  # unsubscribed when the observer stopped


def test_hub_subscriber_yields_none_on_timeout_for_keepalives():
    hub = EventHub()
    it = hub.subscribe(timeout=0.01)
    assert next(it) is None
    hub.publish({"type": "done"})
    assert next(it) == {"type": "done"}
    it.close()
    assert hub.subscriber_count == 0


def test_sse_line_is_one_data_message():
    assert sse_line({"type": "agent_start", "agent": "뉴스"}) == 'data: {"type": "agent_start", "agent": "뉴스"}\n\n'


def test_llm_reads_the_backend_dotenv():
    from pathlib import Path

    from app.runtime import llm

    assert llm.ENV_PATH == Path(__file__).resolve().parent.parent / ".env"


def test_observer_stream_ends_when_the_app_shuts_down():
    import threading

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.runtime.api import sse_events, shutdown_signal
    from app.runtime.events import EventHub

    hub = EventHub()
    app = FastAPI()
    stop = shutdown_signal(app)

    @app.get("/events")
    def events():
        return sse_events(hub, stop=stop)

    received: list[str] = []
    with TestClient(app) as client:  # runs the lifespan: shutdown sets `stop`
        def read():
            with client.stream("GET", "/events") as res:
                for line in res.iter_lines():
                    received.append(line)
        t = threading.Thread(target=read, daemon=True)
        t.start()
        while hub.subscriber_count == 0:
            pass
        hub.publish({"type": "done"})
        stop.set()  # what the lifespan does on shutdown
        t.join(timeout=10)
    assert not t.is_alive(), "stream did not end after shutdown"
    assert any(line.startswith("data: ") and '"done"' in line for line in received)
    assert hub.subscriber_count == 0

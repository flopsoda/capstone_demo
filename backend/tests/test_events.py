from app.runtime.events import AgentError, collect_events, emit, new_run, stream_run

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

import pytest
from playwright.sync_api import expect

from tests.test_dashboard import upload_json
from tests.test_visualization import dashboard_url


DASH = "\u2014"


def tab(page, name):
    page.get_by_role("button", name=name, exact=True).click()


def load(page, url, processes, memory=None, algorithm="FCFS"):
    page.goto(url)
    tab(page, "Process Manager")
    upload_json(page, {"algorithm": algorithm,
                       "configuration": {"time_quantum": 1, "context_switch_cost": 0},
                       "memory": memory or {"enabled": True, "frame_count": 2, "page_size": 4},
                       "processes": processes})
    expect(page.locator("#upload-status")).to_contain_text("Loaded")


def step(page):
    tab(page, "Process Manager")
    with page.expect_response("**/api/step") as response:
        page.locator("#step-button").click()
    page.wait_for_function("busy === false")
    tab(page, "Memory Manager")
    return response.value.json()


def run(page):
    tab(page, "Process Manager")
    page.locator("#run-button").click()
    expect(page.locator("#simulation-status")).to_contain_text("Completed", timeout=15000)
    page.wait_for_function("busy === false")
    tab(page, "Memory Manager")


def rows(page, body):
    return page.locator("#" + body + " tr").evaluate_all(
        "rows => rows.map(row => Array.from(row.cells, cell => cell.textContent))"
    )


@pytest.mark.parametrize("policy", ["fifo", "lru"])
def test_exact_references_replacement_rates_and_release(page, dashboard_url, policy):
    load(page, dashboard_url, [{"pid": "P1", "arrival_time": 0, "burst_time": 6,
         "memory_references": {"0": 1, "1": 5, "2": 2, "3": 9, "4": 6}}],
         {"enabled": True, "frame_count": 2, "page_size": 4, "algorithm": policy}, algorithm="RR")
    snapshot = step(page)
    expect(page.locator("#memory-hit-rate")).to_have_text("0.0%")
    expect(page.locator("#memory-fault-rate")).to_have_text("0.0%")
    expect(page.locator("#memory-total-fault-latency")).to_have_text("0")
    expect(page.locator("#memory-history-body")).to_contain_text("No executed")
    expect(page.locator("#memory-mapping-body")).to_contain_text("No resident")
    # RR exposes separate dispatch and CPU-slice snapshots; four references
    # execute after eight Steps, rather than promising one reference per Step.
    for _ in range(7):
        snapshot = step(page)
    expected_mappings = [["P1", "2", "0"], ["P1", "1", "1"]] if policy == "fifo" else [["P1", "0", "0"], ["P1", "2", "1"]]
    assert rows(page, "memory-mapping-body") == expected_mappings
    assert [[f["pid"], str(f["page"]), str(f["frame"])] for f in snapshot["memory_frames"]] == expected_mappings
    expect(page.locator("#memory-history-body tr")).to_have_count(4)
    assert rows(page, "memory-history-body")[:3] == [
        ["0", "P1", "1", "0", "0", "1", "Fault", DASH, DASH, "0"],
        ["1", "P1", "5", "1", "1", "5", "Fault", DASH, DASH, "0"],
        ["2", "P1", "2", "0", "0", "2", "Hit", DASH, DASH, "0"],
    ]
    run(page)
    history = rows(page, "memory-history-body")
    if policy == "fifo":
        assert history[3:] == [
            ["3", "P1", "9", "2", "0", "1", "Fault", "P1", "0", "0"],
            ["4", "P1", "6", "1", "1", "6", "Hit", DASH, DASH, "0"],
        ]
        expected = ("3", "2", "1", "40.0%", "60.0%")
    else:
        assert history[3:] == [
            ["3", "P1", "9", "2", "1", "5", "Fault", "P1", "1", "0"],
            ["4", "P1", "6", "1", "0", "2", "Fault", "P1", "0", "0"],
        ]
        expected = ("4", "1", "2", "20.0%", "80.0%")
    for id, value in zip(["memory-faults", "memory-hits", "memory-evictions", "memory-hit-rate", "memory-fault-rate"], expected):
        expect(page.locator("#" + id)).to_have_text(value)
    expect(page.locator("#memory-references")).to_have_text("5")
    expect(page.locator("#memory-frame-grid .empty")).to_have_count(2)
    expect(page.locator("#memory-used-frames")).to_have_text("0")
    expect(page.locator("#memory-mapping-body")).to_contain_text("No resident")
    assert len(history) == 5  # accumulated snapshots must not append duplicate rows


def test_process_owned_same_page_and_same_timestamp_transitions(page, dashboard_url):
    load(page, dashboard_url, [
        {"pid": "P1", "arrival_time": 0, "burst_time": 2, "memory_references": {"0": 0}},
        {"pid": "P2", "arrival_time": 0, "burst_time": 4, "memory_references": {"0": 0}},
    ], algorithm="RR")
    for _ in range(4):
        snapshot = step(page)
    assert rows(page, "memory-mapping-body") == [["P1", "0", "0"], ["P2", "0", "1"]]
    assert rows(page, "memory-history-body") == [
        ["0", "P1", "0", "0", "0", "0", "Fault", DASH, DASH, "0"],
        ["1", "P2", "0", "0", "1", "4", "Fault", DASH, DASH, "0"],
    ]
    transitions = [e for e in snapshot["events"] if e["manager"] == "process" and e["previous_state"] != e["new_state"]]
    assert rows(page, "memory-transition-body") == [
        [str(e["timestamp"]), e["entity_id"], e["previous_state"], e["new_state"], e["event_type"], e["action"]]
        for e in transitions
    ]
    assert rows(page, "memory-transition-body")[:2] == [
        ["0", "P1", "NEW", "READY", "arrival", "admit"],
        ["0", "P2", "NEW", "READY", "arrival", "admit"],
    ]


    step(page)
    step(page)
    assert rows(page, "memory-mapping-body") == [["P2", "0", "1"]]
    expect(page.locator("#memory-frame-grid .empty")).to_have_count(1)
    expect(page.locator("#memory-frame-grid .occupied")).to_have_count(1)
    expect(page.locator("#memory-history-body tr")).to_have_count(2)
    run(page)
    expect(page.locator("#memory-frame-grid .empty")).to_have_count(2)
    expect(page.locator("#memory-history-body tr")).to_have_count(2)


def test_fault_delay_transitions_reset_and_input_invalidation(page, dashboard_url):
    load(page, dashboard_url, [{"pid": "P1", "arrival_time": 0, "burst_time": 3,
         "memory_references": {"0": 1, "1": 2}}],
         {"enabled": True, "frame_count": 2, "page_size": 4, "fault_latency": 2})
    step(page)
    step(page)
    expect(page.locator("#memory-transition-body")).to_contain_text("WAITING")
    assert ["0", "P1", "RUNNING", "WAITING", "block", "wait"] in rows(page, "memory-transition-body")
    expect(page.locator("#memory-frame-grid .occupied")).to_have_count(1)
    run(page)
    assert rows(page, "memory-history-body") == [
        ["0", "P1", "1", "0", "0", "1", "Fault", DASH, DASH, "2"],
        ["3", "P1", "2", "0", "0", "2", "Hit", DASH, DASH, "0"],
    ]
    assert ["2", "P1", "WAITING", "READY", "unblock", "unblock"] in rows(page, "memory-transition-body")
    expect(page.locator("#memory-hit-rate")).to_have_text("50.0%")
    expect(page.locator("#memory-total-fault-latency")).to_have_text("2")
    expect(page.locator("#memory-frame-grid .empty")).to_have_count(2)
    tab(page, "Process Manager")
    page.locator("#reset-button").click()
    page.wait_for_function("busy === false")
    tab(page, "Memory Manager")
    for id in ["memory-references", "memory-hits", "memory-faults", "memory-evictions", "memory-total-fault-latency", "memory-used-frames"]:
        expect(page.locator("#" + id)).to_have_text("0")
    expect(page.locator("#memory-hit-rate")).to_have_text("0.0%")
    expect(page.locator("#memory-history-body")).to_contain_text("No executed")
    expect(page.locator("#memory-transition-body")).to_contain_text("No process state transitions")
    expect(page.locator("#memory-mapping-body")).to_contain_text("No resident")
    run(page)
    page.locator("#memory-frame-count").fill("3")
    expect(page.locator("#memory-history-body")).to_contain_text("Start a simulation")
    expect(page.locator("#memory-transition-body")).to_contain_text("Start a simulation")
    expect(page.locator("#memory-mapping-body")).to_contain_text("Start a simulation")
    expect(page.locator("#memory-total-fault-latency")).to_have_text(DASH)
    tab(page, "Process Manager")
    expect(page.locator("#export-results-button")).to_be_disabled()


def test_disabled_memory_and_safe_pid_text(page, dashboard_url):
    pid = '<img src=x onerror="window.unsafeMemory=true">'
    load(page, dashboard_url, [{"pid": pid, "arrival_time": 0, "burst_time": 2,
         "memory_references": {"0": 0}}], algorithm="RR")
    step(page)
    step(page)
    assert rows(page, "memory-mapping-body")[0] == [pid, "0", "0"]
    assert rows(page, "memory-history-body")[0][1] == pid
    assert page.locator("#memory img").count() == 0
    assert page.evaluate("window.unsafeMemory === undefined")
    load(page, dashboard_url, [{"pid": "P1", "arrival_time": 0, "burst_time": 2}], {"enabled": False})
    step(page)
    expect(page.locator("#memory-history-body")).to_contain_text("disabled")
    expect(page.locator("#memory-mapping-body")).to_contain_text("disabled")
    expect(page.locator("#memory-frame-grid")).to_contain_text("not enabled")
    expect(page.locator("#memory-hit-rate")).to_have_text(DASH)
    run(page)
    expect(page.locator("#memory-transition-body")).to_contain_text("TERMINATED")


def test_same_timestamp_reference_order_and_cumulative_latency(page, dashboard_url):
    load(page, dashboard_url, [
        {"pid": "P1", "arrival_time": 0, "burst_time": 2, "memory_references": {"0": 0}},
        {"pid": "P2", "arrival_time": 0, "burst_time": 2, "memory_references": {"0": 0}},
    ], {"enabled": True, "frame_count": 2, "page_size": 4, "fault_latency": 2})
    run(page)
    assert rows(page, "memory-history-body") == [
        ["0", "P1", "0", "0", "0", "0", "Fault", DASH, DASH, "2"],
        ["0", "P2", "0", "0", "1", "4", "Fault", DASH, DASH, "2"],
    ]
    expect(page.locator("#memory-total-fault-latency")).to_have_text("4")
    expect(page.locator("#memory-hit-rate")).to_have_text("0.0%")
    expect(page.locator("#memory-fault-rate")).to_have_text("100.0%")



def test_generic_block_is_not_presented_as_a_page_fault(page, dashboard_url, monkeypatch):
    import app as application
    from src.core.process import Process

    # /api/start does not forward blocking_requests. Inject the supported model
    # input through a test-only factory; keep the real API, engine and UI path.
    def process_with_generic_block(*args, **kwargs):
        return Process(*args, blocking_requests={0: 2}, **kwargs)

    monkeypatch.setattr(application, "Process", process_with_generic_block)
    load(page, dashboard_url, [{"pid": "P1", "arrival_time": 0, "burst_time": 3}])
    responses = []
    page.on("response", lambda response: responses.append(response)
            if response.url.endswith("/api/step") else None)
    run(page)
    snapshot = responses[-1].json()
    assert not any(event["manager"] == "memory" for event in snapshot["events"])
    assert ["0", "P1", "RUNNING", "WAITING", "block", "wait"] in rows(page, "memory-transition-body")
    assert ["2", "P1", "WAITING", "READY", "unblock", "unblock"] in rows(page, "memory-transition-body")
    expect(page.locator("#memory-transition-body")).not_to_contain_text("page_fault")
    expect(page.locator("#memory-transition-body")).not_to_contain_text("Page fault")
    expect(page.locator("#memory-faults")).to_have_text("0")
    expect(page.locator("#memory-history-body")).to_contain_text("No executed memory references")


def test_transition_columns_preserve_actions_without_inferred_causes(page, dashboard_url):
    page.goto(dashboard_url)
    tab(page, "Memory Manager")
    event = {"timestamp": 0, "manager": "process", "entity_id": "P1",
             "previous_state": "RUNNING", "new_state": "WAITING",
             "event_type": "block", "action": "wait",
             "cause": "Generic wait", "message": "Diagnostic text"}
    page.evaluate("events => renderProcessTransitions(events)", [event])
    assert rows(page, "memory-transition-body") == [
        ["0", "P1", "RUNNING", "WAITING", "block", "wait"]
    ]
    table = page.get_by_role("table", name="Process State Transitions", exact=True)
    expect(table.locator("th")).to_have_text([
        "Timestamp (simulation units)", "PID", "Previous state", "New state", "Event type", "Recorded action"
    ])
    expect(table).not_to_contain_text("Diagnostic text")
    expect(table).not_to_contain_text("Generic wait")

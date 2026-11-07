import json
from threading import Thread

import pytest
from playwright.sync_api import expect
from werkzeug.serving import make_server

from app import app
from tests.test_dashboard import enter_process, upload_json


@pytest.fixture(scope="module")
def memory_url():
    server = make_server("127.0.0.1", 0, app)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    thread.join()
    server.server_close()


def payload():
    return {"algorithm": "FCFS", "configuration": {},
            "memory": {"enabled": True, "frame_count": 2, "page_size": 8,
                       "algorithm": "lru", "fault_latency": 0.5},
            "processes": [{"pid": "P1", "arrival_time": 0, "burst_time": 4,
                           "memory_references": {"0": 8, "2": 9},
                           "file_requests": {"1": "mkdir /example"}, "note": "keep"}]}


def open_memory(page, url):
    page.goto(url)
    page.get_by_role("button", name="Process Manager", exact=True).click()
    enter_process(page)
    page.get_by_role("button", name="Memory Manager", exact=True).click()


def add_reference(page, offset="0", address="8"):
    page.locator("#reference-offset").fill(offset)
    page.locator("#reference-address").fill(address)
    page.locator("#reference-save").click()


def test_reference_lifecycle_and_disabled_conflict(page, memory_url):
    open_memory(page, memory_url)
    add_reference(page)
    expect(page.locator("#reference-table-body tr")).to_have_count(1)
    add_reference(page, address="12")
    expect(page.locator("#reference-error")).to_contain_text("already exists")
    page.get_by_role("button", name="Edit reference P1 offset 0", exact=True).click()
    page.locator("#reference-address").fill("12")
    page.locator("#reference-save").click()
    expect(page.locator("#reference-table-body tr td").nth(2)).to_have_text("12")
    page.locator("#memory-enabled").uncheck()
    expect(page.locator("#memory-page-size")).to_be_disabled()
    expect(page.locator("#memory-error")).to_contain_text("references exist")
    requests = []
    page.on("request", lambda request: requests.append(request.url))
    page.evaluate("stepSimulation()")
    assert not any("/api/start" in url for url in requests)
    page.get_by_role("button", name="Remove reference P1 offset 0", exact=True).click()
    expect(page.locator("#memory-error")).to_be_empty()
    page.evaluate("stepSimulation()")
    page.wait_for_function("busy === false")
    assert page.evaluate("JSON.parse(savedInput).memory.enabled") is False
    expect(page.locator("#memory-frame-grid")).to_contain_text("not enabled")


@pytest.mark.parametrize("offset,address", [("-1", "0"), ("4", "0"), ("1.5", "0"), ("0", "-1"), ("0", "1.5"), ("", "0")])
def test_invalid_editor(page, memory_url, offset, address):
    open_memory(page, memory_url)
    add_reference(page, offset, address)
    expect(page.locator("#reference-error")).not_to_be_empty()
    expect(page.locator("#reference-table-body")).to_contain_text("No memory references")


def test_import_real_run_and_change_detection(page, memory_url):
    page.goto(memory_url)
    page.get_by_role("button", name="Process Manager", exact=True).click()
    upload_json(page, payload())
    expect(page.locator("#upload-status")).to_contain_text("Loaded 1")
    page.get_by_role("button", name="Memory Manager", exact=True).click()
    expect(page.locator("#memory-policy")).to_have_value("lru")
    expect(page.locator("#memory-frame-count")).to_have_value("2")
    with page.expect_request("**/api/start") as sent:
        page.evaluate("stepSimulation()")
    assert sent.value.post_data_json["memory"] == payload()["memory"]
    assert sent.value.post_data_json["processes"][0]["note"] == "keep"
    page.wait_for_function("busy === false")
    # Step until a resident frame appears, before process termination releases it.
    for _ in range(3):
        if page.locator(".memory-frame.occupied").count():
            break
        page.evaluate("stepSimulation()")
        page.wait_for_function("busy === false")
    expect(page.locator(".memory-frame.occupied")).to_have_count(1)
    expect(page.locator("#memory-faults")).to_have_text("1")
    page.evaluate("runSimulation()")
    page.wait_for_function("busy === false")
    result = json.loads(page.evaluate("resultsJson"))
    assert result["memory_metrics"]["references"] == 2
    assert any(event["manager"] == "memory" for event in result["events"])
    assert any(event["manager"] == "file" for event in result["events"])
    assert result["processes"][0]["state"] == "TERMINATED"
    page.locator("#memory-frame-count").fill("4")
    with page.expect_request("**/api/start") as changed:
        page.evaluate("stepSimulation()")
    assert changed.value.post_data_json["memory"]["frame_count"] == 4
    page.wait_for_function("busy === false")


INVALID = [
    ("memory", {"algorithm": "optimal"}), ("memory", {"enabled": 1}),
    ("memory", {"frame_count": 0}), ("memory", {"page_size": 1.5}),
    ("memory", {"fault_latency": -1}), ("memory", None),
    ("references", []), ("references", {"1.5": 0}),
    ("references", {"01": 0, "1": 4}), ("references", {"4": 0}),
    ("references", {"0": -1}), ("references", {"0": True}),
    ("references", {"0": 0.5}), ("memory", {"enabled": False}),
    ("association", {"Missing": {"0": 1}}),
]


def invalid_payload(kind, value):
    data = payload()
    if kind == "references":
        data["processes"][0]["memory_references"] = value
    elif kind == "association":
        data["memory_references"] = value
    else:
        data["memory"] = value
    return data


@pytest.mark.parametrize("kind,value", INVALID)
def test_invalid_api(kind, value):
    with app.test_client() as client:
        assert client.post("/api/start", json=invalid_payload(kind, value)).status_code == 400


@pytest.mark.parametrize("kind,value", INVALID)
def test_rejected_import_is_atomic(page, memory_url, kind, value):
    page.goto(memory_url)
    page.get_by_role("button", name="Process Manager", exact=True).click()
    upload_json(page, payload())
    expect(page.locator("#upload-status")).to_contain_text("Loaded")
    before = page.evaluate("JSON.stringify(collectSimulationInput())")
    upload_json(page, invalid_payload(kind, value))
    expect(page.locator("#process-error")).to_contain_text("Upload failed")
    assert page.evaluate("JSON.stringify(collectSimulationInput())") == before


def test_duplicate_keys_rejected(page, memory_url):
    open_memory(page, memory_url)
    text = json.dumps(payload()).replace('"0": 8', '"0": 8, "0": 12')
    page.locator("#process-file").set_input_files({"name": "duplicate.json", "mimeType": "application/json", "buffer": text.encode()})
    expect(page.locator("#process-error")).to_contain_text("Duplicate JSON key")
    with app.test_client() as client:
        assert client.post("/api/start", data=text, content_type="application/json").status_code == 400


def test_old_import_defaults_and_process_removal(page, memory_url):
    open_memory(page, memory_url)
    add_reference(page)
    page.get_by_role("button", name="Process Manager", exact=True).click()
    enter_process(page, pid="P2")
    assert page.evaluate("processes[0].memory_references") == {"0": 8}
    page.get_by_role("button", name="Remove P1", exact=True).click()
    assert page.evaluate("processes.every(p => !Object.keys(p.memory_references ?? {}).length)")
    old = {"algorithm": "FCFS", "processes": [{"pid": "Old", "arrival_time": 0, "burst_time": 2}]}
    upload_json(page, old)
    expect(page.locator("#upload-status")).to_contain_text("Loaded")
    assert page.evaluate("collectSimulationInput().memory") == {"enabled": True, "frame_count": 3, "page_size": 4, "algorithm": "fifo", "fault_latency": 0}
    assert page.evaluate("processes[0].pid") == "Old"


@pytest.mark.parametrize("field,value", [("frame-count", "0"), ("page-size", "1.5"), ("fault-latency", "-1"), ("frame-count", "")])
def test_invalid_controls_block_requests(page, memory_url, field, value):
    open_memory(page, memory_url)
    page.locator("#memory-" + field).fill(value)
    expect(page.locator("#memory-" + field + "-error")).not_to_be_empty()
    requests = []
    page.on("request", lambda request: requests.append(request.url))
    page.evaluate("stepSimulation()")
    assert not any("/api/start" in url for url in requests)


def test_offset_edit_conflict_cancel_and_import_replacement(page, memory_url):
    open_memory(page, memory_url)
    add_reference(page, "0", "8")
    add_reference(page, "1", "12")
    page.get_by_role("button", name="Edit reference P1 offset 0", exact=True).click()
    page.locator("#reference-offset").fill("1")
    page.locator("#reference-save").click()
    expect(page.locator("#reference-error")).to_contain_text("already exists")
    assert page.evaluate("processes[0].memory_references") == {"0": 8, "1": 12}
    page.locator("#reference-offset").fill("2")
    page.locator("#reference-save").click()
    assert page.evaluate("processes[0].memory_references") == {"1": 12, "2": 8}
    page.get_by_role("button", name="Edit reference P1 offset 2", exact=True).click()
    page.locator("#reference-cancel").click()
    assert page.evaluate("processes[0].memory_references") == {"1": 12, "2": 8}
    page.get_by_role("button", name="Edit reference P1 offset 2", exact=True).click()
    # Replacement with the same PID must discard the old object's edit state.
    page.locator("#process-file").set_input_files({"name": "new.json", "mimeType": "application/json", "buffer": json.dumps(payload()).encode()})
    expect(page.locator("#upload-status")).to_contain_text("Loaded")
    expect(page.locator("#reference-pid")).to_be_enabled()
    expect(page.locator("#reference-save")).to_have_text("Add Reference")
    assert page.evaluate("processes[0].memory_references") == {"0": 8, "2": 9}


def test_api_defaults_and_rejected_request_preserves_simulation():
    import app as application
    with app.test_client() as client:
        data = payload()
        del data["memory"]
        response = client.post("/api/start", json=data)
        assert response.status_code == 200
        memory = application.engine.memory_manager
        assert (memory.frame_count, memory.page_size, memory.algorithm, memory.fault_latency) == (3, 4, "fifo", 0)
        original = application.engine
        assert client.post("/api/start", json=invalid_payload("references", {"0.5": 0})).status_code == 400
        assert application.engine is original

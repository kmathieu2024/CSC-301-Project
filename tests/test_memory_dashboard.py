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
    expect(page.locator("#reference-offset-error")).to_contain_text("already exists")
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
    error_id = "reference-offset-error" if offset in ("-1", "4", "1.5", "") else "reference-address-error"
    expect(page.locator("#" + error_id)).not_to_be_empty()
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
    expect(page.locator("#reference-offset-error")).to_contain_text("already exists")
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


@pytest.mark.parametrize("settings", [
    {}, {"frame_count": 0}, {"page_size": 1.5},
    {"algorithm": "optimal"}, {"fault_latency": -1},
])
def test_disabled_memory_validates_without_constructing_manager(monkeypatch, settings):
    import app as application

    def unexpected_construction(*args, **kwargs):
        raise AssertionError("Disabled memory must not construct a MemoryManager")

    monkeypatch.setattr(application.MemoryManager, "__init__", unexpected_construction)
    data = payload()
    data["processes"][0]["memory_references"] = {}
    data["memory"] = {"enabled": False, **settings}
    with app.test_client() as client:
        response = client.post("/api/start", json=data)
    if settings:
        assert response.status_code == 400
    else:
        assert response.status_code == 200
        assert application.engine.memory_manager is None
        assert response.get_json()["memory_frames"] == []


@pytest.mark.parametrize("first_corrected", ["offset", "address"])
def test_reference_field_errors_are_independent(page, memory_url, first_corrected):
    open_memory(page, memory_url)
    add_reference(page, "0", "8")
    before = page.evaluate("JSON.stringify(processes[0].memory_references)")
    add_reference(page, "4", "-1")
    errors = {field: page.locator(f"#reference-{field}-error") for field in ("offset", "address")}
    expect(errors["offset"]).to_contain_text("less than burst time (4)")
    expect(errors["address"]).to_contain_text("whole number of 0 or greater")
    for field, error in errors.items():
        control = page.locator(f"#reference-{field}")
        expect(error).to_be_visible()
        expect(error).to_have_css("color", "rgb(180, 35, 24)")
        expect(error).to_have_css("font-size", "14px")
        expect(control).to_have_attribute("aria-invalid", "true")
        expect(control).to_have_attribute("aria-describedby", f"reference-{field}-error")
        expect(control).to_have_css("border-top-color", "rgb(180, 35, 24)")
        assert error.bounding_box()["y"] >= control.bounding_box()["y"] + control.bounding_box()["height"]
    assert page.evaluate("JSON.stringify(processes[0].memory_references)") == before
    values = {"offset": "1", "address": "12"}
    other = "address" if first_corrected == "offset" else "offset"
    page.locator(f"#reference-{first_corrected}").fill(values[first_corrected])
    expect(errors[first_corrected]).to_be_empty()
    expect(page.locator(f"#reference-{first_corrected}")).not_to_have_attribute("aria-invalid", "true")
    expect(errors[other]).not_to_be_empty()
    expect(page.locator(f"#reference-{other}")).to_have_attribute("aria-invalid", "true")
    assert page.evaluate("JSON.stringify(processes[0].memory_references)") == before
    page.locator(f"#reference-{other}").fill(values[other])
    expect(errors[other]).to_be_empty()
    page.locator("#reference-save").click()
    assert page.evaluate("processes[0].memory_references") == {"0": 8, "1": 12}
    add_reference(page, "0", "-1")
    expect(errors["offset"]).to_contain_text("already exists")
    expect(errors["address"]).not_to_be_empty()
    assert page.evaluate("processes[0].memory_references") == {"0": 8, "1": 12}


@pytest.mark.parametrize("button", ["Run", "Step"])
@pytest.mark.parametrize("problem", ["setting", "disabled", "reference"])
def test_actual_controls_explain_memory_block(page, memory_url, button, problem):
    open_memory(page, memory_url)
    add_reference(page)
    if problem == "setting":
        page.locator("#memory-frame-count").fill("0")
        expect(page.locator("#memory-error")).to_be_empty()
    elif problem == "disabled":
        page.locator("#memory-enabled").uncheck()
        expect(page.locator("#memory-error")).to_contain_text("references exist")
        expect(page.locator("#memory-enabled")).to_have_attribute("aria-describedby", "memory-error")
        assert page.locator("#memory-error").evaluate(
            "element => element.previousElementSibling.contains(document.getElementById('memory-enabled'))"
        )
        expect(page.locator("#memory-error")).to_have_css("color", "rgb(180, 35, 24)")
        expect(page.locator("#memory-frame-count")).to_be_disabled()
    else:
        add_reference(page, "4", "-1")
    page.get_by_role("button", name="Process Manager", exact=True).click()
    requests = []
    page.on("request", lambda request: requests.append(request.url) if "/api/" in request.url else None)
    page.get_by_role("button", name=button, exact=True).click()
    notice = page.locator("#memory-start-error")
    expect(page.locator("#memory")).to_be_visible()
    expect(notice).to_be_visible()
    expect(notice).to_be_in_viewport()
    expect(notice).to_contain_text("simulation could not start")
    expect(notice).to_have_css("color", "rgb(180, 35, 24)")
    assert requests == []
    if problem == "setting":
        page.locator("#memory-frame-count").fill("2")
    elif problem == "disabled":
        page.locator("#memory-enabled").check()
    else:
        page.locator("#reference-offset").fill("1")
        expect(notice).to_be_visible()
        page.locator("#reference-address").fill("12")
    expect(notice).to_be_hidden()
    page.get_by_role("button", name="Process Manager", exact=True).click()
    with page.expect_request("**/api/start"):
        page.get_by_role("button", name="Step", exact=True).click()
    page.wait_for_function("busy === false")


def test_general_reference_error_and_button_styles(page, memory_url):
    page.goto(memory_url)
    page.get_by_role("button", name="Memory Manager", exact=True).click()
    page.locator("#reference-save").click()
    expect(page.locator("#reference-error")).to_have_text("Select an existing process first.")
    expect(page.locator("#reference-error")).to_be_visible()
    expect(page.locator("#reference-offset-error")).to_be_empty()
    expect(page.locator("#reference-address-error")).to_be_empty()
    expect(page.locator("#reference-offset")).not_to_have_attribute("aria-invalid", "true")
    page.get_by_role("button", name="Process Manager", exact=True).click()
    enter_process(page)
    primary_color = page.locator("#process-form .primary").evaluate("element => getComputedStyle(element).backgroundColor")
    page.get_by_role("button", name="Memory Manager", exact=True).click()
    add_reference(page)
    page.mouse.move(0, 0)
    expect(page.locator("#reference-save")).to_have_css("background-color", primary_color)
    page.get_by_role("button", name="Edit reference P1 offset 0", exact=True).click()
    expect(page.locator("#reference-save")).to_have_text("Save Reference")
    expect(page.locator("#reference-save")).to_have_css("background-color", primary_color)
    page.locator("#reference-save").focus()
    page.keyboard.press("Tab")
    expect(page.locator("#reference-cancel")).to_have_css("outline-style", "solid")
    for name in ["Edit reference P1 offset 0", "Remove reference P1 offset 0"]:
        expect(page.get_by_role("button", name=name, exact=True)).to_have_class("memory-secondary")
    checkbox = page.locator("#memory-enabled")
    assert checkbox.bounding_box()["width"] >= 22
    assert checkbox.bounding_box()["height"] >= 22
    page.get_by_text("Enable memory simulation", exact=True).click()
    expect(checkbox).not_to_be_checked()

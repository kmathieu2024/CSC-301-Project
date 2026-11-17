"""Playwright tests against the actual Flask-served device workspace."""

import json
from collections.abc import Iterator
from pathlib import Path
from threading import Thread

import pytest
from playwright.sync_api import Page, expect
from werkzeug.serving import make_server

import app as simulator_api
from app import app
from tests.test_dashboard import enter_process, upload_json
from tests.test_device_api import (
    malformed_enum_workload, peripheral_workload, workload,
)


@pytest.fixture(scope="module")
def device_url() -> Iterator[str]:
    """Serve the real app on an isolated available port."""
    server = make_server("127.0.0.1", 0, app)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    thread.join()
    server.server_close()


def tab(page: Page, name: str = "Device") -> None:
    """Select a manager using the existing navigation control."""
    page.get_by_role("button", name=name + " Manager", exact=True).click()


def run(page: Page) -> None:
    """Exercise the existing step-based Run control through completion."""
    tab(page, "Process")
    page.locator("#run-button").click()
    expect(page.locator("#simulation-status")).to_contain_text(
        "Completed", timeout=30000,
    )
    page.wait_for_function("busy === false", timeout=30000)


def test_controls_conditional_fields_and_request_operations(
    page: Page, device_url: str,
) -> None:
    """Build a real peripheral workload through the forms and run it."""
    page.goto(device_url)
    tab(page, "Process")
    enter_process(page, pid="P1", burst="3")
    tab(page)
    expect(page.locator("#device-config-body")).to_contain_text("No devices configured")
    page.locator("#device-type").select_option("PRINTER")
    expect(page.locator("#device-disk-fields")).to_be_hidden()
    expect(page.locator('#device-policy option[value="SCAN"]')).to_be_disabled()
    page.locator("#device-id").fill("printer0")
    page.get_by_role("button", name="Add device", exact=True).click()
    expect(page.locator("#device-config-body")).to_contain_text("printer0")
    expect(page.locator("#device-request-cylinder-field")).to_be_hidden()
    expect(page.locator("#device-request-operation option")).to_have_count(1)
    expect(page.locator("#device-request-operation")).to_have_value("PRINT")
    page.get_by_role("button", name="Add device request", exact=True).click()
    expect(page.locator("#device-input-body")).to_contain_text("PRINT")
    run(page)
    tab(page)
    expect(page.locator("#device-status-body")).to_contain_text("IDLE")
    expect(page.locator("#device-history-body")).to_contain_text("COMPLETED")
    expect(page.locator("#device-history-body")).to_contain_text("request-0")
    expect(page.locator("#device-timeline")).to_contain_text("Request service")
    assert page.evaluate("JSON.parse(resultsJson).current_time") == 5


def test_disk_validation_remove_references_and_preserve_devices(
    page: Page, device_url: str,
) -> None:
    """Rejected edits preserve accepted inputs; removal cannot orphan requests."""
    page.goto(device_url)
    tab(page, "Process")
    enter_process(page, burst="4")
    tab(page)
    page.locator("#device-id").fill("disk0")
    page.locator("#device-head").fill("100")
    page.get_by_role("button", name="Add device", exact=True).click()
    expect(page.locator("#device-config-error")).to_contain_text("inclusive bounds")
    page.locator("#device-head").fill("50")
    page.locator("#device-policy").select_option("SSTF")
    page.get_by_role("button", name="Add device", exact=True).click()
    page.locator("#device-request-cylinder").fill("100")
    page.get_by_role("button", name="Add device request", exact=True).click()
    expect(page.locator("#device-request-error")).to_contain_text("bounds")
    page.locator("#device-request-cylinder").fill("60")
    page.get_by_role("button", name="Add device request", exact=True).click()
    page.get_by_role("button", name="Remove device disk0", exact=True).click()
    expect(page.locator("#device-config-error")).to_contain_text("Remove requests")
    tab(page, "Process")
    enter_process(page, pid="P2", burst="2")
    tab(page)
    expect(page.locator("#device-config-body")).to_contain_text("SSTF")
    page.get_by_role(
        "button", name="Remove device request P1 offset 0", exact=True,
    ).click()
    page.get_by_role("button", name="Remove device disk0", exact=True).click()
    expect(page.locator("#device-config-body")).to_contain_text("No devices configured")


def test_import_run_reset_and_empty_states(page: Page, device_url: str) -> None:
    """Typed parsing reaches the backend; reset retains inputs and clears activity."""
    page.goto(device_url)
    tab(page, "Process")
    upload_json(page, workload())
    expect(page.locator("#upload-status")).to_contain_text("Loaded")
    run(page)
    tab(page)
    expect(page.locator("#device-timeline")).to_contain_text("Disk seek")
    expect(page.locator("#device-history-body")).to_contain_text("COMPLETED")
    page.locator("#device-history-filter").fill("no-match")
    expect(page.locator("#device-history-body")).to_contain_text("No matching")
    page.locator("#device-history-filter").fill("")
    tab(page, "Process")
    page.locator("#reset-button").click()
    page.wait_for_function("busy === false")
    tab(page)
    expect(page.locator("#device-history-body")).to_contain_text(
        "No device requests submitted",
    )
    expect(page.locator("#device-config-body")).to_contain_text("disk0")
    expect(page.locator("#device-input-body")).to_contain_text("READ")
    run(page)
    assert page.evaluate("JSON.parse(resultsJson).current_time") == 14


def test_demo_end_to_end_and_exports(page: Page, device_url: str) -> None:
    """Load the canonical 12-request demonstration and run through real controls."""
    page.goto(device_url)
    tab(page)
    page.locator("#load-device-demo").click()
    expect(page.locator("#device-demo-status")).to_contain_text("12 requests")
    expect(page.locator("#device-config-body tr")).to_have_count(3)
    expect(page.locator("#device-input-body tr")).to_have_count(12)
    run(page)
    tab(page)
    expect(page.locator("#device-history-body tr")).to_have_count(12)
    expect(page.locator("#device-timeline")).to_contain_text("SCAN endpoint")
    expect(page.locator("#device-timeline")).to_contain_text("Queue waiting")
    expect(page.locator("#device-metrics-body")).to_contain_text("306")
    expect(page.locator("#device-summary")).to_contain_text("12 / 0 / 0")
    expect(page.locator("#device-summary .metric").last.locator("p")).to_have_text(
        "34.587%",
    )
    expect(page.locator("#device-summary .metric").nth(3).locator("p")).to_have_text(
        "0.038",
    )
    result = page.evaluate("JSON.parse(resultsJson)")
    assert result["current_time"] == 319
    assert result["device_metrics"]["devices"]["disk0"]["head_movement"] == 306
    tab(page, "Process")
    with page.expect_download() as download:
        page.locator("#export-results-button").click()
    assert json.loads(Path(download.value.path()).read_text()) == result


@pytest.mark.parametrize("problem", ["offset", "unknown", "policy", "duplicate"])
def test_rejected_import_preserves_results_and_inputs(
    page: Page, device_url: str, problem: str,
) -> None:
    """Imports validate completely before replacing the working workload."""
    page.goto(device_url)
    tab(page, "Process")
    upload_json(page, workload())
    run(page)
    before = page.evaluate("({input: collectSimulationInput(), results: resultsJson})")
    bad = workload()
    if problem == "offset":
        request = bad["processes"][0]["device_requests"].pop("1")
        bad["processes"][0]["device_requests"]["01"] = request
    elif problem == "unknown":
        bad["processes"][0]["device_requests"]["1"]["device_id"] = "missing"
    elif problem == "policy":
        bad["devices"][0]["policy"] = "LOOK"
    else:
        bad["devices"].append(bad["devices"][0])
    upload_json(page, bad)
    expect(page.locator("#process-error")).to_contain_text("Upload failed")
    assert page.evaluate(
        "({input: collectSimulationInput(), results: resultsJson})"
    ) == before


@pytest.mark.parametrize("field", ["device_type", "policy", "initial_direction", "operation"])
@pytest.mark.parametrize("shape", ["array", "object", "boolean", "null"])
def test_nonstring_enum_import_preserves_results_and_drafts(
    page: Page, device_url: str, field: str, shape: str,
) -> None:
    """Rejected coercible enums leave accepted inputs, results, and drafts intact."""
    page.goto(device_url)
    tab(page, "Process")
    upload_json(page, workload())
    run(page)
    tab(page)
    page.locator("#device-id").fill("unsaved-device")
    page.locator("#device-history-filter").fill("P1")
    capture = """() => ({input: collectSimulationInput(), results: resultsJson,
        status: document.querySelector('#simulation-status').textContent,
        history: document.querySelector('#device-history-body').textContent,
        draft: document.querySelector('#device-id').value,
        filter: document.querySelector('#device-history-filter').value,
        exportDisabled: document.querySelector('#export-results-button').disabled})"""
    before = page.evaluate(capture)
    tab(page, "Process")
    upload_json(page, malformed_enum_workload(field, shape))
    expect(page.locator("#process-error")).to_contain_text("Upload failed")
    expect(page.locator("#process-error")).to_contain_text("must be one of")
    assert page.evaluate(capture) == before


def test_tiny_duration_partial_results_render_without_infinity(
    page: Page, device_url: str,
) -> None:
    """Unrepresentable throughput is unavailable, including the UI aggregate."""
    page.goto(device_url)
    response = page.request.post(
        device_url + "/api/start", data=peripheral_workload(1e-320),
    )
    assert response.ok
    for _ in range(3):
        response = page.request.post(device_url + "/api/step")
        assert response.ok
    snapshot = response.json()
    assert snapshot["device_metrics"]["devices"]["disk0"]["throughput"] is None
    page.evaluate("snapshot => renderDeviceResults(snapshot)", snapshot)
    tab(page)
    expect(page.locator("#device-summary")).not_to_contain_text("Infinity")
    expect(page.locator("#device-metrics-body")).not_to_contain_text("Infinity")
    row = page.locator("#device-metrics-body tr").first
    expect(row.locator("td").nth(7)).to_have_text("—")


@pytest.mark.parametrize("case", ["null", "zero", "mixed"])
def test_unavailable_and_zero_utilization_display(
    page: Page, device_url: str, case: str,
) -> None:
    """Render actual underflow as unavailable without changing genuine idle zero."""
    page.goto(device_url)
    data = peripheral_workload(5e-324)
    data["processes"].append({
        "pid": "late", "arrival_time": 1e308, "burst_time": 1,
    })
    if case == "zero":
        data["processes"][0]["device_requests"] = {}
    if case == "mixed":
        data["devices"].append({"device_id": "idle", "device_type": "KEYBOARD"})
    assert page.request.post(device_url + "/api/start", data=data).ok
    response = page.request.post(device_url + "/api/run")
    assert response.ok
    snapshot = response.json()
    expected = "0%" if case == "zero" else "—"
    utilization = snapshot["device_metrics"]["devices"]["disk0"]["utilization"]
    assert utilization == 0 if case == "zero" else utilization is None
    page.evaluate("snapshot => renderDeviceResults(snapshot)", snapshot)
    tab(page)
    expect(page.locator("#device-summary .metric").last.locator("p")).to_have_text(
        expected,
    )
    row = page.locator("#device-metrics-body tr").filter(has_text="disk0")
    expect(row.locator("td").nth(6)).to_have_text(expected)
    if case == "mixed":
        idle = page.locator("#device-metrics-body tr").filter(has_text="idle")
        expect(idle.locator("td").nth(6)).to_have_text("0%")


def test_combined_blocking_device_memory_file_import_and_run(
    page: Page, device_url: str,
) -> None:
    """Merged blocking inputs survive the UI together with all existing I/O maps."""
    data = peripheral_workload(3)
    data["memory"] = {"enabled": True, "frame_count": 1, "page_size": 4,
                      "fault_latency": 1}
    data["processes"][0].update(
        burst_time=2, blocking_requests={"0": 2}, memory_references={"0": 0},
        file_requests={"0": "CREATE /combined"},
    )
    page.goto(device_url)
    tab(page, "Process")
    upload_json(page, data)
    expect(page.locator("#process-table-body")).to_contain_text("P1")
    collected = page.evaluate("collectSimulationInput()")
    for key in ("blocking_requests", "device_requests", "memory_references",
                "file_requests"):
        assert collected["processes"][0][key] == data["processes"][0][key]
    run(page)
    result = page.evaluate("JSON.parse(resultsJson)")
    assert result["current_time"] == 8
    assert result["processes"][0]["blocked_time"] == 6
    assert result["memory_metrics"]["references"] == 1
    assert result["device_metrics"]["devices"]["disk0"]["submitted"] == 1
    assert result["device_metrics"]["devices"]["disk0"]["completed"] == 1
    assert result["file_tree"]["children"][0]["name"] == "combined"
    assert len([e for e in result["events"] if e["manager"] == "file"]) == 1


def test_busy_controls_and_accepted_changes_invalidate_results(
    page: Page, device_url: str,
) -> None:
    """Device controls lock during Run; unrelated CPU edits retain device inputs."""
    page.goto(device_url)
    tab(page, "Process")
    upload_json(page, workload())
    page.locator("#run-button").click()
    page.wait_for_function("running === true")
    tab(page)
    expect(page.locator("#device-id")).to_be_disabled()
    expect(page.locator("#load-device-demo")).to_be_disabled()
    expect(page.locator("#device-request-target")).to_be_disabled()
    page.wait_for_function("busy === false", timeout=30000)
    expect(page.locator("#device-id")).to_be_enabled()
    tab(page, "Process")
    page.locator("#context-switch-cost").fill("1")
    tab(page)
    expect(page.locator("#device-result-status")).to_contain_text("Start a simulation")
    expect(page.locator("#device-config-body")).to_contain_text("disk0")
    expect(page.locator("#device-input-body")).to_contain_text("READ")


def test_legacy_empty_state_and_narrow_layout(page: Page, device_url: str) -> None:
    """Legacy workloads remain usable and the device workspace fits narrow screens."""
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(device_url)
    tab(page, "Process")
    enter_process(page, burst="1")
    run(page)
    tab(page)
    expect(page.locator("#device-result-status")).to_contain_text(
        "No devices configured",
    )
    expect(page.locator("#device-metrics-body")).to_contain_text("No device metrics")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


@pytest.mark.parametrize("outcome", ["FAILED", "CANCELLED"])
def test_failure_and_cancellation_display_from_real_engine_schema(
    page: Page, device_url: str, outcome: str,
) -> None:
    """Render real engine abort data without adding new lifecycle endpoints."""
    page.goto(device_url)
    tab(page, "Process")
    data = workload()
    data["processes"][0]["pid"] = "<script>unsafe</script>"
    response = page.request.post(device_url + "/api/start", data=data)
    assert response.ok
    page.request.post(device_url + "/api/step")
    page.request.post(device_url + "/api/step")
    engine = simulator_api.engine
    engine.advance_time(2)
    reason = "user cancelled" if outcome == "CANCELLED" else "device failed"
    if outcome == "FAILED":
        engine.device_manager.fail_device("disk0", engine.current_time, reason)
    else:
        engine.device_manager.cancel_request(
            next(iter(engine.device_manager.requests)), engine.current_time, reason,
        )
    engine.check_arrivals()
    snapshot = engine.results()
    page.evaluate("snapshot => renderDeviceResults(snapshot)", snapshot)
    tab(page)
    expect(page.locator("#device-history-body")).to_contain_text(outcome)
    expect(page.locator("#device-history-body")).to_contain_text(reason)
    expect(page.locator("#device-timeline")).to_contain_text(reason)
    expect(page.locator("#device-history-body script")).to_have_count(0)

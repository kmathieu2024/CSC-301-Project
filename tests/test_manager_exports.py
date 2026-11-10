import csv
import io
import json

import pytest
from playwright.sync_api import expect

from tests.test_dashboard import upload_json
from tests.test_visualization import dashboard_url


MEMORY_HEADERS = ["timestamp", "pid", "virtual_address", "page", "frame", "physical_address", "hit_fault", "evicted_pid", "evicted_page", "modeled_fault_latency"]
FILE_HEADERS = ["timestamp", "pid", "command", "outcome", "details"]
CONTENT = 'caf\u00e9, "quoted"  text\nsecond line'
PID = 'P,"\u03a9'


def tab(page, name):
    page.get_by_role("button", name=name, exact=True).click()


def load(page, url):
    page.goto(url)
    tab(page, "Process Manager")
    # Single quotes preserve literal double quotes inside shlex's quoted content.
    command = "  WRITE /notes.txt '" + CONTENT + "'  "
    upload_json(page, {"algorithm": "RR", "configuration": {"time_quantum": 1},
        "memory": {"frame_count": 2, "page_size": 4, "algorithm": "fifo"},
        "processes": [{"pid": PID, "arrival_time": 0, "burst_time": 7,
                       "memory_references": {"0": 1, "1": 5, "2": 2, "3": 9, "4": 6},
                       "file_requests": {"0": "CREATE /notes.txt", "1": command,
                                         "2": "READ /notes.txt", "3": "READ /missing.txt"}}]})
    expect(page.locator("#upload-status")).to_contain_text("Loaded")


def download(page, button, filename):
    with page.expect_download() as pending:
        page.locator(button).click()
    result = pending.value
    assert result.suggested_filename == filename
    with open(result.path(), encoding="utf-8", newline="") as file:
        return file.read()


def disabled(page):
    for id in ["memory-csv", "memory-log", "file-csv", "file-log"]:
        expect(page.locator("#export-" + id)).to_be_disabled()


@pytest.mark.parametrize("state", ["partial", "paused", "completed"])
def test_downloads_match_api_and_preserve_content_without_requests(page, dashboard_url, state):
    load(page, dashboard_url)
    disabled(page)
    responses = []
    requests = []
    page.on("response", lambda response: responses.append(response) if response.url.endswith("/api/step") else None)
    page.on("request", lambda request: requests.append(request.url) if "/api/" in request.url else None)
    if state == "partial":
        for _ in range(6):
            page.locator("#step-button").click()
            page.wait_for_function("busy === false")
    else:
        page.locator("#run-button").click()
        if state == "paused":
            page.wait_for_function("JSON.parse(resultsJson || '{}').current_time >= 3")
            page.locator("#pause-button").click()
        else:
            expect(page.locator("#simulation-status")).to_contain_text("Completed", timeout=15000)
        page.wait_for_function("busy === false")
    snapshot = responses[-1].json()
    if state != "completed":
        assert snapshot["processes"][0]["state"] != "TERMINATED"
    expected_memory = [e for e in snapshot["events"] if e["manager"] == "memory"]
    expected_file = [e for e in snapshot["events"] if e["manager"] == "file"]
    assert any(e["message"] == CONTENT for e in expected_file)
    request_count = len(requests)
    tab(page, "Memory Manager")
    text = download(page, "#export-memory-csv", "memory-history.csv")
    memory = list(csv.reader(io.StringIO(text, newline="")))
    assert memory[0] == MEMORY_HEADERS
    assert memory[1:] == [[str(e["timestamp"]), e["entity_id"], str(e["address"]), str(e["page"]),
        str(e["frame"]), str(e["physical_address"]), "Fault" if e["event_type"] == "page_fault" else "Hit",
        e["evicted"][0] if e["evicted"] else "", str(e["evicted"][1]) if e["evicted"] else "", str(e["duration"])] for e in expected_memory]
    displayed = page.locator("#memory-history-body tr").evaluate_all("rows => rows.map(row => Array.from(row.cells, cell => cell.textContent))")
    assert [row[:7] for row in memory[1:]] == [row[:7] for row in displayed]
    assert [json.loads(line) for line in download(page, "#export-memory-log", "memory-events.log").splitlines()] == expected_memory
    tab(page, "File Manager")
    file_csv = list(csv.reader(io.StringIO(download(page, "#export-file-csv", "file-activity.csv"), newline="")))
    assert file_csv[0] == FILE_HEADERS
    assert file_csv[1:] == [[str(e["timestamp"]), e["entity_id"], e["action"], e["outcome"], e["message"]] for e in expected_file]
    assert any(row[4] == CONTENT for row in file_csv[1:])
    assert [json.loads(line) for line in download(page, "#export-file-log", "file-events.log").splitlines()] == expected_file
    tab(page, "Process Manager")
    assert json.loads(download(page, "#export-results-button", "simulation-results.json")) == snapshot
    assert len(requests) == request_count


def test_reset_changes_and_rejected_edits_control_availability(page, dashboard_url):
    load(page, dashboard_url)
    for _ in range(2):
        page.locator("#step-button").click()
        page.wait_for_function("busy === false")
    tab(page, "File Manager")
    before = download(page, "#export-file-csv", "file-activity.csv")
    page.get_by_role("button", name=f"Edit file request {PID} offset 0", exact=True).click()
    page.locator("#file-request-command").fill(" ")
    page.locator("#file-request-save").click()
    expect(page.locator("#file-request-command-error")).not_to_be_empty()
    assert download(page, "#export-file-csv", "file-activity.csv") == before
    page.locator("#file-request-cancel").click()
    tab(page, "Process Manager")
    upload_json(page, {"algorithm": "FCFS", "processes": []})
    expect(page.locator("#process-error")).to_contain_text("Upload failed")
    tab(page, "File Manager")
    assert download(page, "#export-file-csv", "file-activity.csv") == before
    tab(page, "Process Manager")
    page.locator("#reset-button").click()
    page.wait_for_function("busy === false")
    disabled(page)
    for _ in range(2):
        page.locator("#step-button").click()
        page.wait_for_function("busy === false")
    tab(page, "Memory Manager")
    expect(page.locator("#export-memory-csv")).to_be_enabled()
    page.locator("#memory-frame-count").fill("3")
    disabled(page)


def test_busy_run_and_inflight_requests_disable_downloads(page, dashboard_url):
    load(page, dashboard_url)
    for _ in range(2):
        page.locator("#step-button").click()
        page.wait_for_function("busy === false")
    # Hold the next fetch pending until the test explicitly releases it.
    page.evaluate("""() => {
        const originalFetch = window.fetch;
        window.fetch = (...args) => new Promise(resolve => {
            window.releaseExportTestFetch = () => resolve(originalFetch(...args));
        });
    }""")
    page.locator("#step-button").click()
    disabled(page)
    page.evaluate("releaseExportTestFetch()")
    page.wait_for_function("busy === false")
    # Restore normal fetch before exercising Run.
    page.reload()
    load(page, dashboard_url)
    page.locator("#run-button").click()
    page.wait_for_function("running === true")
    tab(page, "Memory Manager")
    disabled(page)
    tab(page, "Process Manager")
    page.locator("#pause-button").click()
    page.wait_for_function("busy === false")


def test_each_manager_requires_its_own_history_and_urls_are_revoked(page, dashboard_url):
    page.goto(dashboard_url)
    tab(page, "Process Manager")
    upload_json(page, {"algorithm": "RR", "configuration": {"time_quantum": 1},
        "processes": [{"pid": "P1", "arrival_time": 0, "burst_time": 3, "memory_references": {"0": 0}}]})
    expect(page.locator("#upload-status")).to_contain_text("Loaded")
    for _ in range(2):
        page.locator("#step-button").click()
        page.wait_for_function("busy === false")
    tab(page, "Memory Manager")
    expect(page.locator("#export-memory-csv")).to_be_enabled()
    expect(page.locator("#export-file-csv")).to_be_disabled()
    expect(page.locator("#export-file-log")).to_be_disabled()
    page.evaluate("""() => {
        window.revokedExportUrls = [];
        const original = URL.revokeObjectURL;
        URL.revokeObjectURL = url => { revokedExportUrls.push(url); original.call(URL, url); };
    }""")
    download(page, "#export-memory-csv", "memory-history.csv")
    page.wait_for_function("revokedExportUrls.length === 1")



def test_memory_log_preserves_same_timestamp_order(page, dashboard_url):
    page.goto(dashboard_url)
    tab(page, "Process Manager")
    upload_json(page, {"algorithm": "FCFS", "memory": {"frame_count": 2, "page_size": 4, "fault_latency": 2},
        "processes": [{"pid": pid, "arrival_time": 0, "burst_time": 1, "memory_references": {"0": 0}} for pid in ["P1", "P2"]]})
    expect(page.locator("#upload-status")).to_contain_text("Loaded")
    responses = []
    page.on("response", lambda response: responses.append(response) if response.url.endswith("/api/step") else None)
    page.locator("#run-button").click()
    expect(page.locator("#simulation-status")).to_contain_text("Completed")
    page.wait_for_function("busy === false")
    expected = [e for e in responses[-1].json()["events"] if e["manager"] == "memory"]
    assert [(e["timestamp"], e["entity_id"]) for e in expected] == [(0, "P1"), (0, "P2")]
    tab(page, "Memory Manager")
    exported = [json.loads(line) for line in download(page, "#export-memory-log", "memory-events.log").splitlines()]
    assert exported == expected


def test_file_exports_use_provided_outcome_for_error_prefix_content(page, dashboard_url):
    load(page, dashboard_url)
    def response_with_diagnostic_content(route):
        response = route.fetch()
        snapshot = response.json()
        for event in snapshot["events"]:
            if event["manager"] == "file":
                # Contract coverage for successful diagnostic text; do not
                # duplicate the backend's known Error:-prefix classification.
                event["outcome"] = "success"
                event["message"] = "Error: ordinary output"
        route.fulfill(response=response, json=snapshot)
    page.route("**/api/step", response_with_diagnostic_content)
    for _ in range(2):
        page.locator("#step-button").click()
        page.wait_for_function("busy === false")
    tab(page, "File Manager")
    expect(page.locator("#file-activity-body tr td").nth(3)).to_have_text("Success")
    table = list(csv.reader(io.StringIO(download(page, "#export-file-csv", "file-activity.csv"))))
    assert table[1][3:] == ["success", "Error: ordinary output"]
    events = [json.loads(line) for line in download(page, "#export-file-log", "file-events.log").splitlines()]
    assert events[0]["outcome"] == "success"
    assert events[0]["message"] == "Error: ordinary output"

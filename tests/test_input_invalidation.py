import json

import pytest
from playwright.sync_api import expect

from tests.test_dashboard import enter_process, upload_json
from tests.test_memory_dashboard import memory_url, payload


CHANGED = "Inputs changed. Run or Step to start a new simulation."


def displays(page):
    return page.evaluate("""() => Array.from(document.querySelectorAll(
        '.simulation-results .metric p, #process-metrics-body, #gantt-chart, #gantt-empty, ' +
        '#memory .metric p, #memory-frame-grid, #file-tree-container, #file-activity-body'
    ), element => [element.id, element.innerHTML, element.hidden])""")


def prepare_results(page, url):
    page.goto(url)
    initial = displays(page)
    page.get_by_role("button", name="Process Manager", exact=True).click()
    data = payload()
    data["memory"]["fault_latency"] = 0
    data["processes"].append({"pid": "P2", "arrival_time": 0, "burst_time": 2})
    upload_json(page, data)
    expect(page.locator("#upload-status")).to_contain_text("Loaded 2")
    page.locator("#run-button").click()
    expect(page.locator("#simulation-status")).to_contain_text("Completed")
    page.wait_for_function("busy === false")
    expect(page.locator("#export-results-button")).to_be_enabled()
    assert displays(page) != initial
    return initial


@pytest.mark.parametrize("change", ["process_remove", "process_add", "reference_edit", "reference_add", "reference_remove", "import", "scheduling", "memory"])
def test_successful_changes_clear_all_results(page, memory_url, change):
    initial = prepare_results(page, memory_url)
    requests = []
    page.on("request", lambda request: requests.append(request.url) if "/api/" in request.url else None)
    if change == "process_remove":
        page.get_by_role("button", name="Remove P2", exact=True).click()
    elif change == "process_add":
        enter_process(page, pid="P3")
    elif change == "import":
        upload_json(page, payload())
        expect(page.locator("#upload-status")).to_contain_text("Loaded 1")
    elif change == "scheduling":
        page.locator("#context-switch-cost").fill("1")
    else:
        page.get_by_role("button", name="Memory Manager", exact=True).click()
        if change == "memory":
            page.locator("#memory-frame-count").fill("4")
        elif change == "reference_remove":
            page.get_by_role("button", name="Remove reference P1 offset 0", exact=True).click()
        else:
            if change == "reference_edit":
                page.get_by_role("button", name="Edit reference P1 offset 0", exact=True).click()
            else:
                page.locator("#reference-offset").fill("1")
            page.locator("#reference-address").fill("16")
            page.locator("#reference-save").click()
    expect(page.locator("#simulation-status")).to_have_text(CHANGED)
    assert displays(page) == initial
    expect(page.locator("#export-results-button")).to_be_disabled()
    expect(page.locator("#step-button")).to_be_enabled()
    assert page.evaluate("resultsJson") == ""
    assert requests == []  # Invalidation is local and never calls Reset.
    expected_input = page.evaluate("collectSimulationInput()")
    page.get_by_role("button", name="Process Manager", exact=True).click()
    with page.expect_request("**/api/start") as request:
        page.locator("#step-button").click()
    assert request.value.post_data_json == expected_input
    page.wait_for_function("busy === false")
    expect(page.locator("#export-results-button")).to_be_enabled()
    assert json.loads(page.evaluate("resultsJson"))["current_time"] == 0


@pytest.mark.parametrize("rejection", ["import", "reference", "process"])
def test_rejected_changes_preserve_inputs_and_results(page, memory_url, rejection):
    prepare_results(page, memory_url)
    before = displays(page)
    before_input = page.evaluate("JSON.stringify(collectSimulationInput())")
    before_results = page.evaluate("resultsJson")
    before_status = page.locator("#simulation-status").inner_text()
    if rejection == "import":
        data = payload()
        data["processes"][0]["memory_references"] = {"0": -1}
        upload_json(page, data)
        expect(page.locator("#process-error")).to_contain_text("Upload failed")
    elif rejection == "process":
        enter_process(page, pid="P1")
        expect(page.locator("#process-error")).to_contain_text("already exists")
    else:
        page.get_by_role("button", name="Memory Manager", exact=True).click()
        page.get_by_role("button", name="Edit reference P1 offset 0", exact=True).click()
        page.locator("#reference-address").fill("-1")
        page.locator("#reference-save").click()
        expect(page.locator("#reference-address-error")).not_to_be_empty()
    assert displays(page) == before
    assert page.evaluate("JSON.stringify(collectSimulationInput())") == before_input
    assert page.evaluate("resultsJson") == before_results
    expect(page.locator("#simulation-status")).to_have_text(before_status)
    expect(page.locator("#export-results-button")).to_be_enabled()


def test_running_locks_workload_until_pause_finishes(page, memory_url):
    page.goto(memory_url)
    page.get_by_role("button", name="Process Manager", exact=True).click()
    data = payload()
    data["processes"][0]["burst_time"] = 50
    upload_json(page, data)
    expect(page.locator("#upload-status")).to_contain_text("Loaded")
    before = page.evaluate("JSON.stringify(collectSimulationInput())")
    page.locator("#run-button").click()
    page.wait_for_function("running === true")
    for selector in ["#process-id", "#process-file", "#algorithm", "#context-switch-cost", "#process-form button", "#process-table-body button"]:
        expect(page.locator(selector)).to_be_disabled()
    page.get_by_role("button", name="Memory Manager", exact=True).click()
    for selector in ["#memory-enabled", "#memory-page-size", "#reference-offset", "#reference-save", "#reference-table-body button"]:
        for control in page.locator(selector).all():
            expect(control).to_be_disabled()
    # Event handlers also reject submissions dispatched while controls are locked.
    page.locator("#process-form").dispatch_event("submit")
    page.locator("#reference-form").dispatch_event("submit")
    assert page.evaluate("JSON.stringify(collectSimulationInput())") == before
    page.get_by_role("button", name="Process Manager", exact=True).click()
    page.locator("#pause-button").click()
    page.wait_for_function("busy === false")
    expect(page.locator("#process-file")).to_be_enabled()
    expect(page.locator("#algorithm")).to_be_enabled()
    expect(page.locator("#process-table-body button")).to_be_enabled()
    page.get_by_role("button", name="Remove P1", exact=True).click()
    expect(page.locator("#simulation-status")).to_have_text(CHANGED)
    expect(page.locator("#export-results-button")).to_be_disabled()

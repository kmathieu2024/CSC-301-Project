import pytest
from playwright.sync_api import expect

from tests.test_dashboard import upload_json
from tests.test_visualization import dashboard_url


def tab(page, manager):
    page.get_by_role("button", name=manager + " Manager", exact=True).click()


def load(page, url, manager):
    page.goto(url)
    tab(page, manager.title())
    page.locator("#load-" + manager + "-test").click()


def run(page):
    tab(page, "Process")
    page.locator("#run-button").click()
    expect(page.locator("#simulation-status")).to_contain_text("Completed")
    page.wait_for_function("busy === false")


@pytest.mark.parametrize("manager", ["memory", "file"])
@pytest.mark.parametrize("control", ["run", "step"])
def test_scenario_loads_without_start_and_passes_on_completion(page, dashboard_url, manager, control):
    page.goto(dashboard_url)
    requests = []
    page.on("request", lambda request: requests.append(request.url) if "/api/" in request.url else None)
    tab(page, manager.title())
    page.locator("#load-" + manager + "-test").click()
    assert requests == []
    expect(page.locator(f"#{manager}-test-results")).to_be_hidden()
    expect(page.locator("#export-results-button")).to_be_disabled()
    data = page.evaluate("collectSimulationInput()")
    assert data["processes"][0]["burst_time"] == 5
    if manager == "memory":
        assert data["memory"] == {"enabled": True, "algorithm": "fifo", "frame_count": 2, "page_size": 4, "fault_latency": 0}
        assert list(data["processes"][0]["memory_references"].values()) == [0, 4, 0, 8, 4]
    else:
        assert list(data["processes"][0]["file_requests"].values()) == ["MKDIR /demo", "CREATE /demo/notes.txt", 'WRITE /demo/notes.txt "Hello OS"', "READ /demo/notes.txt", "READ /missing.txt"]
    responses = []
    page.on("response", lambda response: responses.append(response) if response.url.endswith("/api/step") else None)
    tab(page, "Process")
    page.locator("#step-button").click()
    page.wait_for_function("busy === false")
    expect(page.locator(f"#{manager}-test-results")).to_be_hidden()
    expect(page.locator(f"#{manager}-test-status")).not_to_contain_text("Pass:")
    if control == "run":
        run(page)
    else:
        page.locator("#step-button").click()
        page.wait_for_function("busy === false")
    tab(page, manager.title())
    expect(page.locator(f"#{manager}-test-status")).to_have_text("Pass: all scenario checks passed.")
    expect(page.locator(f"#{manager}-test-body .test-fail")).to_have_count(0)
    snapshot = responses[-1].json()
    if manager == "memory":
        assert {key: snapshot["memory_metrics"][key] for key in ["references", "hits", "faults", "evictions"]} == {"references": 5, "hits": 2, "faults": 3, "evictions": 1}
        expect(page.locator("#memory-test-body tr")).to_have_count(4)
    else:
        assert [e["outcome"] for e in snapshot["events"] if e["manager"] == "file"] == ["success"] * 4 + ["error"]
        expect(page.locator("#file-tree-container")).to_contain_text("notes.txt")


@pytest.mark.parametrize("manager", ["memory", "file"])
def test_mismatched_completed_snapshot_shows_fail(page, dashboard_url, manager):
    load(page, dashboard_url, manager)
    def mismatch(route):
        response = route.fetch()
        snapshot = response.json()
        if all(process["state"] == "TERMINATED" for process in snapshot["processes"]):
            if manager == "memory":
                snapshot["memory_metrics"]["evictions"] = 0
            else:
                for event in snapshot["events"]:
                    if event["manager"] == "file" and event["action"] == "READ /missing.txt":
                        event["outcome"] = "success"
        route.fulfill(response=response, json=snapshot)
    page.route("**/api/step", mismatch)
    run(page)
    tab(page, manager.title())
    expect(page.locator(f"#{manager}-test-status")).to_contain_text("Fail:")
    assert page.locator(f"#{manager}-test-body .test-fail").count() >= 1


def test_file_reset_and_completed_retries_start_fresh(page, dashboard_url):
    load(page, dashboard_url, "file")
    run(page)
    tab(page, "File")
    expect(page.locator("#file-test-status")).to_contain_text("Pass:")
    tab(page, "Process")
    with page.expect_response("**/api/reset") as response:
        page.locator("#reset-button").click()
    page.wait_for_function("busy === false")
    assert response.value.json()["file_tree"]["children"][0]["name"] == "demo"
    tab(page, "File")
    expect(page.locator("#file-test-results")).to_be_hidden()
    for _ in range(2):
        with page.expect_request("**/api/start"):
            run(page)
        tab(page, "File")
        expect(page.locator("#file-test-status")).to_contain_text("Pass:")


@pytest.mark.parametrize("change", ["edit", "import", "reload"])
def test_changes_clear_stale_verdict_and_rejections_preserve_it(page, dashboard_url, change):
    load(page, dashboard_url, "memory")
    run(page)
    tab(page, "Memory")
    expect(page.locator("#memory-test-status")).to_contain_text("Pass:")
    tab(page, "Process")
    upload_json(page, {"algorithm": "FCFS", "processes": []})
    expect(page.locator("#process-error")).to_contain_text("Upload failed")
    tab(page, "Memory")
    expect(page.locator("#memory-test-status")).to_contain_text("Pass:")
    if change == "edit":
        page.locator("#memory-frame-count").fill("3")
    elif change == "import":
        tab(page, "Process")
        upload_json(page, {"algorithm": "FCFS", "processes": [{"pid": "Ordinary", "arrival_time": 0, "burst_time": 1}]})
        expect(page.locator("#upload-status")).to_contain_text("Loaded")
    else:
        tab(page, "File")
        page.locator("#load-file-test").click()
    expect(page.locator("#memory-test-results")).to_be_hidden()
    expect(page.locator("#memory-test-status")).to_be_empty()
    if change != "reload":
        run(page)
        expect(page.locator("#memory-test-status")).to_be_empty()
        expect(page.locator("#file-test-status")).to_be_empty()


def test_load_buttons_disable_while_busy_and_replace_old_results(page, dashboard_url):
    load(page, dashboard_url, "file")
    tab(page, "Process")
    page.locator("#run-button").click()
    page.wait_for_function("running === true")
    for manager in ["memory", "file"]:
        expect(page.locator(f"#load-{manager}-test")).to_be_disabled()
    expect(page.locator("#simulation-status")).to_contain_text("Completed")
    page.wait_for_function("busy === false")
    tab(page, "Memory")
    page.locator("#load-memory-test").click()
    expect(page.locator("#file-tree-container")).to_contain_text("Start a simulation")
    expect(page.locator("#export-results-button")).to_be_disabled()
    expect(page.locator("#memory-test-results")).to_be_hidden()



def test_rejected_file_edit_and_inflight_request_preserve_test_state(page, dashboard_url):
    load(page, dashboard_url, "file")
    run(page)
    tab(page, "File")
    page.get_by_role("button", name="Edit file request P1 offset 0", exact=True).click()
    page.locator("#file-request-command").fill(" ")
    page.locator("#file-request-save").click()
    expect(page.locator("#file-request-command-error")).not_to_be_empty()
    expect(page.locator("#file-test-status")).to_contain_text("Pass:")
    page.locator("#file-request-cancel").click()
    expect(page.locator("#file-test-status")).to_contain_text("Pass:")
    tab(page, "Process")
    page.evaluate("""() => {
        const original = window.fetch;
        window.fetch = (...args) => new Promise(resolve => {
            window.releaseScenarioRequest = () => resolve(original(...args));
        });
    }""")
    page.locator("#reset-button").click()
    expect(page.locator("#load-memory-test")).to_be_disabled()
    expect(page.locator("#load-file-test")).to_be_disabled()
    page.evaluate("releaseScenarioRequest()")
    page.wait_for_function("busy === false")
    expect(page.locator("#load-file-test")).to_be_enabled()
    expect(page.locator("#file-test-results")).to_be_hidden()



@pytest.mark.parametrize("manager", ["memory", "file"])
@pytest.mark.parametrize("control", ["run", "step"])
def test_loading_scenario_clears_invalid_add_request_draft(page, dashboard_url, manager, control):
    from tests.test_dashboard import enter_process

    page.goto(dashboard_url)
    tab(page, "Process")
    enter_process(page, burst="5")
    tab(page, "File")
    page.locator("#file-request-offset").fill("0")
    page.locator("#file-request-command").fill(" ")
    page.locator("#file-request-save").click()
    expect(page.locator("#file-request-command-error")).not_to_be_empty()
    expect(page.locator("#file-request-cancel")).to_be_hidden()  # Add draft, not Edit.
    tab(page, manager.title())
    page.locator(f"#load-{manager}-test").click()
    expect(page.locator("#file-request-command-error")).to_be_empty()
    expect(page.locator("#file-request-offset-error")).to_be_empty()
    expect(page.locator("#file-request-command")).to_have_value("")
    expect(page.locator("#file-request-offset")).to_have_value("")
    expect(page.locator("#file-request-command")).not_to_have_attribute("aria-invalid", "true")
    tab(page, "Process")
    with page.expect_request("**/api/start"):
        if control == "run":
            run(page)
        else:
            page.locator("#step-button").click()
            page.wait_for_function("busy === false")
    if control == "step":
        page.locator("#step-button").click()
        page.wait_for_function("busy === false")
    tab(page, manager.title())
    expect(page.locator(f"#{manager}-test-status")).to_have_text("Pass: all scenario checks passed.")


def test_valid_import_clears_add_draft_but_rejected_import_preserves_it(page, dashboard_url):
    load(page, dashboard_url, "file")
    run(page)
    before_results = page.evaluate("resultsJson")
    tab(page, "File")
    page.locator("#file-request-offset").fill("5")
    page.locator("#file-request-command").fill(" ")
    page.locator("#file-request-save").click()
    expect(page.locator("#file-request-offset-error")).not_to_be_empty()
    tab(page, "Process")
    upload_json(page, {"algorithm": "FCFS", "processes": []})
    expect(page.locator("#process-error")).to_contain_text("Upload failed")
    expect(page.locator("#file-request-offset")).to_have_value("5")
    expect(page.locator("#file-request-command")).to_have_value(" ")
    expect(page.locator("#file-request-command-error")).not_to_be_empty()
    assert page.evaluate("resultsJson") == before_results
    upload_json(page, {"algorithm": "FCFS", "processes": [{"pid": "P1", "arrival_time": 0, "burst_time": 1}]})
    expect(page.locator("#upload-status")).to_contain_text("Loaded")
    expect(page.locator("#file-request-command-error")).to_be_empty()
    expect(page.locator("#file-request-offset")).to_have_value("")
    with page.expect_request("**/api/start"):
        run(page)

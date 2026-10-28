import json
from threading import Thread

import pytest
from playwright.sync_api import Page, Request, Response, expect
from werkzeug.serving import make_server

from app import app


@pytest.fixture(scope="module")
def dashboard_url():
    # Start a test server on an available port.
    server = make_server("127.0.0.1", 0, app)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def prepare_simulation(page: Page, url, algorithm="FCFS"):
    page.goto(url)
    page.get_by_role("button", name="Process Manager", exact=True).click()

    page.locator("#algorithm").select_option(algorithm)
    page.locator("#context-switch-cost").fill("1")

    if algorithm == "RR":
        page.locator("#time-quantum").fill("1")

    if algorithm == "PRIORITY":
        page.locator("#aging-interval").fill("4")

    # P1 has higher priority, while P2 has the shorter burst.
    for pid, burst, priority in [("P1", "2", "1"), ("P2", "1", "2")]:
        page.locator("#process-id").fill(pid)
        page.locator("#arrival-time").fill("2")
        page.locator("#burst-time").fill(burst)
        page.locator("#process-priority").fill(priority)
        page.get_by_role("button", name="Add Process", exact=True).click()


def take_step(page: Page):
    page.locator("#step-button").click()
    # Wait for the request and rendering to finish before another action.
    page.wait_for_function("busy === false")


def run_to_completion(page: Page):
    page.locator("#run-button").click()
    expect(page.locator("#simulation-status")).to_contain_text(
        "Completed", timeout=10000
    )
    page.wait_for_function("busy === false")

def test_srtf_chart_shows_preemption(page: Page, dashboard_url):
    page.goto(dashboard_url)
    page.get_by_role("button", name="Process Manager", exact=True).click()

    page.locator("#algorithm").select_option("SRTF")
    page.locator("#context-switch-cost").fill("0")

    for pid, arrival, burst in [("P1", "0", "5"), ("P2", "1", "1")]:
        page.locator("#process-id").fill(pid)
        page.locator("#arrival-time").fill(arrival)
        page.locator("#burst-time").fill(burst)
        page.locator("#process-priority").fill("1")
        page.get_by_role("button", name="Add Process", exact=True).click()

    run_to_completion(page)

    expect(page.locator("#gantt-chart .gantt-block strong")).to_have_text(
        ["P1", "P2", "P1"]
    )
    expect(page.locator("#gantt-chart .gantt-block small")).to_have_text(
        ["0–1", "1–2", "2–6"]
    )

    rows = page.locator("#process-metrics-body tr")

    expect(rows.nth(0).locator("td")).to_have_text(
        ["P1", "TERMINATED", "0", "5", "0", "6", "1", "6", "0"]
    )
    expect(rows.nth(1).locator("td")).to_have_text(
        ["P2", "TERMINATED", "1", "1", "1", "2", "0", "1", "0"]
    )

    expect(page.locator("#result-average-wait")).to_have_text("0.50")
    expect(page.locator("#result-average-turnaround")).to_have_text("3.50")    

def test_gantt_widths_match_durations(page: Page, dashboard_url):
    prepare_simulation(page, dashboard_url)
    run_to_completion(page)

    blocks = page.locator("#gantt-chart .gantt-block")
    expect(blocks).to_have_count(4)

    widths = []

    for index in range(4):
        box = blocks.nth(index).bounding_box()
        assert box is not None, "Chart block must be displayed"
        widths.append(box["width"])

    assert widths[0] == pytest.approx(widths[1], abs=1)
    assert widths[2] == pytest.approx(widths[3], abs=1)
    assert widths[0] == pytest.approx(2 * widths[2], abs=1)

def test_pause_and_resume_preserves_results(page: Page, dashboard_url):
    start_requests = []

    def record_start_request(request):
        if request.method == "POST" and request.url.endswith("/api/start"):
            start_requests.append(request.url)

    page.on("request", record_start_request)

    prepare_simulation(page, dashboard_url)

    page.locator("#run-button").click()
    expect(page.locator("#pause-button")).to_be_enabled()
    page.locator("#pause-button").click()

    # Allow any in-flight step to finish before checking stability.
    page.wait_for_function("running === false && busy === false")

    expect(page.locator("#step-button")).to_be_enabled()

    time_before = page.locator("#result-current-time").text_content()
    chart_before = page.locator("#gantt-chart").text_content()
    states_before = page.locator("#process-metrics-body").text_content()

    # Deliberately wait longer than the automatic loop's 200 ms delay.
    page.wait_for_timeout(500)

    expect(page.locator("#result-current-time")).to_have_text(time_before)
    expect(page.locator("#gantt-chart")).to_have_text(chart_before)
    expect(page.locator("#process-metrics-body")).to_have_text(states_before)

    run_to_completion(page)

    expect(page.locator("#result-current-time")).to_have_text("6")
    expect(page.locator("#result-average-wait")).to_have_text("1.50")
    expect(page.locator("#result-average-turnaround")).to_have_text("3.00")
    expect(page.locator("#result-utilization")).to_have_text("50.00%")
    expect(page.locator("#result-throughput")).to_have_text("0.333")

    expect(page.locator("#gantt-chart .gantt-block strong")).to_have_text(
        ["Idle", "P1", "Context switch", "P2"]
    )

    assert len(start_requests) == 1


@pytest.mark.parametrize("algorithm", ["FCFS", "SJF", "SRTF", "RR", "PRIORITY"])
def test_completed_visualizations(page: Page, dashboard_url, algorithm):
    prepare_simulation(page, dashboard_url, algorithm)
    run_to_completion(page)

    if algorithm in ["FCFS", "PRIORITY"]:
        expected_time = "6"
        expected_wait = "1.50"
        expected_turnaround = "3.00"
        expected_utilization = "50.00%"
        expected_throughput = "0.333"

        labels = ["Idle", "P1", "Context switch", "P2"]
        intervals = ["0–2", "2–4", "4–5", "5–6"]

        expected_rows = [
            ["P1", "TERMINATED", "2", "2", "2", "4", "0", "2", "0"],
            ["P2", "TERMINATED", "2", "1", "5", "6", "3", "4", "3"],
        ]

    elif algorithm in ["SJF", "SRTF"]:
        expected_time = "6"
        expected_wait = "1.00"
        expected_turnaround = "2.50"
        expected_utilization = "50.00%"
        expected_throughput = "0.333"

        labels = ["Idle", "P2", "Context switch", "P1"]
        intervals = ["0–2", "2–3", "3–4", "4–6"]

        expected_rows = [
            ["P1", "TERMINATED", "2", "2", "4", "6", "2", "4", "2"],
            ["P2", "TERMINATED", "2", "1", "2", "3", "0", "1", "0"],
        ]

    else:  # Round Robin with quantum 1.
        expected_time = "7"
        expected_wait = "2.50"
        expected_turnaround = "4.00"
        expected_utilization = "42.86%"
        expected_throughput = "0.286"

        labels = [
            "Idle", "P1", "Context switch",
            "P2", "Context switch", "P1"
        ]
        intervals = ["0–2", "2–3", "3–4", "4–5", "5–6", "6–7"]

        expected_rows = [
            ["P1", "TERMINATED", "2", "2", "2", "7", "3", "5", "0"],
            ["P2", "TERMINATED", "2", "1", "4", "5", "2", "3", "2"],
        ]

    expected_cards = {
        "result-current-time": expected_time,
        "result-completed": "2 / 2",
        "result-average-wait": expected_wait,
        "result-average-turnaround": expected_turnaround,
        "result-utilization": expected_utilization,
        "result-throughput": expected_throughput,
    }

    for element_id, value in expected_cards.items():
        expect(page.locator("#" + element_id)).to_have_text(value)

    # Verify execution order and interval boundaries.
    expect(page.locator("#gantt-chart .gantt-block strong")).to_have_text(labels)
    expect(page.locator("#gantt-chart .gantt-block small")).to_have_text(intervals)
    expect(page.locator("#gantt-empty")).to_be_hidden()

    # Verify every metric in both process rows.
    rows = page.locator("#process-metrics-body tr")
    expect(rows).to_have_count(2)

    for index, values in enumerate(expected_rows):
        expect(rows.nth(index).locator("td")).to_have_text(values)

    expect(page.locator("#step-button")).to_be_disabled()
    expect(page.locator("#pause-button")).to_be_disabled()
    expect(page.locator("#simulation-error")).to_be_empty()


def test_step_displays_running_and_completed_states(page: Page, dashboard_url):
    prepare_simulation(page, dashboard_url)

    # Dispatch P1.
    take_step(page)

    expect(page.locator("#result-current-time")).to_have_text("2")
    expect(page.locator("#result-completed")).to_have_text("0 / 2")
    expect(page.locator("#process-metrics-body .state-badge")).to_have_text(
        ["RUNNING", "READY"]
    )
    expect(page.locator("#result-average-wait")).to_have_text("—")
    expect(page.locator("#result-average-turnaround")).to_have_text("—")
    expect(page.locator("#gantt-chart .gantt-block strong")).to_have_text(
        ["Idle"]
    )

    # Finish P1.
    take_step(page)

    expect(page.locator("#result-current-time")).to_have_text("4")
    expect(page.locator("#result-completed")).to_have_text("1 / 2")
    expect(page.locator("#process-metrics-body .state-badge")).to_have_text(
        ["TERMINATED", "READY"]
    )
    expect(page.locator("#gantt-chart .gantt-block strong")).to_have_text(
        ["Idle", "P1"]
    )

    # Apply the context switch and dispatch P2.
    take_step(page)

    expect(page.locator("#result-current-time")).to_have_text("5")
    expect(page.locator("#process-metrics-body .state-badge")).to_have_text(
        ["TERMINATED", "RUNNING"]
    )

    # Finish P2.
    take_step(page)

    expect(page.locator("#result-current-time")).to_have_text("6")
    expect(page.locator("#result-completed")).to_have_text("2 / 2")
    expect(page.locator("#step-button")).to_be_disabled()


@pytest.mark.parametrize("reset_point", ["running", "completed"])
def test_reset_clears_visualizations(page: Page, dashboard_url, reset_point):
    prepare_simulation(page, dashboard_url)

    if reset_point == "running":
        take_step(page)
    else:
        run_to_completion(page)

    page.locator("#reset-button").click()
    expect(page.locator("#result-current-time")).to_have_text("0")
    page.wait_for_function("busy === false")

    expect(page.locator("#result-completed")).to_have_text("0 / 2")
    expect(page.locator("#gantt-chart .gantt-block")).to_have_count(0)
    expect(page.locator("#gantt-empty")).to_be_visible()
    expect(page.locator("#step-button")).to_be_enabled()

    for element_id in [
        "result-average-wait",
        "result-average-turnaround",
        "result-utilization",
        "result-throughput",
    ]:
        expect(page.locator("#" + element_id)).to_have_text("—")

    rows = page.locator("#process-metrics-body tr")

    expect(rows.nth(0).locator("td")).to_have_text(
        ["P1", "NEW", "2", "2", "—", "—", "—", "—", "—"]
    )
    expect(rows.nth(1).locator("td")).to_have_text(
        ["P2", "NEW", "2", "1", "—", "—", "—", "—", "—"]
    )

    # Verify stepping works again after reset.
    take_step(page)

    expect(page.locator("#process-metrics-body .state-badge")).to_have_text(
        ["RUNNING", "READY"]
    )


@pytest.mark.parametrize("snapshot", ["partial", "completed", "reset"])
def test_download_results_matches_displayed_snapshot(
    page: Page, dashboard_url: str, snapshot: str
) -> None:
    """Export the displayed backend snapshot without advancing the simulation."""
    prepare_simulation(page, dashboard_url)
    export_button = page.locator("#export-results-button")
    expect(export_button).to_be_disabled()

    requests: list[str] = []
    responses: list[Response] = []

    def record_request(request: Request) -> None:
        if "/api/" in request.url:
            requests.append(request.url)

    def record_response(response: Response) -> None:
        if response.url.endswith(("/api/step", "/api/reset")):
            responses.append(response)

    page.on("request", record_request)
    page.on("response", record_response)

    try:
        with page.expect_response("**/api/step"):
            if snapshot == "completed":
                run_to_completion(page)
            else:
                take_step(page)

        if snapshot == "reset":
            with page.expect_response("**/api/reset"):
                page.locator("#reset-button").click()
                page.wait_for_function("busy === false")

        # Rendering and busy-state synchronization above ensure the last
        # response is the snapshot displayed before the download.
        response = responses[-1]
        endpoint = "/api/reset" if snapshot == "reset" else "/api/step"
        assert response.url.endswith(endpoint)
        assert response.ok
        expected = response.json()
        completed = sum(
            process["state"] == "TERMINATED"
            for process in expected["processes"]
        )
        if snapshot == "completed":
            assert completed == len(expected["processes"]) > 0
        elif snapshot == "partial":
            assert completed < len(expected["processes"])
        else:
            assert expected["current_time"] == 0

        expected_throughput = (
            completed / expected["current_time"]
            if expected["current_time"] > 0 else 0
        )
        assert expected["throughput"] == pytest.approx(expected_throughput)

        request_count = len(requests)
        expect(export_button).to_be_enabled()

        # Repeated exports must preserve the same snapshot and download name.
        for _ in range(2):
            with page.expect_download() as download_info:
                export_button.click()

            download = download_info.value
            assert download.suggested_filename == "simulation-results.json"
            with open(download.path(), encoding="utf-8") as exported_file:
                exported = json.load(exported_file)
            assert exported == expected
            assert exported["throughput"] == pytest.approx(expected_throughput)

        assert len(requests) == request_count
        expect(page.locator("#result-current-time")).to_have_text(
            str(expected["current_time"])
        )
        expect(page.locator("#simulation-error")).to_be_empty()
    finally:
        page.remove_listener("response", record_response)
        page.remove_listener("request", record_request)

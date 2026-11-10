import json

import pytest
from playwright.sync_api import expect

from tests.test_visualization import dashboard_url


def open_comparison(page, url):
    page.goto(url)
    page.get_by_role("button", name="Memory Manager", exact=True).click()


def compare(page):
    page.get_by_role("button", name="Compare FIFO and LRU", exact=True).click()


def test_known_comparison_results_and_independent_inputs(page, dashboard_url):
    open_comparison(page, dashboard_url)
    requests = []
    page.on("request", lambda request: requests.append(request) if "/api/" in request.url else None)
    workload = page.evaluate("processes")
    with page.expect_response("**/api/memory/compare") as response:
        compare(page)
    assert response.value.status == 200
    assert requests[0].post_data_json == {"addresses": [0, 4, 0, 8, 0], "frame_count": 2, "page_size": 4}
    expect(page.locator("#comparison-body tr")).to_have_text([
        "References55", "Hits12", "Faults43", "Evictions21", "Fault rate80.0%60.0%"])
    assert len(requests) == 1
    assert page.evaluate("processes") == workload
    assert page.evaluate("resultsJson") == ""
    expect(page.locator("#export-results-button")).to_be_disabled()
    page.locator("#comparison-addresses").fill("0 4\t0 8 0")
    expect(page.locator("#comparison-results")).to_be_hidden()
    compare(page)
    expect(page.locator("#comparison-status")).to_contain_text("Comparison complete")


@pytest.mark.parametrize("field,value", [
    ("addresses", ""), ("addresses", "0, -1"), ("addresses", "0, 1.5"),
    ("addresses", "0,,4"), ("addresses", "true"),
    ("frame-count", "0"), ("frame-count", "1.5"), ("page-size", "-4"),
])
def test_invalid_comparison_does_not_send_request(page, dashboard_url, field, value):
    open_comparison(page, dashboard_url)
    requests = []
    page.on("request", lambda request: requests.append(request) if "/api/" in request.url else None)
    page.locator("#comparison-" + field).fill(value)
    compare(page)
    expect(page.locator("#comparison-" + field)).to_have_attribute("aria-invalid", "true")
    expect(page.locator("#comparison-" + field + "-error")).not_to_be_empty()
    expect(page.locator("#comparison-results")).to_be_hidden()
    assert requests == []


@pytest.mark.parametrize("status,body", [(400, '{"error":"Invalid trace"}'), (503, "Unavailable")])
def test_failed_comparison_clears_old_results_and_allows_retry(page, dashboard_url, status, body):
    open_comparison(page, dashboard_url)
    compare(page)
    expect(page.locator("#comparison-results")).to_be_visible()
    page.route("**/api/memory/compare", lambda route: route.fulfill(status=status, body=body))
    compare(page)
    expect(page.locator("#comparison-error")).to_contain_text("Comparison failed:")
    expect(page.locator("#comparison-error")).to_contain_text("Invalid trace" if status == 400 else "Unavailable")
    expect(page.locator("#comparison-results")).to_be_hidden()
    expect(page.locator("#compare-memory")).to_be_enabled()
    page.unroute("**/api/memory/compare")
    compare(page)
    expect(page.locator("#comparison-results")).to_be_visible()


def test_comparison_preserves_paused_simulation_and_exports(page, dashboard_url):
    open_comparison(page, dashboard_url)
    page.locator("#load-memory-test").click()
    page.get_by_role("button", name="Process Manager", exact=True).click()
    page.locator("#algorithm").select_option("RR")
    page.locator("#time-quantum").fill("1")
    for _ in range(2):
        page.locator("#step-button").click()
        page.wait_for_function("busy === false")
    before = page.evaluate("({snapshot: resultsJson, input: collectSimulationInput(), saved: savedInput, started})")
    assert json.loads(before["snapshot"])["memory_metrics"]["references"] == 1
    status = page.locator("#simulation-status").text_content()
    page.get_by_role("button", name="Memory Manager", exact=True).click()
    frames = page.locator("#memory-frame-grid").inner_html()
    expect(page.locator("#export-memory-csv")).to_be_enabled()
    requests = []
    page.on("request", lambda request: requests.append(request.url) if "/api/" in request.url else None)
    compare(page)
    expect(page.locator("#comparison-results")).to_be_visible()
    assert requests == [dashboard_url + "/api/memory/compare"]
    assert page.evaluate("({snapshot: resultsJson, input: collectSimulationInput(), saved: savedInput, started})") == before
    assert page.locator("#simulation-status").text_content() == status
    assert page.locator("#memory-frame-grid").inner_html() == frames
    expect(page.locator("#export-memory-csv")).to_be_enabled()
    page.get_by_role("button", name="Process Manager", exact=True).click()
    with page.expect_download() as download:
        page.locator("#export-results-button").click()
    assert json.loads(download.value.path().read_text(encoding="utf-8")) == json.loads(before["snapshot"])
    page.locator("#step-button").click()
    page.wait_for_function("busy === false")
    assert page.evaluate("resultsJson") != before["snapshot"]


def test_pending_comparison_locks_only_its_own_controls(page, dashboard_url):
    open_comparison(page, dashboard_url)
    page.evaluate("""() => {
        const original = window.fetch;
        window.fetch = (...args) => args[0] === '/api/memory/compare'
            ? new Promise(resolve => {
                window.releaseComparison = () => resolve(original(...args));
            }) : original(...args);
    }""")
    compare(page)
    expect(page.locator("#compare-memory")).to_be_disabled()
    expect(page.locator("#comparison-addresses")).to_be_disabled()
    expect(page.locator("#comparison-status")).to_contain_text("Comparing")
    expect(page.locator("#memory-frame-count")).to_be_enabled()
    assert page.evaluate("busy || running") is False
    page.evaluate("releaseComparison()")
    expect(page.locator("#comparison-results")).to_be_visible()
    expect(page.locator("#compare-memory")).to_be_enabled()

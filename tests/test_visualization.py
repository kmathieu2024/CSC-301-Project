from threading import Thread

import pytest
from playwright.sync_api import Page, expect
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
    for pid, burst, priority in [("P1", "2", "0"), ("P2", "1", "1")]:
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
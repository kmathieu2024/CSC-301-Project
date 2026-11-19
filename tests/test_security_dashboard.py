"""Chromium tests of complete, atomic, and asynchronous Security imports."""

from copy import deepcopy
import json
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

import app as simulator_api
from tests.test_network_api import workload
from tests.test_network_dashboard import network_url, load, run, tab


def policy() -> dict:
    """A valid policy different from the initial editor contents."""
    return {"enabled": True, "roles": [{"name": "worker", "permissions": [
        "file:create", "device:read", "network:send"]}],
        "users": [{"username": "P", "roles": ["worker"]}]}


def state(page: Page) -> dict:
    """Observe accepted inputs, drafts, results, and execution status read-only."""
    return page.evaluate("""() => ({
        processes: structuredClone(processes),
        devices: structuredClone(configuredDevices),
        network: structuredClone(configuredNetwork),
        security: document.getElementById('security-config').value,
        resultsJson, savedInput, started, busy, running,
        status: document.getElementById('simulation-status').textContent,
        filter: document.getElementById('network-history-filter').value,
        draft: document.getElementById('network-node-id').value,
        exported: document.getElementById('export-results-button').disabled
    })""")


@pytest.mark.parametrize("network", [False, True])
def test_explicit_policy_replaces_editor_and_controls_execution(
    page: Page, network_url: str, network: bool,
) -> None:
    page.goto(network_url)
    data = workload()
    data["security"] = policy()
    if not network:
        data.pop("network")
        data["processes"][0].pop("network_requests")
        data["processes"][0]["file_requests"] = {"0": "CREATE /allowed"}
    load(page, data)
    assert json.loads(page.locator("#security-config").input_value()) == policy()
    result = run(page)
    assert result["security_enabled"] is True
    assert [e["outcome"] for e in result["security_events"]] == ["allowed"]
    assert result["security_events"][0]["resource_type"] == (
        "network" if network else "file"
    )


@pytest.mark.parametrize("explicit", [False, True])
def test_omitted_policy_preserves_editor_and_explicit_disabled_takes_precedence(
    page: Page, network_url: str, explicit: bool,
) -> None:
    page.goto(network_url)
    text = json.dumps(policy(), indent=4)
    tab(page, "Security")
    page.locator("#security-config").fill(text)
    data = workload()
    if explicit:
        data["security"] = {"enabled": False, "roles": "unused", "users": 42}
    load(page, data)
    if explicit:
        actual = json.loads(page.locator("#security-config").input_value())
        assert actual == data["security"]
    else:
        assert page.locator("#security-config").input_value() == text
    assert run(page)["security_enabled"] is not explicit


@pytest.mark.parametrize("bad", ["role", "permission", "enabled", "device"])
def test_invalid_candidate_preserves_results_drafts_and_accepted_engine(
    page: Page, network_url: str, bad: str,
) -> None:
    page.goto(network_url)
    load(page, workload())
    run(page)
    tab(page)
    page.locator("#network-node-id").fill("unfinished-node")
    page.locator("#network-history-filter").fill("packet")
    accepted = simulator_api.engine
    callbacks = deepcopy(accepted.event_queue)
    before = state(page)
    candidate = workload()
    candidate["security"] = policy()
    if bad == "role":
        candidate["security"]["users"][0]["roles"] = ["missing"]
    elif bad == "permission":
        candidate["security"]["roles"][0]["permissions"] = ["invalid"]
    elif bad == "enabled":
        candidate["security"]["enabled"] = "true"
    else:
        candidate["devices"] = [{"device_id": "bad", "device_type": "unknown"}]
    error = page.evaluate("""async data => {
        try { await loadWorkload(data); return null; } catch(e) { return e.message; }
    }""", candidate)
    assert error
    assert state(page) == before
    assert simulator_api.engine is accepted
    assert accepted.event_queue == callbacks
    expect(page.locator("#export-results-button")).to_be_enabled()


def begin(page: Page, candidate: dict) -> None:
    """Dispatch an import while letting the test control its HTTP response."""
    with page.expect_request("**/api/validate"):
        page.evaluate("""data => {
            window.importAttemptCount += 1;
            window.importResult = 'pending';
            void loadWorkload(data).then(() => window.importResult = 'accepted')
                .catch(e => window.importResult = e.message);
        }""", candidate)
    page.wait_for_function("window.validationIntercepted >= window.importAttemptCount")


def hold_validations(page: Page) -> list:
    """Wait for actual route interception, not just the earlier request event."""
    routes = []
    page.evaluate("window.validationIntercepted = 0; window.importAttemptCount = 0")

    def hold(route):
        routes.append(route)
        page.evaluate("window.validationIntercepted += 1")

    page.route("**/api/validate", hold)
    return routes


@pytest.mark.parametrize("activity", [
    "security_draft", "node_draft", "step", "reset", "pause",
])
def test_pending_validation_cannot_overwrite_new_inputs_or_execution(
    page: Page, network_url: str, activity: str,
) -> None:
    page.goto(network_url)
    load(page, workload())
    routes = hold_validations(page)
    data = workload()
    data["security"] = policy()
    begin(page, data)
    if activity == "security_draft":
        tab(page, "Security")
        page.locator("#security-config").fill('{"enabled": false, "roles": []}')
    elif activity == "node_draft":
        tab(page)
        page.locator("#network-node-id").fill("keep-new-draft")
    elif activity == "step":
        tab(page, "Process")
        page.locator("#step-button").click()
        page.wait_for_function("busy === false && started === true")
    elif activity == "reset":
        page.evaluate("resetSimulation()")
    else:
        page.evaluate("pauseSimulation()")
    before = state(page)
    routes[0].fulfill(json={"valid": True})
    page.wait_for_function("window.importResult !== 'pending'")
    assert "superseded" in page.evaluate("window.importResult")
    assert state(page) == before


def test_explicit_policy_replaces_malformed_editor_without_using_it(
    page: Page, network_url: str,
) -> None:
    page.goto(network_url)
    tab(page, "Security")
    page.locator("#security-config").fill("{unfinished")
    data = workload()
    data["security"] = policy()
    load(page, data)
    assert json.loads(page.locator("#security-config").input_value()) == policy()


def test_candidate_is_detached_from_callers_during_validation(
    page: Page, network_url: str,
) -> None:
    page.goto(network_url)
    routes = hold_validations(page)
    data = workload()
    data["security"] = policy()
    with page.expect_request("**/api/validate"):
        page.evaluate("""data => {
            window.importAttemptCount += 1;
            window.callerCandidate = data;
            window.importResult = 'pending';
            void loadWorkload(data).then(() => window.importResult = 'accepted')
                .catch(e => window.importResult = e.message);
        }""", data)
    page.wait_for_function("window.validationIntercepted === 1")
    page.evaluate("window.callerCandidate.security.enabled = false")
    routes[0].fulfill(json={"valid": True})
    page.wait_for_function("window.importResult === 'accepted'")
    assert json.loads(page.locator("#security-config").input_value()) == policy()


def test_newer_import_wins_when_validation_responses_arrive_in_reverse_order(
    page: Page, network_url: str,
) -> None:
    page.goto(network_url)
    routes = hold_validations(page)
    first = workload()
    first["security"] = policy()
    begin(page, first)
    second = workload()
    second["processes"][0]["pid"] = "newest"
    second["security"] = {"enabled": False}
    begin(page, second)
    routes[1].fulfill(json={"valid": True})
    page.wait_for_function("processes.length === 1 && processes[0].pid === 'newest'")
    before = state(page)
    routes[0].fulfill(json={"valid": True})
    page.wait_for_function("String(window.importResult).includes('superseded')")
    assert state(page) == before


def test_file_preview_explains_backend_validation_requirement(page: Page) -> None:
    dashboard = Path(__file__).resolve().parents[1] / "dashboard" / "index.html"
    page.goto(dashboard.as_uri())
    before = state(page)
    error = page.evaluate("""async data => {
        try { await loadWorkload(data); } catch(e) { return e.message; }
    }""", workload())
    assert "through Flask" in error
    assert state(page) == before


def test_draft_change_invalidates_demo_before_its_file_finishes_loading(
    page: Page, network_url: str,
) -> None:
    page.goto(network_url)
    load(page, workload())
    tab(page)
    routes = []

    def hold(route):
        routes.append(route)
        page.evaluate("window.demoIntercepted = true")

    page.route("**/network-demo.json", hold)
    page.get_by_role("button", name="Load network failure demonstration").click()
    page.wait_for_function("window.demoIntercepted === true")
    page.locator("#network-node-id").fill("newer-draft")
    before = state(page)
    routes[0].fulfill(json=workload())
    expect(page.locator("#network-demo-status")).to_contain_text("superseded")
    assert state(page) == before

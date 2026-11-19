"""Deferred real validation responses cover editor actions and upload ownership."""

from copy import deepcopy
import json

import pytest
from playwright.sync_api import Page, expect

import app as simulator_api
from tests.test_network_api import workload
from tests.test_network_dashboard import network_url, load, run, tab
from tests.test_security_dashboard import state


def combined_workload() -> dict:
    """Provide accepted requests for every existing request editor."""
    data = workload()
    data["memory"] = {"enabled": True, "frame_count": 1, "page_size": 4}
    data["devices"] = [{"device_id": "kbd", "device_type": "KEYBOARD",
                        "default_service_time": 0}]
    data["network"]["events"] = [
        {"timestamp": 0, "action": "LINK_UP", "link_id": "ab"},
    ]
    data["processes"][0].update(
        memory_references={"0": 0}, file_requests={"0": "CREATE /once"},
        device_requests={"0": {"device_id": "kbd", "operation": "READ"}},
    )
    return data


def editor_state(page: Page) -> dict:
    """Capture modes, field state, accepted configuration, and rendered results."""
    return {"accepted": state(page), "editors": page.evaluate("""() => ({
        networkEditing: structuredClone(networkEditing),
        file: fileRequestEdit && {pid:fileRequestEdit.process.pid,
                                 offset:fileRequestEdit.offset},
        memory: referenceEdit && {pid:referenceEdit.process.pid,
                                 offset:referenceEdit.offset},
        fields: Array.from(document.querySelectorAll(
            '#process-form input, #memory input, #memory select, ' +
            '#file-request-form input, #file-request-form textarea, ' +
            '#file-request-form select, #device form input, #device form select, ' +
            '#network form input, #network form select'), field =>
            [field.id, field.value, field.checked, field.disabled, field.readOnly]),
        results: ['.simulation-results', '#network-summary',
                  '#network-history-body', '#file-tree-container',
                  '#memory-frame-grid'].map(selector =>
                    document.querySelector(selector)?.innerHTML),
        generation: workloadImportGeneration
    })""")}


def defer_validations(page: Page) -> list:
    """Validate through Flask now, but release each actual HTTP response later."""
    held = []
    page.evaluate("window.raceValidationCount = 0")

    def hold(route):
        response = route.fetch()
        held.append((route, response))
        page.evaluate("window.raceValidationCount += 1")

    page.route("**/api/validate", hold)
    return held


def begin_import(page: Page, candidate: dict) -> None:
    """Dispatch without blocking Python on the held response."""
    page.evaluate("""data => {
        window.raceImport = 'pending';
        void loadWorkload(data).then(() => window.raceImport = 'accepted')
            .catch(e => window.raceImport = e.message);
    }""", candidate)
    page.wait_for_function("window.raceValidationCount === 1")


@pytest.mark.parametrize("action", [
    "network_link", "network_node", "network_request", "network_event",
    "file_edit", "memory_edit", "file_cancel", "memory_cancel",
    "network_clear", "device_clear", "device_request_clear", "device_edit",
])
def test_editor_action_invalidates_pending_import_without_losing_newer_state(
    page: Page, network_url: str, action: str,
) -> None:
    page.goto(network_url)
    load(page, combined_workload())
    run(page)
    if action == "file_cancel":
        tab(page, "File")
        page.get_by_role("button", name="Edit file request P offset 0").click()
    elif action == "memory_cancel":
        tab(page, "Memory")
        page.get_by_role("button", name="Edit reference P offset 0").click()
    elif action == "network_clear":
        tab(page)
        page.get_by_role("button", name="Edit link ab", exact=True).click()
    elif action == "device_clear":
        tab(page, "Device")
        page.locator("#device-id").fill("draft-device")
        page.locator("#device-id").press("Tab")
    elif action == "device_request_clear":
        tab(page, "Device")
        page.locator("#device-request-service").fill("9")
        page.locator("#device-request-service").press("Tab")
    accepted = simulator_api.engine
    callbacks = deepcopy(accepted.event_queue)
    accepted_inputs = page.evaluate("collectSimulationInput()")
    held = defer_validations(page)
    candidate = workload()
    candidate["security"] = {"enabled": False}
    begin_import(page, candidate)
    generation = page.evaluate("workloadImportGeneration")
    actions = {
        "network_link": ("Network", "Edit link ab"),
        "network_node": ("Network", "Edit node a"),
        "network_request": ("Network", "Edit network request P offset 0"),
        "network_event": ("Network", "Edit topology event 1"),
        "file_edit": ("File", "Edit file request P offset 0"),
        "memory_edit": ("Memory", "Edit reference P offset 0"),
    }
    if action in actions:
        manager, label = actions[action]
        tab(page, manager)
        page.get_by_role("button", name=label, exact=True).click()
    else:
        controls = {
            "file_cancel": "#file-request-cancel",
            "memory_cancel": "#reference-cancel",
            "network_clear": "#network-link-clear",
            "device_clear": "#device-clear-draft",
            "device_request_clear": "#device-request-clear-draft",
        }
        if action == "device_edit":
            tab(page, "Device")
            # Device has no Edit button; changing type edits its actual draft
            # and also populates policy/geometry controls programmatically.
            page.locator("#device-type").select_option("PRINTER")
        else:
            page.locator(controls[action]).click()
    after_action = page.evaluate("workloadImportGeneration")
    assert after_action > generation
    if action != "device_edit":
        assert after_action == generation + 1
    before = editor_state(page)
    assert page.evaluate("collectSimulationInput()") == accepted_inputs
    route, response = held[0]
    assert response.status == 200
    route.fulfill(response=response)
    page.wait_for_function("window.raceImport !== 'pending'")
    assert "superseded" in page.evaluate("window.raceImport")
    assert editor_state(page) == before
    assert simulator_api.engine is accepted
    assert accepted.event_queue == callbacks
    expect(page.locator("#export-results-button")).to_be_enabled()


def test_navigation_and_read_only_actions_do_not_invalidate_authorized_commit(
    page: Page, network_url: str,
) -> None:
    page.goto(network_url)
    load(page, combined_workload())
    held = defer_validations(page)
    candidate = workload()
    candidate["security"] = {"enabled": False}
    begin_import(page, candidate)
    generation = page.evaluate("workloadImportGeneration")
    for manager in ("File", "Memory", "Device", "Network", "Security", "Process"):
        tab(page, manager)
    tab(page, "Memory")
    page.locator("#comparison-addresses").fill("0, 4")
    page.locator("#comparison-frame-count").fill("3")
    tab(page)
    page.get_by_role("region", name="Packet history", exact=True).focus()
    page.locator("#network-history-filter").fill("read-only packet filter")
    assert page.evaluate("workloadImportGeneration") == generation
    held[0][0].fulfill(response=held[0][1])
    page.wait_for_function("window.raceImport === 'accepted'")
    # Form resets and result invalidation during commit preserve this ticket.
    assert page.evaluate("workloadImportGeneration") == generation
    assert page.evaluate("collectSimulationInput().memory.enabled") is False
    assert page.evaluate("configuredDevices") == []
    expect(page.locator("#network-history-filter")).to_have_value(
        "read-only packet filter",
    )


@pytest.mark.parametrize("action", [
    "invalid_file", "invalid_memory", "invalid_device", "invalid_process",
    "valid_network",
])
def test_editor_submissions_invalidate_once_and_preserve_their_newer_state(
    page: Page, network_url: str, action: str,
) -> None:
    page.goto(network_url)
    load(page, combined_workload())
    held = defer_validations(page)
    begin_import(page, workload())
    generation = page.evaluate("workloadImportGeneration")
    controls = {
        "invalid_file": ("File", "#file-request-save"),
        "invalid_memory": ("Memory", "#reference-save"),
        "invalid_device": ("Device", "#device-form button[type=submit]"),
        "invalid_process": ("Process", "#process-form button[type=submit]"),
        "valid_network": ("Network", "#network-settings-form button[type=submit]"),
    }
    manager, selector = controls[action]
    tab(page, manager)
    page.locator(selector).click()
    assert page.evaluate("workloadImportGeneration") == generation + 1
    before = editor_state(page)
    held[0][0].fulfill(response=held[0][1])
    page.wait_for_function("window.raceImport !== 'pending'")
    assert "superseded" in page.evaluate("window.raceImport")
    assert editor_state(page) == before


def upload(page: Page, identity: str, filename: str, invalid: bool = False) -> None:
    """Use the real file input without awaiting the deliberately held response."""
    data = workload()
    data["processes"][0]["pid"] = identity
    if invalid:
        data["security"] = {"enabled": True, "roles": [],
                            "users": [{"username": identity, "roles": ["missing"]}]}
    page.locator("#process-file").set_input_files({
        "name": filename, "mimeType": "application/json",
        "buffer": json.dumps(data).encode(),
    })


def observe_upload_completion(page: Page) -> None:
    """Observe the upload caller's continuation without timer-based delays."""
    page.evaluate("""() => {
        window.finishedUploads = [];
        const original = loadWorkload;
        loadWorkload = (...args) => {
            const pending = original(...args);
            // This reaction is registered before the upload caller awaits it.
            // Its queued microtask runs after that caller's catch/finally.
            const settled = () => queueMicrotask(() =>
                window.finishedUploads.push(args[0].processes[0].pid));
            pending.then(settled, settled);
            return pending;
        };
    }""")


@pytest.mark.parametrize("same_filename", [False, True])
@pytest.mark.parametrize("first_finishes_first", [False, True])
@pytest.mark.parametrize("old_invalid", [False, True])
def test_upload_feedback_and_cleanup_belong_to_the_current_selection(
    page: Page, network_url: str, same_filename: bool, first_finishes_first: bool,
    old_invalid: bool,
) -> None:
    page.goto(network_url)
    tab(page, "Process")
    held = defer_validations(page)
    observe_upload_completion(page)
    upload(page, "A", "first.json", invalid=old_invalid)
    page.wait_for_function("window.raceValidationCount === 1")
    latest_name = "first.json" if same_filename else "latest.json"
    upload(page, "B", latest_name)
    page.wait_for_function("window.raceValidationCount === 2")
    assert held[0][1].status == (400 if old_invalid else 200)
    assert held[1][1].status == 200
    if first_finishes_first:
        old_route, old_response = held[0]
        with page.expect_response("**/api/validate"):
            old_route.fulfill(response=old_response)
        page.wait_for_function("window.finishedUploads.includes('A')")
        assert page.locator("#process-file").evaluate(
            "input => input.files[0]?.name"
        ) == latest_name
        expect(page.locator("#process-error")).to_be_empty()
    held[1][0].fulfill(response=held[1][1])
    expect(page.locator("#upload-status")).to_have_text(
        f"Loaded 1 processes from {latest_name}.",
    )
    expect(page.locator("#process-file")).to_have_value("")
    if not first_finishes_first:
        with page.expect_response("**/api/validate"):
            held[0][0].fulfill(response=held[0][1])
        page.wait_for_function("window.finishedUploads.includes('A')")
    expect(page.locator("#process-error")).to_be_empty()
    expect(page.locator("#upload-status")).to_have_text(
        f"Loaded 1 processes from {latest_name}.",
    )
    assert page.evaluate("processes[0].pid") == "B"


def test_current_upload_failure_remains_actionable_and_cleans_its_own_file(
    page: Page, network_url: str,
) -> None:
    page.goto(network_url)
    load(page, workload())
    before = state(page)
    held = defer_validations(page)
    data = workload()
    data["security"] = {"enabled": True, "roles": [],
                        "users": [{"username": "P", "roles": ["missing"]}]}
    page.locator("#process-file").set_input_files({
        "name": "invalid.json", "mimeType": "application/json",
        "buffer": json.dumps(data).encode(),
    })
    page.wait_for_function("window.raceValidationCount === 1")
    route, response = held[0]
    assert response.status == 400
    route.fulfill(response=response)
    expect(page.locator("#process-error")).to_contain_text(
        "Every assigned role must already exist",
    )
    expect(page.locator("#process-file")).to_have_value("")
    assert state(page) == before

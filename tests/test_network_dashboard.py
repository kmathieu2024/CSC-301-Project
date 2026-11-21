"""Chromium coverage of the real Network workspace and existing Flask controls."""

from collections.abc import Iterator
import json
from pathlib import Path
from threading import Thread
from typing import Any

import pytest
from playwright.sync_api import Page, expect
from werkzeug.serving import make_server

import app as simulator_api
from tests.test_dashboard import enter_process, upload_json
from tests.test_network_api import workload, event, failure_demo


@pytest.fixture(scope="module")
def network_url() -> Iterator[str]:
    """Serve actual dashboard modules on an isolated local port."""
    server = make_server("127.0.0.1", 0, simulator_api.app)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    thread.join()
    server.server_close()


def tab(page: Page, name: str = "Network") -> None:
    page.get_by_role("button", name=name + " Manager", exact=True).click()


@pytest.mark.parametrize("parallel_count", [4, 7])
@pytest.mark.parametrize("node_down", [False, True])
def test_parallel_and_self_link_geometry_is_distinct_and_reproducible(
    page: Page, network_url: str, parallel_count: int, node_down: bool,
) -> None:
    """Real SVG rendering separates lanes, loops, directions, and availability."""
    page.goto(network_url)
    tab(page)
    nodes = {"a": {"state": "UP"},
             "b": {"state": "DOWN" if node_down else "UP"}}
    links = {}
    for source, destination, count in (
        ("a", "b", parallel_count), ("b", "a", parallel_count),
        ("a", "a", 3), ("b", "b", 3),
    ):
        for index in range(count):
            links[f"{source}{destination}-{index}"] = {
                "config": {"source": source, "destination": destination},
                "state": "DOWN" if index == 0 else "UP",
            }

    def render(topology: dict[str, Any]) -> dict[str, Any]:
        return page.evaluate("""data => {
            renderNetworkGraph(data.nodes, data.links);
            const svg = document.getElementById('network-graph');
            return {html: svg.outerHTML, edges: Array.from(
                svg.querySelectorAll('path[data-link-id]'), edge => {
                    const label = edge.nextElementSibling;
                    return {id: edge.dataset.linkId, path: edge.getAttribute('d'),
                        label: [label.getAttribute('x'), label.getAttribute('y')],
                        stroke: edge.getAttribute('stroke'),
                        dashed: edge.getAttribute('stroke-dasharray'),
                        marker: edge.getAttribute('marker-end')};
                })};
        }""", topology)

    topology = {"nodes": nodes, "links": links}
    before = json.dumps(topology, sort_keys=True)
    first = render(topology)
    edges = first["edges"]
    assert len(edges) == len(links)
    assert len({edge["path"] for edge in edges}) == len(links)
    assert len({tuple(edge["label"]) for edge in edges}) == len(links)
    for edge in edges:
        link = links[edge["id"]]
        available = link["state"] == "UP" and all(
            nodes[node]["state"] == "UP" for node in link["config"].values()
        )
        assert edge["stroke"] == ("#25634b" if available else "#b42318")
        assert edge["dashed"] == ("" if available else "6 4")
        assert edge["marker"] == (
            "url(#network-arrow-up)" if available else "url(#network-arrow-down)"
        )
    assert render(topology) == first
    reversed_topology = {
        "nodes": dict(reversed(list(nodes.items()))),
        "links": dict(reversed(list(links.items()))),
    }
    assert render(reversed_topology) == first
    assert json.dumps(topology, sort_keys=True) == before
    expect(page.locator("#network-graph")).to_be_visible()


def run(page: Page) -> dict[str, Any]:
    tab(page, "Process")
    page.locator("#run-button").click()
    expect(page.locator("#simulation-status")).to_contain_text(
        "Completed", timeout=30000,
    )
    page.wait_for_function("busy === false", timeout=30000)
    return page.evaluate("JSON.parse(resultsJson)")


def load(page: Page, data: dict[str, Any]) -> None:
    tab(page, "Process")
    upload_json(page, data)
    expect(page.locator("#upload-status")).to_contain_text("Loaded")


def node(page: Page, identity: str) -> None:
    page.get_by_label("Node ID", exact=True).fill(identity)
    page.get_by_role("button", name="Save node", exact=True).click()


def test_navigation_empty_state_labels_and_initialization(
    page: Page, network_url: str,
) -> None:
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(network_url)
    tab(page)
    heading = page.get_by_role("heading", name="Network Manager", exact=True)
    expect(heading).to_be_visible()
    expect(page.locator("#network-result-status")).to_contain_text("disabled")
    expect(page.get_by_label("Enable networking", exact=True)).not_to_be_checked()
    for label in ("Node ID", "Link ID", "Packet process", "Topology timestamp"):
        expect(page.get_by_label(label, exact=True)).to_be_visible()
    history = page.get_by_role("region", name="Packet history", exact=True)
    expect(history).to_be_visible()
    expect(page.locator("#network-history-body")).to_contain_text("No packets")
    assert errors == []


def test_forms_nodes_links_requests_events_edit_remove_and_run(
    page: Page, network_url: str,
) -> None:
    page.goto(network_url)
    tab(page, "Process")
    enter_process(page, pid="P", burst="1")
    tab(page)
    page.get_by_label("Enable networking", exact=True).check()
    node(page, "a")
    node(page, "b")
    page.get_by_label("Link ID", exact=True).fill("ab")
    page.get_by_label("Link source", exact=True).select_option("a")
    page.get_by_label("Link destination", exact=True).select_option("b")
    page.get_by_label("Link queue policy", exact=True).select_option("PRIORITY")
    page.get_by_role("button", name="Save link", exact=True).click()
    page.get_by_role("button", name="Edit node a", exact=True).click()
    page.get_by_label("Node address (optional)", exact=True).fill("host-a")
    page.get_by_role("button", name="Save node", exact=True).click()
    page.get_by_role("button", name="Edit link ab", exact=True).click()
    page.get_by_label("Propagation delay (time units)", exact=True).fill("0.5")
    page.get_by_role("button", name="Save link", exact=True).click()
    page.get_by_label("Packet source", exact=True).select_option("a")
    page.get_by_label("Packet destination", exact=True).select_option("b")
    page.get_by_role("button", name="Save request", exact=True).click()
    page.get_by_role(
        "button", name="Edit network request P offset 0", exact=True,
    ).click()
    page.get_by_label("Packet size (bytes)", exact=True).fill("200")
    page.get_by_role("button", name="Save request", exact=True).click()
    page.get_by_label("Topology timestamp", exact=True).fill("4")
    page.get_by_label("Topology action", exact=True).select_option("LINK_UP")
    page.get_by_label("Topology target", exact=True).select_option("ab")
    page.get_by_role("button", name="Save event", exact=True).click()
    page.get_by_role("button", name="Edit topology event 1", exact=True).click()
    page.get_by_label("Topology timestamp", exact=True).fill("5")
    page.get_by_role("button", name="Save event", exact=True).click()
    result = run(page)
    assert result["current_time"] == 5  # CPU finishes 3.5; control remains at 5.
    assert result["network_metrics"]["delivered"] == 1
    assert result["network_packets"]["packet-0"]["spec"]["size_bytes"] == 200
    tab(page)
    expect(page.locator("#network-link-body")).to_contain_text("PRIORITY")
    expect(page.locator("#network-graph")).to_be_visible()
    expect(page.locator("#network-node-status-body")).to_contain_text("host-a")
    expect(page.locator("#network-outcomes-body")).to_contain_text("DELIVERED")
    page.get_by_role("button", name="Remove link ab", exact=True).click()
    expect(page.locator("#network-link-error")).not_to_be_empty()
    expect(page.locator("#network-link-body")).to_contain_text("ab")
    page.get_by_role("button", name="Remove topology event 1", exact=True).click()
    page.get_by_role(
        "button", name="Remove network request P offset 0", exact=True,
    ).click()
    page.get_by_role("button", name="Remove link ab", exact=True).click()
    page.get_by_role("button", name="Remove node a", exact=True).click()
    expect(page.locator("#network-node-body")).not_to_contain_text("host-a")


def test_demo_uses_actual_results_and_reset_replays(
    page: Page, network_url: str,
) -> None:
    page.goto(network_url)
    tab(page)
    page.get_by_role(
        "button", name="Load network failure demonstration", exact=True,
    ).click()
    expect(page.locator("#network-demo-status")).to_contain_text("Loaded")
    result = run(page)
    assert result["current_time"] == 3.5
    assert result["network_packets"]["packet-1"]["path"] == ["ac", "cb"]
    assert result["network_outcomes"]["packet-0"]["timestamp"] == 0.5
    assert result["network_outcomes"]["packet-1"]["timestamp"] == 2.5
    assert [result["network_metrics"]["links"][key]["busy_time"]
            for key in ("ab", "ac", "cb")] == [0.5, 1, 1]
    tab(page)
    expect(page.locator("#network-result-status")).to_contain_text("Completed")
    expect(page.locator("#network-history-body")).to_contain_text("ac → cb")
    expect(page.locator("#network-history-body")).to_contain_text("LINK_FAILURE")
    expect(page.locator("#network-timeline")).to_contain_text("0.5–1.5")
    expect(page.locator("#network-timeline")).to_contain_text("Queue waiting")
    expect(page.locator("#network-events-body")).to_contain_text("LINK_DOWN")
    assert page.locator("#network-graph path[marker-end]").count() == 3
    tab(page, "Process")
    page.locator("#reset-button").click()
    expect(page.locator("#network-history-body")).to_contain_text("No packets")
    assert run(page) == result


def test_step_partial_metrics_and_mutation_lock(page: Page, network_url: str) -> None:
    page.goto(network_url)
    load(page, workload())
    page.locator("#step-button").click()
    page.wait_for_function("busy === false && started === true")
    tab(page)
    expect(page.locator("#network-result-status")).to_contain_text("Pending")
    tab(page, "Process")
    page.locator("#step-button").click()
    page.wait_for_function("busy === false")
    tab(page)
    expect(page.locator("#network-result-status")).to_contain_text("Partial")
    expect(page.locator("#network-link-status-body")).to_contain_text("TRANSMITTING")
    tab(page, "Process")
    page.locator("#run-button").click()
    page.wait_for_function("busy === true")
    assert page.locator("#network-node-id").is_disabled()
    assert page.locator("#network-event-save").is_disabled()
    assert page.locator("#load-network-demo").is_disabled()
    expect(page.locator("#simulation-status")).to_contain_text(
        "Completed", timeout=30000,
    )
    page.wait_for_function("busy === false")
    assert page.locator("#network-node-id").is_enabled()


def test_invalid_form_edits_preserve_configuration_and_results(
    page: Page, network_url: str,
) -> None:
    page.goto(network_url)
    load(page, workload())
    result = run(page)
    tab(page)
    before = page.evaluate("JSON.stringify(collectSimulationInput())")
    page.get_by_role("button", name="Edit link ab", exact=True).click()
    page.get_by_label("Bandwidth (bytes / time unit)", exact=True).fill("0")
    page.get_by_role("button", name="Save link", exact=True).click()
    expect(page.locator("#network-link-error")).to_contain_text("positive")
    assert page.evaluate("JSON.stringify(collectSimulationInput())") == before
    assert page.evaluate("JSON.parse(resultsJson)") == result
    page.get_by_role("button", name="Clear link draft", exact=True).click()
    page.get_by_label("Enable networking", exact=True).click()
    expect(page.get_by_label("Enable networking", exact=True)).to_be_checked()
    expect(page.locator("#network-settings-error")).to_contain_text("enabled")
    assert page.evaluate("JSON.parse(resultsJson)") == result


@pytest.mark.parametrize("kind", ["node", "link", "policy_array", "policy_object",
                                  "policy_bool", "policy_null", "offset", "request",
                                  "event_time", "event_action", "event_target",
                                  "disabled", "nonfinite"])
def test_rejected_import_is_atomic_including_filters_drafts_and_export(
    page: Page, network_url: str, kind: str,
) -> None:
    page.goto(network_url)
    load(page, workload())
    run(page)
    tab(page)
    page.locator("#network-node-id").fill("draft-node")
    page.locator("#network-history-filter").fill("packet-0")
    before = page.evaluate("""({input: JSON.stringify(collectSimulationInput()),
        results: resultsJson, draft: document.querySelector('#network-node-id').value,
        filter: document.querySelector('#network-history-filter').value,
        nodes: document.querySelector('#network-node-body').textContent})""")
    data = workload()
    if kind == "node":
        data["network"]["nodes"].append(data["network"]["nodes"][0].copy())
    elif kind == "link":
        data["network"]["links"][0]["source"] = "missing"
    elif kind.startswith("policy_"):
        data["network"]["links"][0]["queue_policy"] = {
            "policy_array": ["FIFO"], "policy_object": {"value": "FIFO"},
            "policy_bool": True, "policy_null": None,
        }[kind]
    elif kind == "offset":
        requests = data["processes"][0]["network_requests"]
        requests["01"] = requests.pop("0")
    elif kind == "request":
        data["processes"][0]["network_requests"]["0"]["size_bytes"] = True
    elif kind.startswith("event_"):
        data["network"]["events"] = [event("LINK_DOWN", 0.5, link_id="ab")]
        data["network"]["events"][0][{
            "event_time": "timestamp", "event_action": "action",
            "event_target": "link_id",
        }[kind]] = {"event_time": -1, "event_action": ["LINK_DOWN"],
                   "event_target": "missing"}[kind]
    elif kind == "disabled":
        data["network"]["enabled"] = False
    else:
        data["network"]["links"][0]["routing_cost"] = float("inf")
    tab(page, "Process")
    upload_json(page, data)
    expect(page.locator("#process-error")).to_contain_text(
        "invalid JSON" if kind == "nonfinite" else "Upload failed"
    )
    after = page.evaluate("""({input: JSON.stringify(collectSimulationInput()),
        results: resultsJson, draft: document.querySelector('#network-node-id').value,
        filter: document.querySelector('#network-history-filter').value,
        nodes: document.querySelector('#network-node-body').textContent})""")
    assert after == before
    expect(page.locator("#export-results-button")).to_be_enabled()


def test_combined_six_managers_apply_imported_security_policy(
    page: Page, network_url: str,
) -> None:
    page.goto(network_url)
    fixture = (Path(__file__).resolve().parents[1] / "test_data" /
               "milestone4" / "six_manager_integration.json")
    assert not json.loads(page.locator("#security-config").input_value())["enabled"]
    tab(page, "Process")
    page.locator("#process-file").set_input_files(fixture)
    expect(page.locator("#upload-status")).to_contain_text("Loaded")
    with page.expect_response("**/api/start") as start:
        result = run(page)
    assert start.value.status == 200
    assert result["security_enabled"] is True
    assert result["current_time"] == 6
    assert result["processes"][0]["blocked_time"] == 5
    assert {event["manager"] for event in result["events"]} == {
        "process", "memory", "file", "security", "device", "network",
    }
    assert [(e["resource_type"], e["outcome"]) for e in result["security_events"]] == [
        ("file", "allowed"), ("device", "allowed"), ("network", "allowed"),
    ]
    expect(page.locator("#result-current-time")).to_have_text("6")
    process_row = page.locator("#process-metrics-body tr").get_by_role("cell")
    expect(process_row.nth(1)).to_have_text("TERMINATED")
    expect(process_row.last).to_have_text("5")

    tab(page, "Memory")
    expect(page.locator("#memory-faults")).to_have_text("1")
    tab(page, "File")
    expect(page.locator("#file-tree-container")).to_contain_text("once")
    expect(page.locator("#file-activity-body")).to_contain_text("CREATE /once")
    expect(page.locator("#file-activity-body")).to_contain_text("Success")

    tab(page, "Security")
    audit_rows = page.locator("#security-audit-body tr").filter(
        has=page.get_by_role("cell", name="allowed", exact=True)
    )
    expect(audit_rows).to_have_count(3)
    for row, action, resource in zip(
        audit_rows.all(), ("create", "read", "send"),
        ("/once", "/devices/keyboard", "/nodes/a/to/b"),
    ):
        expect(row.get_by_role("cell").nth(1)).to_have_text("P")
        expect(row.get_by_role("cell").nth(2)).to_have_text(action)
        expect(row.get_by_role("cell").nth(3)).to_have_text(resource)

    tab(page, "Device")
    expect(page.locator("#device-metrics-body tr").get_by_role("cell").nth(1)).to_have_text("1")
    expect(page.locator("#device-history-body")).to_contain_text("READ")
    expect(page.locator("#device-timeline")).to_contain_text(
        "keyboard · request-0 Request service (completed) | 2–3"
    )
    expect(page.locator("#device-timeline .device-service")).to_be_visible()

    tab(page, "Network")
    expect(page.locator('#network-graph path[data-link-id="ab"]')).to_have_attribute(
        "marker-end", "url(#network-arrow-up)"
    )
    expect(page.locator("#network-link-status-body tr").get_by_role("cell")).to_have_text(
        ["ab", "a → b", "UP", "—", "0", "0"]
    )
    for outcome, count in [("Delivered", "1"), ("Dropped", "0"), ("Failed", "0"), ("Cancelled", "0")]:
        metric = page.locator("#network-summary .metric").filter(
            has=page.get_by_role("heading", name=f"{outcome} packets", exact=True)
        )
        expect(metric.locator("p")).to_have_text(count)
    expect(page.locator("#network-outcomes-body")).to_contain_text("DELIVERED")

    # Replay with one permission removed to verify the denied audit display.
    data = json.loads(fixture.read_text(encoding="utf-8"))
    data["security"]["roles"][0]["permissions"].remove("network:send")
    load(page, data)
    run(page)
    tab(page, "Security")
    denied = page.locator("#security-audit-body tr").filter(
        has=page.get_by_role("cell", name="denied", exact=True)
    )
    cells = denied.get_by_role("cell")
    expect(cells.nth(1)).to_have_text("P")
    expect(cells.nth(2)).to_have_text("send")
    expect(cells.nth(3)).to_have_text("/nodes/a/to/b")


def test_null_metrics_never_coerce_to_zero_or_percent_suffix(
    page: Page, network_url: str,
) -> None:
    page.goto(network_url)
    load(page, workload())
    result = run(page)
    for metric in ("throughput", "goodput", "average_delivery_latency",
                   "average_queue_wait"):
        result["network_metrics"][metric] = None
    result["network_metrics"]["links"]["ab"]["utilization"] = None
    result["network_metrics"]["links"]["ab"]["average_queue_length"] = None
    page.evaluate("result => renderNetworkResults(result)", result)
    tab(page)
    row = page.locator("#network-metrics-body tr").first
    expect(row.locator("td").nth(4)).to_have_text("—")
    text = page.locator("#network-summary").inner_text()
    assert "NaN" not in text and "Infinity" not in text and "—%" not in text
    result["network_metrics"]["links"]["ab"]["utilization"] = 0
    page.evaluate("result => renderNetworkResults(result)", result)
    expect(row.locator("td").nth(4)).to_have_text("0%")


def test_legacy_import_clears_network_and_responsive_layout(
    page: Page, network_url: str,
) -> None:
    page.goto(network_url)
    load(page, workload())
    legacy = workload()
    legacy.pop("network")
    legacy["processes"][0].pop("network_requests")
    load(page, legacy)
    assert page.evaluate("configuredNetwork") is None
    tab(page)
    page.set_viewport_size({"width": 390, "height": 844})
    expect(page.get_by_label("Enable networking", exact=True)).not_to_be_checked()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    result = run(page)
    assert result["current_time"] == 1 and result["network_metrics"] is None


@pytest.mark.parametrize("action", ["LINK_DOWN", "LINK_UP", "NODE_DOWN", "NODE_UP"])
def test_all_topology_actions_from_accessible_forms(
    page: Page, network_url: str, action: str,
) -> None:
    page.goto(network_url)
    load(page, workload())
    tab(page)
    page.get_by_label("Topology action", exact=True).select_option(action)
    page.get_by_label("Topology target", exact=True).select_option(
        "ab" if action.startswith("LINK_") else "a"
    )
    page.get_by_role("button", name="Save event", exact=True).click()
    result = run(page)
    assert result["network_metrics"]["failed"] == int(action.endswith("DOWN"))
    assert result["network_metrics"]["delivered"] == int(action.endswith("UP"))
    tab(page)
    expect(page.locator("#network-events-body")).to_contain_text(action)
    if action.endswith("DOWN"):
        expect(page.locator("#network-link-status-body")).to_contain_text("UNAVAILABLE")


def test_edit_imported_request_defaults_and_process_changes_preserve_drafts(
    page: Page, network_url: str,
) -> None:
    page.goto(network_url)
    load(page, workload())
    tab(page)
    page.get_by_role(
        "button", name="Edit network request P offset 0", exact=True,
    ).click()
    priority = page.get_by_label("Packet priority (lower first)", exact=True)
    expect(priority).to_have_value("1")
    page.get_by_label("Packet size (bytes)", exact=True).fill("200")
    page.get_by_role("button", name="Save request", exact=True).click()
    assert page.evaluate("processes[0].network_requests['0'].size_bytes") == 200
    page.get_by_role("button", name="Edit link ab", exact=True).click()
    page.get_by_label("Bandwidth (bytes / time unit)", exact=True).fill("222")
    tab(page, "Process")
    enter_process(page, pid="other", burst="1")
    tab(page)
    bandwidth = page.get_by_label("Bandwidth (bytes / time unit)", exact=True)
    expect(bandwidth).to_have_value("222")
    assert page.evaluate(
        "configuredNetwork.links[0].bandwidth_bytes_per_time_unit"
    ) == 100
    page.get_by_role("button", name="Clear link draft", exact=True).click()
    page.get_by_role(
        "button", name="Edit network request P offset 0", exact=True,
    ).click()
    tab(page, "Process")
    page.get_by_role("button", name="Remove P", exact=True).click()
    assert page.evaluate("networkEditing.request") is None
    assert page.locator("#network-request-pid").is_enabled()


def test_invalid_security_import_preserves_existing_results_and_policy(
    page: Page, network_url: str,
) -> None:
    page.goto(network_url)
    load(page, workload())
    result = run(page)
    policy = page.locator("#security-config").input_value()
    data = workload()
    data["security"] = {"enabled": True, "roles": [],
                        "users": [{"username": "P", "roles": ["missing"]}]}
    tab(page, "Process")
    upload_json(page, data)
    expect(page.locator("#process-error")).to_contain_text(
        "Every assigned role must already exist",
    )
    assert page.evaluate("JSON.parse(resultsJson)") == result
    assert page.locator("#security-config").input_value() == policy
    expect(page.locator("#export-results-button")).to_be_enabled()


def test_cancelled_outcomes_render_through_real_backend_snapshot(
    page: Page, network_url: str,
) -> None:
    page.goto(network_url)
    load(page, workload())
    page.locator("#step-button").click()
    page.wait_for_function("busy === false && started === true")
    page.locator("#step-button").click()
    page.wait_for_function("busy === false")
    engine = simulator_api.engine
    engine.apply_network_update(engine.network_manager.cancel_packet("packet-0", 0))
    result = run(page)
    assert result["network_metrics"]["cancelled"] == 1
    tab(page)
    expect(page.locator("#network-history-body")).to_contain_text("CANCELLED")
    expect(page.locator("#network-timeline")).to_contain_text("PACKET_CANCELLED")


def test_overflow_exponent_and_duplicate_keys_reject_without_erasing_results(
    page: Page, network_url: str,
) -> None:
    page.goto(network_url)
    load(page, workload())
    result = run(page)
    data = workload()
    data["network"]["links"][0]["routing_cost"] = float("inf")
    overflow = json.dumps(data).replace("Infinity", "1e309")
    duplicate = json.dumps(workload()).replace('"0": {', '"0": {}, "0": {', 1)
    for raw in (overflow, duplicate):
        page.locator("#process-file").set_input_files({
            "name": "invalid-network.json", "mimeType": "application/json",
            "buffer": raw.encode(),
        })
        expect(page.locator("#process-error")).to_contain_text("Upload failed")
        assert page.evaluate("JSON.parse(resultsJson)") == result


def test_disabled_configuration_can_retain_nodes_without_requests(
    page: Page, network_url: str,
) -> None:
    page.goto(network_url)
    data = workload()
    data["processes"][0].pop("network_requests")
    data["network"]["enabled"] = False
    load(page, data)
    tab(page)
    expect(page.get_by_label("Enable networking", exact=True)).not_to_be_checked()
    expect(page.locator("#network-link-body")).to_contain_text("ab")
    result = run(page)
    assert result["network_metrics"] is None
    tab(page)
    expect(page.locator("#network-result-status")).to_contain_text("disabled")


def test_user_identifiers_render_as_text_not_html(page: Page, network_url: str) -> None:
    page.goto(network_url)
    data = workload()
    identity = '<img src=x onerror="window.networkInjected=true">'
    data["network"]["nodes"][0]["node_id"] = identity
    data["network"]["links"][0]["source"] = identity
    data["processes"][0]["network_requests"]["0"]["source"] = identity
    load(page, data)
    result = run(page)
    assert result["network_metrics"]["delivered"] == 1
    tab(page)
    expect(page.locator("#network-node-status-body")).to_contain_text(identity)
    assert page.locator("#network img").count() == 0
    assert page.evaluate("window.networkInjected ?? false") is False


def test_demo_download_uses_the_verified_workload_fixture() -> None:
    """The dashboard loader serves the same data as the deterministic API test."""
    response = simulator_api.app.test_client().get("/network-demo.json")
    assert response.status_code == 200
    assert response.get_json() == failure_demo()


def test_positive_partial_metrics_use_current_snapshot_without_finishing_transport(
    page: Page, network_url: str,
) -> None:
    page.goto(network_url)
    data = workload()
    data["processes"].append({"pid": "other", "arrival_time": 1, "burst_time": 1})
    load(page, data)
    for _ in range(5):
        page.locator("#step-button").click()
        page.wait_for_function("busy === false && started === true")
        result = page.evaluate("JSON.parse(resultsJson)")
        if result["current_time"] == 1:
            break
    else:
        raise AssertionError("Did not reach partial API snapshot")
    tab(page)
    expect(page.locator("#network-result-status")).to_contain_text("Partial")
    row = page.locator("#network-metrics-body tr").first
    expect(row.locator("td").nth(1)).to_have_text("1")
    expect(row.locator("td").nth(4)).to_have_text("100%")
    expect(page.locator("#network-timeline")).to_contain_text("0–1")
    assert result["network_metrics"]["delivered"] == 0

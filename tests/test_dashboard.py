from pathlib import Path

from playwright.sync_api import Page, expect

import json

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_invalid_context_switch_blocks_process_entry(page: Page):
    # Open the real dashboard and select the Process Manager tab.
    dashboard = ROOT / "dashboard" / "index.html"
    page.goto(dashboard.as_uri())
    page.get_by_role("button", name="Process Manager", exact=True).click()

    # Enter an invalid configuration value.
    context_cost = page.locator("#context-switch-cost")
    context_cost.fill("-1")

    # Check that the error appears beside the field.
    error = page.locator("#context-switch-cost-error")
    expect(error).to_be_visible()
    expect(error).to_have_text(
        "Context-switch cost must be a whole number of 0 or greater."
    )
    expect(context_cost).to_have_attribute("aria-invalid", "true")

    # Enter valid process information.
    page.locator("#process-id").fill("P1")
    page.locator("#arrival-time").fill("0")
    page.locator("#burst-time").fill("4")
    page.locator("#process-priority").fill("1")
    page.get_by_role("button", name="Add Process", exact=True).click()

    # Invalid configuration must prevent adding the process.
    expect(page.locator("#process-error")).to_have_text(
        "Fix the scheduling configuration before adding a process."
    )
    expect(page.locator("#process-table-body")).to_have_text(
        "No processes added yet."
    )

    # Correct the configuration and try again.
    context_cost.fill("0")
    expect(error).to_be_empty()
    expect(context_cost).not_to_have_attribute("aria-invalid", "true")

    page.get_by_role("button", name="Add Process", exact=True).click()

    # The process should now appear.
    rows = page.locator("#process-table-body tr")
    expect(rows).to_have_count(1)
    expect(rows.first.locator("td")).to_have_text(
        ["P1", "0", "4", "1", "Remove"]
    )

def test_negative_priority_rejected_and_zero_accepted(page: Page):
    dashboard = ROOT / "dashboard" / "index.html"
    page.goto(dashboard.as_uri())
    page.get_by_role("button", name="Process Manager", exact=True).click()

    # Enter valid process information except for priority.
    page.locator("#process-id").fill("P1")
    page.locator("#arrival-time").fill("0")
    page.locator("#burst-time").fill("4")
    page.locator("#process-priority").fill("-1")

    page.get_by_role("button", name="Add Process", exact=True).click()

    # Negative priority must be rejected.
    expect(page.locator("#process-error")).to_have_text(
        "Priority must be a whole number of 0 or greater."
    )
    expect(page.locator("#process-table-body")).to_have_text(
        "No processes added yet."
    )

    # Zero is a valid priority.
    page.locator("#process-priority").fill("0")
    page.get_by_role("button", name="Add Process", exact=True).click()

    expect(page.locator("#process-error")).to_be_empty()

    rows = page.locator("#process-table-body tr")
    expect(rows).to_have_count(1)
    expect(rows.first.locator("td")).to_have_text(
        ["P1", "0", "4", "0", "Remove"]
    )

@pytest.mark.parametrize("invalid_priority", [-1, 1.5, "1", None, True])
def test_upload_rejects_invalid_priority(page: Page, invalid_priority):
    dashboard = ROOT / "dashboard" / "index.html"
    page.goto(dashboard.as_uri())
    page.get_by_role("button", name="Process Manager", exact=True).click()

    # Add an existing process and select RR.
    page.locator("#algorithm").select_option("RR")
    page.locator("#process-id").fill("Existing")
    page.locator("#arrival-time").fill("0")
    page.locator("#burst-time").fill("4")
    page.locator("#process-priority").fill("1")
    page.get_by_role("button", name="Add Process", exact=True).click()

    data = {
        "algorithm": "FCFS",
        "processes": [
            {
                "pid": "Uploaded",
                "arrival_time": 0,
                "burst_time": 3,
                "priority": invalid_priority,
            }
        ],
    }

    # Upload an in-memory file without creating a temporary disk file.
    page.locator("#process-file").set_input_files({
        "name": "invalid-priority.json",
        "mimeType": "application/json",
        "buffer": json.dumps(data).encode("utf-8"),
    })

    expect(page.locator("#process-error")).to_have_text(
        "Upload failed: Process 1: priority must be a whole number "
        "of 0 or greater."
    )

    # Rejected uploads must preserve the existing process and algorithm.
    rows = page.locator("#process-table-body tr")
    expect(rows).to_have_count(1)
    expect(rows.first.locator("td")).to_have_text(
        ["Existing", "0", "4", "1", "Remove"]
    )
    expect(page.locator("#algorithm")).to_have_value("RR")
    expect(page.locator("#upload-status")).to_be_empty()


def test_upload_defaults_missing_priority_to_zero(page: Page):
    dashboard = ROOT / "dashboard" / "index.html"
    page.goto(dashboard.as_uri())
    page.get_by_role("button", name="Process Manager", exact=True).click()

    data = {
        "algorithm": "PRIORITY",
        "processes": [
            {
                "pid": "P1",
                "arrival_time": 0,
                "burst_time": 4,
                # Priority intentionally omitted.
            }
        ],
    }

    page.locator("#process-file").set_input_files({
        "name": "missing-priority.json",
        "mimeType": "application/json",
        "buffer": json.dumps(data).encode("utf-8"),
    })

    expect(page.locator("#upload-status")).to_have_text(
        "Loaded 1 processes from missing-priority.json."
    )
    expect(page.locator("#process-error")).to_be_empty()
    expect(page.locator("#algorithm")).to_have_value("PRIORITY")
    expect(page.locator("#aging-field")).to_be_visible()

    rows = page.locator("#process-table-body tr")
    expect(rows).to_have_count(1)
    expect(rows.first.locator("td")).to_have_text(
        ["P1", "0", "4", "0", "Remove"]
    )

def open_process_dashboard(page: Page):
    dashboard = ROOT / "dashboard" / "index.html"
    page.goto(dashboard.as_uri())
    page.get_by_role("button", name="Process Manager", exact=True).click()


def enter_process(page: Page, pid="P1", arrival="0", burst="4", priority="1"):
    page.locator("#process-id").fill(pid)
    page.locator("#arrival-time").fill(arrival)
    page.locator("#burst-time").fill(burst)
    page.locator("#process-priority").fill(priority)
    page.get_by_role("button", name="Add Process", exact=True).click()


def upload_json(page: Page, data):
    page.locator("#process-file").set_input_files({
        "name": "processes.json",
        "mimeType": "application/json",
        "buffer": json.dumps(data).encode("utf-8"),
    })

@pytest.mark.parametrize("algorithm", ["FCFS", "SJF", "SRTF", "RR", "PRIORITY"])
def test_algorithm_shows_relevant_configuration(page: Page, algorithm):
    open_process_dashboard(page)
    page.locator("#algorithm").select_option(algorithm)

    expect(page.locator("#context-switch-cost")).to_be_visible()

    if algorithm == "RR":
        expect(page.locator("#quantum-field")).to_be_visible()
    else:
        expect(page.locator("#quantum-field")).to_be_hidden()

    if algorithm == "PRIORITY":
        expect(page.locator("#aging-field")).to_be_visible()
        expect(page.locator("#aging-interval")).to_have_value("4")
    else:
        expect(page.locator("#aging-field")).to_be_hidden()


@pytest.mark.parametrize(
    "algorithm,field,invalid_value,message",
    [
        ("FCFS", "context-switch-cost", "",
         "Context-switch cost must be a whole number of 0 or greater."),
        ("FCFS", "context-switch-cost", "1.5",
         "Context-switch cost must be a whole number of 0 or greater."),
        ("RR", "time-quantum", "0",
         "Time quantum must be a whole number of 1 or greater."),
        ("RR", "time-quantum", "",
         "Time quantum must be a whole number of 1 or greater."),
        ("RR", "time-quantum", "1.5",
         "Time quantum must be a whole number of 1 or greater."),
        ("PRIORITY", "aging-interval", "0",
         "Aging interval must be a whole number of 1 or greater."),
        ("PRIORITY", "aging-interval", "",
         "Aging interval must be a whole number of 1 or greater."),
        ("PRIORITY", "aging-interval", "1.5",
         "Aging interval must be a whole number of 1 or greater."),
    ],
)
def test_invalid_configuration_blocks_entry(
    page: Page, algorithm, field, invalid_value, message
):
    open_process_dashboard(page)
    page.locator("#algorithm").select_option(algorithm)
    page.locator("#" + field).fill(invalid_value)

    error = page.locator("#" + field + "-error")
    expect(error).to_be_visible()
    expect(error).to_have_text(message)
    expect(error).to_have_css("color", "rgb(180, 35, 24)")

    enter_process(page)

    expect(page.locator("#process-error")).to_have_text(
        "Fix the scheduling configuration before adding a process."
    )
    expect(page.locator("#process-table-body")).to_have_text(
        "No processes added yet."
    )


def test_switching_algorithm_clears_irrelevant_error(page: Page):
    open_process_dashboard(page)
    page.locator("#algorithm").select_option("RR")
    page.locator("#time-quantum").fill("0")

    expect(page.locator("#time-quantum-error")).not_to_be_empty()

    page.locator("#algorithm").select_option("FCFS")

    expect(page.locator("#time-quantum-error")).to_be_empty()
    expect(page.locator("#time-quantum")).not_to_have_attribute(
        "aria-invalid", "true"
    )

    enter_process(page)
    expect(page.get_by_role("button", name="Remove P1", exact=True)).to_be_visible()

@pytest.mark.parametrize(
    "field,value,message",
    [
        ("process-id", "   ", "Please fill in all process fields."),
        ("arrival-time", "", "Please fill in all process fields."),
        ("burst-time", "", "Please fill in all process fields."),
        ("process-priority", "", "Please fill in all process fields."),
        ("arrival-time", "-1",
         "Arrival time must be a whole number of 0 or greater."),
        ("arrival-time", "1.5",
         "Arrival time must be a whole number of 0 or greater."),
        ("burst-time", "0",
         "Burst time must be a whole number of 1 or greater."),
        ("burst-time", "1.5",
         "Burst time must be a whole number of 1 or greater."),
        ("process-priority", "1.5",
         "Priority must be a whole number of 0 or greater."),
    ],
)
def test_invalid_process_input(page: Page, field, value, message):
    open_process_dashboard(page)

    page.locator("#process-id").fill("P1")
    page.locator("#arrival-time").fill("0")
    page.locator("#burst-time").fill("4")
    page.locator("#process-priority").fill("1")
    page.locator("#" + field).fill(value)
    page.get_by_role("button", name="Add Process", exact=True).click()

    expect(page.locator("#process-error")).to_have_text(message)
    expect(page.locator("#process-table-body")).to_have_text(
        "No processes added yet."
    )


def test_duplicate_process_id_is_rejected(page: Page):
    open_process_dashboard(page)
    enter_process(page, pid="P1")
    enter_process(page, pid=" P1 ")

    expect(page.locator("#process-error")).to_have_text(
        "That process ID already exists."
    )
    expect(page.locator("#process-table-body tr")).to_have_count(1)


def test_remove_processes_restores_empty_table(page: Page):
    open_process_dashboard(page)
    enter_process(page, pid="P1")
    enter_process(page, pid="P2")

    page.get_by_role("button", name="Remove P1", exact=True).click()

    rows = page.locator("#process-table-body tr")
    expect(rows).to_have_count(1)
    expect(rows.first.locator("td").first).to_have_text("P2")

    page.get_by_role("button", name="Remove P2", exact=True).click()

    expect(page.locator("#process-table-body")).to_have_text(
        "No processes added yet."
    )

def test_valid_upload_replaces_existing_processes(page: Page):
    open_process_dashboard(page)
    enter_process(page, pid="Existing")

    data = {
        "algorithm": "SJF",
        "processes": [
            {"pid": "P1", "arrival_time": 0, "burst_time": 4, "priority": 1},
            {"pid": "P2", "arrival_time": 1, "burst_time": 2, "priority": 0},
        ],
    }

    # Upload twice to check that rows are replaced rather than appended.
    for _ in range(2):
        upload_json(page, data)

        expect(page.locator("#upload-status")).to_have_text(
            "Loaded 2 processes from processes.json."
        )
        expect(page.locator("#process-file")).to_have_value("")

        rows = page.locator("#process-table-body tr")
        expect(rows).to_have_count(2)
        expect(rows.nth(0).locator("td")).to_have_text(
            ["P1", "0", "4", "1", "Remove"]
        )
        expect(rows.nth(1).locator("td")).to_have_text(
            ["P2", "1", "2", "0", "Remove"]
        )

    expect(page.locator("#algorithm")).to_have_value("SJF")
    expect(page.locator("#process-error")).to_be_empty()


@pytest.mark.parametrize(
    "case",
    [
        "malformed_json",
        "invalid_algorithm",
        "empty_processes",
        "duplicate_ids",
        "negative_arrival",
        "zero_burst",
    ],
)

def test_invalid_upload_preserves_existing_data(page: Page, case):
    open_process_dashboard(page)
    page.locator("#algorithm").select_option("RR")
    enter_process(page, pid="Existing")

    data = {
        "algorithm": "FCFS",
        "processes": [
            {"pid": "P1", "arrival_time": 0, "burst_time": 4, "priority": 1}
        ],
    }

    if case == "invalid_algorithm":
        data["algorithm"] = "INVALID"
        expected_error = (
            "Upload failed: Algorithm must be FCFS, SJF, SRTF, RR, or PRIORITY."
        )
    elif case == "empty_processes":
        data["processes"] = []
        expected_error = (
            "Upload failed: The file must contain a nonempty processes list."
        )
    elif case == "duplicate_ids":
        data["processes"].append(dict(data["processes"][0]))
        expected_error = "Upload failed: Process 2: duplicate ID P1."
    elif case == "negative_arrival":
        data["processes"][0]["arrival_time"] = -1
        expected_error = (
            "Upload failed: Process 1: arrival time must be a whole number "
            "of 0 or greater."
        )
    elif case == "zero_burst":
        data["processes"][0]["burst_time"] = 0
        expected_error = (
            "Upload failed: Process 1: burst time must be a whole number "
            "of 1 or greater."
        )
    else:
        expected_error = "The file contains invalid JSON."

    if case == "malformed_json":
        page.locator("#process-file").set_input_files({
            "name": "broken.json",
            "mimeType": "application/json",
            "buffer": b'{"algorithm":',
        })
    else:
        upload_json(page, data)

    expect(page.locator("#process-error")).to_have_text(expected_error)
    expect(page.locator("#algorithm")).to_have_value("RR")

    rows = page.locator("#process-table-body tr")
    expect(rows).to_have_count(1)
    expect(rows.first.locator("td")).to_have_text(
        ["Existing", "0", "4", "1", "Remove"]
    )

@pytest.mark.parametrize("button", ["Run", "Step"])
def test_controls_require_processes(page: Page, button):
    open_process_dashboard(page)
    page.get_by_role("button", name=button, exact=True).click()

    expect(page.locator("#simulation-error")).to_have_text(
        "Add or upload at least one process first."
    )


@pytest.mark.parametrize("button", ["Run", "Step"])
def test_controls_focus_invalid_configuration(page: Page, button):
    open_process_dashboard(page)
    enter_process(page)

    page.locator("#algorithm").select_option("RR")
    page.locator("#time-quantum").fill("0")
    page.get_by_role("button", name=button, exact=True).click()

    expect(page.locator("#time-quantum")).to_be_focused()
    expect(page.locator("#simulation-status")).to_have_text(
        "Simulation not started."
    )

@pytest.mark.parametrize("button", ["Run", "Step"])
def test_valid_controls_report_backend_required(page: Page, button):
    open_process_dashboard(page)
    enter_process(page)
    page.get_by_role("button", name=button, exact=True).click()

    expect(page.locator("#simulation-error")).to_be_empty()
    expect(page.locator("#simulation-status")).to_have_text(
        "FCFS inputs are valid. Backend connection is required to simulate."
    )

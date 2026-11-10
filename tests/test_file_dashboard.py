import pytest
from playwright.sync_api import expect

from tests.test_dashboard import enter_process, upload_json
from tests.test_visualization import dashboard_url


def tab(page, name):
    page.get_by_role("button", name=name, exact=True).click()


def prepare(page, url, burst="8"):
    page.goto(url)
    tab(page, "Process Manager")
    enter_process(page, burst=burst)
    tab(page, "File Manager")


def add_request(page, offset, command):
    page.locator("#file-request-offset").fill(str(offset))
    page.locator("#file-request-command").fill(command)
    page.locator("#file-request-save").click()


def run(page):
    tab(page, "Process Manager")
    page.locator("#run-button").click()
    expect(page.locator("#simulation-status")).to_contain_text("Completed", timeout=15000)
    page.wait_for_function("busy === false")
    tab(page, "File Manager")


def step(page):
    tab(page, "Process Manager")
    with page.expect_response("**/api/step") as response:
        page.locator("#step-button").click()
    page.wait_for_function("busy === false")
    tab(page, "File Manager")
    return response.value.json()


def file_rows(page):
    return page.locator("#file-activity-body tr").evaluate_all(
        "rows => rows.map(row => Array.from(row.cells, cell => cell.textContent))")


def test_editor_lifecycle_preserves_exact_commands_and_process_fields(page, dashboard_url):
    prepare(page, dashboard_url)
    command = '  WRITE /notes.txt "hello  world"  '
    add_request(page, 0, command)
    expect(page.locator("#file-request-table-body tr td").nth(2)).to_have_text(command, use_inner_text=False)
    assert page.evaluate("processes[0].file_requests") == {"0": command}
    tab(page, "Process Manager")
    enter_process(page, pid="P2")
    tab(page, "File Manager")
    assert page.evaluate("processes[0].file_requests") == {"0": command}
    page.get_by_role("button", name="Edit file request P1 offset 0", exact=True).click()
    page.locator("#file-request-command").fill("DELETE /notes.txt")
    page.locator("#file-request-cancel").click()
    assert page.evaluate("processes[0].file_requests") == {"0": command}
    page.get_by_role("button", name="Edit file request P1 offset 0", exact=True).click()
    page.locator("#file-request-offset").fill("1")
    page.locator("#file-request-save").click()
    assert page.evaluate("processes[0].file_requests") == {"1": command}
    with page.expect_request("**/api/start") as request:
        step(page)
    assert request.value.post_data_json["processes"][0]["file_requests"] == {"1": command}
    page.get_by_role("button", name="Remove file request P1 offset 1", exact=True).click()
    expect(page.locator("#file-request-table-body")).to_contain_text("No file requests")
    expect(page.locator("#file-activity-body")).to_contain_text("No file operations")


@pytest.mark.parametrize("offset,command", [("-1", "READ x"), ("8", "READ x"), ("1.5", "READ x"), ("", "READ x"), ("0", "   ")])
def test_invalid_fields_preserve_requests(page, dashboard_url, offset, command):
    prepare(page, dashboard_url)
    add_request(page, 2, "READ missing.txt")
    before = page.evaluate("JSON.stringify(processes)")
    add_request(page, offset, command)
    field = "command" if not command.strip() else "offset"
    expect(page.locator(f"#file-request-{field}-error")).not_to_be_empty()
    expect(page.locator(f"#file-request-{field}-error")).to_have_css("color", "rgb(180, 35, 24)")
    expect(page.locator(f"#file-request-{field}")).to_have_attribute("aria-invalid", "true")
    expect(page.locator(f"#file-request-{field}")).to_have_attribute("aria-describedby", f"file-request-{field}-error")
    assert page.evaluate("JSON.stringify(processes)") == before


def test_simultaneous_errors_duplicates_and_edit_collisions(page, dashboard_url):
    prepare(page, dashboard_url, burst="4")
    add_request(page, 0, "READ x")
    add_request(page, 1, "READ y")
    add_request(page, 4, " ")
    expect(page.locator("#file-request-offset-error")).to_contain_text("less than burst time")
    expect(page.locator("#file-request-command-error")).to_contain_text("nonempty")
    page.locator("#file-request-offset").fill("2")
    expect(page.locator("#file-request-offset-error")).to_be_empty()
    expect(page.locator("#file-request-command-error")).not_to_be_empty()
    add_request(page, 0, "READ z")
    expect(page.locator("#file-request-offset-error")).to_contain_text("already exists")
    page.get_by_role("button", name="Edit file request P1 offset 0", exact=True).click()
    page.locator("#file-request-offset").fill("1")
    page.locator("#file-request-save").click()
    expect(page.locator("#file-request-offset-error")).to_contain_text("already exists")
    assert page.evaluate("processes[0].file_requests") == {"0": "READ x", "1": "READ y"}


def test_real_operations_errors_reset_and_expansion(page, dashboard_url):
    prepare(page, dashboard_url)
    commands = ["MKDIR /docs", "CREATE /docs/notes.txt", 'WRITE /docs/notes.txt "Hello OS"', "READ /docs/notes.txt", "READ /missing.txt"]
    for offset, command in enumerate(commands):
        add_request(page, offset, command)
    step(page)  # dispatch
    snapshot = step(page)  # FCFS executes the remaining burst and requests
    assert snapshot["file_tree"]["children"][0]["children"][0]["name"] == "notes.txt"
    assert file_rows(page)[:4] == [
        ["0", "P1", commands[0], "Success", "No output"],
        ["1", "P1", commands[1], "Success", "No output"],
        ["2", "P1", commands[2], "Success", "No output"],
        ["3", "P1", commands[3], "Success", "Hello OS"],
    ]
    assert file_rows(page)[4][0:4] == ["4", "P1", commands[4], "Error"]
    assert "not found" in file_rows(page)[4][4]
    # Select the exact directory node, avoiding its root ancestor.
    directory = page.locator("#file-tree-container details").filter(has=page.locator(":scope > summary", has_text="docs"))
    directory.locator(":scope > summary").click()
    expect(directory).to_have_attribute("open", "")
    expect(page.locator("#file-tree-container")).to_contain_text("notes.txt (8 bytes)")
    tab(page, "Process Manager")
    page.locator("#reset-button").click()
    page.wait_for_function("busy === false")
    tab(page, "File Manager")
    expect(directory).to_have_attribute("open", "")
    expect(page.locator("#file-tree-container")).to_contain_text("notes.txt (8 bytes)")
    expect(page.locator("#file-activity-body")).to_contain_text("No file operations")
    run(page)
    assert file_rows(page)[0][3] == "Error"  # Reset preserves /docs; MKDIR cannot recreate it.
    page.get_by_role("button", name="Remove file request P1 offset 4", exact=True).click()
    tab(page, "Process Manager")
    expect(page.locator("#export-results-button")).to_be_disabled()
    with page.expect_response("**/api/start"):
        run(page)
    assert file_rows(page)[0][3] == "Success"  # Fresh start owns a new tree.


INVALID_MAPS = [None, [], "READ x", {"-1": "READ x"}, {"1.5": "READ x"}, {"01": "READ x", "1": "READ y"}, {"4": "READ x"}, {"0": ""}, {"0": "  "}, {"0": 1}, {"0": True}]


@pytest.mark.parametrize("requests", INVALID_MAPS)
def test_rejected_import_preserves_inputs_and_results(page, dashboard_url, requests):
    prepare(page, dashboard_url, burst="4")
    add_request(page, 0, "CREATE /keep.txt")
    run(page)
    before_input = page.evaluate("JSON.stringify(collectSimulationInput())")
    before_results = page.evaluate("resultsJson")
    before_tree = page.locator("#file-tree-container").inner_html()
    tab(page, "Process Manager")
    upload_json(page, {"algorithm": "RR", "processes": [{"pid": "New", "arrival_time": 0, "burst_time": 4, "file_requests": requests}]})
    expect(page.locator("#process-error")).to_contain_text("Upload failed")
    assert page.evaluate("JSON.stringify(collectSimulationInput())") == before_input
    assert page.evaluate("resultsJson") == before_results
    assert page.locator("#file-tree-container").inner_html() == before_tree
    expect(page.locator("#export-results-button")).to_be_enabled()


def test_import_fields_replacement_removal_and_old_files(page, dashboard_url):
    prepare(page, dashboard_url)
    add_request(page, 0, "READ x")
    page.get_by_role("button", name="Edit file request P1 offset 0", exact=True).click()
    data = {"algorithm": "FCFS", "memory": {"algorithm": "lru", "frame_count": 2},
            "processes": [{"pid": "P1", "arrival_time": 0, "burst_time": 4, "priority": 2,
                           "memory_references": {"1": 8}, "file_requests": {"0": '  WRITE x "a  b"  '}, "note": "retained"}]}
    tab(page, "Process Manager")
    upload_json(page, data)
    expect(page.locator("#upload-status")).to_contain_text("Loaded")
    tab(page, "File Manager")
    expect(page.locator("#file-request-save")).to_have_text("Add Request")
    expect(page.locator("#file-request-pid")).to_be_enabled()
    actual = page.evaluate("collectSimulationInput()")
    assert actual["processes"][0] == data["processes"][0]
    assert actual["memory"]["algorithm"] == "lru"
    page.get_by_role("button", name="Edit file request P1 offset 0", exact=True).click()
    tab(page, "Process Manager")
    page.get_by_role("button", name="Remove P1", exact=True).click()
    tab(page, "File Manager")
    expect(page.locator("#file-request-table-body")).to_contain_text("No file requests")
    expect(page.locator("#file-request-cancel")).to_be_hidden()
    expect(page.locator("#file-request-pid")).to_be_enabled()
    tab(page, "Process Manager")
    upload_json(page, {"algorithm": "FCFS", "processes": [{"pid": "Old", "arrival_time": 0, "burst_time": 1}]})
    expect(page.locator("#upload-status")).to_contain_text("Loaded")
    assert page.evaluate("collectSimulationInput().processes[0].file_requests") == {}


def test_process_working_directories_and_safe_text(page, dashboard_url):
    prepare(page, dashboard_url)
    data = {"algorithm": "FCFS", "processes": [
        {"pid": "P1", "arrival_time": 0, "burst_time": 5, "file_requests": {"0": "MKDIR /docs", "1": "CD /docs", "2": "CREATE notes.txt", "3": 'WRITE notes.txt "<img src=x onerror=alert(1)>"', "4": "PWD"}},
        {"pid": "P2", "arrival_time": 5, "burst_time": 4, "file_requests": {"0": "PWD", "1": "READ /docs/notes.txt", "2": "CREATE /<b>name.txt", "3": "READ notes.txt"}},
    ]}
    tab(page, "Process Manager")
    upload_json(page, data)
    expect(page.locator("#upload-status")).to_contain_text("Loaded")
    run(page)
    log = file_rows(page)
    assert log[4][1:] == ["P1", "PWD", "Success", "/docs"]
    assert log[5][1:] == ["P2", "PWD", "Success", "/"]
    assert log[6][1:] == ["P2", "READ /docs/notes.txt", "Success", "<img src=x onerror=alert(1)>"]
    assert log[8][3] == "Error"  # P2's relative path resolves from root.
    expect(page.locator("#file-tree-container")).to_contain_text("<b>name.txt")
    assert page.locator("#file-tree-container b, #file-activity-body img").count() == 0


def test_rejected_edit_and_cancel_preserve_results_and_run_lock(page, dashboard_url):
    prepare(page, dashboard_url)
    add_request(page, 0, "CREATE /keep.txt")
    run(page)
    before = page.evaluate("resultsJson")
    page.get_by_role("button", name="Edit file request P1 offset 0", exact=True).click()
    page.locator("#file-request-command").fill(" ")
    page.locator("#file-request-save").click()
    expect(page.locator("#file-request-command-error")).not_to_be_empty()
    assert page.evaluate("resultsJson") == before
    assert page.evaluate("processes[0].file_requests") == {"0": "CREATE /keep.txt"}
    page.locator("#file-request-cancel").click()
    assert page.evaluate("resultsJson") == before
    tab(page, "Process Manager")
    page.locator("#context-switch-cost").fill("1")
    page.locator("#run-button").click()
    page.wait_for_function("running === true")
    tab(page, "File Manager")
    for selector in ["#file-request-pid", "#file-request-offset", "#file-request-command", "#file-request-save", "#file-request-table-body button"]:
        for control in page.locator(selector).all():
            expect(control).to_be_disabled()
    tab(page, "Process Manager")
    page.locator("#pause-button").click()
    page.wait_for_function("busy === false")
    tab(page, "File Manager")
    expect(page.locator("#file-request-save")).to_be_enabled()



def test_duplicate_json_keys_are_rejected_atomically(page, dashboard_url):
    prepare(page, dashboard_url)
    add_request(page, 0, "READ x")
    before = page.evaluate("JSON.stringify(collectSimulationInput())")
    tab(page, "Process Manager")
    page.locator("#process-file").set_input_files({
        "name": "duplicate.json", "mimeType": "application/json",
        "buffer": b'{"algorithm":"FCFS","processes":[{"pid":"New","arrival_time":0,"burst_time":2,"file_requests":{"0":"READ x","0":"READ y"}}]}'})
    expect(page.locator("#process-error")).to_contain_text("Duplicate JSON key")
    assert page.evaluate("JSON.stringify(collectSimulationInput())") == before


@pytest.mark.parametrize("outcome,label", [("success", "Success"), ("error", "Error")])
def test_log_uses_outcome_without_parsing_message_prefix(page, dashboard_url, outcome, label):
    page.goto(dashboard_url)
    tab(page, "File Manager")
    # Renderer-only regression: the current backend misclassifies READ content
    # starting with Error:. The UI must not duplicate or override that logic.
    page.evaluate("result => renderFileManager(result)", {
        "events": [{"manager": "file", "timestamp": 0, "entity_id": "P1",
                    "action": "READ x", "outcome": outcome, "message": "Error: legitimate file content"}]
    })
    assert file_rows(page) == [["0", "P1", "READ x", label, "Error: legitimate file content"]]


def test_no_process_error_and_multiline_command_preservation(page, dashboard_url):
    page.goto(dashboard_url)
    tab(page, "File Manager")
    page.locator("#file-request-save").click()
    expect(page.locator("#file-request-error")).to_have_text("Select an existing process first.")
    tab(page, "Process Manager")
    enter_process(page)
    tab(page, "File Manager")
    command = 'WRITE x "first\nsecond"'
    add_request(page, 0, command)
    page.get_by_role("button", name="Edit file request P1 offset 0", exact=True).click()
    expect(page.locator("#file-request-command")).to_have_value(command)
    page.locator("#file-request-save").click()
    assert page.evaluate("processes[0].file_requests") == {"0": command}



def test_new_commands_handles_utf8_storage_and_owner(page, dashboard_url):
    prepare(page, dashboard_url, burst="9")
    commands = ["CREATE /notes.txt", "OPEN /notes.txt w+", 'HANDLE_WRITE 1 "caf\u00e9"',
                "CLOSE 1", 'APPEND /notes.txt "!"', "OPEN /notes.txt r",
                "HANDLE_READ 2", "CLOSE 2", "STORAGE"]
    for offset, command in enumerate(commands):
        add_request(page, offset, command)
    run(page)
    log = file_rows(page)
    assert [row[2] for row in log] == commands
    assert [row[3] for row in log] == ["Success"] * 9
    assert [log[i][4] for i in [1, 5, 6, 8]] == ["1", "2", "caf\u00e9!", "6"]
    expect(page.locator("#file-storage-used")).to_have_text("6")
    expect(page.locator("#file-tree-container")).to_contain_text("notes.txt (6 bytes) | Owner: P1")
    expect(page.locator("#file-tree-container summary")).to_contain_text("Owner: system")
    tab(page, "Process Manager")
    page.locator("#reset-button").click()
    page.wait_for_function("busy === false")
    tab(page, "File Manager")
    expect(page.locator("#file-storage-used")).to_have_text("6")
    page.get_by_role("button", name="Remove file request P1 offset 8", exact=True).click()
    expect(page.locator("#file-storage-used")).to_have_text("\u2014")


def test_permission_denial_and_error_prefix_read_use_backend_outcomes(page, dashboard_url):
    prepare(page, dashboard_url, burst="7")
    commands = ["CREATE /notes.txt", 'WRITE /notes.txt "Error: legitimate content"',
                "CHMOD /notes.txt r--", "READ /notes.txt", 'APPEND /notes.txt "denied"',
                "STAT /notes.txt", "READ /notes.txt"]
    for offset, command in enumerate(commands):
        add_request(page, offset, command)
    run(page)
    log = file_rows(page)
    assert [row[3] for row in log] == ["Success", "Success", "Success", "Success", "Error", "Success", "Success"]
    assert log[3][4] == log[6][4] == "Error: legitimate content"
    assert "not authorized" in log[4][4].lower()
    assert "r--" in log[5][4]
    expect(page.locator("#file-storage-used")).to_have_text(str(len("Error: legitimate content".encode("utf-8"))))
    # Export preserves the corrected backend outcome even when content starts Error:.
    import csv
    with page.expect_download() as download:
        page.locator("#export-file-csv").click()
    with open(download.value.path(), encoding="utf-8", newline="") as exported:
        records = list(csv.DictReader(exported))
    assert records[3]["outcome"] == "success"
    assert records[3]["details"] == "Error: legitimate content"
    assert records[4]["outcome"] == "error"


def test_storage_updates_from_partial_snapshots_and_handles_are_closed(page, dashboard_url):
    prepare(page, dashboard_url)
    tab(page, "Process Manager")
    page.locator("#algorithm").select_option("RR")
    page.locator("#time-quantum").fill("1")
    tab(page, "File Manager")
    for offset, command in enumerate(["CREATE /x", 'WRITE /x "\u00e9"', 'APPEND /x "!"', "OPEN /x r", "CLOSE 1", "HANDLE_READ 1"]):
        add_request(page, offset, command)
    snapshot = step(page)
    assert snapshot["file_storage_used"] == 0
    expect(page.locator("#file-storage-used")).to_have_text("0")
    for expected in [0, 2, 3]:
        snapshot = step(page)
        assert snapshot["file_storage_used"] == expected
        expect(page.locator("#file-storage-used")).to_have_text(str(expected))
        if expected != 3:
            step(page)  # next dispatch snapshot
    run(page)
    assert file_rows(page)[5][3] == "Error"
    assert "closed" in file_rows(page)[5][4].lower()

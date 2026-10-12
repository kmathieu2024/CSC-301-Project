from pathlib import Path

from playwright.sync_api import Page, expect


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
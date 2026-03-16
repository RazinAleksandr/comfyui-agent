#!/usr/bin/env python3
"""
Comprehensive QA E2E Test Suite — AI Influencer Studio
Tests all pages, flows, data integrity, and edge cases.
"""

import json
import datetime
import traceback
from playwright.sync_api import sync_playwright, Page, Browser

BASE_URL = "http://localhost:8000"
SCREENSHOTS_DIR = "/tmp/screenshots"
INFLUENCER_IDS = ["emi2souls", "grannys"]
KNOWN_RUN_ID = "20260316_112159"

bugs = []
bug_id_counter = [0]
pages_tested = []
flows_tested = []
console_errors = []
working = []


def next_bug_id():
    bug_id_counter[0] += 1
    return bug_id_counter[0]


def add_bug(severity, page_url, description, expected, actual, screenshot=None):
    b = {
        "id": next_bug_id(),
        "severity": severity,
        "page": page_url,
        "description": description,
        "expected": expected,
        "actual": actual,
        "screenshot": screenshot or "",
    }
    bugs.append(b)
    print(f"  [BUG #{b['id']}] {severity.upper()}: {description}")
    return b


def screenshot(page: Page, name: str) -> str:
    path = f"{SCREENSHOTS_DIR}/{name}"
    try:
        page.screenshot(path=path, full_page=True)
    except Exception as e:
        print(f"  [SCREENSHOT FAILED] {name}: {e}")
        path = ""
    return path


def setup_console_capture(page: Page, page_label: str):
    def on_console(msg):
        if msg.type in ("error", "warning"):
            entry = f"[{page_label}] {msg.type.upper()}: {msg.text}"
            console_errors.append(entry)

    def on_page_error(err):
        entry = f"[{page_label}] PAGE ERROR: {err}"
        console_errors.append(entry)
        print(f"  [JS ERROR] {err}")

    page.on("console", on_console)
    page.on("pageerror", on_page_error)


def check_no_object_object(page: Page, url: str, context: str):
    """Check for [object Object] in page text."""
    try:
        content = page.content()
        if "[object Object]" in content:
            # Get the specific text surrounding it for context
            idx = content.find("[object Object]")
            snippet = content[max(0, idx - 100):idx + 120]
            s = screenshot(page, f"qa_bug_{next_bug_id():02d}_object_object.png")
            add_bug(
                "major",
                url,
                f"[object Object] rendered as text on {context}",
                "Human-readable data displayed",
                f"[object Object] found in page content. Snippet: ...{snippet}...",
                s,
            )
            return True
    except Exception as e:
        print(f"  [CHECK ERROR] object-object check: {e}")
    return False


def check_no_undefined_null(page: Page, url: str, context: str):
    """Check for raw undefined/null displayed as visible text."""
    try:
        # Look for visible text nodes containing 'undefined' or 'null' as standalone
        found = page.evaluate("""() => {
            const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
            const results = [];
            let node;
            while ((node = walker.nextNode())) {
                const t = node.textContent.trim();
                if (t === 'undefined' || t === 'null') {
                    results.push(t);
                }
            }
            return results;
        }""")
        if found:
            s = screenshot(page, f"qa_bug_{next_bug_id():02d}_undefined_null.png")
            add_bug(
                "major",
                url,
                f"Raw 'undefined' or 'null' displayed as text on {context}",
                "No raw undefined/null values displayed",
                f"Found values: {found}",
                s,
            )
            return True
    except Exception as e:
        print(f"  [CHECK ERROR] undefined/null check: {e}")
    return False


def check_images(page: Page, url: str, context: str):
    """Check for broken images (empty src or failed loads)."""
    try:
        broken = page.evaluate("""() => {
            const imgs = Array.from(document.querySelectorAll('img'));
            return imgs.filter(img => {
                const src = img.getAttribute('src') || '';
                return src === '' || src === 'undefined' || src === 'null';
            }).map(img => ({src: img.getAttribute('src'), alt: img.alt, className: img.className}));
        }""")
        if broken:
            s = screenshot(page, f"qa_bug_{next_bug_id():02d}_broken_images.png")
            add_bug(
                "minor",
                url,
                f"Images with empty/invalid src attribute on {context}",
                "All images have valid src URLs",
                f"Found {len(broken)} broken images: {json.dumps(broken[:3])}",
                s,
            )
            return True
    except Exception as e:
        print(f"  [CHECK ERROR] image check: {e}")
    return False


# ============================================================
# PHASE 1 & 2: Page Load + Data Integrity
# ============================================================

def test_home_page(page: Page):
    print("\n[PHASE 1] Testing Home Page...")
    url = f"{BASE_URL}/"
    setup_console_capture(page, "home")

    try:
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_timeout(4000)
        pages_tested.append(url)

        s = screenshot(page, "qa_01_home_load.png")

        # Check page title
        title = page.title()
        if "Avatar" not in title and "AI" not in title and "Influencer" not in title:
            add_bug("minor", url, "Page title does not reflect app purpose",
                    "Title should contain 'AI', 'Avatar', or 'Influencer'",
                    f"Actual title: '{title}'", s)
        else:
            working.append(f"Home page title: '{title}'")

        # Check the main heading
        heading = page.locator("h1").first
        if heading.count() > 0:
            heading_text = heading.inner_text()
            working.append(f"Home heading: '{heading_text}'")
        else:
            add_bug("major", url, "No h1 heading on home page",
                    "H1 heading visible", "No h1 found", s)

        # Check influencer cards — should be exactly 2
        cards = page.locator("a.group").all()
        card_count = len(cards)
        if card_count == 0:
            # Try broader selector
            cards = page.locator("[class*='Card']").all()
            card_count = len(cards)

        # Look for specific influencer names
        content = page.content()
        has_emi = "Emi Noir" in content or "emi2souls" in content
        has_grannys = "grannys" in content

        if not has_emi:
            add_bug("critical", url, "emi2souls influencer not showing on home page",
                    "'Emi Noir' card visible on home page",
                    "emi2souls / Emi Noir not found in page content", s)
        else:
            working.append("emi2souls influencer card visible on home page")

        if not has_grannys:
            add_bug("major", url, "grannys influencer not showing on home page",
                    "'grannys' card visible on home page",
                    "grannys not found in page content", s)
        else:
            working.append("grannys influencer card visible on home page")

        # Check create button
        create_btn = page.locator("text=Create New Avatar")
        if create_btn.count() == 0:
            add_bug("major", url, "Create New Avatar button not found",
                    "'Create New Avatar' button visible", "Not found on page", s)
        else:
            working.append("Create New Avatar button present")

        # Data integrity checks
        check_no_object_object(page, url, "Home Page")
        check_no_undefined_null(page, url, "Home Page")
        check_images(page, url, "Home Page")

        # Check hashtag badges render correctly
        hashtag_badges = page.locator("text=#fitness, text=#gaming, text=#dancing").all()
        # Use content check instead
        if "#fitness" in content or "#gaming" in content or "#dancing" in content:
            working.append("Hashtag badges render correctly on home page")

        # Check profile images loaded (they should have valid URLs)
        profile_imgs = page.evaluate("""() => {
            const imgs = Array.from(document.querySelectorAll('img'));
            return imgs.map(img => ({src: img.src, naturalWidth: img.naturalWidth, complete: img.complete}));
        }""")
        for img in profile_imgs:
            if img.get('src') and '/files/' in img.get('src', ''):
                if img.get('naturalWidth', 0) == 0 and img.get('complete'):
                    add_bug("minor", url, "Profile image failed to load",
                            "Profile image loads successfully",
                            f"Image with src {img['src']} has naturalWidth=0", s)

        print(f"  Home page OK: emi2souls={has_emi}, grannys={has_grannys}")

    except Exception as e:
        add_bug("critical", url, f"Home page test crashed: {e}",
                "Test runs successfully", traceback.format_exc(), "")
        print(f"  [CRASH] Home page: {e}")


def test_avatar_detail_page(page: Page, avatar_id: str):
    print(f"\n[PHASE 1] Testing Avatar Detail Page: {avatar_id}...")
    url = f"{BASE_URL}/avatar/{avatar_id}"
    setup_console_capture(page, f"avatar_{avatar_id}")

    try:
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_timeout(4000)
        pages_tested.append(url)

        s = screenshot(page, f"qa_02_avatar_{avatar_id}.png")

        content = page.content()

        # Check not 404/error page
        if "not found" in content.lower() and "Avatar not found" not in content:
            add_bug("critical", url, f"Avatar detail page shows error for {avatar_id}",
                    "Avatar profile displays", "Page shows 'not found' error", s)
            return

        # Check avatar name and ID visible
        if avatar_id == "emi2souls":
            if "Emi Noir" not in content:
                add_bug("major", url, "Avatar name 'Emi Noir' not visible on detail page",
                        "'Emi Noir' displayed", "Name not found in content", s)
            else:
                working.append(f"Avatar name 'Emi Noir' displays on {avatar_id} detail page")

            if "@emi2souls" not in content and "emi2souls" not in content:
                add_bug("minor", url, "Avatar ID badge not visible",
                        "@emi2souls badge visible", "ID not in content", s)
            else:
                working.append("Avatar ID badge visible")

        # Check 6 pipeline stage cards in overview
        stage_labels = ["Trend Ingestion", "Download", "Candidate Filter",
                        "VLM Scoring", "Review", "Generation"]
        missing_stages = [s_label for s_label in stage_labels if s_label not in content]
        if missing_stages:
            s2 = screenshot(page, f"qa_bug_{next_bug_id():02d}_missing_stages_{avatar_id}.png")
            add_bug("major", url, f"Pipeline stage overview missing stages: {missing_stages}",
                    "All 6 pipeline stage cards visible",
                    f"Missing: {missing_stages}", s2)
        else:
            working.append(f"All 6 pipeline stage overview cards present on {avatar_id}")

        # Check Start Pipeline button
        if "Start Pipeline" not in content:
            add_bug("major", url, "Start Pipeline button not found on avatar detail page",
                    "'Start Pipeline' button visible", "Not found", s)
        else:
            working.append("Start Pipeline button present")

        # Check Edit/Delete buttons
        if "Edit" not in content:
            add_bug("minor", url, "Edit button not found on avatar detail page",
                    "Edit button visible", "Not found", s)
        else:
            working.append("Edit button present")

        if "Delete" not in content:
            add_bug("minor", url, "Delete button not found",
                    "Delete button visible", "Not found", s)
        else:
            working.append("Delete button present")

        # Check Generation Tasks section
        if "Generation Tasks" not in content:
            add_bug("major", url, "Generation Tasks section missing",
                    "'Generation Tasks' section visible", "Not found", s)
        else:
            working.append("Generation Tasks section present")

        # Check task cards (emi2souls should have at least 1 run)
        if avatar_id == "emi2souls":
            if "20260316_112159" not in content and "Run 20260316" not in content:
                add_bug("major", url, "Expected pipeline run not showing in task list",
                        "Run 20260316_112159 visible in task list",
                        "Run ID not found in page content", s)
            else:
                working.append("Pipeline run 20260316_112159 visible in task list")

        # Check task cards clickable (look for stage status indicators)
        has_completed_stages = "completed" in content or "bg-green-50" in content
        if avatar_id == "emi2souls" and not has_completed_stages:
            add_bug("minor", url, "Task cards don't show completed stage status",
                    "Stage status badges visible on task cards",
                    "No 'completed' indicators found", s)

        # grannys should show empty state
        if avatar_id == "grannys":
            no_tasks_msg = ("No generation tasks yet" in content or
                           "Start a pipeline" in content)
            if not no_tasks_msg:
                add_bug("minor", url, "grannys avatar missing empty state message",
                        "'No generation tasks yet' message visible",
                        "Empty state message not found", s)
            else:
                working.append("Empty state message shown correctly for grannys")

        # Data integrity
        check_no_object_object(page, url, f"Avatar Detail ({avatar_id})")
        check_no_undefined_null(page, url, f"Avatar Detail ({avatar_id})")
        check_images(page, url, f"Avatar Detail ({avatar_id})")

        print(f"  Avatar detail {avatar_id} tested OK")

    except Exception as e:
        add_bug("critical", url, f"Avatar detail page {avatar_id} crashed: {e}",
                "Test runs successfully", traceback.format_exc(), "")
        print(f"  [CRASH] Avatar detail {avatar_id}: {e}")


def test_task_detail_page(page: Page):
    print(f"\n[PHASE 1] Testing Task Detail Page...")
    url = f"{BASE_URL}/task/emi2souls/{KNOWN_RUN_ID}"
    setup_console_capture(page, "task_detail")

    try:
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_timeout(5000)
        pages_tested.append(url)

        s = screenshot(page, "qa_03_task_detail.png")
        content = page.content()

        # Check not error page
        if "not found" in content.lower() and "Task" in content and "not found" in content:
            add_bug("critical", url, "Task detail page shows 'not found' error",
                    "Task detail page loads with pipeline data",
                    "Page shows not-found state", s)
            return

        # Check run ID visible
        if KNOWN_RUN_ID not in content:
            add_bug("major", url, "Run ID not visible on task detail page",
                    f"Run ID {KNOWN_RUN_ID} displayed on page",
                    "Run ID not found in content", s)
        else:
            working.append(f"Run ID {KNOWN_RUN_ID} visible on task detail page")

        # Check 6 stages present
        stage_labels = ["Trend Ingestion", "Download", "Candidate Filter",
                        "VLM Scoring", "Review", "Generation"]
        missing_stages = [sl for sl in stage_labels if sl not in content]
        if missing_stages:
            s2 = screenshot(page, f"qa_bug_{next_bug_id():02d}_missing_task_stages.png")
            add_bug("major", url, f"Task detail page missing stages: {missing_stages}",
                    "All 6 stage sections visible on task detail page",
                    f"Missing: {missing_stages}", s2)
        else:
            working.append("All 6 pipeline stages visible on task detail page")

        # Check ingestion count (21 tiktok + 28 instagram = 49 items)
        if "21" not in content and "49" not in content:
            add_bug("minor", url, "Ingestion item count not visible on task detail page",
                    "Item counts visible for each stage",
                    "Counts 21 or 49 not found in content", s)

        # Check Completed badges for stages that are done
        completed_count = content.count("completed")
        if completed_count < 4:
            add_bug("major", url, "Task detail page not showing completed stage statuses",
                    "At least 4 stages showing 'completed' status",
                    f"Only {completed_count} 'completed' occurrences found", s)
        else:
            working.append(f"Stage completion statuses visible ({completed_count} 'completed' occurrences)")

        # Check VLM section content
        if "VLM Scoring" in content:
            if "Gemini" in content or "model" in content.lower() or "accepted" in content.lower():
                working.append("VLM scoring section shows model/accept data")
            # VLM accepted count should be 7 + 9 = 16
            # This is mapped from accepted field

        # Check Review section
        if "Review" in content:
            if "approved" in content.lower() or "skipped" in content.lower():
                working.append("Review section shows approval/skip data")
            else:
                add_bug("minor", url, "Review section missing approval data",
                        "Review section shows approved/skipped counts",
                        "No 'approved' or 'skipped' text found", s)

        # Check Generation section
        if "Generation" in content:
            # Generation jobs should show as "lost" since backend restarted
            if "lost" in content or "generation" in content.lower():
                working.append("Generation section present")
            # Check for server management UI
            if "Server" in content or "server" in content:
                working.append("Server management section visible on task detail")

        # Check video thumbnails present
        video_elements = page.evaluate("""() => {
            const videos = Array.from(document.querySelectorAll('video'));
            const imgs = Array.from(document.querySelectorAll('img')).filter(
                img => img.src.includes('/files/')
            );
            return {video_count: videos.length, file_img_count: imgs.length};
        }""")
        if video_elements['video_count'] > 0 or video_elements['file_img_count'] > 0:
            working.append(f"Video/media elements found: {video_elements['video_count']} videos, {video_elements['file_img_count']} file images")
        else:
            add_bug("minor", url, "No video thumbnails visible on task detail page",
                    "Video thumbnails visible for downloaded/filtered/selected videos",
                    "No video or file image elements found", s)

        # Take full-page screenshot scrolling down
        page.evaluate("window.scrollTo(0, document.body.scrollHeight / 3)")
        page.wait_for_timeout(500)
        screenshot(page, "qa_03b_task_detail_mid.png")
        page.evaluate("window.scrollTo(0, document.body.scrollHeight * 2 / 3)")
        page.wait_for_timeout(500)
        screenshot(page, "qa_03c_task_detail_bottom.png")
        page.evaluate("window.scrollTo(0, 0)")

        # Data integrity
        check_no_object_object(page, url, "Task Detail Page")
        check_no_undefined_null(page, url, "Task Detail Page")

        # Check for empty thumbnail src attribute (known potential bug from mapper)
        blank_thumbnails = page.evaluate("""() => {
            const imgs = Array.from(document.querySelectorAll('img'));
            return imgs.filter(img => {
                const src = img.getAttribute('src') || '';
                return src === '' || src === 'undefined';
            }).length;
        }""")
        if blank_thumbnails > 0:
            s2 = screenshot(page, f"qa_bug_{next_bug_id():02d}_blank_thumbnails.png")
            add_bug("minor", url,
                    f"{blank_thumbnails} images with empty src attribute on task detail page",
                    "All displayed images have valid src URLs",
                    f"{blank_thumbnails} img elements have empty src",
                    s2)

        print(f"  Task detail page tested OK")

    except Exception as e:
        add_bug("critical", url, f"Task detail page crashed: {e}",
                "Test runs successfully", traceback.format_exc(), "")
        print(f"  [CRASH] Task detail: {e}")


# ============================================================
# PHASE 3: Interactive Flows
# ============================================================

def test_create_influencer_dialog(page: Page):
    print("\n[PHASE 3] Testing Create Influencer Dialog...")
    url = f"{BASE_URL}/"
    flows_tested.append("Create Influencer Dialog")

    try:
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_timeout(3000)

        # Find and click the Create New Avatar card
        create_card = page.locator("text=Create New Avatar").first
        if create_card.count() == 0:
            add_bug("major", url, "Cannot find Create New Avatar button to test dialog",
                    "Create button clickable", "Button not found", "")
            return

        before_s = screenshot(page, "qa_04_create_before.png")

        create_card.click()
        page.wait_for_timeout(1000)

        after_s = screenshot(page, "qa_04_create_dialog_open.png")
        content = page.content()

        # Check dialog opened
        if "Create New Avatar" not in content or "ID (slug)" not in content:
            add_bug("major", url, "Create influencer dialog did not open on button click",
                    "Modal dialog opens with form fields",
                    "Dialog content (ID/Name fields) not found after click",
                    after_s)
            return
        else:
            working.append("Create New Avatar dialog opens on button click")

        # Check all form fields present
        fields = ["ID (slug)", "Name", "Description", "Hashtags", "Video Selection Requirements", "Reference Image"]
        missing_fields = [f for f in fields if f not in content]
        if missing_fields:
            add_bug("minor", url, f"Create dialog missing form fields: {missing_fields}",
                    "All form fields present in create dialog",
                    f"Missing: {missing_fields}", after_s)
        else:
            working.append("All form fields present in Create Avatar dialog")

        # Fill in a test influencer (will be cleaned up or we cancel)
        id_input = page.locator("input#influencer_id")
        name_input = page.locator("input#name")

        if id_input.count() > 0:
            id_input.fill("qa_test_influencer_delete_me")
        if name_input.count() > 0:
            name_input.fill("QA Test Influencer")

        desc_input = page.locator("textarea#description")
        if desc_input.count() > 0:
            desc_input.fill("Automated QA test influencer — safe to delete")

        hashtags_input = page.locator("input#hashtags")
        if hashtags_input.count() > 0:
            hashtags_input.fill("qa, test, automation")

        filled_s = screenshot(page, "qa_04_create_filled.png")

        # Test form validation — try submitting with empty required fields in a fresh dialog
        # For now just verify the Create Avatar button is present and enabled
        submit_btn = page.locator("button[type='submit']").last
        if submit_btn.count() > 0:
            is_disabled = submit_btn.is_disabled()
            working.append(f"Create Avatar submit button found, disabled={is_disabled}")

        # Submit the form to test creation
        if submit_btn.count() > 0 and not submit_btn.is_disabled():
            with page.expect_response(lambda r: "/api/v1/influencers" in r.url, timeout=10000) as resp_info:
                submit_btn.click()

            resp = resp_info.value
            if resp.status == 200:
                page.wait_for_timeout(2000)
                after_create_s = screenshot(page, "qa_04_create_after.png")
                content_after = page.content()
                if "QA Test Influencer" in content_after or "qa_test_influencer" in content_after:
                    working.append("New influencer created and appears on home page after dialog submit")
                else:
                    add_bug("major", url, "New influencer not visible after creation",
                            "Newly created influencer appears in influencer list",
                            "Influencer not found in home page after creation",
                            after_create_s)
            else:
                add_bug("major", url, f"Create influencer API returned {resp.status}",
                        "API returns 200 on influencer creation",
                        f"API returned status {resp.status}", filled_s)

        print("  Create dialog flow tested OK")

    except Exception as e:
        add_bug("major", url, f"Create influencer dialog test crashed: {e}",
                "Dialog test runs without error", traceback.format_exc(), "")
        print(f"  [CRASH] Create dialog: {e}")


def test_edit_influencer_dialog(page: Page):
    print("\n[PHASE 3] Testing Edit Influencer Dialog...")
    url = f"{BASE_URL}/avatar/emi2souls"
    flows_tested.append("Edit Influencer Dialog")

    try:
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_timeout(4000)

        before_s = screenshot(page, "qa_05_edit_before.png")

        edit_btn = page.locator("button:has-text('Edit')").first
        if edit_btn.count() == 0:
            add_bug("major", url, "Edit button not found on avatar detail page",
                    "Edit button clickable", "Button not found", before_s)
            return

        edit_btn.click()
        page.wait_for_timeout(1000)

        dialog_s = screenshot(page, "qa_05_edit_dialog.png")
        content = page.content()

        # Check dialog has pre-filled data
        if "Edit Avatar" not in content:
            add_bug("major", url, "Edit dialog did not open",
                    "Edit dialog opens with current influencer data",
                    "Edit dialog not found", dialog_s)
            return
        else:
            working.append("Edit Avatar dialog opens on button click")

        # Check pre-filled values
        name_input_val = page.evaluate("() => document.querySelector('input#edit-name')?.value || ''")
        if name_input_val == "Emi Noir":
            working.append("Edit dialog pre-fills current name 'Emi Noir'")
        elif name_input_val == "":
            add_bug("major", url, "Edit dialog does not pre-fill influencer name",
                    "Edit dialog pre-fills current name in input",
                    f"Name input is empty, expected 'Emi Noir'", dialog_s)
        else:
            working.append(f"Edit dialog pre-fills name field: '{name_input_val}'")

        # Check description pre-fill
        desc_val = page.evaluate("() => document.querySelector('textarea#edit-description')?.value || ''")
        if len(desc_val) > 10:
            working.append("Edit dialog pre-fills description")
        else:
            add_bug("minor", url, "Edit dialog does not pre-fill description",
                    "Description textarea pre-filled with current value",
                    f"Description textarea value: '{desc_val[:50]}'", dialog_s)

        # Close the dialog without saving (press Escape)
        page.keyboard.press("Escape")
        page.wait_for_timeout(500)

        print("  Edit dialog flow tested OK")

    except Exception as e:
        add_bug("major", url, f"Edit dialog test crashed: {e}",
                "Edit dialog test runs without error", traceback.format_exc(), "")
        print(f"  [CRASH] Edit dialog: {e}")


def test_delete_dialog(page: Page):
    print("\n[PHASE 3] Testing Delete Influencer Dialog (cancel only)...")
    url = f"{BASE_URL}/avatar/emi2souls"
    flows_tested.append("Delete Influencer Dialog (cancel)")

    try:
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_timeout(4000)

        delete_btn = page.locator("button:has-text('Delete')").first
        if delete_btn.count() == 0:
            add_bug("major", url, "Delete button not found",
                    "Delete button visible on avatar detail", "Not found", "")
            return

        delete_btn.click()
        page.wait_for_timeout(1000)

        dialog_s = screenshot(page, "qa_06_delete_dialog.png")
        content = page.content()

        if "Delete Avatar" not in content and "Are you sure" not in content:
            add_bug("major", url, "Delete confirmation dialog did not open",
                    "Delete dialog with confirmation prompt opens",
                    "Dialog not found after clicking Delete", dialog_s)
        else:
            working.append("Delete confirmation dialog opens correctly")

            # Check for confirmation text
            if "cannot be undone" in content.lower() or "are you sure" in content.lower():
                working.append("Delete dialog shows appropriate warning text")
            else:
                add_bug("minor", url, "Delete dialog missing strong warning message",
                        "'This action cannot be undone' message in delete dialog",
                        "No strong warning found", dialog_s)

        # Cancel (press Escape or click Cancel)
        cancel_btn = page.locator("button:has-text('Cancel')").first
        if cancel_btn.count() > 0:
            cancel_btn.click()
        else:
            page.keyboard.press("Escape")
        page.wait_for_timeout(500)

        # Verify we're still on the avatar page
        current_url = page.url
        if "emi2souls" in current_url:
            working.append("Cancel on delete dialog keeps user on avatar detail page")

        print("  Delete dialog flow tested OK")

    except Exception as e:
        add_bug("major", url, f"Delete dialog test crashed: {e}",
                "Delete dialog test runs without error", traceback.format_exc(), "")
        print(f"  [CRASH] Delete dialog: {e}")


def test_start_pipeline_dialog(page: Page):
    print("\n[PHASE 3] Testing Start Pipeline Dialog...")
    url = f"{BASE_URL}/avatar/emi2souls"
    flows_tested.append("Start Pipeline Dialog")

    try:
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_timeout(4000)

        before_s = screenshot(page, "qa_07_pipeline_before.png")

        pipeline_btn = page.locator("button:has-text('Start Pipeline')").first
        if pipeline_btn.count() == 0:
            add_bug("major", url, "Start Pipeline button not found",
                    "Start Pipeline button visible", "Not found", before_s)
            return

        pipeline_btn.click()
        page.wait_for_timeout(1500)

        dialog_s = screenshot(page, "qa_07_pipeline_dialog.png")
        content = page.content()

        if "Start Pipeline" not in content or "Platforms" not in content:
            add_bug("major", url, "Start Pipeline dialog did not open or missing content",
                    "Pipeline dialog opens with platform options",
                    "Dialog content not found", dialog_s)
        else:
            working.append("Start Pipeline dialog opens with platform options")

        # Check TikTok checkbox present
        tiktok_check = page.locator("input[type='checkbox']").first
        if tiktok_check.count() > 0:
            working.append("Platform checkboxes present in Start Pipeline dialog")
        else:
            add_bug("minor", url, "No checkboxes in Start Pipeline dialog",
                    "TikTok/Instagram platform checkboxes visible",
                    "No checkbox inputs found", dialog_s)

        # Check limit field
        if "Limit per platform" in content or "limit" in content.lower():
            working.append("Limit field present in Start Pipeline dialog")

        # Check hashtags pre-filled from influencer
        hashtags_input = page.locator("input#hashtags").last
        if hashtags_input.count() > 0:
            ht_val = hashtags_input.input_value()
            if len(ht_val) > 0:
                working.append(f"Hashtags pre-filled in pipeline dialog: '{ht_val[:50]}'")
            else:
                add_bug("minor", url, "Hashtags not pre-filled in Start Pipeline dialog",
                        "Influencer hashtags pre-fill pipeline dialog",
                        "Hashtags input is empty", dialog_s)

        # Close dialog
        page.keyboard.press("Escape")
        page.wait_for_timeout(500)

        print("  Start Pipeline dialog tested OK")

    except Exception as e:
        add_bug("major", url, f"Start Pipeline dialog test crashed: {e}",
                "Pipeline dialog test runs without error", traceback.format_exc(), "")
        print(f"  [CRASH] Start Pipeline dialog: {e}")


def test_task_detail_video_click(page: Page):
    print("\n[PHASE 5] Testing Video Thumbnail Click & Modal...")
    url = f"{BASE_URL}/task/emi2souls/{KNOWN_RUN_ID}"
    flows_tested.append("Video Thumbnail Click Modal")

    try:
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_timeout(5000)

        # Find video thumbnails — look for clickable video items
        # These are img elements in clickable containers within the task detail
        clickable_videos = page.locator("[class*='cursor-pointer'] img, button img, [role='button'] img").all()

        if len(clickable_videos) == 0:
            # Try finding thumbnail cards which might be divs
            clickable_videos = page.locator("img[src*='/files/']").all()

        if len(clickable_videos) == 0:
            add_bug("minor", url, "No clickable video thumbnails found on task detail page",
                    "Video thumbnails clickable to open video player modal",
                    "No clickable image elements found", "")
            return

        working.append(f"Found {len(clickable_videos)} video thumbnails on task detail")

        # Click the first one
        first_thumb = clickable_videos[0]
        before_s = screenshot(page, "qa_08_video_before.png")

        first_thumb.click()
        page.wait_for_timeout(1500)

        after_s = screenshot(page, "qa_08_video_modal.png")
        content = page.content()

        # Check if a modal/dialog opened
        modal_open = page.evaluate("""() => {
            const dialog = document.querySelector('[role="dialog"]');
            const modal = document.querySelector('.modal, [data-state="open"]');
            return !!(dialog || modal);
        }""")

        video_el = page.query_selector("video")

        if modal_open or video_el:
            working.append("Video modal/player opens on thumbnail click")

            if video_el:
                video_src = video_el.get_attribute("src") or ""
                if video_src and "/files/" in video_src:
                    working.append(f"Video element has valid src: {video_src[:80]}...")
                elif not video_src:
                    add_bug("major", url, "Video element in modal has no src attribute",
                            "Video src attribute set to valid file URL",
                            "Video src is empty", after_s)

            # Close the modal
            page.keyboard.press("Escape")
            page.wait_for_timeout(800)

            close_check_s = screenshot(page, "qa_08_video_closed.png")
            modal_still_open = page.evaluate("""() => {
                const dialog = document.querySelector('[data-state="open"]');
                return !!dialog;
            }""")
            if not modal_still_open:
                working.append("Video modal closes correctly with Escape key")
            else:
                add_bug("minor", url, "Video modal does not close with Escape key",
                        "Pressing Escape closes video modal",
                        "Modal still appears open after Escape", close_check_s)
        else:
            add_bug("major", url, "Video thumbnail click does not open modal/player",
                    "Clicking video thumbnail opens a video player modal",
                    "No dialog or video element found after click",
                    after_s)

        print("  Video thumbnail click tested")

    except Exception as e:
        add_bug("major", url, f"Video thumbnail click test crashed: {e}",
                "Video click test runs without error", traceback.format_exc(), "")
        print(f"  [CRASH] Video thumbnail: {e}")


def test_navigation_flows(page: Page):
    print("\n[PHASE 3] Testing Navigation Flows...")
    flows_tested.append("Navigation: Home -> Avatar -> Task")

    try:
        # Navigate from home to avatar
        page.goto(f"{BASE_URL}/", wait_until="domcontentloaded")
        page.wait_for_timeout(3000)

        # Click emi2souls card
        emi_link = page.locator("a[href='/avatar/emi2souls']").first
        if emi_link.count() == 0:
            # Try text-based
            emi_link = page.locator("text=Emi Noir").first

        if emi_link.count() > 0:
            emi_link.click()
            page.wait_for_timeout(4000)

            current_url = page.url
            if "avatar/emi2souls" in current_url:
                working.append("Navigation from home to emi2souls avatar detail works")
            else:
                add_bug("major", f"{BASE_URL}/", "Clicking influencer card does not navigate to detail page",
                        "Clicking card navigates to /avatar/emi2souls",
                        f"Current URL: {current_url}", screenshot(page, "qa_09_nav_fail.png"))
                return

            # Now find a task link and click it
            task_link = page.locator(f"a[href*='/task/emi2souls/']").first
            if task_link.count() > 0:
                task_link.click()
                page.wait_for_timeout(4000)

                current_url = page.url
                if "/task/" in current_url:
                    working.append("Navigation from avatar detail to task detail works")

                    # Click back button
                    back_btn = page.locator("button:has-text('Back')").first
                    if back_btn.count() > 0:
                        back_btn.click()
                        page.wait_for_timeout(2000)
                        if "avatar/emi2souls" in page.url:
                            working.append("Back button on task detail navigates back to avatar detail")
                        else:
                            add_bug("minor", current_url, "Back button on task detail doesn't navigate to avatar",
                                    "Back button returns to avatar detail page",
                                    f"URL after back: {page.url}", "")
                else:
                    add_bug("major", f"{BASE_URL}/avatar/emi2souls",
                            "Clicking task card does not navigate to task detail",
                            "Clicking task card navigates to /task/emi2souls/{run_id}",
                            f"Current URL: {current_url}", screenshot(page, "qa_09_task_nav_fail.png"))
            else:
                add_bug("major", f"{BASE_URL}/avatar/emi2souls",
                        "No task card links found on avatar detail page",
                        "Task cards should be clickable links",
                        "No <a> elements with /task/ href found", "")
        else:
            add_bug("critical", f"{BASE_URL}/",
                    "Cannot find clickable emi2souls card on home page",
                    "Influencer cards are clickable links",
                    "No link found for emi2souls", "")

        print("  Navigation flows tested")

    except Exception as e:
        add_bug("major", f"{BASE_URL}/", f"Navigation flow test crashed: {e}",
                "Navigation works without error", traceback.format_exc(), "")
        print(f"  [CRASH] Navigation: {e}")


# ============================================================
# PHASE 4: State Persistence
# ============================================================

def test_state_persistence(page: Page):
    print("\n[PHASE 4] Testing State Persistence after reload...")
    url = f"{BASE_URL}/task/emi2souls/{KNOWN_RUN_ID}"
    flows_tested.append("State Persistence after Reload")

    try:
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_timeout(5000)
        content_before = page.content()

        has_completed = "completed" in content_before
        has_run_id = KNOWN_RUN_ID in content_before

        page.reload(wait_until="domcontentloaded")
        page.wait_for_timeout(5000)
        content_after = page.content()

        # Compare key data points
        if has_run_id and KNOWN_RUN_ID not in content_after:
            add_bug("critical", url, "Run ID disappears after page reload",
                    "Same task data visible after page reload",
                    "Run ID not found after reload", screenshot(page, "qa_10_reload_fail.png"))
        elif has_completed and "completed" not in content_after:
            add_bug("major", url, "Stage completion status lost after page reload",
                    "Completed stages still show 'completed' after reload",
                    "No 'completed' found after reload", screenshot(page, "qa_10_reload_fail.png"))
        else:
            working.append("Page state persists correctly after reload (data from API)")
            screenshot(page, "qa_10_after_reload.png")

        print("  State persistence tested")

    except Exception as e:
        add_bug("major", url, f"State persistence test crashed: {e}",
                "State persistence test runs without error", traceback.format_exc(), "")
        print(f"  [CRASH] State persistence: {e}")


# ============================================================
# PHASE 6: Error States
# ============================================================

def test_invalid_avatar(page: Page):
    print("\n[PHASE 6] Testing Invalid Avatar Route...")
    url = f"{BASE_URL}/avatar/nonexistent-id-xyz"
    flows_tested.append("Invalid Avatar Route")

    try:
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_timeout(3000)
        pages_tested.append(url)

        s = screenshot(page, "qa_11_invalid_avatar.png")
        content = page.content()

        # Should show a graceful error, not a crash or stack trace
        shows_error_gracefully = (
            "not found" in content.lower() or
            "Avatar not found" in content or
            "Back to" in content
        )
        shows_crash = "TypeError" in content or "ReferenceError" in content or "Error:" in content

        if shows_crash:
            add_bug("critical", url, "Invalid avatar route shows JavaScript error/stack trace",
                    "Graceful 'not found' message displayed",
                    "JavaScript error or stack trace visible", s)
        elif shows_error_gracefully:
            working.append("Invalid avatar route shows graceful error/not-found state")
        else:
            add_bug("minor", url, "Invalid avatar route doesn't show user-friendly error",
                    "User-friendly 'avatar not found' message",
                    "No error message and no crash — unclear state", s)

        print("  Invalid avatar route tested")

    except Exception as e:
        add_bug("major", url, f"Invalid avatar route test crashed: {e}",
                "Test runs without error", traceback.format_exc(), "")
        print(f"  [CRASH] Invalid avatar: {e}")


def test_invalid_task(page: Page):
    print("\n[PHASE 6] Testing Invalid Task Route...")
    url = f"{BASE_URL}/task/emi2souls/invalid-task-id-xyz"
    flows_tested.append("Invalid Task Route")

    try:
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_timeout(3000)
        pages_tested.append(url)

        s = screenshot(page, "qa_12_invalid_task.png")
        content = page.content()

        shows_error_gracefully = (
            "not found" in content.lower() or
            "Task not found" in content or
            "Back to" in content
        )
        shows_crash = "TypeError" in content or "ReferenceError" in content

        if shows_crash:
            add_bug("critical", url, "Invalid task route shows JavaScript error/stack trace",
                    "Graceful 'task not found' message",
                    "JavaScript error visible", s)
        elif shows_error_gracefully:
            working.append("Invalid task route shows graceful error state")
        else:
            add_bug("minor", url, "Invalid task route doesn't show user-friendly error",
                    "User-friendly 'task not found' message",
                    "No clear error state", s)

        print("  Invalid task route tested")

    except Exception as e:
        add_bug("major", url, f"Invalid task route test crashed: {e}",
                "Test runs without error", traceback.format_exc(), "")
        print(f"  [CRASH] Invalid task: {e}")


def test_generation_section(page: Page):
    print("\n[PHASE 5] Testing Generation/Server Section on Task Detail...")
    url = f"{BASE_URL}/task/emi2souls/{KNOWN_RUN_ID}"
    flows_tested.append("Generation Section UI")

    try:
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_timeout(5000)

        # Scroll to bottom to find generation section
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(1000)

        s = screenshot(page, "qa_13_generation_section.png")
        content = page.content()

        # Check generation job status — should show "lost" since server restarted
        if "generation" in content.lower() or "Generation" in content:
            working.append("Generation section visible on task detail")

            # Generation jobs were started but server restarted, so status should be "lost"
            if "lost" in content:
                working.append("Generation jobs correctly show 'lost' status after server restart")
            elif "completed" in content:
                working.append("Generation jobs show 'completed' status")

            # Check for server management UI elements
            if "Server" in content or "GPU" in content:
                working.append("Server management info visible in generation section")

            # Check for "Run All" or generation trigger
            if "Run" in content and ("Generate" in content or "generation" in content.lower()):
                working.append("Generation controls visible")

        # Check if generation job count shows 3 (we know there are 3 jobs)
        if "3" in content:
            # This is a very loose check — just verifying some numbers render
            pass

        # Check for any rendering of the 3 generation jobs
        gen_job_elements = page.evaluate("""() => {
            // Look for job ID patterns like short hex strings
            const text = document.body.innerText;
            const hexPattern = /[0-9a-f]{12}/g;
            const matches = text.match(hexPattern) || [];
            return matches.slice(0, 5);
        }""")
        if gen_job_elements:
            working.append(f"Generation job IDs visible: {gen_job_elements[:2]}")

        page.evaluate("window.scrollTo(0, 0)")

        print("  Generation section tested")

    except Exception as e:
        add_bug("major", url, f"Generation section test crashed: {e}",
                "Generation section test runs without error", traceback.format_exc(), "")
        print(f"  [CRASH] Generation section: {e}")


def test_review_section(page: Page):
    print("\n[PHASE 5] Testing Review Section on Task Detail...")
    url = f"{BASE_URL}/task/emi2souls/{KNOWN_RUN_ID}"
    flows_tested.append("Review Section UI")

    try:
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_timeout(5000)

        content = page.content()

        # Review is completed with some approved and some skipped
        if "Review" in content:
            # Count approved items (we know 2 are approved from API)
            if "approved" in content.lower():
                working.append("Review section shows approved video count")

            if "skipped" in content.lower() or "skip" in content.lower():
                working.append("Review section shows skipped video count")

            # Check for prompt display
            if "sks girl" in content:
                working.append("Review prompts display correctly (e.g., 'sks girl poses with iphone')")

            # Screenshot the review area
            screenshot(page, "qa_14_review_section.png")

        print("  Review section tested")

    except Exception as e:
        add_bug("major", url, f"Review section test crashed: {e}",
                "Review section test runs without error", traceback.format_exc(), "")
        print(f"  [CRASH] Review section: {e}")


def test_cleanup_qa_influencer(page: Page):
    """Delete the QA test influencer created during testing."""
    print("\n[CLEANUP] Deleting QA test influencer...")
    try:
        import requests
        resp = requests.delete(f"{BASE_URL}/api/v1/influencers/qa_test_influencer_delete_me")
        if resp.status_code in (200, 404):
            print(f"  Cleanup: qa_test_influencer_delete_me deleted (status {resp.status_code})")
        else:
            print(f"  Cleanup: unexpected status {resp.status_code}")
    except Exception as e:
        print(f"  Cleanup failed: {e}")


def test_console_errors_summary(page: Page):
    """Check a fresh load of key pages for console errors."""
    print("\n[PHASE 2] Checking console errors on fresh page loads...")

    test_pages = [
        (f"{BASE_URL}/", "home"),
        (f"{BASE_URL}/avatar/emi2souls", "avatar_emi"),
        (f"{BASE_URL}/task/emi2souls/{KNOWN_RUN_ID}", "task_detail"),
    ]

    for url, label in test_pages:
        try:
            errors_before = len(console_errors)
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_timeout(4000)
            errors_after = len(console_errors)

            new_errors = console_errors[errors_before:errors_after]
            errors_only = [e for e in new_errors if "ERROR" in e]

            if errors_only:
                print(f"  Console errors on {label}: {len(errors_only)}")
                for err in errors_only[:5]:
                    print(f"    {err[:120]}")
            else:
                working.append(f"No JavaScript console errors on {label} page")
        except Exception as e:
            print(f"  [CHECK ERROR] Console errors on {label}: {e}")


# ============================================================
# ADDITIONAL: Check API endpoints used by the frontend
# ============================================================

def test_api_endpoints(page: Page):
    """Verify key API endpoints return proper data."""
    print("\n[PHASE 2] Testing API Endpoints...")
    flows_tested.append("API Endpoint Validation")

    endpoints = [
        ("/api/v1/influencers", "GET influencers list"),
        ("/api/v1/influencers/emi2souls", "GET single influencer"),
        (f"/api/v1/parser/runs?influencer_id=emi2souls&limit=5", "GET pipeline runs"),
        (f"/api/v1/parser/runs/{KNOWN_RUN_ID}?influencer_id=emi2souls", "GET single run"),
        ("/api/v1/generation/server/status", "GET server status"),
        ("/api/v1/generation/servers", "GET servers list"),
        ("/api/v1/parser/defaults", "GET parser defaults"),
        ("/api/v1/jobs?limit=10", "GET jobs list"),
    ]

    for path, label in endpoints:
        try:
            response = page.evaluate(f"""async () => {{
                const r = await fetch('{path}');
                const text = await r.text();
                let data;
                try {{ data = JSON.parse(text); }} catch(e) {{ data = text.substring(0, 100); }}
                return {{status: r.status, isJson: typeof data !== 'string', preview: JSON.stringify(data).substring(0, 100)}};
            }}""")

            if response['status'] == 200 and response['isJson']:
                working.append(f"API endpoint OK: {label} ({path})")
            elif response['status'] == 200 and not response['isJson']:
                add_bug("major", f"{BASE_URL}{path}",
                        f"API endpoint returns HTML instead of JSON: {label}",
                        "API returns valid JSON",
                        f"Response is not JSON. Preview: {response['preview'][:80]}",
                        "")
            else:
                add_bug("major", f"{BASE_URL}{path}",
                        f"API endpoint returned unexpected status: {label}",
                        "API returns 200 OK",
                        f"Status: {response['status']}, Preview: {response['preview'][:80]}",
                        "")
        except Exception as e:
            add_bug("major", f"{BASE_URL}{path}",
                    f"API endpoint test failed: {label}",
                    "API endpoint accessible from browser",
                    f"Error: {e}", "")


# ============================================================
# MAIN
# ============================================================

def main():
    print("=" * 60)
    print("QA TEST SUITE — AI Influencer Studio")
    print(f"Date: {datetime.datetime.now().isoformat()}")
    print(f"Base URL: {BASE_URL}")
    print("=" * 60)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="QA-Test-Bot/1.0",
        )
        page = context.new_page()

        # Run all test phases
        test_home_page(page)
        test_avatar_detail_page(page, "emi2souls")
        test_avatar_detail_page(page, "grannys")
        test_task_detail_page(page)

        # API validation
        test_api_endpoints(page)

        # Interactive flows
        test_create_influencer_dialog(page)
        test_edit_influencer_dialog(page)
        test_delete_dialog(page)
        test_start_pipeline_dialog(page)

        # Navigation
        test_navigation_flows(page)

        # Video playback
        test_task_detail_video_click(page)

        # Section-specific tests
        test_review_section(page)
        test_generation_section(page)

        # State persistence
        test_state_persistence(page)

        # Error states
        test_invalid_avatar(page)
        test_invalid_task(page)

        # Console error summary
        test_console_errors_summary(page)

        # Cleanup
        test_cleanup_qa_influencer(page)

        browser.close()

    # ============================================================
    # FINAL REPORT
    # ============================================================
    print("\n")
    print("=" * 60)
    print("=== QA TEST REPORT — AI Influencer Studio ===")
    print(f"Date: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Base URL: {BASE_URL}")
    print()
    print("--- SUMMARY ---")
    print(f"Pages Tested: {len(set(pages_tested))}")
    print(f"Flows Tested: {len(flows_tested)}")
    criticals = [b for b in bugs if b['severity'] == 'critical']
    majors = [b for b in bugs if b['severity'] == 'major']
    minors = [b for b in bugs if b['severity'] == 'minor']
    print(f"Total Bugs Found: {len(bugs)} (Critical: {len(criticals)}, Major: {len(majors)}, Minor: {len(minors)})")
    print()

    if console_errors:
        print("--- CONSOLE ERRORS CAPTURED ---")
        unique_errors = list(dict.fromkeys(console_errors))
        for e in unique_errors[:20]:
            print(f"  {e[:150]}")
        print()

    print("--- BUGS ---")
    for bug in bugs:
        print(f"\nBUG #{bug['id']} | {bug['severity'].upper()}")
        print(f"Page: {bug['page']}")
        print(f"Description: {bug['description']}")
        print(f"Expected: {bug['expected']}")
        print(f"Actual: {bug['actual'][:200]}")
        if bug['screenshot']:
            print(f"Screenshot: {bug['screenshot']}")
        print("---")

    print("\n--- WORKING CORRECTLY ---")
    for item in working:
        print(f"  - {item}")

    print("\n--- RECOMMENDATIONS ---")
    if criticals:
        print("CRITICAL (Fix immediately):")
        for b in criticals:
            print(f"  - [BUG #{b['id']}] {b['description']}")
    if majors:
        print("MAJOR (Fix before next release):")
        for b in majors:
            print(f"  - [BUG #{b['id']}] {b['description']}")
    if minors:
        print("MINOR (Improvements):")
        for b in minors:
            print(f"  - [BUG #{b['id']}] {b['description']}")

    if not bugs:
        print("  No bugs found! All tests passed.")

    print("\n--- SCREENSHOTS ---")
    import os
    ss_files = sorted(f for f in os.listdir(SCREENSHOTS_DIR) if f.startswith("qa_"))
    for f in ss_files:
        print(f"  {SCREENSHOTS_DIR}/{f}")

    print("\n" + "=" * 60)
    return len(criticals) > 0


if __name__ == "__main__":
    import sys
    has_criticals = main()
    sys.exit(1 if has_criticals else 0)

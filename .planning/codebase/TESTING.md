# Testing Patterns

**Analysis Date:** 2026-03-27

## Test Framework

**Runner:**
- No pytest or unittest configured for backend unit tests
- Frontend: No test runner (Jest, Vitest) configured
- E2E: Playwright (`playwright.sync_api`) for browser automation

**Test file location:**
- `qa_test.py` at project root — comprehensive E2E test suite
- No unit test files detected in `src/` directories
- No component tests for frontend

**Run commands:**
```bash
# Run E2E tests (Playwright-based)
python qa_test.py

# Build frontend before testing
cd frontend && ./node_modules/.bin/vite build
```

## Test File Organization

**Location:**
- E2E tests: `qa_test.py` at project root (not co-located with source)

**Structure:**
```python
qa_test.py
├── Setup: BASE_URL, SCREENSHOTS_DIR, INFLUENCER_IDS, KNOWN_RUN_ID
├── Global state: bugs[], pages_tested[], flows_tested[], console_errors[], working[]
├── Helpers: next_bug_id(), add_bug(), screenshot(), setup_console_capture()
├── Checks: check_no_object_object(), check_no_undefined_null(), check_images()
└── Test phases (functions): test_home_page(), test_avatar_detail(), test_review_panel(), ...
```

## Test Structure

**Suite organization (Playwright E2E pattern):**

```python
def test_home_page(page: Page):
    """Test phase with setup, assertions, and error tracking."""
    print("\n[PHASE 1] Testing Home Page...")
    url = f"{BASE_URL}/"
    setup_console_capture(page, "home")  # Attach JS error listener

    try:
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_timeout(4000)  # Allow async load
        pages_tested.append(url)

        s = screenshot(page, "qa_01_home_load.png")

        # Assertion-like checks
        title = page.title()
        if "Avatar" not in title:
            add_bug("minor", url, "Page title issue", "...", "...", s)

        # Data integrity checks
        check_no_object_object(page, url, "Home Page")
        check_no_undefined_null(page, url, "Home Page")
        check_images(page, url, "Home Page")

    except Exception as e:
        add_bug("critical", url, f"Test crashed: {e}", "...", traceback.format_exc(), "")
```

**Patterns:**

1. **Setup:** Initialize page, attach console/error listeners, navigate
2. **Assertions:** Use locators (`page.locator()`), `.count()`, `.inner_text()`, `.content()`
3. **JavaScript evaluation:** Use `page.evaluate()` to run in-browser code for DOM inspection
4. **Timeouts:** Explicit `page.wait_for_timeout()` for async operations
5. **Screenshots:** Capture state at key points, store path in bug report
6. **Try-except:** Wrap all test logic to prevent cascade failures

## Mocking

**Framework:** No mocking library used; tests are live E2E against real API

**Service mocking:**
- Backend has `VastAgentServiceMock` in `src/vast_agent/service_mock.py` for development
- Activated via `VAST_MOCK=1` environment variable
- Allows testing without VastAI rental costs

**Test data:**
- Uses real influencers: `INFLUENCER_IDS = ["emi2souls", "grannys"]`
- Known run: `KNOWN_RUN_ID = "20260316_112159"` for stable test references

## Fixtures and Test Data

**Test data patterns:**

```python
BASE_URL = "http://localhost:8000"
SCREENSHOTS_DIR = "/tmp/screenshots"
INFLUENCER_IDS = ["emi2souls", "grannys"]
KNOWN_RUN_ID = "20260316_112159"
```

**Global state for test tracking:**
```python
bugs = []                    # Collected issues
bug_id_counter = [0]        # Counter (in list for mutability in nested functions)
pages_tested = []           # URLs visited
flows_tested = []           # Test flows completed
console_errors = []         # JS errors/warnings captured
working = []                # Successfully validated features
```

**Error tracking:**
```python
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
```

## Coverage

**Requirements:** No formal coverage enforcement detected

**Test scope:**
- E2E tests cover: Page loads, navigation, data display, form submission, image loading
- NOT covered: Unit tests, API contract tests, database migrations
- NOT covered: Frontend component isolation tests

**Current E2E coverage from `qa_test.py`:**

| Phase | Scope | Coverage |
|-------|-------|----------|
| 1 | Home page load, influencer cards, buttons | Page title, heading, cards, button presence |
| 2 | Avatar detail page, pipeline runs | Run metadata, status display, links |
| 3 | Review panel, approval flow | Video display, form submission, auto-save |
| 4 | Generation flow | Job creation, status tracking, server selection |
| 5 | Generated content display | Video player, download, sharing |

**Data integrity checks applied to all pages:**
- `check_no_object_object()` — Detects `[object Object]` rendered as text
- `check_no_undefined_null()` — Detects raw `undefined` or `null` displayed
- `check_images()` — Detects broken image URLs (empty src)

## Test Types

**E2E Tests (Playwright):**
- **Scope:** Full browser → API → database workflow
- **Approach:** Page-based test functions, shared `Page` instance from `sync_playwright()`
- **Environment:** Requires running backend at `http://localhost:8000`
- **State:** Uses real database and files; not isolated between tests (sequential)

**Unit Tests:**
- Not implemented; codebase lacks unit test framework

**Integration Tests:**
- Not implemented separately; E2E tests serve this purpose

**Component Tests (React):**
- Not implemented; no test runner configured

## Common Patterns

**Page interaction pattern:**
```python
# Locator pattern
heading = page.locator("h1").first
if heading.count() > 0:
    text = heading.inner_text()

# Broader selector fallback
cards = page.locator("a.group").all()
if not cards:
    cards = page.locator("[class*='Card']").all()

# CSS selector + content matching
create_btn = page.locator("text=Create New Avatar")
if create_btn.count() > 0:
    create_btn.click()
```

**DOM inspection pattern (JavaScript evaluation):**
```python
# Check image state in browser
profile_imgs = page.evaluate("""() => {
    const imgs = Array.from(document.querySelectorAll('img'));
    return imgs.map(img => ({
        src: img.src,
        naturalWidth: img.naturalWidth,
        complete: img.complete
    }));
}""")

for img in profile_imgs:
    if img.get('src') and img.get('naturalWidth', 0) == 0:
        # Image failed to load
        add_bug(...)
```

**Error tracking pattern:**
```python
def check_no_undefined_null(page: Page, url: str, context: str):
    try:
        found = page.evaluate("""...""")  # Run JS
        if found:
            s = screenshot(page, f"qa_bug_{next_bug_id():02d}_undefined_null.png")
            add_bug("major", url, f"Found undefined/null on {context}", ...)
            return True
    except Exception as e:
        print(f"  [CHECK ERROR] undefined/null check: {e}")
    return False
```

**Async/async-like patterns (Playwright is sync in this context):**
- `page.goto()` and `page.wait_for_timeout()` block until complete
- `page.evaluate()` executes JS synchronously and returns result
- No Promise handling in the sync API

**Console capture pattern:**
```python
def setup_console_capture(page: Page, page_label: str):
    def on_console(msg):
        if msg.type in ("error", "warning"):
            entry = f"[{page_label}] {msg.type.upper()}: {msg.text}"
            console_errors.append(entry)

    def on_page_error(err):
        entry = f"[{page_label}] PAGE ERROR: {err}"
        console_errors.append(entry)

    page.on("console", on_console)
    page.on("pageerror", on_page_error)
```

## Test Execution Flow

**Setup phase (qa_test.py):**
1. Create Playwright context: `sync_playwright()` → `browser.new_context()` → `context.new_page()`
2. Set viewport and user agent if needed
3. Attach console/error listeners via `setup_console_capture()`
4. Navigate to URL with `page.goto(..., wait_until="domcontentloaded")`
5. Wait for dynamic content: `page.wait_for_timeout(ms)`

**Assertion phase:**
1. Use locators to inspect DOM: `page.locator()`, `.count()`, `.inner_text()`
2. Evaluate JavaScript for complex checks: `page.evaluate()`
3. Compare against expected values
4. Capture screenshot on failure

**Cleanup phase:**
1. No explicit cleanup per test (Playwright handles context lifecycle)
2. Final report generation: print bugs, console errors, working features

## Known Testing Limitations

**Gaps:**
- No backend unit tests for critical modules (`job_manager.py`, `database.py`, `vast_agent/manager.py`)
- No API contract tests (Pydantic models in backend ↔ TypeScript types in frontend)
- No database migration tests
- No frontend component tests
- No async/parallel test execution (E2E tests run sequentially)
- No test isolation — shared database state between tests

**How to address:**
- Add pytest unit tests in `tests/` directory for core logic
- Add pydantic model round-trip validation tests
- Consider adding integration tests for critical database operations
- Add Vitest for frontend component testing if needed

---

*Testing analysis: 2026-03-27*

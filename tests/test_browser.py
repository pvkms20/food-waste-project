"""Real-browser tests (Playwright + Chromium) against a live server on a throwaway database.

Skipped automatically when Playwright or its browser is not installed.
Screenshots are written to reports/screenshots (ignored by git).
"""
import os
import re
import threading
from datetime import datetime, timedelta

import pytest

playwright_sync = pytest.importorskip("playwright.sync_api")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHOTS = os.path.join(ROOT, "reports", "screenshots")
SIZES = {"desktop": (1366, 800), "tablet": (820, 1100), "mobile": (390, 844)}


@pytest.fixture(scope="module")
def live(request):
    import app as appmod
    from werkzeug.serving import make_server
    appmod.reset_demo()
    server = make_server("127.0.0.1", 0, appmod.app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


@pytest.fixture(scope="module")
def browser():
    with playwright_sync.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as exc:  # browser binary missing
            pytest.skip(f"Chromium not available: {exc}")
        yield b
        b.close()


def new_page(browser, size="desktop", errors=None):
    w, h = SIZES[size]
    page = browser.new_context(viewport={"width": w, "height": h}).new_page()
    if errors is not None:
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    return page


def shot(page, name):
    os.makedirs(SHOTS, exist_ok=True)
    page.screenshot(path=os.path.join(SHOTS, name + ".png"), full_page=True)


def no_horizontal_scroll(page):
    return page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1")


def test_fifteen_step_journey(live, browser):
    errors = []
    page = new_page(browser, "desktop", errors)
    expiry = (datetime.now() + timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M")

    # 1. Prediction screen is the main screen with the sidebar
    page.goto(live + "/")
    assert page.locator("#predict-form").is_visible()
    assert page.locator("aside.sidebar").is_visible() and page.locator(".navbar").count() == 0
    assert page.locator('aside a[aria-current="page"]').inner_text().strip() == "Waste Prediction"
    shot(page, "01-prediction-desktop")

    # 2. Empty and implausible input is blocked client-side with field messages
    page.fill("#meals_served", "")
    page.click("#predict-btn")
    assert "required" in page.locator("#e-meals").inner_text()
    assert page.locator("#result").is_hidden()
    page.fill("#meals_served", "10"); page.fill("#food_prepared_kg", "9000")
    page.click("#predict-btn")
    assert "not plausible" in page.locator("#e-prep").inner_text()

    # 3. Valid prediction: result revealed and scrolled into view, three distinct quantities
    page.fill("#meals_served", "300"); page.fill("#food_prepared_kg", "140")
    page.fill("#edible_percent", "80")
    page.click("#predict-btn")
    page.wait_for_selector("#result:not([hidden])")
    page.wait_for_function("document.getElementById('q-waste').textContent !== '0'")
    page.wait_for_timeout(900)
    waste = float(page.locator("#q-waste").inner_text())
    edible = float(page.locator("#q-edible").inner_text())
    offer = float(page.locator("#q-offer").inner_text())
    assert waste > 0 and abs(edible - waste * 0.8) < 0.15 and offer <= edible
    top = page.evaluate("document.getElementById('result').getBoundingClientRect().top")
    assert -5 <= top < 400, top
    assert page.locator("#decision-title").inner_text() and page.locator("#range-bar").is_visible()
    shot(page, "02-prediction-result-desktop")

    # 4. Inputs outside the training range produce a visible warning
    page.fill("#meals_served", "5000"); page.fill("#food_prepared_kg", "1750")
    page.click("#predict-btn")
    page.wait_for_function("!document.getElementById('input-warnings').hidden")
    assert "outside the range" in page.locator("#input-warnings").inner_text()

    # 5. Create a donation from the prediction
    page.fill("#meals_served", "300"); page.fill("#food_prepared_kg", "140")
    page.click("#predict-btn")
    page.wait_for_function("!document.getElementById('donate-link').hidden")
    page.click("#donate-link")
    assert "/donate?qty=" in page.url and page.input_value("#quantity_kg")

    # 6. Register it
    page.fill("#donor", "Browser Test Hostel"); page.fill("#quantity_kg", "20"); page.fill("#expiry_time", expiry)
    page.click("button[type=submit]")
    page.wait_for_url(re.compile(r"/donation/DON-[0-9A-F]{8}$"))
    code = page.url.rsplit("/", 1)[1]
    assert "recorded on the blockchain" in page.locator(".alert").first.inner_text()

    # 7. QR code image really loaded
    assert page.locator(".qr-box img").evaluate("i => i.complete && i.naturalWidth > 0")
    shot(page, "03-donation-pending-desktop")

    # 8. Public tracking page hides the donor and the controls
    public = new_page(browser, "mobile", errors)
    public.goto(f"{live}/track/{code}")
    body = public.inner_text("body")
    assert code in body and "Browser Test Hostel" not in body and public.locator("main form").count() == 0
    assert no_horizontal_scroll(public)
    shot(public, "04-public-tracking-mobile")

    # 9-12. Walk the whole lifecycle with the operator buttons
    for label, extra in (("Accepted by NGO", None), ("Picked up", ("collected_kg", "18")),
                         ("In transit", None), ("Delivered", ("delivered_kg", "17.5"))):
        form = page.locator(f'form:has(button:has-text("Mark as {label}"))')
        if extra:
            form.locator(f'input[name="{extra[0]}"]').fill(extra[1])
        form.locator("button").click()
        page.wait_for_load_state()
        assert "Status updated" in page.locator(".alert").first.inner_text()

    # 13. Timeline: every step done, real timestamps, no more actions
    assert page.locator(".tl-step.done").count() == 5
    assert "No further changes are possible" in page.inner_text("body")
    assert "17.5 kg" in page.locator("dl.kv").inner_text()
    shot(page, "05-donation-delivered-desktop")

    # 14. Verify by reference
    page.goto(f"{live}/verify?code={code}")
    assert page.locator(".verdict h2").first.inner_text().strip() == "Verified"
    assert page.locator(".checklist li .bad").count() == 0
    shot(page, "06-verify-desktop")

    # 15. Demo tamper test is detected, then restored
    page.goto(live + "/chain")
    page.locator("details:has-text('Demo tools') summary").click()
    page.once("dialog", lambda d: d.accept())
    page.click("button:has-text('Demo tamper test')")
    page.wait_for_url(re.compile(r"/verify$"))
    assert "tampering detected" in page.inner_text("body").lower()
    shot(page, "07-tamper-detected-desktop")
    page.goto(live + "/chain")
    page.locator("details:has-text('Demo tools') summary").click()
    page.click("button:has-text('Restore original')")
    page.goto(f"{live}/verify?code={code}")
    assert page.locator(".verdict h2").first.inner_text().strip() == "Verified"

    assert errors == [], errors


def test_status_page_is_a_page_and_json_still_works(live, browser):
    page = new_page(browser)
    page.goto(live + "/status")
    assert "System status" in page.inner_text("h1") and "{" not in page.inner_text("main")[:5]
    resp = page.request.get(live + "/status?format=json")
    assert resp.json()["blockchain_valid"] is True


def test_sidebar_collapses_and_remembers(live, browser):
    page = new_page(browser)
    page.goto(live + "/overview")
    w_open = page.locator("aside.sidebar").evaluate("e => e.getBoundingClientRect().width")
    page.click("#sb-toggle")
    page.wait_for_timeout(400)
    w_closed = page.locator("aside.sidebar").evaluate("e => e.getBoundingClientRect().width")
    assert w_closed < 90 < 200 < w_open
    assert page.locator("aside a[aria-label='Overview']").get_attribute("title") == "Overview"   # tooltip when collapsed
    page.reload()
    assert page.locator("aside.sidebar").evaluate("e => e.getBoundingClientRect().width") < 90
    shot(page, "08-sidebar-collapsed-desktop")


@pytest.mark.parametrize("size", ["tablet", "mobile"])
def test_pages_fit_small_screens(live, browser, size):
    errors = []
    page = new_page(browser, size, errors)
    for path in ("/", "/overview", "/donations", "/donate", "/track", "/verify", "/chain", "/model", "/status"):
        page.goto(live + path)
        assert no_horizontal_scroll(page), (size, path)
    if size == "mobile":
        page.goto(live + "/")
        assert page.locator("aside.sidebar").evaluate("e => e.getBoundingClientRect().right") <= 0   # hidden off-canvas
        page.click("#nav-open")
        page.wait_for_timeout(400)
        assert page.locator("aside.sidebar").evaluate("e => e.getBoundingClientRect().left") >= 0
        shot(page, "09-mobile-menu")
        page.keyboard.press("Escape")
        page.wait_for_timeout(400)
        assert page.locator("aside.sidebar").evaluate("e => e.getBoundingClientRect().right") <= 0
    shot(page, f"10-overview-{size}")
    assert errors == [], errors


def test_keyboard_and_labels(live, browser):
    page = new_page(browser)
    page.goto(live + "/")
    # every form control has an accessible name
    names = page.evaluate("""() => [...document.querySelectorAll('#predict-form input, #predict-form select')]
        .filter(e => e.type !== 'hidden').map(e => !!(e.labels && e.labels.length) || !!e.getAttribute('aria-label'))""")
    assert all(names)
    page.keyboard.press("Tab")
    assert page.evaluate("document.activeElement.className") == "skip"       # skip link is first focus stop
    assert page.evaluate("document.querySelectorAll('h1').length") == 1
    assert page.evaluate("document.documentElement.lang") == "en"

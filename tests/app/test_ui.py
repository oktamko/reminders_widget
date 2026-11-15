"""The window's HTML/JS against a fake window.pywebview.api (Playwright, headless)."""
import json
import os
from pathlib import Path

import pytest

UI = Path(__file__).resolve().parents[2] / "src" / "reminders_widget" / "app" / "ui" / "index.html"
BROWSERS = [r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Google\Chrome\Application\chrome.exe"]
EXE = next((b for b in BROWSERS if os.path.exists(b)), None)
pytestmark = pytest.mark.skipif(EXE is None, reason="no Chromium-based browser for UI tests")

STATE = {
    "lists": [{"id": "List/L1", "name": "Work", "color": None}, {"id": "List/L2", "name": "School", "color": "#F0997B"}],
    "selected": ["List/L1", "List/L2"], "sort": "priority", "title": "All",
    "reminders": [
        {"id": "Reminder/A", "list": "List/L1", "title": "Alpha", "notes": "", "priority": 0, "due": None,
         "due_label": "", "overdue": False, "all_day": False, "readonly": False, "recurring": False},
        {"id": "Reminder/RO", "list": "List/L2", "title": "Locked", "notes": "", "priority": 1, "due": None,
         "due_label": "", "overdue": False, "all_day": False, "readonly": True, "recurring": False},
    ],
    "status": "ok", "banner": None,
}

FAKE = """
window.__calls = [];
window.__state = %s;
const ok = () => ({ok: true, state: window.__state});
const rec = name => (...args) => { window.__calls.push([name, ...args]);
  return Promise.resolve(window.__fail ? {ok: false, message: 'Nope.'} : ok()); };
window.pywebview = {api: {
  state: () => Promise.resolve(window.__state),
  select: rec('select'), set_sort: rec('set_sort'), add: rec('add'), complete: rec('complete'),
  set_priority: rec('set_priority'), rename: rec('rename'), set_notes: rec('set_notes'),
  set_due: rec('set_due'), move: rec('move'), sign_in: rec('sign_in'), open_update: rec('open_update'), set_view: rec('set_view'),
}};
window.addEventListener('load', () => window.dispatchEvent(new Event('pywebviewready')));
"""


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch(executable_path=EXE, headless=True)
        yield b
        b.close()


def open_page(browser, state=STATE):
    page = browser.new_page(viewport={"width": 400, "height": 640})
    page.add_init_script(FAKE % json.dumps(state))
    page.goto(UI.as_uri())
    page.wait_for_selector(".row")
    return page


def calls(page):
    return page.evaluate("window.__calls")


def row(page, rid):
    return page.locator(f'.row[data-id="{rid}"]')


def test_priority_click_cycles(browser):
    page = open_page(browser)
    row(page, "Reminder/A").locator(".pr").click()
    assert calls(page)[-1] == ["set_priority", "Reminder/A", 9]


def test_keys_set_priority_on_focused_row(browser):
    page = open_page(browser)
    r = row(page, "Reminder/A")
    r.focus()
    page.keyboard.press("1")
    page.keyboard.press("0")
    assert calls(page)[-2:] == [["set_priority", "Reminder/A", 1], ["set_priority", "Reminder/A", 0]]


def test_ring_completes_after_delay(browser):
    page = open_page(browser)
    row(page, "Reminder/A").locator(".ring").click()
    assert calls(page) == []
    page.wait_for_timeout(1800)
    assert ["complete", "Reminder/A", True] in calls(page)


def test_second_ring_click_cancels(browser):
    page = open_page(browser)
    ring = row(page, "Reminder/A").locator(".ring")
    ring.click()
    page.wait_for_timeout(300)
    ring.click()
    page.wait_for_timeout(1800)
    assert calls(page) == []


def test_double_click_renames(browser):
    page = open_page(browser)
    row(page, "Reminder/A").locator(".t").dblclick()
    inp = row(page, "Reminder/A").locator(".t input")
    inp.fill("Beta")
    inp.press("Enter")
    assert calls(page)[-1] == ["rename", "Reminder/A", "Beta"]


def test_detail_panel_due_all_day_and_notes(browser):
    page = open_page(browser)
    row(page, "Reminder/A").locator(".more").click()
    page.wait_for_selector("#detail.show")
    page.fill("#dDue", "2026-11-12T09:00")
    page.dispatch_event("#dDue", "change")
    assert calls(page)[-1] == ["set_due", "Reminder/A", "2026-11-12T09:00", False]
    page.check("#dAllDay")
    assert calls(page)[-1] == ["set_due", "Reminder/A", "2026-11-12", True]
    page.fill("#dNotes", "a note")
    page.click("#dTitle")          # blur the notes field
    assert ["set_notes", "Reminder/A", "a note"] in calls(page)


def test_changing_the_due_date_of_a_reminder_with_an_alert_says_the_alert_stays(browser):   # probe round 4
    state = json.loads(json.dumps(STATE))
    state["reminders"][0]["alarm"] = True
    page = open_page(browser, state)
    row(page, "Reminder/A").locator(".more").click()
    page.wait_for_selector("#detail.show")
    page.fill("#dDue", "2026-11-12T09:00")
    page.dispatch_event("#dDue", "change")
    page.wait_for_timeout(100)
    assert calls(page)[-1][0] == "set_due"
    assert page.inner_text("#toast") == "The alert keeps its old time. Change it on iPhone."


def test_context_menu_sets_priority(browser):
    page = open_page(browser)
    row(page, "Reminder/A").click(button="right")
    page.click('#menu button[data-p="5"]')
    assert calls(page)[-1] == ["set_priority", "Reminder/A", 5]


def test_update_bar_button_opens_the_release(browser):   # plan 3 Task 2
    state = dict(STATE, banner={"text": "Reminders Widget 0.2.0 is available.", "action": "open_update", "tone": "info"})
    page = open_page(browser, state)
    assert page.inner_text("#bannerAction") == "Download"
    page.click("#bannerAction")
    assert calls(page)[-1] == ["open_update"]


def test_banner_action_signs_in(browser):
    state = dict(STATE, status="session_expired",
                 banner={"text": "Sign in again to keep syncing.", "action": "sign_in", "tone": "warning"})
    page = open_page(browser, state)
    assert page.inner_text("#bannerText") == "Sign in again to keep syncing."
    page.click("#bannerAction")
    assert calls(page)[-1] == ["sign_in"]


def test_readonly_row_makes_no_calls(browser):
    page = open_page(browser)
    r = row(page, "Reminder/RO")
    r.locator(".pr").click()
    r.locator(".ring").click()
    r.locator(".t").dblclick()
    page.wait_for_timeout(1800)
    assert calls(page) == []
    assert "can't be changed" in page.inner_text("#toast")


def open_add(page):
    page.wait_for_timeout(150)          # let the first render settle: :hover only updates on a mouse move
    page.mouse.move(1, 1)
    page.hover("#addrow")
    page.wait_for_timeout(350)


def test_add_calls_api_with_the_first_open_list(browser):
    page = open_page(browser)
    open_add(page)
    page.fill("#add", "Buy milk")
    page.press("#add", "Enter")
    assert calls(page)[-1] == ["add", "Buy milk", "List/L1"]


def test_state_event_rerenders(browser):
    page = open_page(browser)
    new = dict(STATE, reminders=STATE["reminders"][:1] + [dict(STATE["reminders"][0], id="Reminder/C", title="Gamma")])
    page.evaluate("s => window.dispatchEvent(new CustomEvent('rw:state', {detail: s}))", new)
    page.wait_for_selector('.row[data-id="Reminder/C"]')
    assert page.locator(".row").count() == 2


def push(page, state):
    page.evaluate("s => window.dispatchEvent(new CustomEvent('rw:state', {detail: s}))", state)


def test_detail_panel_does_not_send_stale_text(browser):   # final review 2
    page = open_page(browser)
    row(page, "Reminder/A").locator(".more").click()
    page.wait_for_selector("#detail.show")
    newer = dict(STATE, reminders=[dict(STATE["reminders"][0], notes="from iPhone", title="Alpha 2")]
                 + STATE["reminders"][1:])
    push(page, newer)                       # the iPhone edited it while the panel is open
    page.click("#dNotes")
    page.click("#dTitle")
    page.click("#dClose")
    assert calls(page) == []


def test_untouched_title_with_trailing_space_is_not_renamed(browser):   # final review 2
    state = dict(STATE, reminders=[dict(STATE["reminders"][0], title="Alpha ")] + STATE["reminders"][1:])
    page = open_page(browser, state)
    row(page, "Reminder/A").locator(".more").click()
    page.click("#dTitle")
    page.click("#dClose")
    assert calls(page) == []


def test_failed_rename_restores_the_row(browser):   # final review 9
    page = open_page(browser)
    page.evaluate("window.__fail = true")
    row(page, "Reminder/A").locator(".t").dblclick()
    inp = row(page, "Reminder/A").locator(".t input")
    inp.fill("Beta")
    inp.press("Enter")
    page.wait_for_timeout(200)
    assert row(page, "Reminder/A").locator(".t input").count() == 0
    assert row(page, "Reminder/A").locator(".t").inner_text() == "Alpha"


def test_failed_complete_keeps_row_visible(browser):   # final review 9
    page = open_page(browser)
    page.evaluate("window.__fail = true")
    row(page, "Reminder/A").locator(".ring").click()
    page.wait_for_timeout(2000)
    assert row(page, "Reminder/A").evaluate("n => getComputedStyle(n).opacity") == "1"


def test_failed_add_keeps_the_typed_text(browser):   # final review 9
    page = open_page(browser)
    page.evaluate("window.__fail = true")
    open_add(page)
    page.fill("#add", "Buy milk")
    page.press("#add", "Enter")
    page.wait_for_timeout(200)
    assert page.input_value("#add") == "Buy milk"


def test_escape_in_notes_saves_typed_text(browser):   # final review 11
    page = open_page(browser)
    row(page, "Reminder/A").locator(".more").click()
    page.fill("#dNotes", "typed")
    page.keyboard.press("Escape")
    assert ["set_notes", "Reminder/A", "typed"] in calls(page)


def test_ring_stays_filled_when_state_arrives_during_undo(browser):   # final review 14a
    page = open_page(browser)
    row(page, "Reminder/A").locator(".ring").click()
    push(page, STATE)
    assert "on" in row(page, "Reminder/A").locator(".ring").get_attribute("class")


def test_not_signed_in_shows_sign_in_prompt(browser):
    state = dict(STATE, lists=[], selected=[], reminders=[], title="All", status="session_expired",
                 signed_in=False)
    page = browser.new_page(viewport={"width": 400, "height": 640})
    page.add_init_script(FAKE % json.dumps(state))
    page.goto(UI.as_uri())
    page.wait_for_selector(".empty")
    assert "Sign in to iCloud" in page.inner_text(".empty")
    page.click(".empty button")
    assert calls(page)[-1] == ["sign_in"]


def test_no_mode_switch_in_the_list_panel(browser):   # UI feedback 3
    page = open_page(browser)
    assert page.locator("#modes").count() == 0


def _drag(page, selector, dx=60, dy=30):
    page.evaluate("() => { window.pywebview._jsApiCallback = (n, a) => window.__calls.push([n, ...a]); }")
    box = page.locator(selector).first.bounding_box()
    x, y = box["x"] + 4, box["y"] + box["height"] / 2
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x + dx / 2, y + dy / 2)
    page.mouse.move(x + dx, y + dy)
    page.mouse.up()
    return [c for c in calls(page) if c[0] == "pywebviewMoveWindow"]


def test_desktop_widget_drags_from_the_title_and_empty_spots(browser):   # Martin's desktop run
    page = open_page(browser)                       # the state no longer carries a mode (feedback 2026-11-15)
    assert _drag(page, "#ttl")
    page2 = open_page(browser)
    assert _drag(page2, ".right")                 # the empty strip right of the picker


def test_rows_and_buttons_do_not_drag(browser):
    page = open_page(browser)
    assert not _drag(page, ".row") and not _drag(page, "#add")


def test_older_state_is_ignored(browser):
    page = open_page(browser, dict(STATE, version=5))
    old = dict(STATE, version=3, reminders=[])
    push(page, old)
    assert page.locator(".row").count() == 2


def test_syncing_state_pulses_the_dot(browser):
    page = open_page(browser, dict(STATE, version=1))
    push(page, dict(STATE, version=2, syncing=True))
    assert "syncing" in page.get_attribute("#status", "class")


def test_hidden_detail_panel_is_inert(browser):
    page = open_page(browser)
    assert page.evaluate("document.getElementById('detail').inert") is True
    row(page, "Reminder/A").locator(".more").click()
    assert page.evaluate("document.getElementById('detail').inert") is False


def test_title_swap_last_request_wins(browser):
    page = open_page(browser, dict(STATE, version=1))
    push(page, dict(STATE, version=2, title="Work"))
    push(page, dict(STATE, version=3, title="School"))
    page.wait_for_timeout(400)
    assert page.inner_text("#ttl") == "School"


# --- UI feedback 2026-11-15 ---------------------------------------------------
def open_picker(page):
    page.hover("#pk")
    page.wait_for_timeout(500)


def test_picker_selects_all_lists_at_once(browser):   # feedback 2
    page = open_page(browser, dict(STATE, selected=["List/L2"]))
    open_picker(page)
    page.click("text=All lists")
    assert calls(page)[-1] == ["select", ["List/L1", "List/L2"]]


def test_picker_offers_today_and_scheduled(browser):   # feedback 6
    page = open_page(browser)
    open_picker(page)
    page.click(".opt.smart >> text=Today")
    assert calls(page)[-1] == ["set_view", "today"]
    page.click(".opt.smart >> text=Scheduled")
    assert calls(page)[-1] == ["set_view", "scheduled"]


def test_new_reminder_field_appears_only_on_the_plus_which_is_last(browser):   # feedback 4
    page = open_page(browser)
    assert page.evaluate("document.getElementById('list').lastElementChild.id") == "addrow"
    opacity = "getComputedStyle(document.getElementById('add')).opacity"
    assert page.evaluate(opacity) == "0"
    open_add(page)
    page.wait_for_function(f"{opacity} === '1'", timeout=2000)   # fades in on hover


def test_new_reminder_goes_to_the_list_picked_on_its_tag(browser):   # feedback 1
    page = open_page(browser)
    open_add(page)
    assert "Work" in page.inner_text("#addTarget")
    page.click("#addTarget")
    page.click("#targets >> text=School")
    assert "School" in page.inner_text("#addTarget")
    page.fill("#add", "Read chapter 3")
    page.press("#add", "Enter")
    assert calls(page)[-1] == ["add", "Read chapter 3", "List/L2"]


def test_list_has_no_visible_scrollbar(browser):   # feedback 5
    page = open_page(browser)
    assert page.evaluate("getComputedStyle(document.getElementById('list')).scrollbarWidth") == "none"


def test_date_sort_shows_day_headings(browser):   # feedback 6
    state = json.loads(json.dumps(STATE))
    state["sort"] = "date"
    state["reminders"][0]["group"] = "today"
    state["reminders"][1]["group"] = "none"
    page = open_page(browser, state)
    assert page.locator("#list .grp").all_text_contents() == ["Today", "No date"]
    page.wait_for_timeout(300)                      # the label swaps with a short fade
    assert page.inner_text("#sortl") == "By date"


def test_sort_button_cycles_priority_date_icloud(browser):
    page = open_page(browser)
    page.click("#sortb")
    assert calls(page)[-1] == ["set_sort", "date"]


# --- feedback 2026-11-15, second round ------------------------------------------
def test_no_scrollbar_flashes_while_the_list_panel_opens(browser):
    page = open_page(browser)
    assert page.evaluate("getComputedStyle(document.getElementById('opts')).scrollbarWidth") == "none"


def test_move_a_reminder_from_the_detail_panel(browser):
    page = open_page(browser)
    row(page, "Reminder/A").locator(".more").click()
    page.wait_for_selector("#detail.show")
    assert "Work" in page.inner_text("#dList")
    page.click("#dList")
    page.click("#targets >> text=School")
    assert calls(page)[-1] == ["move", "Reminder/A", "List/L2"]


def test_move_a_reminder_from_the_context_menu(browser):
    page = open_page(browser)
    row(page, "Reminder/A").click(button="right")
    page.click('#menu button[data-a="move"]')
    page.click("#targets >> text=School")
    assert calls(page)[-1] == ["move", "Reminder/A", "List/L2"]


def test_list_menu_stays_inside_the_widget(browser):
    page = open_page(browser)
    row(page, "Reminder/A").locator(".more").click()
    page.wait_for_selector("#detail.show")
    page.click("#dList")
    left, right, width = page.evaluate("""() => { const m = document.getElementById('targets').getBoundingClientRect();
        return [m.left, m.right, document.getElementById('g').getBoundingClientRect().right]; }""")
    assert left >= 0 and right <= width


def test_detail_panel_shows_the_new_list_after_a_move(browser):   # Martin: the tag kept the old list
    page = open_page(browser)
    row(page, "Reminder/A").locator(".more").click()
    page.wait_for_selector("#detail.show")
    page.evaluate("""() => { window.pywebview.api.move = (id, list) => {     // the API answers with the moved reminder
        const st = JSON.parse(JSON.stringify(window.__state));
        st.reminders.find(r => r.id === id).list = list;
        return Promise.resolve({ok: true, state: st}); }; }""")
    page.click("#dList")
    page.click("#targets >> text=School")
    page.wait_for_timeout(100)
    assert "School" in page.inner_text("#dList")

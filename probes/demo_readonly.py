"""Read-only demo: your real reminders, pulled by the plan-1 core, shown in a widget window.

Run it yourself:  .\\.venv\\Scripts\\python.exe probes\\demo_readonly.py
Fixture data, no iCloud:  ... probes\\demo_readonly.py --fake
Write the page only:      ... probes\\demo_readonly.py --fake --dump-html out.html

- Sign-in reuses the Brave session in .session/ (same as the probes).
- Writing to iCloud is blocked: the transport refuses records/modify.
- The local database lives in a temporary folder and is deleted at the end.
- Nothing is written to probe-results.txt; reminder text only appears in the window.
"""
import json
import shutil
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from reminders_widget.icloud.cloudkit import CloudKit  # noqa: E402
from reminders_widget.store import Store  # noqa: E402
from reminders_widget.sync import SyncEngine  # noqa: E402
from reminders_widget.view import ensure_selection  # noqa: E402

PAGE = HERE / "demo_widget.html"
LIST_COLOURS = ["#85B7EB", "#F0997B", "#97C459", "#AFA9EC", "#5DCAA5", "#ED93B1", "#FAC775", "#B4B2A9"]


class ReadOnlyTransport:
    """Playwright request context as a CloudKit Transport that can never write."""

    def __init__(self, context):
        self.request = context.request

    def post(self, url, body):
        if url.rstrip("/").endswith("records/modify"):
            raise RuntimeError("demo is read-only: records/modify blocked")
        from probe import ICLOUD_HEADERS
        resp = self.request.post(url, headers=ICLOUD_HEADERS, data=json.dumps(body))
        try:
            data = resp.json()
        except Exception:  # non-JSON error page
            data = {}
        return resp.status, data if isinstance(data, dict) else {}


def pull_real(store):
    from playwright.sync_api import sync_playwright
    from probe import launch_browser, wait_for_login

    with sync_playwright() as pw:
        context = launch_browser(pw)
        page = context.pages[0] if context.pages else context.new_page()
        page.goto("https://www.icloud.com/reminders/")
        try:
            base = wait_for_login(context, page)
            result = SyncEngine(CloudKit(ReadOnlyTransport(context), base), store).run_once()
        finally:
            context.close()
    return result.status


def pull_fake(store):
    from tests.fakes.fake_cloudkit import FakeCloudKit

    records = json.loads((ROOT / "tests" / "fixtures" / "records.json").read_text(encoding="utf-8"))
    return SyncEngine(CloudKit(FakeCloudKit(records), "https://fake"), store).run_once().status


def due_text(due):
    if due is None:
        return "", False
    today = date.today()
    if isinstance(due, datetime):
        local = due.astimezone()
        day, time_part = local.date(), local.strftime(" %H:%M")
    else:
        day, time_part = due, ""
    late = day < today
    names = {0: "Today", 1: "Tomorrow", -1: "Yesterday"}
    delta = (day - today).days
    label = names.get(delta, day.strftime("%a %d %b") if abs(delta) < 300 else day.strftime("%d %b %Y"))
    return label + time_part, late


def page_data(store):
    lists = store.lists()
    colours = {lst.record_name: lst.color or LIST_COLOURS[i % len(LIST_COLOURS)] for i, lst in enumerate(lists)}
    reminders = []
    for r in store.reminders():
        if r.completed or r.deleted or r.list_id not in colours:
            continue
        due, late = due_text(r.due)
        reminders.append({
            "id": r.record_name, "l": r.list_id, "t": r.title or "(bez názvu)", "p": r.priority,
            "d": due, "late": late, "note": bool(r.notes), "ro": r.readonly, "rec": r.recurring,
            "c": r.created.timestamp() if r.created else 0,
        })
    return {
        "lists": [{"id": lst.record_name, "n": lst.name, "c": colours[lst.record_name]} for lst in lists],
        "selected": sorted(ensure_selection(lists, set())),
        "reminders": reminders,
    }


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    fake = "--fake" in sys.argv
    tmp = Path(tempfile.mkdtemp(prefix="rw-demo-"))
    store = Store(tmp / "demo.db")
    try:
        print("Sťahujem pripomienky cez jadro z plánu 1 (len čítanie)...")
        status = pull_fake(store) if fake else pull_real(store)
        data = page_data(store)
        print(f"Stav synchronizácie: {status}; zoznamov: {len(data['lists'])}, "
              f"aktívnych pripomienok: {len(data['reminders'])}")
        html = PAGE.read_text(encoding="utf-8").replace(
            "/*DATA*/null", json.dumps(data, ensure_ascii=False))
        if "--dump-html" in sys.argv:
            out = Path(sys.argv[sys.argv.index("--dump-html") + 1])
            out.write_text(html, encoding="utf-8")
            print(f"Stránka zapísaná: {out}")
            return
        import webview
        webview.create_window("Reminders – ukážka (len čítanie)", html=html,
                              width=400, height=620, background_color="#141826")
        webview.start()
    finally:
        store.close()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()

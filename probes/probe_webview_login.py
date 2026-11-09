"""Throwaway probes 2 and 3: sign in inside a pywebview (WebView2) window, then call
iCloud with plain httpx and only the cookies WebView2 hands back.

Run it yourself:  .venv/Scripts/python probes/probe_webview_login.py

- You sign in on Apple's own page inside the window this script opens (2FA included).
  The script never sees your password.
- Cookie VALUES are never printed or written; only names and domains are logged.
- It only reads (zones/list, one changes/zone page). Nothing is written to iCloud.
- Results are appended to probe-results.txt (counts and statuses, no reminder text).
- The window's storage folder %TEMP%/rw-probe-webview is deleted at the end.
"""
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

import httpx

from probe import CONTAINER_PATH, ICLOUD_HEADERS, LOGIN_TIMEOUT_S, RESULTS_FILE, ZONE, Results

STORAGE_DIR = Path(tempfile.gettempdir()) / "rw-probe-webview"
POLL_INTERVAL_S = 5
VALIDATE_URL = "https://setup.icloud.com/setup/ws/1/validate"


# --- Pure helpers (unit-tested) ---

def _morsels(cookies):
    """Yield (name, morsel) for every cookie; pywebview returns one SimpleCookie each."""
    for cookie in cookies:
        yield from cookie.items()


def cookie_names(cookies):
    return [name for name, _ in _morsels(cookies)]


def has_webauth(cookies):
    return any(name.upper().startswith("X-APPLE-WEBAUTH") for name in cookie_names(cookies))


def has_trust_cookie(cookies):
    return any("TRUST" in name.upper() for name in cookie_names(cookies))


def cookie_summary(cookies):
    """'name (domain), ...' - never the values."""
    parts = [f"{name} ({morsel['domain'] or '?'})" for name, morsel in _morsels(cookies)]
    return ", ".join(parts) if parts else "(žiadne cookies)"


def to_httpx_cookies(cookies):
    jar = httpx.Cookies()
    for name, morsel in _morsels(cookies):
        jar.set(name, morsel.value, domain=morsel["domain"] or "", path=morsel["path"] or "/")
    return jar


# --- Probe steps ---

def post_json(client, url, body, label, results):
    """POST and return parsed JSON; logs endpoint and status only, never the body."""
    resp = client.post(url, content=json.dumps(body))
    results.add(f"HTTP {label}", "INFO", f"status {resp.status_code}")
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code} pri {label}")
    return resp.json()


def call_icloud(cookies, results):
    with httpx.Client(headers=ICLOUD_HEADERS, cookies=to_httpx_cookies(cookies),
                      timeout=30) as client:
        validated = results.run("validate cez httpx",
                                lambda: post_json(client, VALIDATE_URL, None, "validate", results))
        if not validated:
            return
        base = validated.get("webservices", {}).get("ckdatabasews", {}).get("url")
        if not base:
            results.add("validate cez httpx", "FAIL", "odpoveď neobsahuje ckdatabasews.url")
            return
        base = base.rstrip("/") + "/" + CONTAINER_PATH

        def list_zones():
            zones = post_json(client, f"{base}/zones/list", {}, "zones/list", results)["zones"]
            results.add("zones/list", "INFO", f"{len(zones)} zón")
            for zone in zones:
                if zone["zoneID"]["zoneName"] == ZONE:
                    return zone["zoneID"]["ownerRecordName"]
            raise RuntimeError("Zóna Reminders sa nenašla")

        owner = results.run("zones/list cez httpx", list_zones)
        if not owner or owner is True:
            return

        def one_page():
            body = {"zones": [{"zoneID": {"zoneName": ZONE, "ownerRecordName": owner}}]}
            zone = post_json(client, f"{base}/changes/zone", body, "changes/zone", results)["zones"][0]
            results.add("changes/zone", "INFO",
                        f"{len(zone.get('records', []))} záznamov, moreComing={bool(zone.get('moreComing'))}")

        results.run("changes/zone cez httpx", one_page)


def wait_for_cookies(window, results):
    """Poll until X-APPLE-WEBAUTH cookies show up; return the cookies seen last."""
    deadline = time.time() + LOGIN_TIMEOUT_S
    cookies = []
    while time.time() < deadline:
        try:
            cookies = window.get_cookies() or []
        except Exception as exc:  # window closed by the user, or WebView2 not ready
            results.add("čítanie cookies", "FAIL", f"{type(exc).__name__}")
            return cookies, False
        if has_webauth(cookies):
            return cookies, True
        time.sleep(POLL_INTERVAL_S)
    return cookies, False


def run_probe(window, results):
    print(f"\nV okne sa prihlás na iCloude (máš na to {LOGIN_TIMEOUT_S // 60} minút).")
    print("Ak sa objaví otázka, či dôverovať prehliadaču, zvoľ Dôverovať.\n")
    try:
        cookies, found = wait_for_cookies(window, results)
        if not found:
            results.add("prihlásenie v pywebview, cookies X-APPLE-WEBAUTH", "FAIL",
                        "nenašli sa; vrátené cookies: " + cookie_summary(cookies))
            return
        results.add("prihlásenie v pywebview, cookies X-APPLE-WEBAUTH", "OK")
        results.add("cookies po prihlásení", "INFO", cookie_summary(cookies))
        results.add("trust cookie (názov obsahuje TRUST)",
                    "OK" if has_trust_cookie(cookies) else "FAIL",
                    "prítomná" if has_trust_cookie(cookies) else "chýba")
        call_icloud(cookies, results)
    finally:
        window.destroy()


class AppendResults(Results):
    """Like Results, but appends to probe-results.txt so probe 1's lines survive."""

    def add(self, step, status, detail=""):
        line = f"{status:4} {step}" + (f" - {detail}" if detail else "")
        self.lines.append(line)
        print(f"  -> {line}")
        with RESULTS_FILE.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")


def main():
    import webview

    sys.stdout.reconfigure(encoding="utf-8")
    results = AppendResults()
    results.add("--- sonda 2 a 3: prihlásenie v pywebview, httpx ---", "INFO")
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        window = webview.create_window("iCloud – skúška prihlásenia", "https://www.icloud.com/",
                                       width=1000, height=800)
        webview.start(run_probe, (window, results), private_mode=False,
                      storage_path=str(STORAGE_DIR))
    finally:
        shutil.rmtree(STORAGE_DIR, ignore_errors=True)
    print(f"\nHotovo. Výsledky bez cookie hodnôt: {RESULTS_FILE.name}")


if __name__ == "__main__":
    main()

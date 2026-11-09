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
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

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


def url_host(url):
    """Host only - never path or query, which may carry tokens."""
    try:
        return urlparse(url or "").hostname or "?"
    except ValueError:
        return "?"


def is_signin_url(url):
    host = url_host(url)
    return host in ("idmsa.apple.com", "appleid.apple.com") or "signin" in (url or "").lower()


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
        def validate():
            try:
                return post_json(client, VALIDATE_URL, None, "validate", results)
            except Exception as exc:
                raise RuntimeError(f"{exc}; {HOST_ONLY_NOTE}") from None

        validated = results.run("validate cez httpx", validate)
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
        if not isinstance(owner, str):  # run() returns None on failure
            return

        def one_page():
            body = {"zones": [{"zoneID": {"zoneName": ZONE, "ownerRecordName": owner}}]}
            zone = post_json(client, f"{base}/changes/zone", body, "changes/zone", results)["zones"][0]
            results.add("changes/zone", "INFO",
                        f"{len(zone.get('records', []))} záznamov, moreComing={bool(zone.get('moreComing'))}")

        results.run("changes/zone cez httpx", one_page)


TRUST_WAIT_S = 60
HOST_ONLY_NOTE = ("get_cookies() vidí len cookies pre aktuálnu adresu okna; "
                  "host-only cookies iných hostov (napr. *.apple.com) nie sú viditeľné")


class WindowClosed(Exception):
    pass


def current_url(window):
    try:
        return window.get_current_url()
    except Exception:
        return None


def read_cookies(window, closed):
    if closed.is_set():
        raise WindowClosed()
    try:
        return window.get_cookies() or []
    except Exception:
        if closed.is_set():
            raise WindowClosed()
        raise


def wait_for_cookies(window, closed):
    """Wait for X-APPLE-WEBAUTH, then up to TRUST_WAIT_S more for the trust cookie.

    Returns (cookies, found_webauth). Raises WindowClosed if the user closes the window.
    """
    deadline = time.time() + LOGIN_TIMEOUT_S
    cookies = []
    while time.time() < deadline:
        cookies = read_cookies(window, closed)
        if has_webauth(cookies):
            break
        time.sleep(POLL_INTERVAL_S)
    else:
        return cookies, False
    trust_deadline = time.time() + TRUST_WAIT_S
    while not has_trust_cookie(cookies) and time.time() < trust_deadline:
        time.sleep(POLL_INTERVAL_S)
        cookies = read_cookies(window, closed)
    return cookies, True


def run_probe(window, results):
    closed = threading.Event()
    window.events.closed += closed.set
    print(f"
V okne sa prihlás na iCloude (máš na to {LOGIN_TIMEOUT_S // 60} minút).")
    print("Ak sa objaví otázka, či dôverovať prehliadaču, zvoľ Dôverovať.
")
    step = "prihlásenie v pywebview, cookies X-APPLE-WEBAUTH"
    try:
        try:
            cookies, found = wait_for_cookies(window, closed)
            if found:
                cookies = read_cookies(window, closed)  # fresh snapshot for jar and trust check
        except WindowClosed:
            results.add(step, "FAIL", "okno zatvorené pred dokončením prihlásenia")
            return
        url = current_url(window)
        host = url_host(url)
        if not found:
            results.add(step, "FAIL",
                        f"nenašli sa; host okna {host}, stále na prihlasovacej stránke: "
                        f"{is_signin_url(url)}; vrátené cookies: " + cookie_summary(cookies))
            return
        results.add(step, "OK")
        results.add("cookies po prihlásení", "INFO", f"host okna {host}: " + cookie_summary(cookies))
        trust = has_trust_cookie(cookies)
        results.add("trust cookie (názov obsahuje TRUST)", "OK" if trust else "FAIL",
                    "prítomná" if trust else
                    f"chýba ani po {TRUST_WAIT_S} s; stále na prihlasovacej stránke: {is_signin_url(url)}")
        call_icloud(cookies, results)
    finally:
        try:
            window.destroy()
        except Exception:
            pass


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
        results.add("upratanie dočasného priečinka okna", "INFO",
                    "zostal (zamknutý?)" if STORAGE_DIR.exists() else "zmazaný")
    print(f"\nHotovo. Výsledky bez cookie hodnôt: {RESULTS_FILE.name}")


if __name__ == "__main__":
    main()

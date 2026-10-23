"""Owned, headed Playwright browsers. No connection to personal browser profiles.

Playwright objects live on one worker thread, independently of the UIA lock.
Only observed element references are writable. No arbitrary page scripts,
credential extraction, CDP listener, forced actions, or implicit submission.
"""
from __future__ import annotations

import atexit
from concurrent.futures import Future
import json
import os
from pathlib import Path
import queue
import re
import threading
import time
from urllib.parse import urlsplit, urlunsplit
import uuid


def profile_root() -> Path:
    override = os.environ.get("WINAUTO_BROWSER_PROFILE_ROOT")
    root = Path(override) if override else Path(os.environ["LOCALAPPDATA"]) / "winauto-mcp" / "browser-profiles"
    if not root.is_absolute():
        raise ValueError("browser profile root must be absolute")
    return root


def checked_url(url: str) -> str:
    parts = urlsplit(url)
    if url != "about:blank" and (parts.scheme not in ("http", "https") or not parts.hostname):
        raise ValueError("only http(s) URLs and about:blank are supported")
    if parts.username or parts.password:
        raise ValueError("URLs containing credentials are refused")
    return url


def display_url(url: str) -> str:
    p = urlsplit(url)
    # Redirects and page links can contain userinfo even when entry URLs do not.
    host = p.hostname or ''
    if ':' in host:
        host = f'[{host}]'
    authority = host + (f':{p.port}' if p.port else '')
    return urlunsplit((p.scheme, authority, p.path, "", ""))


class ProfileLease:
    """OS-released exclusive lease; stale files do not imply live ownership."""
    def __init__(self, path: Path):
        import msvcrt
        path.mkdir(parents=True, exist_ok=True)
        self.file = (path / ".winauto.lock").open("a+b")
        if path.joinpath('.winauto.lock').stat().st_size == 0:
            self.file.write(b"\0")
            self.file.flush()
        self.file.seek(0)
        try:
            msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            self.file.close()
            raise ValueError("profile is owned by another WinAuto process; do not reuse it concurrently") from None

    def close(self):
        if not self.file.closed:
            import msvcrt
            self.file.seek(0)
            msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
            self.file.close()


ELEMENT_INFO = """el => ({
 tag:el.tagName.toLowerCase(), role:el.getAttribute('role'),
 type:(el.getAttribute('type') || '').toLowerCase(), id:el.id || null,
 name:el.getAttribute('name'), placeholder:el.getAttribute('placeholder'),
 href:el.tagName === 'A' ? el.href : null,
 label:el.getAttribute('aria-label') || Array.from(el.labels || []).map(x=>x.innerText).join(' '),
 text:(el.innerText || '').slice(0,400),
 sensitive:el.type === 'password' || /(?:^|\\s)(?:current-password|new-password|one-time-code)(?:\\s|$)/i.test(el.autocomplete || ''),
 value:(el.type === 'password' || /(?:^|\\s)(?:current-password|new-password|one-time-code)(?:\\s|$)/i.test(el.autocomplete || '')) ? '[redacted]' :
   (el.type === 'file' ? Array.from(el.files || []).map(f=>f.name) :
   ('value' in el ? el.value : (el.isContentEditable ? el.innerText : null))),
 disabled:!!el.disabled || el.getAttribute('aria-disabled') === 'true',
 readonly:!!el.readOnly || el.getAttribute('aria-readonly') === 'true',
 checked:'checked' in el ? el.checked : el.getAttribute('aria-checked')
})"""
ELEMENT_SELECTOR = 'button,input,textarea,select,a[href],[role="button"],[role="checkbox"],[role="radio"],[role="combobox"],[role="option"],[contenteditable="true"]'


def validate_steps(steps: list[dict]) -> None:
    if not isinstance(steps, list) or not 1 <= len(steps) <= 20:
        raise ValueError("provide 1..20 steps")
    for step in steps:
        if not isinstance(step, dict) or step.get("do") not in {"click", "fill", "check", "select", "upload", "read"}:
            raise ValueError("supported steps: click, fill, check, select, upload, read")
        allowed = {"do", "ref", "text", "checked", "value", "files"}
        if set(step) - allowed or not isinstance(step.get("ref"), str):
            raise ValueError("each step needs an observed ref and only documented keys")
        if step["do"] == "fill" and (not isinstance(step.get("text"), str) or len(step["text"]) > 100000):
            raise ValueError("fill needs literal text of at most 100000 characters")
        if step["do"] == "check" and not isinstance(step.get("checked"), bool):
            raise ValueError("check needs checked=true/false")
        if step["do"] == "select" and not isinstance(step.get("value"), str):
            raise ValueError("select needs an option value")
        if step["do"] == "upload":
            files = step.get("files")
            if not isinstance(files, list) or not 1 <= len(files) <= 30:
                raise ValueError("upload needs 1..30 absolute local file paths")
            for file in files:
                if not isinstance(file, str) or not Path(file).is_absolute() or not Path(file).is_file():
                    raise ValueError("every upload path must be an existing absolute local file")


class BrowserWorker:
    def __init__(self):
        self._requests = queue.Queue()
        self._gate = threading.Lock()
        self._thread = None
        self._sessions = {}
        self._events = []
        self._pw = None

    def call(self, method: str, **kwargs):
        acquired = self._gate.acquire(timeout=40) if method == 'shutdown' else self._gate.acquire(blocking=False)
        if not acquired:
            raise RuntimeError("browser worker busy; no action queued or performed")
        try:
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name="winauto-browser", daemon=True)
                self._thread.start()
            future = Future()
            self._requests.put((method, kwargs, future))
            # The worker owns action deadlines. Never return a transport timeout
            # while a detached mutation continues in the background.
            return future.result()
        finally:
            self._gate.release()

    def _run(self):
        while True:
            method, kwargs, future = self._requests.get()
            try:
                result = getattr(self, "_" + method)(**kwargs)
                self._events.append({"method": method, "ok": not isinstance(result, dict) or result.get("ok", True),
                                     "at": time.time(), "completed_steps": result.get("completed_steps") if isinstance(result, dict) else None})
                self._events = self._events[-100:]
                future.set_result(result)
            except Exception as error:
                # Playwright errors can include field values, paths and page text.
                # Only validation errors authored here are returned verbatim.
                safe = str(error) if isinstance(error, ValueError) else f"{type(error).__name__}: browser operation failed; inspect current state before retrying"
                self._events.append({"method": method, "ok": False, "at": time.time(), "error_type": type(error).__name__})
                future.set_exception(RuntimeError(safe))

    def _session(self, session_id):
        if session_id not in self._sessions:
            raise ValueError("unknown browser session; inspect browser_status/open the intended profile")
        return self._sessions[session_id]

    def _page(self, session_id, tab_id):
        session = self._session(session_id)
        self._index_pages(session)
        page = session["pages"].get(tab_id)
        if page is None or page.is_closed():
            raise ValueError("tab closed or unknown; inspect browser_tabs")
        return session, page

    def _index_pages(self, session):
        for page in session["context"].pages:
            if page not in session["pages"].values():
                session["pages"][uuid.uuid4().hex] = page

    def _status(self):
        self._reap_closed()
        return {"backend": "playwright-persistent", "headed": True, "sessions": [
            {"session_id": key, "profile": s["profile"], "browser": s["browser"]} for key, s in self._sessions.items()],
            "personal_profiles_attached": False, "arbitrary_javascript": False}

    def _reap_closed(self):
        for key, session in list(self._sessions.items()):
            browser = session['context'].browser
            if browser is not None and not browser.is_connected():
                session['lease'].close()
                del self._sessions[key]

    def _open(self, profile, url, browser):
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,47}", profile):
            raise ValueError("profile must be 1..48 letters, digits, underscore or hyphen")
        if profile.lower().split('.')[0] in {"con", "prn", "aux", "nul", *[f"com{x}" for x in range(1,10)], *[f"lpt{x}" for x in range(1,10)]}:
            raise ValueError("reserved profile name")
        if browser not in {"msedge", "chrome", "chromium"}:
            raise ValueError("browser must be msedge, chrome or chromium")
        checked_url(url)
        self._reap_closed()
        for key, session in self._sessions.items():
            if session["profile"].casefold() == profile.casefold():
                if session["browser"] != browser:
                    raise ValueError("profile already open with another browser")
                return {"ok": True, "session_id": key, "reused": True, "tabs": self._tabs(key), "navigation_performed": False}
        root = profile_root().resolve()
        path = root / profile
        if path.resolve().parent != root or path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
            raise ValueError('profile must be a real directory directly under the dedicated profile root')
        lease = ProfileLease(path)
        try:
            marker = path / '.winauto-profile.json'
            if marker.exists() and json.loads(marker.read_text(encoding='utf-8'))['browser'] != browser:
                raise ValueError('profile belongs to another browser; use a different profile name')
            marker.write_text(json.dumps({'browser': browser}), encoding='utf-8')
            if self._pw is None:
                from playwright.sync_api import sync_playwright
                self._pw = sync_playwright().start()
            options = {"headless": False, "no_viewport": True, "timeout": 30000}
            if browser != "chromium":
                options["channel"] = browser
            context = self._pw.chromium.launch_persistent_context(str(path), **options)
        except Exception:
            lease.close()
            raise
        key = uuid.uuid4().hex
        session = {"profile": profile, "browser": browser, "context": context, "lease": lease, "pages": {}, "observations": {}}
        self._sessions[key] = session
        context.set_default_timeout(5000)
        self._index_pages(session)
        page = next(iter(context.pages), None) or context.new_page()
        if url != "about:blank" and page.url != url:
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=20000)
            except Exception:
                # Preserve the browser and any partially loaded login/form.
                return {"ok": False, "session_id": key, "profile": profile, "profile_path": str(path),
                        "phase": "navigation", "browser_open": True, "tabs": self._tabs(key),
                        "error": "Navigation incomplete; inspect the existing tab instead of opening another browser"}
        return {"ok": True, "session_id": key, "profile": profile, "profile_path": str(path), "reused": False, "tabs": self._tabs(key)}

    def _tabs(self, session_id):
        session = self._session(session_id)
        self._index_pages(session)
        return [{"tab_id": key, "title": page.title(), "url": display_url(page.url)} for key, page in session["pages"].items() if not page.is_closed()]

    def _navigate(self, session_id, tab_id, url):
        checked_url(url)
        session, page = self._page(session_id, tab_id)
        session["observations"].pop(tab_id, None)
        page.goto(url, wait_until="domcontentloaded", timeout=20000)
        return {"ok": True, "url": display_url(page.url), "title": page.title()}

    def _snapshot(self, session_id, tab_id, limit):
        if not 1 <= limit <= 200:
            raise ValueError("limit must be 1..200")
        session, page = self._page(session_id, tab_id)
        old = session["observations"].pop(tab_id, None)
        if old:
            for el in old["refs"].values():
                try: el.dispose()
                except Exception: pass
        refs, elements = {}, []
        token = uuid.uuid4().hex
        deadline = time.monotonic() + 8
        truncated = False
        handles = page.query_selector_all(ELEMENT_SELECTOR)
        for el in handles:
            if len(elements) >= limit or time.monotonic() >= deadline:
                truncated = True
                break
            visible = el.is_visible()
            if not visible and (el.get_attribute('type') or '').lower() != 'file':
                el.dispose()
                continue
            data = el.evaluate(ELEMENT_INFO)
            if data.get('href'):
                data['href'] = display_url(data['href'])
            ref = "e" + str(len(elements) + 1)
            refs[ref] = el
            elements.append({"ref": ref, "visible": visible, **data})
        for el in handles:
            if el not in refs.values():
                el.dispose()
        session["observations"][tab_id] = {"token": token, "url": page.url, "refs": refs}
        return {"ok": True, "observation_id": token, "url": display_url(page.url), "title": page.title(),
                "text": page.locator("body").inner_text(timeout=3000)[:12000], "elements": elements,
                "truncated": truncated, "scope": "main frame only", "child_frames": max(0, len(page.frames)-1)}

    def _actions(self, session_id, tab_id, observation_id, steps, timeout_ms):
        validate_steps(steps)
        if not 1000 <= timeout_ms <= 30000:
            raise ValueError("timeout_ms must be 1000..30000 for the whole batch")
        session, page = self._page(session_id, tab_id)
        observation = session["observations"].get(tab_id)
        if not observation or observation["token"] != observation_id or observation["url"] != page.url:
            raise ValueError("stale observation; take a fresh browser_snapshot before acting")
        # Validate all references and upload paths before the first action.
        for step in steps:
            if step["ref"] not in observation["refs"]:
                raise ValueError("unknown ref; no action performed")
        report = {"ok": True, "completed_steps": 0, "steps": [], "automatic_retry": False}
        deadline = time.monotonic() + timeout_ms / 1000
        for index, step in enumerate(steps):
            verb = step["do"]
            el = observation["refs"][step["ref"]]
            try:
                remaining = int((deadline - time.monotonic()) * 1000)
                if remaining <= 0:
                    raise ValueError("batch deadline reached before this action")
                if page.url != observation["url"] or not el.evaluate("el => el.isConnected"):
                    raise ValueError("page navigated or observed element was replaced; reinspect")
                info = el.evaluate(ELEMENT_INFO)
                if info["sensitive"]:
                    raise ValueError("credential input is manual; password/OTP elements are not actionable")
                if verb != "read" and (info["disabled"] or info["readonly"]):
                    raise ValueError("disabled/read-only element; use its visible picker or resolve the blocker")
                if verb == "click":
                    el.click(timeout=remaining)
                elif verb == "fill":
                    el.fill(step["text"], timeout=remaining)
                    actual = el.evaluate(ELEMENT_INFO)["value"]
                    if actual != step["text"]:
                        raise ValueError("fill readback differs; inspect partial state before retrying")
                elif verb == "check":
                    el.set_checked(step["checked"], timeout=remaining)
                    if el.is_checked() != step["checked"]:
                        raise ValueError("checked-state readback differs")
                elif verb == "select":
                    values = el.select_option(value=step["value"], timeout=remaining)
                    if values != [step["value"]]:
                        raise ValueError("selected value differs")
                elif verb == "upload":
                    if info["type"] != "file":
                        raise ValueError("upload ref must be an input[type=file]")
                    el.set_input_files(step["files"], timeout=remaining)
                item = {"index": index, "do": verb, "ref": step["ref"], "ok": True}
                if verb == "read":
                    item["state"] = info
                report["steps"].append(item)
                report["completed_steps"] += 1
            except Exception as error:
                report.update(ok=False, stopped_at_step=index, error_type=type(error).__name__,
                              error=str(error) if isinstance(error, ValueError) else "Browser action failed; inspect current state before retrying",
                              mutation_outcome="failed step may have changed state; completed steps must not be replayed")
                break
        report["url"] = display_url(page.url)
        return report

    def _screenshot(self, session_id, tab_id):
        _, page = self._page(session_id, tab_id)
        return page.screenshot(timeout=5000)

    def _close(self, session_id):
        session = self._session(session_id)
        # Release the profile only after the owned browser has closed.
        session["context"].close()
        session["lease"].close()
        del self._sessions[session_id]
        return {"ok": True, "profile": session["profile"], "profile_retained": True}

    def _history(self):
        return {"events": self._events[-100:], "values_and_credentials_logged": False}

    def _shutdown(self):
        for key in list(self._sessions):
            self._close(key)
        if self._pw:
            self._pw.stop()
            self._pw = None
        return {"ok": True}

    def shutdown(self):
        if self._thread is not None:
            self.call("shutdown")


worker = BrowserWorker()
atexit.register(worker.shutdown)

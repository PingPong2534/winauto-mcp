"""Opt-in real MCP probes for #11/#13/#14, using only isolated synthetic apps.

Run with --output <empty directory> --edge <msedge.exe> and/or --unity <Unity.exe>.
Never discovered by smoke.py. Retain fixtures/evidence for concrete regression reuse.
Each takeover is a bounded normal foreground_steps call; no alternate injector.
"""
import argparse
import asyncio
import base64
import ctypes
from contextlib import contextmanager
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time

import psutil
import win32gui
import win32process
from PIL import Image
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / 'fixtures'


def wait_value(fn, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = fn()
        if value:
            return value
        time.sleep(.2)
    raise TimeoutError('fixture did not become ready')


def read_json(path):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def owned_window(pid, title):
    processes = {pid}
    try:
        processes.update(p.pid for p in psutil.Process(pid).children(recursive=True))
    except psutil.NoSuchProcess:
        return None
    matches = []
    def inspect(hwnd, _):
        if (win32gui.IsWindowVisible(hwnd)
                and win32process.GetWindowThreadProcessId(hwnd)[1] in processes
                and title in win32gui.GetWindowText(hwnd)):
            matches.append(hwnd)
    win32gui.EnumWindows(inspect, None)
    return matches[0] if len(matches) == 1 else None


@contextmanager
def process(args, log):
    with log.open('wb') as output:
        proc = subprocess.Popen(args, stdout=output, stderr=output,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            yield proc
        finally:
            # Terminate only this probe's own process tree, never existing apps.
            try:
                children = psutil.Process(proc.pid).children(recursive=True)
            except psutil.NoSuchProcess:
                children = []
            proc.terminate()
            for child in children:
                try:
                    child.terminate()
                except psutil.NoSuchProcess:
                    pass
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
            _, alive = psutil.wait_procs(children, timeout=3)
            for child in alive:
                try:
                    child.kill()
                except psutil.NoSuchProcess:
                    pass
            psutil.wait_procs(alive, timeout=3)


class Probe:
    def __init__(self, session, output):
        self.session, self.output = session, output
        self.evidence = {}

    async def call(self, name, args, label):
        result = await self.session.call_tool(name, args)
        texts = [b.text for b in result.content if b.type == 'text']
        self.evidence[label] = {'tool': name, 'arguments': args,
                                'is_error': result.is_error, 'texts': texts}
        for i, block in enumerate(result.content):
            if block.type == 'image':
                (self.output / f'{label}-{i}.png').write_bytes(base64.b64decode(block.data))
        self.save()
        if result.is_error:
            raise RuntimeError(f'{label}: {texts}')
        try:
            return json.loads(texts[0])
        except (IndexError, ValueError):
            return texts

    def save(self):
        (self.output / 'evidence.json').write_text(json.dumps(self.evidence, indent=2), encoding='utf-8')

    def pixels(self, label, color):
        images = list(self.output.glob(f'{label}-*.png'))
        assert len(images) == 1, images
        with Image.open(images[0]) as image:
            samples = list(image.convert('RGB').resize((100, 100)).getdata())
        ratio = sum(max(abs(a-b) for a, b in zip(pixel, color)) <= 5 for pixel in samples) / len(samples)
        assert ratio > .15, (label, ratio, 'expected GPU content missing')
        self.evidence[f'{label}-pixels'] = {'expected_rgb': color, 'matching_fraction': ratio}
        self.save()

    async def foreground(self, steps, label, timeout=5000):
        clear_since = None
        def idle():
            nonlocal clear_since
            if any(ctypes.windll.user32.GetAsyncKeyState(key) & 0x8000 for key in range(1, 256)):
                clear_since = None
            elif clear_since is None:
                clear_since = time.monotonic()
            return clear_since is not None and time.monotonic() - clear_since >= .5
        wait_value(idle, timeout=30)  # passive check; the native gate revalidates
        report = await self.call('foreground_steps', {'steps': steps, 'reason': 'Isolated interoperability regression',
            'allow_takeover': True, 'timeout_ms': timeout}, label)
        assert report['ok'], report
        assert report['phase'] == 'released' and not report['input_blocked'], report
        assert report['keys_released'] and report['mouse_buttons_released'], report
        return report

    async def edge(self, exe):
        state = {}
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                data = (FIXTURES / 'interop.html').read_bytes()
                self.send_response(200); self.send_header('Content-Type', 'text/html'); self.end_headers()
                self.wfile.write(data)
            def do_POST(self):
                state.update(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                self.send_response(204); self.end_headers()
            def log_message(self, *args):
                pass
        http = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=http.serve_forever, daemon=True).start()
        try:
            with process([exe, f'--user-data-dir={self.output / "edge-profile"}', '--no-first-run', '--guest', '--disable-sync',
                    '--force-renderer-accessibility', '--window-size=900,650', '--window-position=0,100',
                    f'--app=http://127.0.0.1:{http.server_port}'], self.output / 'edge.log') as browser:
                hwnd = wait_value(lambda: owned_window(browser.pid, 'WinAuto WebGL Probe'))
                wait_value(lambda: state.get('webgl'))
                await self.call('list_windows', {}, 'edge-windows')
                await self.call('attach_window', {'hwnd': hwnd, 'label': 'Isolated Edge WebGL regression'}, 'edge-attach')
                try:
                    await self.call('get_elements', {}, 'edge-elements')
                    await self.call('screenshot', {'capture_mode': 'screen'}, 'edge-before')
                    # The owned fixture canvas fills the browser document. Its size
                    # is reported by the fixture, and the current UIA tree is retained.
                    width = win32gui.GetClientRect(hwnd)[2]
                    height = win32gui.GetClientRect(hwnd)[3]
                    assert state['width'] == width and state['height'] <= height
                    for i in range(3):
                        before = state['clicks']
                        await self.foreground([{'do': 'click', 'x': width // 2, 'y': height // 2},
                            {'do': 'capture'}], f'edge-click-capture-{i}')
                        wait_value(lambda: state['clicks'] == before + 1, timeout=5)
                        self.pixels(f'edge-click-capture-{i}', (26, 178, 64))
                    self.evidence['edge-app-state'] = dict(state)
                    self.save()
                finally:
                    await self.call('detach_window', {}, 'edge-detach')
        finally:
            http.shutdown(); http.server_close()

    async def unity(self, exe, reuse=False):
        project = self.output / 'unity-project'
        if not reuse:
            (project / 'Assets/Editor').mkdir(parents=True)
            (project / 'Packages').mkdir()
            (project / 'ProjectSettings').mkdir()
            (project / 'Packages/manifest.json').write_text(json.dumps({'dependencies': {
                'com.unity.modules.ui': '1.0.0', 'com.unity.modules.uielements': '1.0.0'}}))
            (project / 'ProjectSettings/ProjectVersion.txt').write_text('m_EditorVersion: 6000.3.24f1\n')
            shutil.copy2(FIXTURES / 'unity/ProbeUi.cs', project / 'Assets/ProbeUi.cs')
            shutil.copy2(FIXTURES / 'unity/ProbeBuild.cs', project / 'Assets/Editor/ProbeBuild.cs')
            result = subprocess.run([exe, '-batchmode', '-quit', '-projectPath', str(project),
                '-executeMethod', 'ProbeBuild.Build', '-logFile', str(self.output / 'unity-build.log')],
                creationflags=subprocess.CREATE_NO_WINDOW, timeout=900)
            assert result.returncode == 0, 'inspect unity-build.log'
        player = project / 'Build/InteropProbe.exe'
        self.evidence['unity-build'] = {'sha256': hashlib.sha256(player.read_bytes()).hexdigest()}
        state_path = project / 'Build/probe-state.json'
        state_path.unlink(missing_ok=True)
        with process([str(player), '-screen-fullscreen', '0', '-screen-width', '900', '-screen-height', '650',
                      '-logFile', str(self.output / 'unity-player.log')], self.output / 'unity-player-process.log') as app:
            hwnd = wait_value(lambda: owned_window(app.pid, 'WinAuto UI Toolkit Probe'))
            state = wait_value(lambda: (s if (s := read_json(state_path)) and s.get('ready') else None))
            listeners = [connection.laddr.port for connection in psutil.Process(app.pid).net_connections(kind='inet')
                         if connection.status == psutil.CONN_LISTEN]
            self.evidence['unity-listening-ports'] = listeners
            self.save()
            assert not listeners, 'fixture must not expose profiler/debugger listeners'
            await self.call('list_windows', {}, 'unity-windows')
            await self.call('attach_window', {'hwnd': hwnd, 'label': 'Isolated Unity UI Toolkit regression'}, 'unity-attach')
            try:
                await self.call('get_elements', {}, 'unity-elements')
                await self.foreground([{'do': 'capture'}], 'unity-before')
                # Geometry is measured by UI Toolkit in the fixture's 1:1 panel.
                x, y = round(state['x'] + state['width'] * .7), round(state['y'] + state['height'] / 2)
                await self.foreground([{'do': 'click', 'x': x, 'y': y}, {'do': 'wait', 'ms': 200},
                    {'do': 'type', 'text': 'help'}, {'do': 'wait', 'ms': 100},
                    {'do': 'key', 'keys': ['enter'], 'hold_ms': 120}, {'do': 'wait', 'ms': 200},
                    {'do': 'capture'}], 'unity-return')
                state = wait_value(lambda: (s if (s := read_json(state_path)) and s['returns'] == 1 else None), 5)
                assert state['text'] == 'help', state
                await self.foreground([{'do': 'click', 'x': x, 'y': y},
                    {'do': 'key', 'keys': ['escape'], 'hold_ms': 120}, {'do': 'wait', 'ms': 200},
                    {'do': 'capture'}], 'unity-escape')
                state = wait_value(lambda: (s if (s := read_json(state_path)) and s['escapes'] == 1 else None), 5)
                assert not state['overlay'], state
                self.evidence['unity-app-state'] = state
                self.save()
            finally:
                await self.call('detach_window', {}, 'unity-detach')
        await self.unity_editor(exe, project)

    async def unity_editor(self, exe, project):
        (project / 'editor-ready.json').unlink(missing_ok=True)
        with process([exe, '-projectPath', str(project), '-force-d3d12', '-executeMethod', 'ProbeBuild.Open',
                '-logFile', str(self.output / 'unity-editor.log')], self.output / 'unity-editor-process.log') as app:
            def editor_ready():
                if app.poll() is not None:
                    raise RuntimeError(f'isolated Editor exited before ready (code={app.returncode}); inspect unity-editor.log')
                return read_json(project / 'editor-ready.json')
            wait_value(editor_ready, timeout=180)
            wait_value(lambda: (s if (s := read_json(project / 'probe-state.json')) and s.get('ready') else None))
            time.sleep(2)  # let startup shader/layout work finish outside any input lease
            hwnd = wait_value(lambda: owned_window(app.pid, 'Unity'))
            await self.call('attach_window', {'hwnd': hwnd, 'label': 'Isolated Unity DX12 Game View regression'}, 'editor-attach')
            try:
                await self.call('get_elements', {}, 'editor-elements')
                width, height = win32gui.GetClientRect(hwnd)[2:]
                region = [10, 100, min(width, 700), min(height, 500)]
                await self.foreground([{'do': 'capture', 'region': region}], 'editor-foreground')
                self.pixels('editor-foreground', (24, 48, 80))
                await self.call('screenshot', {'capture_mode': 'screen'}, 'editor-visible')
                self.pixels('editor-visible', (24, 48, 80))
                await self.call('screenshot', {}, 'editor-auto')
                self.evidence['editor-app-state'] = read_json(project / 'probe-state.json')
                self.save()
            finally:
                await self.call('detach_window', {}, 'editor-detach')


async def main(args):
    args.output.mkdir(parents=True, exist_ok=args.reuse_unity)
    previous = args.output / 'evidence.json'
    if previous.exists():
        previous.rename(args.output / f'evidence-{time.time_ns()}.json')
    params = StdioServerParameters(command=sys.executable, args=[str(ROOT / 'server.py')],
        cwd=str(args.output), env={'TEMP': str(args.output), 'TMP': str(args.output)})
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write, read_timeout_seconds=1200) as session:
            await session.initialize()
            probe = Probe(session, args.output)
            probe.evidence['server'] = {'checkout': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT).decode().strip(),
                'dirty': bool(subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT).strip()),
                'module_sha256': {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                    for name in ('server.py', 'foreground_control.py', 'screenshot.py', 'takeover_indicator.py')}}
            if args.edge:
                await probe.edge(args.edge)
            if args.editor_only:
                await probe.unity_editor(args.unity, args.output / 'unity-project')
            elif args.unity:
                await probe.unity(args.unity, args.reuse_unity)
            probe.evidence['passed'] = True
            probe.save()
            print(json.dumps({'passed': True, 'evidence': str(args.output / 'evidence.json')}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--edge')
    parser.add_argument('--unity')
    parser.add_argument('--reuse-unity', action='store_true', help='reuse this output directory\'s existing fixture build')
    parser.add_argument('--editor-only', action='store_true', help='retest only the existing isolated Editor fixture')
    args = parser.parse_args()
    if not (args.edge or args.unity):
        parser.error('select at least one of --edge or --unity')
    if args.editor_only and not (args.unity and args.reuse_unity):
        parser.error('--editor-only requires --unity and --reuse-unity')
    asyncio.run(main(args))

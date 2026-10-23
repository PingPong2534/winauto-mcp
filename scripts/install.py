"""Install runtime-backed skill + MCP plugins together in native AI clients."""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
CLIENTS = ('codex', 'claude-code')
REQUIRED_TOOLS = {'list_windows', 'attach_window', 'get_elements', 'read_element', 'control_set', 'foreground_steps', 'guarded_steps',
                  'browser_status', 'browser_open', 'browser_snapshot', 'browser_actions', 'browser_close'}


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_suffix(path.suffix + '.tmp')
    staged.write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    staged.replace(path)


def command(args, env, json_result=False):
    result = subprocess.run([str(a) for a in args], env=env, capture_output=True,
                            text=True, encoding='utf-8', errors='replace', timeout=180)
    if result.returncode:
        raise RuntimeError(f"{' '.join(str(a) for a in args[:4])} failed: "
                           f"{(result.stderr or result.stdout).strip()[:1600]}")
    return json.loads(result.stdout) if json_result else result.stdout


def executable(client):
    name = 'claude' if client == 'claude-code' else 'codex'
    found = shutil.which(name)
    if not found and client == 'codex':
        candidate = Path(os.environ.get('LOCALAPPDATA', '')) / 'Programs/OpenAI/Codex/bin/codex.exe'
        found = str(candidate) if candidate.is_file() else None
    return found


def load_plan(path):
    plan = read_json(path)
    for key in ('name', 'marketplace'):
        if not re.fullmatch(r'[a-z][a-z0-9-]*', plan[key]):
            raise ValueError(f'Invalid installation {key}')
    if not re.fullmatch(r'\d+\.\d+\.\d+', plan['version']):
        raise ValueError('Installation version must be major.minor.patch')
    for client in CLIENTS:
        for dep in plan['required_plugins'][client]:
            if set(dep) != {'id', 'marketplace_source'} or not re.fullmatch(
                    r'[\w.-]+@[\w.-]+', dep['id']) or not dep['marketplace_source']:
                raise ValueError(f'Invalid required plugin for {client}')
    return plan


def extension_connector(repo, node=None):
    node = node or shutil.which('node')
    if not node:
        raise RuntimeError('Node.js 20+ required for Microsoft Playwright Extension mode')
    return {'command': str(Path(node).resolve()), 'args': [
        str(Path(repo).resolve() / 'browser-extension/node_modules/@playwright/mcp/cli.js'),
        '--extension', '--browser', 'msedge', '--no-webmcp',
        '--timeout-action', '5000', '--timeout-navigation', '15000']}


def ensure_extension_runtime(repo, plan, env):
    config = plan.get('browser_extension', {})
    if not config.get('enabled'):
        return None
    node = shutil.which('node')
    npm = shutil.which('npm.cmd') or shutil.which('npm')
    if not node or not npm:
        raise RuntimeError('Node.js 20+ and npm required; rerun install.ps1 to prepare dependencies')
    version = command([node, '--version'], env).strip()
    if not re.fullmatch(r'v\d+\.\d+\.\d+', version) or int(version[1:].split('.')[0]) < 20:
        raise RuntimeError('Node.js 20+ required for Microsoft Playwright Extension mode')
    package = Path(repo) / 'browser-extension'
    declared = read_json(package / 'package.json')['dependencies'].get('@playwright/mcp')
    if config.get('package') != '@playwright/mcp' or declared != config.get('version'):
        raise RuntimeError('Microsoft Playwright runtime must match the pinned installation plan')
    if config.get('browser') != 'msedge':
        raise RuntimeError('This extension connector supports Microsoft Edge only')
    command([npm, 'ci', '--prefix', package, '--ignore-scripts', '--no-audit', '--no-fund'], env)
    if read_json(package / 'node_modules/@playwright/mcp/package.json')['version'] != declared:
        raise RuntimeError('Installed Microsoft Playwright MCP version differs from the lock')
    return extension_connector(repo, node)


async def probe_extension(connector):
    """Handshake/tool discovery only; never connect to or change a browser."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    params = StdioServerParameters(**connector)
    async with stdio_client(params) as (reader, writer):
        async with ClientSession(reader, writer, read_timeout_seconds=20) as session:
            info = await session.initialize()
            names = sorted(t.name for t in (await session.list_tools()).tools)
            if 'playwright' not in info.server_info.name.lower() or not {'browser_snapshot','browser_tabs','browser_fill_form'} <= set(names):
                raise RuntimeError('Microsoft extension MCP handshake/tool discovery failed')
            return names


async def probe_server(python, server):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    import tempfile
    with tempfile.TemporaryDirectory(prefix='winauto-install-probe-') as cwd:
        params = StdioServerParameters(command=str(python), args=[str(server)], cwd=cwd,
                                      env={'TEMP': cwd, 'TMP': cwd})
        async with stdio_client(params) as (reader, writer):
            async with ClientSession(reader, writer, read_timeout_seconds=30) as session:
                info = await session.initialize()
                names = {t.name for t in (await session.list_tools()).tools}
                if info.server_info.name != 'winauto-mcp' or not REQUIRED_TOOLS <= names:
                    raise RuntimeError('Server handshake did not expose the required winauto tools')
                # Passive query only: installation never attaches or changes a user app.
                status = await session.call_tool('keyboard_status', {})
                if status.is_error:
                    raise RuntimeError('Server status query failed')
                browser = await session.call_tool('browser_status', {})
                if browser.is_error or json.loads(next(x.text for x in browser.content if x.type == 'text'))['sessions']:
                    raise RuntimeError('Passive browser status query failed')
                return sorted(names)


def build_bundle(repo, state, plan, python):
    repo, state = Path(repo).resolve(), Path(state).resolve()
    source = repo / 'skills/winauto-mcp'
    if not (source / 'SKILL.md').is_file():
        raise RuntimeError('Required winauto-mcp skill is missing')
    digest = hashlib.sha256()
    digest.update(json.dumps(plan, sort_keys=True).encode())
    digest.update(json.dumps({'repo': str(repo), 'python': str(Path(python).resolve())}).encode())
    files = sorted([*source.rglob('*'), *repo.glob('*.py'), repo / 'requirements.txt', repo / 'installation.json'])
    files += [repo / 'browser-extension/package.json', repo / 'browser-extension/package-lock.json']
    for file in files:
        if file.is_file() and '__pycache__' not in file.parts:
            digest.update(file.relative_to(repo).as_posix().encode())
            digest.update(file.read_bytes())
    # Cache identity changes with skill/server content, avoiding stale plugin copies.
    version = plan['version'] + '+' + digest.hexdigest()[:12]
    market = state / 'marketplace'
    plugin = market / 'plugins' / plan['name'] / version
    skill = plugin / 'skills/winauto-mcp'
    shutil.copytree(source, skill, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '*.pyo'))
    common = {'name': plan['name'], 'version': version,
              'description': 'Windows UIA, guarded foreground control and owned persistent Playwright browsers.',
              'author': {'name': 'winauto-mcp contributors'}}
    codex = dict(common, skills='./skills/', mcpServers='./.mcp.json',
                 interface={'displayName': 'WinAuto MCP',
                            'shortDescription': 'Windows UIA controls and verified workflows'})
    claude = dict(common)
    if plan['required_plugins']['claude-code']:
        claude['dependencies'] = [dep['id'] for dep in plan['required_plugins']['claude-code']]
    write_json(plugin / '.codex-plugin/plugin.json', codex)
    write_json(plugin / '.claude-plugin/plugin.json', claude)
    connectors = {'winauto': {'command': str(Path(python).resolve()), 'args': [str(repo / 'server.py')]}}
    if plan.get('browser_extension', {}).get('enabled'):
        connectors['playwright-extension'] = extension_connector(repo)
    write_json(plugin / '.mcp.json', {'mcpServers': connectors})
    relative = './' + plugin.relative_to(market).as_posix()
    write_json(market / '.agents/plugins/marketplace.json', {
        'name': plan['marketplace'], 'plugins': [{
            'name': plan['name'], 'source': {'source': 'local', 'path': relative},
            'policy': {'installation': 'AVAILABLE', 'authentication': 'ON_INSTALL'},
            'category': 'Productivity'}]})
    write_json(market / '.claude-plugin/marketplace.json', {
        'name': plan['marketplace'], 'owner': common['author'],
        'plugins': [{'name': plan['name'], 'source': relative}]})
    return market, plugin


def installed(client, cli, env):
    data = command([cli, 'plugin', 'list', '--json'], env, True)
    return data['installed'] if client == 'codex' else data


def ensure_marketplace(client, cli, source, name, env):
    if client == 'codex':
        entry = command([cli, 'plugin', 'marketplace', 'add', source, '--json'], env, True)
        if entry['marketplaceName'] != name:
            raise RuntimeError(f'Marketplace source did not register the expected name {name}')
        if entry.get('alreadyAdded') and not Path(source).exists():
            command([cli, 'plugin', 'marketplace', 'upgrade', name, '--json'], env, True)
    else:
        entries = command([cli, 'plugin', 'marketplace', 'list', '--json'], env, True)
        existing = next((entry for entry in entries if entry.get('name') == name), None)
        if existing:
            if Path(source).exists() and (existing.get('source') != 'directory'
                    or Path(existing.get('path', '')).resolve() != Path(source).resolve()):
                raise RuntimeError(f'Marketplace {name} is already registered from a different source')
            # Updating a known local/Git catalog lets install/update find its new revision.
            command([cli, 'plugin', 'marketplace', 'update', name], env)
        else:
            command([cli, 'plugin', 'marketplace', 'add', source, '--scope', 'user'], env)


def ensure_plugin(client, cli, plugin_id, env):
    if client == 'codex':
        data = command([cli, 'plugin', 'add', plugin_id, '--json'], env, True)
        path = Path(data['installedPath'])
    else:
        found = next((p for p in installed(client, cli, env)
                      if p['id'] == plugin_id and p.get('scope') == 'user'), None)
        action = 'update' if found else 'install'
        command([cli, 'plugin', action, plugin_id, '--scope', 'user'], env)
        data = next((p for p in installed(client, cli, env)
                     if p['id'] == plugin_id and p.get('scope') == 'user'), None)
        if not data:
            raise RuntimeError(f'{plugin_id} was not installed in {client}')
        if data.get('enabled') is not True:
            command([cli, 'plugin', 'enable', plugin_id, '--scope', 'user'], env)
        path = Path(data['installPath'])
    key = 'pluginId' if client == 'codex' else 'id'
    if not any(p.get(key) == plugin_id and p.get('enabled') is True
               for p in installed(client, cli, env)):
        raise RuntimeError(f'{plugin_id} is not enabled in {client}')
    return path


def verify_bundle(source, target):
    for file in (Path(source) / 'skills').rglob('*'):
        if file.is_file() and '__pycache__' not in file.parts and file.suffix not in ('.pyc', '.pyo'):
            copy = Path(target) / file.relative_to(source)
            if not copy.is_file() or copy.read_bytes() != file.read_bytes():
                raise RuntimeError(f'Installed skill is missing or stale: {copy}')
    if read_json(Path(source) / '.mcp.json') != read_json(Path(target) / '.mcp.json'):
        raise RuntimeError('Installed MCP connector does not match this server')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--client', choices=['auto', 'both', *CLIENTS], default='auto')
    parser.add_argument('--state-dir', type=Path, default=Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'winauto-mcp')
    parser.add_argument('--codex-home', type=Path)
    parser.add_argument('--claude-home', type=Path)
    args = parser.parse_args(argv)
    state = args.state_dir.resolve()
    report = {'ok': False, 'started_at': datetime.now(timezone.utc).isoformat(),
              'phase': 'preflight', 'server': str(ROOT / 'server.py'), 'clients': {},
              'restart_required': True}
    write_json(state / 'installation-result.json', report)
    if os.name != 'nt':
        raise RuntimeError('winauto-mcp requires a local Windows desktop host')
    plan = load_plan(ROOT / 'installation.json')
    chosen = list(CLIENTS) if args.client == 'both' else (
        [c for c in CLIENTS if executable(c)] if args.client == 'auto' else [args.client])
    if not chosen:
        raise RuntimeError('No supported client found. Install Codex or Claude Code first.')
    env = os.environ.copy()
    if args.codex_home:
        env['CODEX_HOME'] = str(args.codex_home.resolve())
        args.codex_home.mkdir(parents=True, exist_ok=True)
    if args.claude_home:
        env['CLAUDE_CONFIG_DIR'] = str(args.claude_home.resolve())
        args.claude_home.mkdir(parents=True, exist_ok=True)
    paths = {}
    for client in chosen:
        cli = executable(client)
        if not cli:
            raise RuntimeError(f'Missing {client} CLI; complete bundle installation cannot continue')
        command([cli, 'plugin', 'list', '--help'], env)
        paths[client] = cli
    names = asyncio.run(probe_server(sys.executable, ROOT / 'server.py'))
    extension = ensure_extension_runtime(ROOT, plan, env)
    extension_names = asyncio.run(probe_extension(extension)) if extension else []
    report['browser_extension'] = {'enabled': bool(extension), 'package_version': plan.get('browser_extension', {}).get('version'),
                                   'tool_count': len(extension_names), 'tools': extension_names,
                                   'connection_approval': 'interactive; no token bypass configured',
                                   'browser_connection_verified': False}
    market, plugin = build_bundle(ROOT, state, plan, sys.executable)
    report.update(tools=names, phase='registering')
    write_json(state / 'installation-result.json', report)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')
    for client in chosen:
        home = Path(env.get('CODEX_HOME', str(Path.home() / '.codex'))) if client == 'codex' else Path(
            env.get('CLAUDE_CONFIG_DIR', str(Path.home() / '.claude')))
        for config in ('config.toml',) if client == 'codex' else ('settings.json', 'plugins/installed_plugins.json', 'plugins/known_marketplaces.json'):
            old = home / config
            if old.is_file():
                dest = state / 'backups' / stamp / client / config
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(old, dest)
    try:
        for client in chosen:
            cli = paths[client]
            for dep in plan['required_plugins'][client]:
                ensure_marketplace(client, cli, dep['marketplace_source'], dep['id'].split('@')[1], env)
                ensure_plugin(client, cli, dep['id'], env)
            if client == 'claude-code':
                command([cli, 'plugin', 'validate', plugin], env)
            ensure_marketplace(client, cli, str(market), plan['marketplace'], env)
            target = ensure_plugin(client, cli, plan['name'] + '@' + plan['marketplace'], env)
            verify_bundle(plugin, target)
            connector = read_json(target / '.mcp.json')['mcpServers']['winauto']
            connected = asyncio.run(probe_server(connector['command'], connector['args'][0]))
            if connected != names:
                raise RuntimeError(f'{client} connector exposes a different tool set')
            if extension:
                ext = read_json(target / '.mcp.json')['mcpServers']['playwright-extension']
                if asyncio.run(probe_extension(ext)) != extension_names:
                    raise RuntimeError(f'{client} extension connector exposes a different tool set')
            report['clients'][client] = {'plugin': plan['name'] + '@' + plan['marketplace'],
                                         'installed_path': str(target), 'skill': True,
                                         'mcp_connector': True, 'enabled': True,
                                         'tool_count': len(connected),
                                         'required_plugins': [d['id'] for d in plan['required_plugins'][client]]}
        report['ok'] = True
        report['phase'] = 'complete'
    except Exception as exc:
        report['error'] = str(exc)
        raise
    finally:
        write_json(state / 'installation-result.json', report)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
    try:
        sys.exit(main())
    except Exception as exc:
        print(f'Installation incomplete: {exc}', file=sys.stderr)
        sys.exit(1)

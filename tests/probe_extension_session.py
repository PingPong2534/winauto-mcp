"""Read-only TikTok probe using Microsoft's real extension MCP transport.

Interactive stdin: connect, inspect, search, stop. Native extension approval
remains interactive. Never invokes upload, click, navigation or publishing.
Disconnecting MCP preserves the user's existing browser and tabs.
"""
import argparse
import asyncio
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import re
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('installer', ROOT/'scripts/install.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


def search_field(content):
    matches = list(re.finditer(
        r'(?m)^(\s*)- textbox[^\n]*\[ref=([^\]]+)\][^\n]*\n'
        r'\1  - /placeholder: Search for post description'
        r'(?:\n\1  - text: ([^\n]*))?', content))
    if len(matches)!=1:
        raise RuntimeError('Unique observed Posts search textbox missing')
    return matches[0].group(2), matches[0].group(3) or ''


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--search-text', default='winauto-read-only-probe')
    parser.add_argument('--evidence', type=Path, default=Path(os.environ['LOCALAPPDATA'])/'winauto-mcp/verification/extension-probe.json')
    args = parser.parse_args()
    path = args.evidence
    path.parent.mkdir(parents=True, exist_ok=True)
    evidence = {
        'route': 'official-microsoft-extension', 'publish_actions': 0,
        'upload_actions': 0, 'observations': []}

    def record(event, **data):
        entry = {'event': event, 'at': datetime.now(timezone.utc).isoformat(), **data}
        evidence['observations'].append(entry)
        path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        print(json.dumps({k:v for k,v in entry.items() if k!='snapshot'}, ensure_ascii=False), flush=True)

    async with stdio_client(StdioServerParameters(**installer.extension_connector(ROOT))) as (reader, writer):
        async with ClientSession(reader, writer, read_timeout_seconds=120) as session:
            await session.initialize()
            tools = {t.name: t.input_schema for t in (await session.list_tools()).tools}
            record('handshake', tool_count=len(tools))

            async def call(name, args):
                result = await session.call_tool(name, args)
                content = '\n'.join(x.text for x in result.content if x.type=='text')
                if result.is_error:
                    # Upstream errors may contain credentials, URLs or page text.
                    raise RuntimeError(f'{name} failed; inspect current state')
                return content

            async def snapshot():
                content = await call('browser_snapshot', {})
                if not ('https://www.tiktok.com/tiktokstudio/content' in content
                        and 'Posts' in content and 'Drafts' in content):
                    raise RuntimeError('Current selected tab is not authenticated TikTok Posts; no form action')
                return content

            print('READY: connect/inspect/search/stop; approve only the intended TikTok tab', flush=True)
            while True:
                command = (await asyncio.to_thread(sys.stdin.readline)).strip()
                if not command or command=='stop':
                    break
                try:
                    if command=='connect':
                        tabs = await call('browser_tabs', {'action':'list'})
                        record('connected_tabs', tiktok_studio_listed='https://www.tiktok.com/tiktokstudio/content' in tabs)
                    elif command=='inspect':
                        await snapshot()
                        record('authenticated_posts', verified=True)
                    elif command=='search':
                        content = await snapshot()
                        ref, before = search_field(content)
                        # Refuse to overwrite an existing search/draft value.
                        if before:
                            raise RuntimeError('Search already contains data; leave it unchanged')
                        await call('browser_fill_form', {'fields':[{'name':'Posts search', 'type':'textbox', 'target':ref, 'value':args.search_text}]})
                        filled = await snapshot()
                        current, value = search_field(filled)
                        record('search_filled', verified=value==args.search_text)
                        await call('browser_fill_form', {'fields':[{'name':'Posts search', 'type':'textbox', 'target':current, 'value':''}]})
                        restored = await snapshot()
                        record('search_restored', verified=search_field(restored)[1]=='')
                    else:
                        record('invalid_command')
                except Exception as error:
                    record('probe_error', error_type=type(error).__name__)
    record('disconnected', user_browser_close_called=False)


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    asyncio.run(main())

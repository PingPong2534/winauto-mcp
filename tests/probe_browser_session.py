"""Human-assisted, READ-ONLY real-site persistence probe through actual MCP.

Open the dedicated visible profile, sign in manually, then send stdin commands:
inspect, reopen, restart, resume, stop. There is no publish/upload action path.
The resume check only fills and clears the observed Posts search field.
"""
import argparse
import asyncio
from contextlib import AsyncExitStack
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]


async def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', default='tiktok')
    parser.add_argument('--url', default='https://www.tiktok.com/tiktokstudio/content')
    parser.add_argument('--search-text', default='winauto-read-only-probe')
    parser.add_argument('--evidence', type=Path, default=Path(os.environ['LOCALAPPDATA'])/'winauto-mcp/verification/persistent-probe.json')
    args=parser.parse_args()
    if args.url != 'https://www.tiktok.com/tiktokstudio/content':
        parser.error('this real-site probe is restricted to the TikTok owner Posts page')
    stack=None;session=None;sid=None;tab=None
    evidence={'profile':args.profile,'url':args.url,'external_publish_actions':0,'external_upload_actions':0,'observations':[]}

    async def call(name, **kwargs):
        response=await session.call_tool(name,kwargs)
        if response.is_error:
            raise RuntimeError(next(x.text for x in response.content if x.type=='text'))
        return json.loads(next(x.text for x in response.content if x.type=='text'))

    def record(event, data):
        entry={'event':event,'at':datetime.now(timezone.utc).isoformat(),**data}
        evidence['observations'].append(entry)
        args.evidence.parent.mkdir(parents=True,exist_ok=True)
        args.evidence.write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print(json.dumps(entry,ensure_ascii=False),flush=True)

    async def connect():
        nonlocal stack,session
        stack=AsyncExitStack()
        reader,writer=await stack.enter_async_context(stdio_client(StdioServerParameters(
            command=str(ROOT/'.venv/Scripts/python.exe'),args=[str(ROOT/'server.py')],cwd=str(ROOT))))
        session=await stack.enter_async_context(ClientSession(reader,writer,read_timeout_seconds=45))
        await session.initialize()

    async def open_profile():
        nonlocal sid,tab
        result=await call('browser_open',profile=args.profile,url=args.url)
        sid=result['session_id'];tab=result['tabs'][0]['tab_id']
        record('open',{'ok':result['ok'],'session_id':sid,'navigation':result['tabs'][0]['url']})

    async def inspect(event='inspect'):
        snapshot=await call('browser_snapshot',session_id=sid,tab_id=tab,limit=200)
        text=snapshot['text']
        authenticated=('/tiktokstudio/' in snapshot['url'] and 'Posts' in text and 'Drafts' in text and 'Upload' in text)
        record(event,{'authenticated_owner_ui':authenticated,
                      'posts_search_present':any('search for post description' in (x.get('placeholder') or '').lower() for x in snapshot['elements'])})
        return snapshot,authenticated

    await connect()
    try:
        await open_profile()
        await inspect('initial')
        print('READY: manual login if needed; commands inspect/reopen/restart/resume/stop',flush=True)
        while True:
            command=(await asyncio.to_thread(sys.stdin.readline)).strip()
            if not command or command=='stop':break
            try:
                if command=='inspect':await inspect()
                elif command=='reopen':
                    await call('browser_close',session_id=sid);await open_profile();await inspect('after_browser_reopen')
                elif command=='restart':
                    await stack.aclose();await connect();await open_profile();await inspect('after_mcp_restart')
                elif command=='resume':
                    snapshot,authenticated=await inspect('before_resume')
                    if not authenticated:raise RuntimeError('manual login required; no form action performed')
                    fields=[x for x in snapshot['elements']if x.get('visible') and x.get('tag')=='input'
                            and 'search for post description' in (x.get('placeholder')or'').lower()]
                    if len(fields)!=1:raise RuntimeError('unique observed Posts search field not available; no action performed')
                    if fields[0].get('value'):raise RuntimeError('preserve the existing user search; no action performed')
                    result=await call('browser_actions',session_id=sid,tab_id=tab,observation_id=snapshot['observation_id'],
                                      steps=[{'do':'fill','ref':fields[0]['ref'],'text':args.search_text},
                                             {'do':'read','ref':fields[0]['ref']}])
                    record('resume_search',{'ok':result['ok'],'completed_steps':result['completed_steps'],'publish_clicked':False})
                    # Reobserve before clearing; do not replay an uncertain mutation.
                    snapshot=await call('browser_snapshot',session_id=sid,tab_id=tab)
                    fields=[x for x in snapshot['elements']if x.get('tag')=='input' and x.get('value')==args.search_text]
                    if len(fields)==1:
                        cleared=await call('browser_actions',session_id=sid,tab_id=tab,observation_id=snapshot['observation_id'],
                                           steps=[{'do':'fill','ref':fields[0]['ref'],'text':''}])
                        record('restore_search',{'ok':cleared['ok']})
                else:record('invalid_command',{'allowed':['inspect','reopen','restart','resume','stop']})
            except Exception as error:
                record('probe_error',{'error_type':type(error).__name__})
    finally:
        await stack.aclose()
        record('stopped',{'owned_browser_closed':True,'profile_retained':True})


if __name__=='__main__':asyncio.run(main())

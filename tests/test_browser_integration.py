"""Real headed Edge + stdio MCP, isolated profile and local authenticated fixture.

No TikTok access, personal profile, external upload or publication. Includes
browser close/reopen and complete MCP process restart with the same profile.
"""
import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]
EDGE = Path(os.environ.get('PROGRAMFILES(X86)', 'C:/Program Files (x86)'))/'Microsoft/Edge/Application/msedge.exe'


class FixtureHandler(BaseHTTPRequestHandler):
    publications = 0

    def log_message(self, *_): pass

    def do_GET(self):
        authenticated = 'fixture_session=valid' in self.headers.get('Cookie', '')
        if self.path == '/login':
            self.send_response(200);self.end_headers()
            self.wfile.write(b'<title>Owned login fixture</title><button onclick="location.href=\'/session\'">Sign in fixture</button>')
        elif self.path == '/session':
            self.send_response(302);self.send_header('Set-Cookie','fixture_session=valid; Path=/; HttpOnly; Max-Age=3600');self.send_header('Location','/home');self.end_headers()
        elif self.path == '/home' and authenticated:
            self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.end_headers()
            self.wfile.write('''<title>Owned authenticated fixture</title><h1>Authenticated fixture account</h1>
              <textarea aria-label="Draft" oninput="localStorage.draft=this.value"></textarea>
              <input aria-label="Readonly" value="unchanged" readonly>
              <input type="password" aria-label="Password" value="fixture-secret">
              <input type="PASSWORD" aria-label="Uppercase password" value="fixture-uppercase-secret">
              <input autocomplete="ONE-TIME-CODE" aria-label="OTP" value="fixture-code">
              <input type="file" aria-label="File" style="display:none">
              <input type="checkbox" aria-label="AI disclosure">
              <select aria-label="Audience"><option value="private">Private</option><option value="public">Public</option></select>
              <form action="/publish" method="post"><button>Publish fixture</button></form>
              <script>document.querySelector('textarea').value=localStorage.draft||'';</script>'''.encode())
        else:
            self.send_response(302);self.send_header('Location','/login');self.end_headers()

    def do_POST(self):
        type(self).publications += 1
        self.send_response(200);self.end_headers();self.wfile.write(b'Publication must never be requested by this test')


@unittest.skipUnless(os.name == 'nt' and EDGE.is_file(), 'Installed Edge required; no browser installed implicitly')
class BrowserIntegrationTests(unittest.TestCase):
    def test_login_and_partial_work_survive_browser_and_mcp_restart(self):
        FixtureHandler.publications = 0
        web = ThreadingHTTPServer(('127.0.0.1', 0), FixtureHandler)
        thread = threading.Thread(target=web.serve_forever, daemon=True);thread.start()
        url = f'http://127.0.0.1:{web.server_port}'

        async def tool(session, name, **arguments):
            response = await session.call_tool(name, arguments)
            if response.is_error:
                raise AssertionError(f'{name}: {response.content}')
            return json.loads(next(x.text for x in response.content if x.type == 'text'))

        def ref(snapshot, label):
            return next(x['ref'] for x in snapshot['elements'] if x['label'] == label)

        async def run(root):
            params = StdioServerParameters(command=str(ROOT/'.venv/Scripts/python.exe'),args=[str(ROOT/'server.py')],
                cwd=root,env={'WINAUTO_BROWSER_PROFILE_ROOT':str(Path(root)/'profiles')})
            expected = 'ร่างทดสอบ 😀\nทำงานต่อหลังเปิดใหม่'
            async with stdio_client(params) as (read,write):
                async with ClientSession(read,write,read_timeout_seconds=45) as session:
                    await session.initialize()
                    opened = await tool(session,'browser_open',profile='fixture',url=url+'/home')
                    self.assertTrue(opened['ok']);sid=opened['session_id'];tab=opened['tabs'][0]['tab_id']
                    snap=await tool(session,'browser_snapshot',session_id=sid,tab_id=tab)
                    self.assertIn('Sign in fixture',snap['text'])
                    clicked=await tool(session,'browser_actions',session_id=sid,tab_id=tab,observation_id=snap['observation_id'],
                        steps=[{'do':'click','ref':snap['elements'][0]['ref']}]);self.assertTrue(clicked['ok'])
                    snap=await tool(session,'browser_snapshot',session_id=sid,tab_id=tab)
                    self.assertIn('Authenticated fixture account',snap['text'])
                    self.assertEqual(next(x['value']for x in snap['elements']if x['label']=='Password'),'[redacted]')
                    for label in ('Uppercase password', 'OTP'):
                        self.assertEqual(next(x['value'] for x in snap['elements'] if x['label']==label),'[redacted]')
                        refused=await tool(session,'browser_actions',session_id=sid,tab_id=tab,observation_id=snap['observation_id'],
                            steps=[{'do':'fill','ref':ref(snap,label),'text':'must-not-write'}])
                        self.assertFalse(refused['ok'])
                        self.assertEqual(refused['completed_steps'],0)
                    file=Path(root)/'owned upload.txt';file.write_text('owned local fixture')
                    actions=[{'do':'fill','ref':ref(snap,'Draft'),'text':expected},
                             {'do':'check','ref':ref(snap,'AI disclosure'),'checked':True},
                             {'do':'select','ref':ref(snap,'Audience'),'value':'private'},
                             {'do':'upload','ref':ref(snap,'File'),'files':[str(file)]},
                             {'do':'fill','ref':ref(snap,'Readonly'),'text':'must refuse'}]
                    result=await tool(session,'browser_actions',session_id=sid,tab_id=tab,observation_id=snap['observation_id'],steps=actions)
                    self.assertFalse(result['ok']);self.assertEqual(result['completed_steps'],4)
                    snap=await tool(session,'browser_snapshot',session_id=sid,tab_id=tab)
                    self.assertEqual(next(x['value']for x in snap['elements']if x['label']=='Draft'),expected)
                    self.assertEqual(next(x['value']for x in snap['elements']if x['label']=='File'),['owned upload.txt'])
                    self.assertEqual(FixtureHandler.publications,0)
                    await tool(session,'browser_close',session_id=sid)
                    again=await tool(session,'browser_open',profile='fixture',url=url+'/home')
                    snap=await tool(session,'browser_snapshot',session_id=again['session_id'],tab_id=again['tabs'][0]['tab_id'])
                    self.assertIn('Authenticated fixture account',snap['text'])
                    self.assertEqual(next(x['value']for x in snap['elements']if x['label']=='Draft'),expected)
                    # Server finally closes the owned context on stdio EOF.
            async with stdio_client(params) as (read,write):
                async with ClientSession(read,write,read_timeout_seconds=45) as session:
                    await session.initialize()
                    again=await tool(session,'browser_open',profile='fixture',url=url+'/home')
                    snap=await tool(session,'browser_snapshot',session_id=again['session_id'],tab_id=again['tabs'][0]['tab_id'])
                    self.assertIn('Authenticated fixture account',snap['text'])
                    self.assertEqual(next(x['value']for x in snap['elements']if x['label']=='Draft'),expected)
                    continued=await tool(session,'browser_actions',session_id=again['session_id'],tab_id=again['tabs'][0]['tab_id'],
                        observation_id=snap['observation_id'],steps=[{'do':'fill','ref':ref(snap,'Draft'),'text':expected+' ต่อได้แล้ว'}])
                    self.assertTrue(continued['ok'])
                    history=await tool(session,'browser_history');self.assertNotIn(expected,json.dumps(history))
                    await tool(session,'browser_close',session_id=again['session_id'])
            self.assertEqual(FixtureHandler.publications,0)

        try:
            with tempfile.TemporaryDirectory(prefix='winauto-browser-') as root: asyncio.run(run(root))
        finally: web.shutdown();web.server_close();thread.join(3)


if __name__ == '__main__': unittest.main(verbosity=2)

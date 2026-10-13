import sys
import json, pathlib, subprocess, tempfile, unittest
ROOT=pathlib.Path(__file__).resolve().parents[1]
CAO=ROOT/'scripts'/'cao.py'
class HostOutputTests(unittest.TestCase):
    def test_claude_pretool_native_output(self):
        with tempfile.TemporaryDirectory() as td:
            repo=pathlib.Path(td); subprocess.run(['git','init','-q'],cwd=repo,check=True, timeout=20)
            subprocess.run([sys.executable,str(CAO),'init'],cwd=repo,check=True,stdout=subprocess.DEVNULL, timeout=20)
            p=subprocess.run([sys.executable,str(CAO),'host-event','--host','claude-code','--repo',str(repo)],cwd=repo,text=True,input=json.dumps({'event':'pre_tool','tool_name':'Write','tool_input':{'path':'a.py','content':'x'}}),capture_output=True,check=True, timeout=20)
            out=json.loads(p.stdout); self.assertEqual(out['hookSpecificOutput']['hookEventName'],'PreToolUse'); self.assertEqual(out['hookSpecificOutput']['permissionDecision'],'deny')
    def test_codex_pretool_native_output(self):
        with tempfile.TemporaryDirectory() as td:
            repo=pathlib.Path(td); subprocess.run(['git','init','-q'],cwd=repo,check=True, timeout=20)
            subprocess.run([sys.executable,str(CAO),'init'],cwd=repo,check=True,stdout=subprocess.DEVNULL, timeout=20)
            p=subprocess.run([sys.executable,str(CAO),'host-event','--host','codex','--repo',str(repo)],cwd=repo,text=True,input=json.dumps({'event':'pre_tool','tool_name':'apply_patch','tool_input':{'command':'*** Begin Patch'}}),capture_output=True,check=True, timeout=20)
            out=json.loads(p.stdout); self.assertEqual(out['hookSpecificOutput']['permissionDecision'],'deny')
if __name__=='__main__': unittest.main()

"""Exercise the real T005 empty/missing-corpus gate using a temporary task."""
from pathlib import Path
import json,os,subprocess,sys,tempfile
ROOT=Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix='research_workflow_gate_') as td:
 d=Path(td);manifest=d/'manifest.json'
 manifest.write_text(json.dumps({'schema':'instsci.unified_analysis_manifest.v1','schema_version':'1.0.0','question':'Offline missing corpus check','items':[]}),encoding='utf-8')
 env={**os.environ,'PYTHONUTF8':'1','PYTHONDONTWRITEBYTECODE':'1'}
 # Block networking in the child interpreter, even if a future regression reaches it.
 wrapper='import runpy,socket,sys\ndef blocked(*a,**k): raise RuntimeError("Network disabled by release test")\nsocket.socket.connect=blocked\nsocket.create_connection=blocked\nsocket.getaddrinfo=blocked\nsys.argv='+repr([str(ROOT/'deliveries/T005_research_pipeline/research_runner.py'),'--manifest',str(manifest),'--output',str(d/'run'),'--question','Offline missing corpus check','--backend','paperqa_native','--config',str(ROOT/'deliveries/T005_research_pipeline/configs/gemini_proxy_backend.yaml')])+'\nrunpy.run_path(sys.argv[0],run_name="__main__")'
 p=subprocess.run([sys.executable,'-B','-c',wrapper],cwd=ROOT,env=env,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=60)
 state=d/'run/run_result.json'
 result=json.loads(state.read_text(encoding='utf-8')) if state.exists() else {}
 # A missing parser/import must not masquerade as a successful empty-corpus test.
 expected_reason=any('清单文件内容条目为空' in str(e) for e in result.get('errors_or_unresolved_claims',[]))
 ok=p.returncode==1 and result.get('execution_status')=='FAILED' and expected_reason
 print(json.dumps({'empty_corpus_rejected':ok,'exit_code':p.returncode,'state_written':state.exists(),'execution_status':result.get('execution_status'),'network':'blocked'},ensure_ascii=False))
 if not ok:
  print(p.stdout[-1800:]);print(p.stderr[-1000:]);sys.exit(1)

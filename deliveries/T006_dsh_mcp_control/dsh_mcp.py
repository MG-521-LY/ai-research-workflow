"""MCP tools for the two user-authorized existing DSH research sessions."""
from __future__ import annotations
import hashlib,json,re,sqlite3,time,os
from pathlib import Path
from typing import Literal
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / 'deps'))
import dsh_rpc

ROOT=Path(os.environ.get('AI_RESEARCH_ROOT', str(Path(__file__).resolve().parents[2]))).resolve()
HERE=Path(__file__).resolve().parent
STATE=HERE/'state';STATE.mkdir(exist_ok=True)
mcp=FastMCP('dsh_control_mcp',instructions='Control only user-authorized AI research sessions. Accepted prompts are not completed work. Never return authentication or model credentials.')

def database():
    conn=sqlite3.connect(STATE/'dispatches.sqlite3',timeout=10)
    conn.row_factory=sqlite3.Row
    conn.execute('CREATE TABLE IF NOT EXISTS dispatches (request_id TEXT PRIMARY KEY, session_id TEXT, task_path TEXT, sha256 TEXT, mode TEXT, status TEXT, response TEXT, updated_at REAL)')
    return conn

def session_id(value:str)->str:
    if value not in dsh_rpc.SESSIONS:raise ValueError('Only user-authorized research sessions are allowed')
    return value

def session_info(item:dict)->dict:
    projection=item.get('projections',{}) or {};values=projection.get('values',{})
    return {'session_id':item.get('sessionId'),'title':values.get('title'),'cwd':item.get('cwd'),'running':item.get('running'),'agent_available':item.get('agentAvailable'),'as_of_seq':projection.get('asOfSeq'),'model_selection':values.get('modelSelection'),
        'inbox_counts':{k:len(v) for k,v in values.get('inbox',{}).items()},
        'inbox_request_ids':{k:[x.get('requestId') or x.get('source',{}).get('rpcId') for x in v if isinstance(x,dict) and (x.get('requestId') or x.get('source',{}).get('rpcId'))] for k,v in values.get('inbox',{}).items()}}

@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True,destructiveHint=False,idempotentHint=True,openWorldHint=False))
def dsh_list_sessions()->dict:
    """Read status and model selection for the two existing research sessions only."""
    result=dsh_rpc.list_sessions()
    return {'sessions':[session_info(x) for x in result.get('sessions',[])],'has_more':result.get('has_more',False)}

@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False,destructiveHint=False,idempotentHint=True,openWorldHint=False))
def dsh_create_reviewer()->dict:
    """Create the single additional DSH review session explicitly authorized by the user, selecting Gemini 3.8 before prompting.

    Repeated calls return the recorded session instead of creating more chats.
    An uncertain creation is not retried automatically.
    """
    marker=STATE/'reviewer_creation.json'
    if marker.is_file():
        value=json.loads(marker.read_text(encoding='utf-8'))
        if not value.get('session_id'):raise ValueError('Previous creation needs inspection; no automatic duplicate creation')
        return value|{'deduplicated':True}
    marker.write_text(json.dumps({'status':'creating','authorization':'User explicitly allowed additional DSH sessions to reduce Codex usage.'}),encoding='utf-8')
    value=dsh_rpc.call('session/create',{'request':{'cwd':str(ROOT)}})
    if not isinstance(value,dict) or not value.get('sessionId'):raise ValueError('Unexpected create result; inspect before retrying')
    sid=value['sessionId'];dsh_rpc.SESSIONS[sid]='会话三_整链验收与联调'
    managed=STATE/'managed_sessions.json'
    registry=json.loads(managed.read_text(encoding='utf-8')) if managed.is_file() else {}
    registry[sid]='会话三_整链验收与联调';managed.write_text(json.dumps(registry,ensure_ascii=False,indent=2),encoding='utf-8')
    record={'status':'created','session_id':sid,'execution_complete':False}
    marker.write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
    selected=dsh_rpc.call('session/selectModel',{'request':{'sessionId':sid,'provider':'antigravity-local','model':'gemini-3.8-flash-high'}})
    record['model_selection']=selected;marker.write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
    return record

@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True,destructiveHint=False,idempotentHint=True,openWorldHint=False))
def dsh_get_session(session:str)->dict:
    """Read current running/queue/model status for an authorized DSH research session."""
    sid=session_id(session)
    info=next((session_info(x) for x in dsh_rpc.list_sessions().get('sessions',[]) if x.get('sessionId')==sid),None)
    if info is None:raise ValueError('Authorized session was not returned by the DSH controller')
    return info

@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True,destructiveHint=False,idempotentHint=True,openWorldHint=False))
def dsh_read_progress(session:str,after_seq:int=0)->dict:
    """Read bounded recent user/assistant text and event counts, excluding tool arguments/results and reasoning.

    The returned sequence is a cursor. An empty message list may mean the agent
    is working through tool calls. This is progress evidence, not acceptance.
    """
    sid=session_id(session)
    projection=dsh_rpc.call('session/projections',{'request':{'sessionId':sid}})
    if not isinstance(projection,dict):raise ValueError('Session projection unavailable')
    seq=projection.get('asOfSeq',0)
    if seq<=after_seq:return {'session_id':sid,'as_of_seq':seq,'messages':[],'new_event_counts':{},'changed':False}
    page=dsh_rpc.call('session/page',{'request':{'address':{'kind':'session','sessionId':sid},'throughSeq':seq,'maxMessages':12}})
    messages=[];counts={}
    for record in page.get('records',[]):
        event=record.get('event',{})
        if event.get('seq',0)<=after_seq:continue
        kind=event.get('type','unknown');counts[kind]=counts.get(kind,0)+1
        if kind not in ('user/message','assistant/message'):continue
        data=event.get('data',{})
        message=data.get('message',data if kind=='user/message' else {})
        if kind=='user/message' and message.get('source',{}).get('kind') not in ('user',None):continue
        parts=[x.get('text','') for x in message.get('content',[]) if x.get('type')=='text']
        if not parts:continue
        body='\n'.join(parts)
        body=re.sub(r'(?i)((?:api[_-]?key|authorization|cookie|bearer|secret)\s*[:=]\s*)\S+',r'\1[REDACTED]',body)
        messages.append({'seq':event.get('seq'),'role':message.get('role','user' if kind=='user/message' else 'assistant'),'request_id':message.get('source',{}).get('rpcId'),'text':body[-10000:],'truncated':len(body)>10000})
    return {'session_id':sid,'as_of_seq':seq,'messages':messages[-8:],'new_event_counts':counts,'changed':True,'window_may_be_partial':page.get('hasMore',False)}

@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False,destructiveHint=False,idempotentHint=True,openWorldHint=True))
def dsh_submit_task(session:str,task_path:str,request_id:str,mode:Literal['queue','steer']='queue')->dict:
    """Submit a concrete project dispatch document to an existing DSH session.

    A stable unique request_id prevents accidental resubmission. queue waits for
    the next turn; steer delivers at the next safe processing boundary. This
    returns admission, never a claim that implementation or research is complete.
    """
    sid=session_id(session)
    if not request_id.strip() or len(request_id)>120:raise ValueError('Provide a stable request_id of 1-120 characters')
    path=Path(task_path).resolve();dispatch_root=(ROOT/'dispatches').resolve()
    review_root=(ROOT/'reviews/20261016_dsh_chain_acceptance').resolve()
    review_targets={'next_tasks_session1.md':'session-d3685e0e-eb54-4d87-b80e-fed113e56152','next_tasks_session2.md':'session-11aa8c01-b58c-4d7a-9818-e04fda4348fd'}
    delegated=path.parent==review_root and review_targets.get(path.name)==sid
    if (not path.is_relative_to(dispatch_root) and not delegated) or path.suffix.lower()!='.md':raise ValueError('Task must be a project dispatch document or the explicitly delegated reviewer task for this destination')
    if delegated:path=review_root/path.name
    text=path.read_text(encoding='utf-8')
    if not text.strip() or len(text)>24000:raise ValueError('Task document must contain 1-24000 characters')
    digest=hashlib.sha256(text.encode()).hexdigest()
    with database() as conn:
        conn.execute('BEGIN IMMEDIATE')
        row=conn.execute('SELECT * FROM dispatches WHERE request_id=?',(request_id,)).fetchone()
        if row:
            if row['session_id']!=sid or row['sha256']!=digest or row['mode']!=mode:raise ValueError('request_id is already bound to different task content or destination')
            return {'request_id':request_id,'status':row['status'],'deduplicated':True,'accepted':row['status']=='accepted','execution_complete':False}
        if delegated and conn.execute("SELECT COUNT(*) FROM dispatches WHERE task_path=?",(str(path),)).fetchone()[0]>=3:raise ValueError('Delegated review limit reached; save remaining findings for Codex')
        conn.execute('INSERT INTO dispatches VALUES (?,?,?,?,?,?,?,?)',(request_id,sid,str(path),digest,mode,'sending','{}',time.time()))
    request={'requestId':request_id,'sessionId':sid,'mode':mode,'content':[{'type':'text','text':text}],'clientTimeZone':'Asia/Shanghai'}
    try:
        response=dsh_rpc.call('session/prompt',{'request':request},timeout=40)
    except Exception:
        # No automatic retry after ambiguous delivery; inspect the session/receipt.
        with database() as conn:conn.execute('UPDATE dispatches SET status=?,updated_at=? WHERE request_id=?',('delivery_unconfirmed',time.time(),request_id))
        raise
    accepted=isinstance(response,dict) and response.get('accepted') is True
    with database() as conn:conn.execute('UPDATE dispatches SET status=?,response=?,updated_at=? WHERE request_id=?',('accepted' if accepted else 'rejected',json.dumps(response,ensure_ascii=False),time.time(),request_id))
    return {'request_id':request_id,'session_id':sid,'task_path':str(path),'task_sha256':digest,'mode':mode,'accepted':accepted,'execution_complete':False}

@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True,destructiveHint=False,idempotentHint=True,openWorldHint=False))
def dsh_dispatch_receipt(request_id:str)->dict:
    """Read the admission receipt for a task; accepted does not mean finished."""
    with database() as conn:row=conn.execute('SELECT * FROM dispatches WHERE request_id=?',(request_id,)).fetchone()
    if row is None:raise ValueError('No such dispatch receipt')
    return {k:row[k] for k in ('request_id','session_id','task_path','sha256','mode','status','updated_at')}|{'execution_complete':False}

@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True,destructiveHint=False,idempotentHint=True,openWorldHint=False))
def dsh_research_progress()->dict:
    """Read local research run states. Returned completed values are producer claims requiring independent review."""
    base=ROOT/'deliveries/T005_research_pipeline/runs';runs=[]
    if base.is_dir():
        for folder in sorted(base.iterdir()):
            result=folder/'run_result.json'
            if not folder.is_dir() or not result.is_file():continue
            try:
                value=json.loads(result.read_text(encoding='utf-8'))
                runs.append({'run':folder.name,'reported_status':value.get('status'),'model':value.get('model_name'),'stages':value.get('stages_status'),'unresolved_count':len(value.get('errors_or_unresolved_claims',[])),'report_exists':bool(value.get('report_path') and Path(value['report_path']).is_file())})
            except (OSError,ValueError):runs.append({'run':folder.name,'read_state':'temporarily_unreadable'})
    ingest_path=ROOT/'deliveries/T004_download_zotero_bridge/ten_paper_real_ingest/ingest_result.json'
    ingest={'result_exists':ingest_path.is_file()}
    if ingest_path.is_file():
        try:
            value=json.loads(ingest_path.read_text(encoding='utf-8'))
            ingest.update({k:value.get(k) for k in ('status','ready_for_research','input_count','verified_count','pending_items','manifest_sha256')})
            manifest=ingest_path.parent/'papers_manifest.json'
            if manifest.is_file():
                raw=manifest.read_bytes();data=json.loads(raw.decode('utf-8'))
                ingest.update(manifest_export_count=len(data) if isinstance(data,list) else None,manifest_sha256_matches_report=hashlib.sha256(raw).hexdigest()==value.get('manifest_sha256'))
        except (OSError,ValueError):ingest['read_state']='temporarily_unreadable'
    return {'runs':runs,'reported_ingest':ingest,'independently_accepted':False}

if __name__=='__main__':mcp.run(transport='stdio')

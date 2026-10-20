"""Integration checks through real MCP; never sends a new DSH prompt."""
import asyncio,json,os,sys
from datetime import timedelta
from pathlib import Path
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
HERE=Path(__file__).resolve().parent

def payload(result):
    if result.structuredContent:return result.structuredContent
    return json.loads(result.content[0].text)

async def main():
    params=StdioServerParameters(command=sys.executable,args=['-B',str(HERE/'dsh_mcp.py')],env={**os.environ,'PYTHONUTF8':'1'})
    checks={}
    async with stdio_client(params) as (read,write):
        async with ClientSession(read,write,read_timeout_seconds=timedelta(seconds=90)) as client:
            init=await client.initialize()
            checks['real_mcp_initialization']=init.serverInfo.name=='dsh_control_mcp'
            listed=await client.list_tools()
            checks['tools_discovery']={'dsh_list_sessions','dsh_read_progress','dsh_submit_task','dsh_create_reviewer'}.issubset({t.name for t in listed.tools})
            value=payload(await client.call_tool('dsh_list_sessions',{}))
            sessions=value['sessions']
            checks['two_authorized_existing_sessions']={'session-d3685e0e-eb54-4d87-b80e-fed113e56152','session-11aa8c01-b58c-4d7a-9818-e04fda4348fd'}.issubset({x['session_id'] for x in sessions})
            checks['both_selected_gemini38']=all(x['model_selection']['next']['model']=='gemini-3.8-flash-high' for x in sessions)
            created=payload(await client.call_tool('dsh_create_reviewer',{}))
            checks['reviewer_creation_deduplicated']=created.get('deduplicated') is True and created.get('session_id') in {x['session_id'] for x in sessions}
            forbidden=await client.call_tool('dsh_get_session',{'session':'not-authorized'})
            checks['unrelated_session_blocked']=bool(forbidden.isError)
            sid='session-d3685e0e-eb54-4d87-b80e-fed113e56152'
            outside=await client.call_tool('dsh_submit_task',{'session':sid,'task_path':str(HERE/'README.md'),'request_id':'test-never-send-outside'})
            checks['outside_dispatch_path_blocked']=bool(outside.isError)
            wrong_review_target=await client.call_tool('dsh_submit_task',{'session':'session-11aa8c01-b58c-4d7a-9818-e04fda4348fd','task_path':str(HERE.parents[1]/'reviews/20261016_dsh_chain_acceptance/next_tasks_session1.md'),'request_id':'test-never-send-wrong-review-destination'})
            checks['reviewer_task_destination_restricted']=bool(wrong_review_target.isError)
            receipt=payload(await client.call_tool('dsh_dispatch_receipt',{'request_id':'codex-research-chain-ten-v1-session1'}))
            checks['real_dispatch_accepted_not_complete']=receipt['status']=='accepted' and receipt['execution_complete'] is False
            duplicate=payload(await client.call_tool('dsh_submit_task',{'session':sid,'task_path':receipt['task_path'],'request_id':receipt['request_id'],'mode':'queue'}))
            checks['duplicate_not_resubmitted']=duplicate.get('deduplicated') is True and duplicate['accepted'] is True
            wrong=await client.call_tool('dsh_submit_task',{'session':'session-11aa8c01-b58c-4d7a-9818-e04fda4348fd','task_path':receipt['task_path'],'request_id':receipt['request_id'],'mode':'queue'})
            checks['request_id_destination_binding']=bool(wrong.isError)
            info=next(x for x in sessions if x['session_id']==sid)
            progress=payload(await client.call_tool('dsh_read_progress',{'session':sid,'after_seq':info['as_of_seq']}))
            checks['cursor_progress_read']=progress['as_of_seq']>=info['as_of_seq']
    result={'protocol':init.protocolVersion,'checks':checks,'all_passed':all(checks.values()),'new_prompts_sent':0}
    (HERE/'state/protocol_test_results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0 if result['all_passed'] else 1

if __name__=='__main__':raise SystemExit(asyncio.run(main()))

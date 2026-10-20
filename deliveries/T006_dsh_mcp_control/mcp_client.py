"""Use the real MCP stdio protocol, not a direct Python call to server functions."""
import argparse,asyncio,json,os,sys
from datetime import timedelta
from pathlib import Path
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
HERE=Path(__file__).resolve().parent

async def main():
    parser=argparse.ArgumentParser();parser.add_argument('tool');parser.add_argument('--arguments',default='{}');parser.add_argument('--save');args=parser.parse_args()
    env={**os.environ,'PYTHONUTF8':'1','PYTHONDONTWRITEBYTECODE':'1'}
    params=StdioServerParameters(command=sys.executable,args=['-B',str(HERE/'dsh_mcp.py')],cwd=str(HERE),env=env)
    async with stdio_client(params) as (read,write):
        async with ClientSession(read,write,read_timeout_seconds=timedelta(seconds=90)) as session:
            init=await session.initialize()
            if args.tool=='tools/list':
                listed=await session.list_tools();value={'server':init.serverInfo.name,'protocol':init.protocolVersion,'tools':[t.name for t in listed.tools]}
            else:
                called=await session.call_tool(args.tool,arguments=json.loads(args.arguments))
                value={'server':init.serverInfo.name,'protocol':init.protocolVersion,'is_error':bool(called.isError),'result':called.structuredContent or [json.loads(x.text) if x.type=='text' and x.text.startswith('{') else {'type':x.type,'text':getattr(x,'text','')} for x in called.content]}
            rendered=json.dumps(value,ensure_ascii=False,indent=2)
            if args.save:
                target=Path(args.save).resolve()
                if not target.is_relative_to(HERE.parents[1]):raise ValueError('Save result only inside the research project')
                target.parent.mkdir(parents=True,exist_ok=True);target.write_text(rendered,encoding='utf-8')
            print(rendered)
            if value.get('is_error'):raise SystemExit(1)

if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8');asyncio.run(main())

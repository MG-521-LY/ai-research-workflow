"""Offline release checks. Do not read ignored local papers, state or credentials."""
from pathlib import Path
import ast,hashlib,json,re,sys
from release_files import public_files

ROOT=Path(__file__).resolve().parents[1]
errors=[];counts={'python':0,'json':0,'current_doc_links':0}
patterns={
 'github_token':re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})\b'),
 'private_key':re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
 'provider_key':re.compile(r'\b(?:sk-[A-Za-z0-9]{28,}|AIza[A-Za-z0-9_-]{30,})\b')}
try:files=public_files(ROOT)
except ValueError as e:
 print(json.dumps({'ok':False,'errors':[str(e)]}));sys.exit(1)
for p in files:
 rel=p.relative_to(ROOT).as_posix()
 if p.is_symlink() or not p.is_file():errors.append(rel+': not a regular release file');continue
 if p.suffix.lower() in {'.pdf','.sqlite','.sqlite3','.db','.pem','.key','.pyd','.exe'}:
  errors.append(rel+': forbidden binary or private data file');continue
 if p.name=='.env' or (p.name.startswith('.env.') and p.name!='.env.example') or 'state' in p.relative_to(ROOT).parts:
  errors.append(rel+': local environment/state must not be published');continue
 try:s=p.read_text(encoding='utf-8-sig')
 except UnicodeDecodeError:errors.append(rel+': unexpected non-UTF8 content');continue
 for label,rx in patterns.items():
  if rx.search(s):errors.append(rel+': possible '+label+' (value suppressed)')
 if p.suffix=='.py':
  try:ast.parse(s,filename=rel);counts['python']+=1
  except SyntaxError as e:errors.append(rel+': syntax line '+str(e.lineno))
 if p.suffix=='.json':
  try:json.loads(s);counts['json']+=1
  except ValueError:errors.append(rel+': invalid JSON')
docs=['README.md','SECURITY.md','NOTICE.md','CONTRIBUTING.md']
docs += [p.relative_to(ROOT).as_posix() for p in files if p.parent==ROOT/'docs' and p.suffix=='.md']
for name in docs:
 p=ROOT/name
 if not p.exists():errors.append(name+': missing');continue
 for target in re.findall(r'\]\(([^)]+)\)',p.read_text(encoding='utf-8')):
  if re.match(r'[A-Za-z]+:',target) or target.startswith('#'):continue
  target=target.split('#',1)[0]
  if target and not (p.parent/target).exists():errors.append(name+': missing linked file '+target)
  else:counts['current_doc_links']+=1
for name in ['workflow.py','InstSCI/pyproject.toml','InstSCI/instsci/data/institutional_identity_policy.json','InstSCI-topic-library/skills/instsci-topic-library/scripts/topic_library.py','deliveries/T005_research_pipeline/research_runner.py','deliveries/T004_download_zotero_bridge/pipeline_runner.py','deliveries/T006_dsh_mcp_control/mcp_client.py']:
 if not (ROOT/name).is_file():errors.append(name+': required entry missing')
manifest=ROOT/'source_manifest.json'
if not manifest.is_file():errors.append('source_manifest.json: missing')
else:
 data=json.loads(manifest.read_text(encoding='utf-8'))
 rows=data['files'];actual={r['path'] for r in rows}
 expected={p.relative_to(ROOT).as_posix() for p in files}-{'source_manifest.json'}
 if len(actual)!=len(rows):errors.append('source_manifest.json: duplicate path')
 for name in sorted(expected-actual):errors.append(name+': missing from release manifest')
 for name in sorted(actual-expected):errors.append(name+': stale or unexpected release manifest entry')
 if data.get('file_count_excluding_manifest')!=len(expected):errors.append('source_manifest.json: file count mismatch')
 for row in rows:
  if row['path'] not in expected:continue
  p=ROOT/row['path']
  if p.is_file() and not p.is_symlink():
   content=p.read_bytes()
   if hashlib.sha256(content).hexdigest()!=row['sha256'] or len(content)!=row['bytes']:errors.append(row['path']+': release manifest hash or size mismatch')
print(json.dumps({'ok':not errors,'checks':counts,'errors':errors,'external_operations':0},ensure_ascii=False,indent=2))
sys.exit(bool(errors))

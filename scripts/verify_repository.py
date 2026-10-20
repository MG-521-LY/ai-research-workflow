"""Offline release checks. No model, publisher, Zotero, DSH, or auth operations."""
from pathlib import Path
import ast,hashlib,json,re,sys

ROOT=Path(__file__).resolve().parents[1]
errors=[];counts={'python':0,'json':0,'current_doc_links':0}
patterns={
 'github_token':re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})\b'),
 'private_key':re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
 'provider_key':re.compile(r'\b(?:sk-[A-Za-z0-9]{28,}|AIza[A-Za-z0-9_-]{30,})\b')}
skip={'.git','.venv','venv','__pycache__','node_modules','.pytest_cache'}
for p in ROOT.rglob('*'):
 if not p.is_file() or any(x in skip for x in p.relative_to(ROOT).parts):continue
 rel=p.relative_to(ROOT).as_posix()
 if p.suffix.lower() in {'.pdf','.sqlite','.sqlite3','.db','.pem','.key','.pyd','.exe'}:errors.append(rel+': forbidden binary or private data file')
 if p.name=='.env' or 'state' in p.relative_to(ROOT).parts:errors.append(rel+': local environment/state must not be published')
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
for name in ['README.md','SECURITY.md','NOTICE.md','docs/INSTALLATION.md','docs/TUTORIAL.md','docs/ARCHITECTURE.md','docs/TROUBLESHOOTING.md','docs/VERIFICATION.md']:
 p=ROOT/name
 if not p.exists():errors.append(name+': missing');continue
 for target in re.findall(r'\]\(([^)]+)\)',p.read_text(encoding='utf-8')):
  if re.match(r'[A-Za-z]+:',target) or target.startswith('#'):continue
  target=target.split('#',1)[0]
  if target and not (p.parent/target).exists():errors.append(name+': missing linked file '+target)
  else:counts['current_doc_links']+=1
for name in ['InstSCI/pyproject.toml','InstSCI/instsci/data/institutional_identity_policy.json','InstSCI-topic-library/skills/instsci-topic-library/scripts/topic_library.py','deliveries/T005_research_pipeline/research_runner.py','deliveries/T004_download_zotero_bridge/pipeline_runner.py','deliveries/T006_dsh_mcp_control/mcp_client.py']:
 if not (ROOT/name).is_file():errors.append(name+': required entry missing')
manifest=ROOT/'source_manifest.json'
if manifest.exists():
 for row in json.loads(manifest.read_text(encoding='utf-8'))['files']:
  p=ROOT/row['path']
  if not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest()!=row['sha256']:errors.append(row['path']+': release manifest hash mismatch')
print(json.dumps({'ok':not errors,'checks':counts,'errors':errors,'external_operations':0},ensure_ascii=False,indent=2))
sys.exit(bool(errors))

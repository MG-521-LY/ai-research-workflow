"""Authenticated loopback access to the installed DSH session-controller API.

Uses the user's existing local browser-session grant in memory. No grant,
cookie, signing material, or model key is returned or persisted by this module.
The DSH archive and credential file are never modified.
Supports safe dynamic discovery of DSH loopback listening port after upgrade/restart,
strictly verifying process ownership against the canonical installed executable path before sending authorization tokens.
Differentiates idempotent read queries from mutating calls (delivery_unconfirmed on timeout).
"""
from __future__ import annotations
import base64, hashlib, hmac, json, os, subprocess, sys, time, uuid
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / 'deps'))
import yaml

SESSIONS = {}  # Explicitly authorize local sessions in state/managed_sessions.json.

managed_path = HERE / 'state/managed_sessions.json'
if managed_path.is_file():
    try:
        managed = json.loads(managed_path.read_text(encoding='utf-8'))
        SESSIONS.update({k: v for k, v in managed.items() if k.startswith('session-')})
    except Exception:
        pass

# Known mutating endpoints that MUST NOT be blindly retried upon transport uncertainty
MUTATING_ENDPOINTS = frozenset({'session/create', 'session/prompt', 'session/selectModel'})
READ_ONLY_ENDPOINTS = frozenset({'session/list', 'session/projections', 'session/page', 'session/modelCatalog'})

DEFAULT_DSH_EXECUTABLE = Path(r"E:\APP\deepseekHarness\DeepSeek Harness.exe")

def get_trusted_dsh_executable() -> Path:
    """Return canonical path to trusted DeepSeek Harness executable.
    Respects DSH_EXECUTABLE_PATH or DSH_EXE_PATH if set and points to an existing file.
    """
    env_path = os.environ.get("DSH_EXECUTABLE_PATH") or os.environ.get("DSH_EXE_PATH")
    if env_path:
        p = Path(env_path).resolve()
        if p.is_file():
            return p
    return DEFAULT_DSH_EXECUTABLE.resolve()

def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode('ascii')

def _authorization_cookie(authority: str | None = None) -> str:
    target_authority = authority or AUTHORITY
    if not target_authority:
        raise RuntimeError('Target authority not established')
    cred_path = Path.home() / '.dsh' / '.credentials.yaml'
    if not cred_path.is_file():
        raise RuntimeError('DSH credentials file not found')
    data = yaml.safe_load(cred_path.read_text(encoding='utf-8'))
    record = data.get('records', {}).get('client-connection/browser-session')
    if not isinstance(record, dict) or record.get('kind') != 'grant':
        raise RuntimeError('DSH existing browser-session grant unavailable; no authentication settings were changed')
    payload = record.get('payload', {})
    if payload.get('version') != 1:
        raise RuntimeError('Unsupported DSH local grant format')
    encoded = payload.get('secret', '')
    secret = base64.urlsafe_b64decode(encoded + '=' * ((4 - len(encoded) % 4) % 4))
    if len(secret) != 32:
        raise RuntimeError('Invalid DSH local grant length')
    now = int(time.time() * 1000)
    body = b64(json.dumps({'version': 1, 'authority': target_authority, 'issuedAt': now, 'expiresAt': now + 600000}, separators=(',', ':')).encode())
    value = 'v1.' + body + '.' + b64(hmac.new(secret, body.encode(), hashlib.sha256).digest())
    name = 'dsh-auth-' + b64(hashlib.sha256(target_authority.encode()).digest())
    return name + '=' + value

def _run_cmd_clean(cmd: list[str], timeout: int = 10) -> str:
    """Run a system command safely without triggering UnicodeDecodeError in _readerthread on non-UTF8 Windows consoles."""
    try:
        res = subprocess.run(cmd, capture_output=True, timeout=timeout)
        if res.returncode != 0:
            return ""
        for enc in ("utf-8", "gbk", "cp936", "latin1"):
            try:
                return res.stdout.decode(enc)
            except Exception:
                continue
        return res.stdout.decode("utf-8", errors="replace")
    except Exception:
        return ""

def _get_verified_dsh_pids() -> set[int]:
    """Retrieve process IDs strictly matching the verified canonical installation path.
    Rejects any unverified process name or path spoofing; fails explicitly if path cannot be verified.
    Zero fallback to bare image name or tasklist.
    """
    trusted_exe = str(get_trusted_dsh_executable())
    pids = set()
    try:
        cmd = r'''@(Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath } | Select-Object ProcessId, ExecutablePath) | ConvertTo-Json -Compress'''
        out = _run_cmd_clean(["powershell", "-NoProfile", "-Command", cmd], timeout=10).strip()
        if out:
            raw = json.loads(out)
            items = [raw] if isinstance(raw, dict) else list(raw)
            for item in items:
                if isinstance(item, dict):
                    exe_path = item.get("ExecutablePath")
                    pid = item.get("ProcessId")
                    if exe_path and isinstance(pid, int):
                        if os.path.normcase(os.path.abspath(exe_path)) == os.path.normcase(trusted_exe):
                            pids.add(pid)
    except Exception:
        pass

    if not pids:
        try:
            out = _run_cmd_clean(["wmic", "process", "where", "ExecutablePath is not null", "get", "ProcessId,ExecutablePath", "/format:csv"], timeout=10)
            if out:
                for line in out.splitlines():
                    parts = [p.strip() for p in line.split(",") if p.strip()]
                    if len(parts) >= 3 and parts[-1].isdigit():
                        exe_candidate = parts[1]
                        if os.path.normcase(os.path.abspath(exe_candidate)) == os.path.normcase(trusted_exe):
                            pids.add(int(parts[-1]))
        except Exception:
            pass

    return pids

def _is_port_owned_by_dsh(port: int, dsh_pids: set[int]) -> bool:
    """Verify that a specific loopback listening port is strictly owned by an identified DSH process."""
    if not dsh_pids:
        return False
    try:
        out = _run_cmd_clean(["netstat", "-ano", "-p", "tcp"], timeout=10)
        if out:
            for line in out.splitlines():
                parts = line.split()
                if len(parts) >= 5 and parts[0] == "TCP" and parts[3] == "LISTENING":
                    addr = parts[1]
                    pid_str = parts[4]
                    if addr == f"127.0.0.1:{port}" and pid_str.isdigit():
                        if int(pid_str) in dsh_pids:
                            return True
    except Exception:
        pass
    return False

def _probe_port_safely(port: int, dsh_pids: set[int]) -> bool:
    """Probe a candidate port ONLY AFTER confirming process ownership. Never send tokens to arbitrary ports."""
    if not (1 <= port <= 65535):
        return False
    if not _is_port_owned_by_dsh(port, dsh_pids):
        return False

    authority = f"127.0.0.1:{port}"
    base = f"http://127.0.0.1:{port}"
    rpc_id = str(uuid.uuid4())
    body = {'type': 'client-request', 'rpcId': rpc_id, 'method': 'session/list', 'payload': {'args': {'_request': {}}}}
    try:
        cookie = _authorization_cookie(authority)
        req = Request(
            base + '/api/session/list',
            data=json.dumps(body, ensure_ascii=False).encode('utf-8'),
            method='POST',
            headers={'content-type': 'application/json', 'Cookie': cookie}
        )
        with urlopen(req, timeout=2.0) as resp:
            data = json.loads(resp.read(4 * 1024 * 1024))
            if data.get('rpcId') != rpc_id:
                return False
            val = data.get('result', {})
            if isinstance(val, dict) and val.get('ok') is True:
                items = val.get('value', {}).get('items', [])
                if isinstance(items, list):
                    found_sids = {x.get('sessionId') for x in items if isinstance(x, dict)}
                    if any(sid in found_sids for sid in SESSIONS):
                        return True
    except Exception:
        return False
    return False

def discover_endpoint() -> tuple[str, str]:
    """Dynamically discover DSH loopback endpoint with strict process provenance verification.
    Never probe or transmit authorization credentials to arbitrary or unverified listening ports.
    """
    dsh_pids = _get_verified_dsh_pids()
    if not dsh_pids:
        raise RuntimeError("No running DeepSeek Harness processes found matching canonical executable path; cannot discover endpoint")

    env_port = os.environ.get("DSH_CONTROL_PORT") or os.environ.get("DSH_PORT")
    if env_port and env_port.isdigit():
        p = int(env_port)
        if _is_port_owned_by_dsh(p, dsh_pids):
            if _probe_port_safely(p, dsh_pids):
                return f"http://127.0.0.1:{p}", f"127.0.0.1:{p}"
            else:
                raise RuntimeError(f"Configured DSH port {p} is owned by DSH but did not respond to session/list protocol probe")
        else:
            raise RuntimeError(f"Configured DSH port {p} does not belong to a verified DeepSeek Harness process")

    candidate_ports: list[int] = []
    try:
        pid_list_str = ",".join(str(p) for p in dsh_pids)
        cmd = f'''@(Get-NetTCPConnection -State Listen | Where-Object {{ $_.OwningProcess -in @({pid_list_str}) -and $_.LocalAddress -eq "127.0.0.1" }} | Select-Object -ExpandProperty LocalPort | Sort-Object -Unique) | ConvertTo-Json -Compress'''
        out = _run_cmd_clean(["powershell", "-NoProfile", "-Command", cmd], timeout=10).strip()
        if out:
            ports = json.loads(out)
            candidate_ports = [ports] if isinstance(ports, int) else list(ports)
    except Exception:
        candidate_ports = []

    if not candidate_ports:
        try:
            out = _run_cmd_clean(["netstat", "-ano", "-p", "tcp"], timeout=10)
            if out:
                for line in out.splitlines():
                    parts = line.split()
                    if len(parts) >= 5 and parts[0] == "TCP" and parts[3] == "LISTENING":
                        addr = parts[1]
                        pid_str = parts[4]
                        if addr.startswith("127.0.0.1:") and pid_str.isdigit():
                            if int(pid_str) in dsh_pids:
                                candidate_ports.append(int(addr.split(":")[-1]))
        except Exception:
            pass

    verified = []
    for port in sorted(set(candidate_ports)):
        if _probe_port_safely(port, dsh_pids):
            verified.append(port)

    if len(verified) == 1:
        port = verified[0]
        return f"http://127.0.0.1:{port}", f"127.0.0.1:{port}"
    elif len(verified) == 0:
        raise RuntimeError("DSH loopback controller is unreachable or not responding with valid protocol; inspect before dispatch")
    else:
        raise RuntimeError(f"DSH loopback endpoint is ambiguous ({verified}); inspect before dispatch")

BASE: str | None = None
AUTHORITY: str | None = None

def ensure_endpoint(force_rediscover: bool = False) -> tuple[str, str]:
    global BASE, AUTHORITY
    if not force_rediscover and BASE and AUTHORITY:
        try:
            dsh_pids = _get_verified_dsh_pids()
            port = int(AUTHORITY.split(":")[-1])
            if _probe_port_safely(port, dsh_pids):
                return BASE, AUTHORITY
        except Exception:
            pass
    base, authority = discover_endpoint()
    BASE = base
    AUTHORITY = authority
    return BASE, AUTHORITY

try:
    ensure_endpoint()
except Exception:
    BASE = 'http://127.0.0.1:19387'
    AUTHORITY = '127.0.0.1:19387'

def call(endpoint: str, args: dict, timeout: int = 30):
    allowed = {'session/list', 'session/projections', 'session/page', 'session/prompt', 'session/modelCatalog', 'session/selectModel', 'session/create'}
    if endpoint not in allowed:
        raise ValueError('Endpoint not exposed by this bridge')
    ensure_endpoint()
    rpc_id = str(uuid.uuid4())
    body = {'type': 'client-request', 'rpcId': rpc_id, 'method': endpoint, 'payload': {'args': args}}
    req = Request(
        BASE + '/api/' + endpoint,
        data=json.dumps(body, ensure_ascii=False).encode('utf-8'),
        method='POST',
        headers={'content-type': 'application/json', 'Cookie': _authorization_cookie()}
    )
    
    is_mutating = endpoint in MUTATING_ENDPOINTS

    try:
        with urlopen(req, timeout=timeout) as response:
            result = json.loads(response.read(16 * 1024 * 1024))
    except HTTPError as exc:
        raise RuntimeError(f'DSH controller HTTP {exc.code}; no authentication was disabled') from None
    except URLError as exc:
        if is_mutating:
            raise RuntimeError(f'delivery_unconfirmed: mutating call {endpoint} failed with URLError ({exc.reason}); automatic resend inhibited to prevent duplicates') from None

        try:
            ensure_endpoint(force_rediscover=True)
            req = Request(
                BASE + '/api/' + endpoint,
                data=json.dumps(body, ensure_ascii=False).encode('utf-8'),
                method='POST',
                headers={'content-type': 'application/json', 'Cookie': _authorization_cookie()}
            )
            with urlopen(req, timeout=timeout) as response:
                result = json.loads(response.read(16 * 1024 * 1024))
        except Exception:
            raise RuntimeError('DSH loopback controller unavailable') from None

    if result.get('rpcId') != rpc_id:
        raise RuntimeError('DSH RPC response identity mismatch')
    value = result.get('result')
    if isinstance(value, dict) and value.get('ok') is False:
        error = value.get('error', {})
        raise RuntimeError('DSH RPC error: ' + str(error.get('code', 'unknown')) + ': ' + str(error.get('message', 'Request rejected'))[:450])
    if isinstance(value, dict) and value.get('ok') is True:
        return value.get('value')
    return value

def list_sessions():
    result = call('session/list', {'_request': {}})
    if not isinstance(result, dict):
        return {'unexpected_result_type': type(result).__name__}
    if not isinstance(result.get('items'), list):
        return {'unexpected_result_keys': list(result)}
    return {
        'sessions': [
            {k: x.get(k) for k in ('sessionId', 'cwd', 'running', 'updatedAt', 'agentAvailable', 'projections')}
            for x in result['items']
            if x.get('sessionId') in SESSIONS
        ],
        'has_more': bool(result.get('cursor') or result.get('nextCursor'))
    }

if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    value = list_sessions()
    for item in value.get('sessions', []):
        projection = item.pop('projections', {}) or {}
        values = projection.get('values', {})
        item.update(
            title=values.get('title'),
            model_selection=values.get('modelSelection'),
            inbox_counts={k: len(v) for k, v in values.get('inbox', {}).items()}
        )
    print(json.dumps(value, ensure_ascii=False, indent=2))

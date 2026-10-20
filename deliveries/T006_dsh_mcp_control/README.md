# DSH MCP 控制桥接（T006）

**状态**：升级后持久适配完成，支持动态回环端口安全自发现与恢复（2026-10-17）。  
**会话更新**：第三验收会话已接替升级为 `session-9769d0b9-17d3-4dd6-b2d7-f462e77b8387`（PTC preset + gemini-3.8-flash-high）；旧会话 `session-d92ffcb2-28b0-42ad-9fd7-19a2f6611b13` 历史完整保留，不再重发或调度。既有会话一、二、三均处于完全可控状态。

---

## 一、 系统架构与持久化适配

```text
Codex / 验收客户端
      │  (stdio MCP 协议握手，工具调用)
      ▼
dsh_mcp.py  (FastMCP stdio 服务)
      │  (会话白名单、防重台账、投递门禁)
      ▼
dsh_rpc.py  (安全动态回环发现、内存短时HMAC认证Cookie签发)
      │  (HTTP POST /api/session/*，零凭据泄露，仅限 127.0.0.1)
      ▼
DeepSeek Harness 本机 Controller (端口升级后动态变化，如 55751)
      ├── 会话一: session-d3685e0e-eb54-4d87-b80e-fed113e56152
      ├── 会话二: session-11aa8c01-b58c-4d7a-9818-e04fda4348fd
      └── 会话三(接替): session-9769d0b9-17d3-4dd6-b2d7-f462e77b8387
```

### 动态端口安全自发现机制（Zero Restart of DSH）
DSH 升级后后端端口动态随机绑定（原固定 19387 已废弃）。`dsh_rpc.py` 实现了多层容错且安全闭环的自动端点发现：
1. **环境变量覆盖（可选）**：检查 `$env:DSH_CONTROL_PORT` 或 `$env:DSH_PORT`（仅限 127.0.0.1 合法端口）；
2. **CIM 进程监听扫描**：通过 PowerShell CIM 查询 `DeepSeek Harness.exe` 进程并在 `127.0.0.1` 上监听的 TCP 端口；
3. **Netstat 回环兜底过滤**：若 CIM 权限受限，回退解析 `netstat -ano -p tcp` 中全部 `127.0.0.1:port` LISTENING 候选；
4. **认证协议真伪探针**：利用本机内存中读取的原版 browser-session grant 计算 HMAC 签名，向每个候选端点发送 `POST /api/session/list` 探针。必须验证：
   - RPC 返回包含匹配的 `rpcId`；
   - `result.ok == true`；
   - 返回 items 包含已授权的科研会话 ID。
5. **歧义与不可达严格熔断**：若发现 0 个有效端点，明确抛出 `RuntimeError('DSH loopback controller is unreachable...')`；若 >1 个，明确抛出歧义异常，坚决不盲投。
6. **自动断线重连**：当执行过程中遭遇 `URLError`（如 DSH 进程重载端口漂移），`call()` 自动触发单次安全重新发现并重试，无需重启 DSH。

---

## 二、 运行与恢复方法

### 1. 运行环境
使用已配置 MCP SDK 的独立环境：
- Python：`E:\AI\AI_reserch\InstSCI\.venv\Scripts\python.exe`
- 隔离依赖：`deliveries\T006_dsh_mcp_control\deps` (PyYAML 6.0.3)

### 2. 命令行快速查询
```powershell
# 1. 查询所有授权会话状态及模型
& "E:\AI\AI_reserch\InstSCI\.venv\Scripts\python.exe" -B deliveries\T006_dsh_mcp_control\dsh_rpc.py

# 2. 通过真实 MCP stdio 协议运行完整自动化回归测试（12 项检查）
& "E:\AI\AI_reserch\InstSCI\.venv\Scripts\python.exe" -B deliveries\T006_dsh_mcp_control\test_mcp_protocol.py
```

### 3. 故障恢复守则
- **DSH 重启/端口漂移**：无需任何手动改动，stdio 客户端下次调用时将自动完成回环自发现；
- **任务投递状态不明（`delivery_unconfirmed`）**：严格保持 SQLite 记录，不自动重投；先用 `dsh_read_progress` 检查目标会话的 `as_of_seq` 与消息尾部，核实真实接收后再行决定；
- **严禁扩权**：严格限定目标会话白名单，严格限定暴露 endpoint 集合，严格禁止关闭认证或持久化 Token。

---

## 三、 工具列表与权限门禁

| 工具 | 权限与用途 | 安全限制 |
| :--- | :--- | :--- |
| `dsh_list_sessions` | 只读：列出三个授权会话状态、模型与收件箱 | 仅限白名单内会话 |
| `dsh_get_session` | 只读：单个授权会话运行状态及序列号 | 仅限白名单内会话 |
| `dsh_read_progress` | 只读：带序列号游标的消息文本与事件增量 | 自动脱敏凭据，不暴露内部推理与工具参数 |
| `dsh_submit_task` | 写入：提交 Markdown 任务文档至会话收件箱 | 稳定 request_id 幂等防重，路径白名单限定 |
| `dsh_dispatch_receipt` | 只读：查询任务投递凭证（accepted ≠ finished） | 读取 SQLite 防重台账 |
| `dsh_research_progress` | 只读：读取 T005 本地 run_result 与 T004 ingest | 生产方状态声明，不代表独立验收 |
| `dsh_create_reviewer` | 幂等：接替会话去重记录 | 已完成接替会话记录，重复调用返回已有会话 |

---

## 四、 真实协议验证证据

`state/protocol_test_results.json` 记录了当前最新实测结果：
- `real_mcp_initialization`: `true`
- `tools_discovery`: `true`
- `two_authorized_existing_sessions`: `true`
- `both_selected_gemini38`: `true`
- `reviewer_creation_deduplicated`: `true`
- `unrelated_session_blocked`: `true`
- `outside_dispatch_path_blocked`: `true`
- `reviewer_task_destination_restricted`: `true`
- `real_dispatch_accepted_not_complete`: `true`
- `duplicate_not_resubmitted`: `true`
- `request_id_destination_binding`: `true`
- `cursor_progress_read`: `true`
- **综合判定**：`all_passed: true`，未发送任何新指令，零越权。

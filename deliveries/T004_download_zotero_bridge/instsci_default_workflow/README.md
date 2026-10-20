# InstSci 默认文献工作流集成与通用入口运行指南

## 一、 模块定位与职责边界

本模块位于 `deliveries/T004_download_zotero_bridge/instsci_default_workflow/`，作为 InstSci 主题文献采集、评审与本地 Zotero 确权入库的**统一生产入口**。

### 1. 核心职责
- **业务主入口编排（workflow_runner.py）**：提供 `run`、`sync`、`from-task`、`import-local`、`recover`、`status`、`export` 标准 CLI 子命令。
- **运行锁与互斥安全（mutex_lock.py）**：采用独占文件创建（`xb` 模式）与边界单次持锁、内部函数无锁复用架构，既保证跨进程严格排他，又彻底消除嵌套调用自锁。
- **本地 Zotero 薄适配器（thin_adapter.py）**：在无云端 API 凭证环境下，通过只读 SQLite 镜像深度核查（含 `deletedItems` 回收站过滤）与本地附件存储比对，实现 **100% 零重复 HTTP POST 与零冗余写入**。
- **统一不可变分析清单（manifest_builder.py）**：汇聚并严格核验本地有效 PDF 文件的格式与物理 SHA256，输出确定性下游分析清单 `unified_analysis_manifest.json`。

### 2. 真实必备环境与能力边界声明
- **环境要求**：Python 3.10+（环境解释器路径：`E:\AI\AI_reserch\InstSCI\.venv\Scripts\python.exe`）、本地已安装且配置好的 Zotero 客户端、已初始化的本地资料目录。
- **原生 InstSci 云端同步边界**：InstSci 原生 `execute_zotero_sync` 基于 `PyzoteroSyncBackend`，强制要求云端 `ZOTERO_LIBRARY_ID` 与 `ZOTERO_API_KEY`。在本地单机离线科研环境中，由 T004 薄适配器以只读 SQLite 快照核验安全承接，**不要求新云端账号、不上传全库、不硬编码云端密钥**。
- **出版商闭源下载边界**：本次课题扩充所需文献均为合法 Open Access 直链（Sciendo OA 与 arXiv OA）；闭源付费下载依赖可见环境下人工在场进行校园网统一认证（CARSI/WebVPN）与验证码识别，严禁伪造虚假浏览器运行日志。

---

## 二、 生产 CLI 业务命令速查指南（PowerShell 规范单行调用）

以下命令均使用现成环境完整路径直接执行，避免反斜线续行语法错误。

### 1. 从自然语言主题全流程运行（cmd_run）
```powershell
& "E:\AI\AI_reserch\InstSCI\.venv\Scripts\python.exe" "E:\AI\AI_reserch\deliveries\T004_download_zotero_bridge\instsci_default_workflow\workflow_runner.py" run --task "E:\AI\AI_reserch\deliveries\T004_download_zotero_bridge\instsci_chain_integration\upstream_topic_task" --output "E:\AI\AI_reserch\deliveries\T004_download_zotero_bridge\instsci_default_workflow\workspace\run_fulfilled" --topic "水下机器人推力分配与容错重构优化控制" --count 3 --manifest-out "E:\AI\AI_reserch\deliveries\T004_download_zotero_bridge\instsci_default_workflow\workspace\run_fulfilled\manifest.json"
```
- **运行契约**：初始化任务 -> 检索元数据 -> 检查评审状态 -> 若存在未评审条目停留在 `awaiting_agent_review` 状态交由宿主 Agent 在 `review_input.json` 中提供评审结果 -> 规划批次 -> 获取全文 -> 严格核验获取清单有效文件 -> 若实际已获数未满足 `--count` 目标，返回 `partial` 并退出码 1；满足目标时同步至 Zotero 并输出 `complete`。

### 2. 同步已有 InstSci 主题任务（cmd_sync）
```powershell
& "E:\AI\AI_reserch\InstSCI\.venv\Scripts\python.exe" "E:\AI\AI_reserch\deliveries\T004_download_zotero_bridge\instsci_default_workflow\workflow_runner.py" sync --target "E:\AI\AI_reserch\deliveries\T004_download_zotero_bridge\instsci_chain_integration\upstream_topic_task" --output "E:\AI\AI_reserch\deliveries\T004_download_zotero_bridge\instsci_default_workflow\workspace\sync_out" --manifest-out "E:\AI\AI_reserch\deliveries\T004_download_zotero_bridge\instsci_default_workflow\workspace\sync_out\manifest.json"
```
- **运行契约**：读取已完成获取的上游任务，核对本地 Zotero 真实条目与附件，生成规范分析清单。**同任务重跑实现增量复用，0 次重复网络请求**。

### 3. 本地已有文献记录 / 历史清单导入（cmd_import-local）
```powershell
& "E:\AI\AI_reserch\InstSCI\.venv\Scripts\python.exe" "E:\AI\AI_reserch\deliveries\T004_download_zotero_bridge\instsci_default_workflow\workflow_runner.py" import-local --records "E:\AI\AI_reserch\deliveries\T004_download_zotero_bridge\instsci_default_workflow\unified_analysis_manifest.json" --output "E:\AI\AI_reserch\deliveries\T004_download_zotero_bridge\instsci_default_workflow\workspace\import_out" --manifest-out "E:\AI\AI_reserch\deliveries\T004_download_zotero_bridge\instsci_default_workflow\workspace\import_out\manifest.json"
```
- **运行契约**：接收条目列表或具备 `items` 数组的清单对象，严格核实本地 PDF 物理文件与 SHA256，复用 Zotero 已有条目。

### 4. 故障中断检查与可信恢复（cmd_recover）
```powershell
& "E:\AI\AI_reserch\InstSCI\.venv\Scripts\python.exe" "E:\AI\AI_reserch\deliveries\T004_download_zotero_bridge\instsci_default_workflow\workflow_runner.py" recover --bridge-dir "E:\AI\AI_reserch\deliveries\T004_download_zotero_bridge\instsci_default_workflow\workspace\sync_out"
```
- **运行契约**：检查陈旧进程锁（自动清理死进程锁；活锁绝不抢占）；严格解析子批次 `batch_state.json` 与 `zotero_mapping.json`；若 mapping 文件损坏则报错并标记 `blocked`；若 Zotero 数据库中真实存在父子附件则安全回填台账。
- **强制解锁损坏锁**：`--force-unlock` 参数仅在确认锁文件被非 JSON 乱码损坏且持有进程已死时手动使用。

### 5. 查看当前任务与文库状态（cmd_status）
```powershell
& "E:\AI\AI_reserch\InstSCI\.venv\Scripts\python.exe" "E:\AI\AI_reserch\deliveries\T004_download_zotero_bridge\instsci_default_workflow\workflow_runner.py" status --dir "E:\AI\AI_reserch\deliveries\T004_download_zotero_bridge\instsci_default_workflow\workspace\sync_out"
```
- **运行契约**：输出运行锁持有状态、台账总条目数、已验证条目数及待验证条目数。

### 6. 导出不可变分析清单交付下游 PaperQA（cmd_export）
```powershell
& "E:\AI\AI_reserch\InstSCI\.venv\Scripts\python.exe" "E:\AI\AI_reserch\deliveries\T004_download_zotero_bridge\instsci_default_workflow\workflow_runner.py" export --bridge-dir "E:\AI\AI_reserch\deliveries\T004_download_zotero_bridge\instsci_default_workflow\workspace\sync_out" --output "E:\AI\AI_reserch\deliveries\T004_download_zotero_bridge\instsci_default_workflow\workspace\analysis_manifest.json"
```

---

## 三、 目标缺口与恢复真实使用说明

1. **目标缺口（Target Gaps）处理契约**：
   - 当调用 `run` 指定 `--count N` 时，工作流会依据上游实际已下载并通过格式/大小核验的唯一有效条目数进行对齐；
   - 若实际获取篇数少于目标需求（例如申请 5 篇但经筛选仅获取 3 篇），工作流将安全同步已获得的 3 篇合法子集至输出目录，同时在顶层明确返回状态 `partial`、退出码 `1` 并展示已获数与目标缺口（`Target count not fulfilled: requested 5, but only 3 verified paper(s) acquired`），严禁虚构满额。
2. **主题一致性保护（Topic Mismatch Protection）**：
   - 若传入的已有任务目录中已记录的主题与本次调用指定的 `--topic` 不一致，工作流拒绝静默覆盖已有任务，严格报错并退出（Exit Code: 1）。
3. **可信恢复真实契约**：
   - 遭遇非正常退出（如进程被杀或映射损坏），运行 `recover` 将对所有条目目录核验真实证据；损坏映射返回 `blocked` 阻止污染；合法实据安全回填台账。

---

## 四、 交付资产与校验指纹

| 文件名 | 相对路径 | 物理文件字节哈希 (File Bytes SHA256) |
| :--- | :--- | :--- |
| **工作流总运行器** | `workflow_runner.py` | `ca23bec05763d6ce6994ffb31be7a1a2d3710dcced1f07a69277a400c7e078cb` |
| **统一规范验证器** | `unified_verifier.py` | `1a1556335691130ac2a7ed76245114ca896fc7ae6203005615ab3a0cc80f71fd` |
| **本地薄适配器** | `thin_adapter.py` | `95f9f6d783684c3e96183b68555087de30eb044bc0c6763f123e9402b4f5620e` |
| **清单生成器** | `manifest_builder.py` | `67fa31464e1dafcdf299a8f74080edfacfe2352397bbff1e1432c3996b9d4bea` |
| **原子互斥锁** | `mutex_lock.py` | `c2e5dc2664e538f5ef5e8b78503fae9c10f8766df52294a2af515fcdaf9c8a39` |
| **13 篇统一分析清单** | `unified_analysis_manifest.json` | `def9f084378165155802dde4aa4a36613b6b413506dc916ecf6b5b257eff7260` |
| **实操业务证据集** | `business_evidence_run/business_evidence_summary.json` | 包含 10 个业务场景（含 cmd_run 满额、未满额、主题冲突及 sync/import-local/recover）实操日志 |
| **完整整改报告** | `使用与验证报告.md` | 单独存档，记录历次审计整改明细 |

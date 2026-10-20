# T004 批量下载与 Zotero 自动衔接 (入口与运行结果闭环交付版)

任务编号: T004-DOWNLOAD-ZOTERO-BRIDGE  
执行角色: DeepSeek Harness (dsh)  
交付版本: R5 机器结果与全出口强契约终审闭环版
日期: 2026-10-16（北京时间）  
主调度器 SHA256: `483a58fcc39bba811b7f669d9f50a2ea65617a3b0e5e18b403160167d762fd84`

---

## 1. 模块定位与核心入口

本模块为整条 AI 科学研究链条的**文献主入口**：
1. **向上衔接**：对接知网检索下载（通过 `cnki-topic-library` 增强副本）与本地已存 PDF 目录；
2. **知识库管理**：向 Zotero 知识库进行严格校验并入库，构建台账与防重入机制；
3. **向下协同**：生成双向兼容的 `papers_manifest.json`，并实际联动下游 **T005 通用学术研究流水线**（`T005_research_pipeline/research_runner.py`），流式跟踪任务执行并接收核验 `run_result.json` 状态，直通最终研究综合报告。

---

## 2. 核心改进落实 (依据整链 R3/R4 审核要求)

1. **真实题录来源处理**：
   - 彻底废除按文件名长短或无意义黑名单猜测正式 title 的逻辑；
   - 优先核验已知可靠映射 (Zotero 映射 / 同源 task 元数据) 与 PDF 元数据 (/Title)；
   - 无法证实题录时，文件名永远只作为候选 (`title_candidate`)，正式 `title` 保持为空 (`""`) 列入待补状态 (`TITLE_MISSING`)，记录来源置信度 (`metadata_confidence: low_candidate`)，不假充已核验题录，也不阻止其他有效文献继续。
2. **既有台账当前证据核查 (`verify_current_ledger_evidence`)**：
   - 检查物理文件存在性、完整大小与 SHA256 哈希；
   - 严格核查 Zotero 存储附件路径 (`zotero_stored_pdf_path`) 是否真实完好，工作副本存在绝不代替 Zotero 存储附件完好证明；
   - 检查附件存储路径与只读 SQLite 数据库快照父子归属；
   - 数据库被锁时记录 `db_locked_historical_retained`；文件损坏、存储附件缺失或归属无法证实如实降级为 `PENDING_VERIFY` 并从分析清单剔除。
3. **长任务执行与流式进展呈现**：
   - 采用多线程守护队列 (`threading.Thread` + `queue.Queue`) 与 `PYTHONUNBUFFERED=1` 实现真·无阻塞流式实时读取，终端逐行呈现下游执行进展，并完整沉淀至 `t005_execution.log`；
   - 支持长任务超时（默认 1800 秒）与现场状态保留。
4. **机器可读结果接收与严格身份/哈希一致性校验**：
   - 接收下游 `run_result.json` 与 `pipeline_state.json`；
   - **全面核查运行身份与必填字段**：当下游标为 `completed` 时，严格校验必填身份字段（`question`、`manifest_sha256`、`report_sha256`、`run_fingerprint`）；若字段缺失、清单哈希不吻合或产出报告内容哈希不匹配，严厉阻断并置退出码 5；
   - 准确传递 `completed` / `partial` / `failed` 状态与未完成阶段列表；下游异常退出时不标整链成功；
   - 将交接契约、运行身份、清单哈希及下游结果位置保存于 `t005_handoff_contract.json`。
5. **独立输出目录协同**：
   - 调度运行默认写入独立下游目录：`E:/AI/AI_reserch/deliveries/T005_research_pipeline/runs/chain_delivery_r3_from_t004/`。

---

## 3. 命令行调用说明

### 3.1 一站式整链运行命令 (`run-pipeline`)
```bash
# 基于已核验清单一键启动全链研究并协同下游（完成从入口到终稿报告闭环）
python pipeline_runner.py run-pipeline ^
  --output "E:/AI/AI_reserch/deliveries/T004_download_zotero_bridge/whole_chain_run" ^
  --question "生成式人工智能对大学生自主学习与批判性思维的影响机制、赋能边界与提升路径是什么？"

# 指定下游独立研究目录
python pipeline_runner.py run-pipeline ^
  --output "E:/AI/AI_reserch/deliveries/T004_download_zotero_bridge/whole_chain_run" ^
  --research-output "E:/AI/AI_reserch/deliveries/T005_research_pipeline/runs/chain_delivery_r3_from_t004" ^
  --question "生成式人工智能对大学生自主学习与批判性思维的影响机制、赋能边界与提升路径是什么？"
```

### 3.2 分阶段命令
```bash
# 递归扫描 PDF 目录生成清单（候选未核验题名留空待补）
python pipeline_runner.py scan-dir --input-dir <目录> --output <输出目录> --sample-role thrust_allocation_study

# 复核台账当前证据并导出分析清单
python pipeline_runner.py status --output "E:/AI/AI_reserch/deliveries/T004_download_zotero_bridge/whole_chain_run"

# 独立交接启动下游 T005
python pipeline_runner.py handoff-research ^
  --output "E:/AI/AI_reserch/deliveries/T004_download_zotero_bridge/whole_chain_run" ^
  --question "生成式人工智能对大学生自主学习与批判性思维的影响机制、赋能边界与提升路径是什么？"
```

---

## 4. 自动化测试与验证

```bash
python -B E:/AI/AI_reserch/reviews/T004_download_zotero_bridge/R2_independent_review/run_review.py
python -B E:/AI/AI_reserch/reviews/20261016_chain_r4_audit/check_persistence.py
python -B E:/AI/AI_reserch/reviews/20261016_t005_final_review/check_t005.py
```
- 20 组独立探针全部通过 (Exit code: 0)；
- 7 项端到端隔离测试全部通过 (Exit code: 0)；
- 上游 21 项单元测试全部通过 (Exit code: 0)；
- R4 结果落地与恢复复测通过 (Exit code: 0)；
- T005 终审复测通过 (Exit code: 0)。

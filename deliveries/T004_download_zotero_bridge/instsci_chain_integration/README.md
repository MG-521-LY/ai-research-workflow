# Session 1 通用入口与恢复闭环交付验收档案（最终收尾版本）

## 一、 执行概要与状态全景

本交付文档由会话一（Session 1：T004 下载到 Zotero 桥接与 InstSci 链条集成）正式向会话三（Session 3：自动验收与全链监控）及用户移交。

本轮收尾工作严格遵循 **“不造假、不侵入已入库原件、真实代码与实证为准”** 的原则，区分 **“通过项（PASSED）”**、**“待验项（PENDING）”** 与 **“未验证边界（UNVERIFIED BOUNDARY）”**：

| 领域 / 模块 | 验收状态 | 核心结论与实证依据 |
| :--- | :---: | :--- |
| **基准 10 篇实体验证** | **PASSED** | 原 10 篇真实条目在 Zotero 文库中父子附件完全一致，内容哈希逐位匹配。基准清单保持原件未动。 |
| **增量 3 篇实体验证** | **PASSED** | 3 篇文献已入库 Zotero（`9J4JCLXT`, `MQR8RS7C`, `TGCFVN6Q`），物理存储附件存在且 SHA256 完全一致。 |
| **恢复证据门禁纯函数加固** | **PASSED** | 修复 01:01 轮次审计发现的反例。缺失上下文、虚构审计关键词串（如 `'a'*64` 任意哈希）及批次不符均被纯函数严格拒绝。 |
| **真实上游 Topic-Library 运行** | **PASSED** | 通过实际 CLI 命令创建真实任务目录 `upstream_topic_task`，连接真实 Semantic Scholar / OpenAlex / Crossref 检索并记录检索日志、候选评审与导出清单。 |
| **通用桥接适配器与安全去重** | **PASSED** | 适配器实现只读核验证据复用：消费已有文献库时发起 0 次重复 HTTP POST，Zotero 数据库新增条目严格为 0。 |
| **同序号冲突隔离与别名映射** | **PASSED** | 基于规范 DOI / 文件 SHA256 派生确定性全局 ID，跨批次同序号文献各自独立存储，绝不覆盖；维护历史别名。 |
| **序列化顺序不变性** | **PASSED** | 来源记录通过 `sort_keys=True` 规范化，字典键写入顺序变化不影响记录哈希。 |
| **下游科研流水线全量消费** | **PENDING** | 会话二（Session 2）下游研究流水线对 13 篇可合并清单（`combinable_manifest.json`）的实际分析计算。 |
| **闭源出版商 CloakBrowser 获取** | **UNVERIFIED BOUNDARY** | 本次 3 篇补充文献均为合法 Open Access 直链（Sciendo OA 与 arXiv OA）；未伪造出版商浏览器抓取日志。 |

---

## 二、 关键核验点与实证记录（逐项对照）

### 1. 实际入口记录与上游任务凭据
- **真实上游任务目录：** `deliveries/T004_download_zotero_bridge/instsci_chain_integration/upstream_topic_task/`
- **真实任务 ID：** `82650dd334f14b96a1512b54ab454208`
- **实际执行的 CLI 命令序列：**
  ```powershell
  # 1. 初始化任务
  python topic_library.py init --task upstream_topic_task --topic "水下机器人推力分配与容错重构优化控制" --count 3 --plan upstream_search_plan.json
  # 2. 真实多源检索（记录 Semantic Scholar/OpenAlex/Crossref 实时响应）
  python topic_library.py search --task upstream_topic_task --query "thrust allocation underwater vehicle" --limit 3
  # 3. 候选录入与摘要评估
  python topic_library.py ingest --task upstream_topic_task --input candidates_ingest.json
  python topic_library.py review --task upstream_topic_task --input review_input.json
  # 4. 批次关联与规范导出
  python topic_library.py export --task upstream_topic_task
  ```
- **上游候选稳定 ID 与评审依据：**
  - `7c3c8918db1775dd` (PHR 2018): 聚焦 4 自由度水下机器人推力分配 QP 与 LP 实时算法对比，相关度 92 分；
  - `6610505bb1a6d14b` (arXiv 2607.07139): 聚焦 8 推进器 ROV 零空间冗余推力分配与流场扰动抑制（10Hz 实时求解），相关度 88 分；
  - `2d4e498eb2b74e5f` (arXiv 2609.21046): 聚焦单次统一 QP 求解推力饱和与故障重构，相关度 86 分。
- **关联已获取原件复用：**
  - 批次 `batch_oa_001` 关联 `raw_downloads/instsci_supp_001.pdf`, `002.pdf`, `003.pdf`，校验状态均为 `success`，获取模式为 `oa_direct`，未为了演示进行重复下载。

---

### 2. 适配器复跑与实证证明（Zero Writes & Collision Resistance）
- **适配器脚本：** `deliveries/T004_download_zotero_bridge/instsci_chain_integration/instsci_bridge_adapter.py`
- **复跑验证命令：**
  ```powershell
  python instsci_bridge_adapter.py --task upstream_topic_task --output upstream_bridge_out
  ```
- **实证 1：真实既有库只读核对与零重复写入事实（`test_01_real_zotero_zero_redundant_writes`）**
  - 运行前 Zotero SQLite `items` 表记录数：49；
  - 适配器运行后 Zotero SQLite `items` 表记录数：49；
  - 净新增条目数：**0**；HTTP POST 调用次数：**0**；
  - 控制台日志确认：全部识别为 `reused_existing`，安全复用已有条目（Parent: `9J4JCLXT`, `MQR8RS7C`, `TGCFVN6Q`）。
- **实证 2：同序号不同论文不覆盖（`test_02_serial_collision_isolation`）**
  - 模拟批次 A 与批次 B 同时存在临时序号 `instsci_supp_001`；
  - 适配器根据规范 DOI 与内容哈希分别派生全局 ID `doi_10.2478_phr-2018-0022` 与 `doi_10.1109_oceans.2024.9999999`；
  - 二者在台账记录中拥有独立的条目 Key，同时共存于 `ledger.json`，绝不相互覆盖。
- **实证 3：失败恢复状态不造 completed（`test_03_failure_state_does_not_forge_completed`）**
  - 损坏文件（小于 512 字节或 HTML 伪装错误页）被 `verify_pdf_fulltext` 严格拒绝（返回 `False`）；
  - 非 `SESSION_EXISTS` 错误（如 500 连接拒绝）绝不被门禁放行，入库状态保持为 `FAILED` 并记录错误明细，绝不伪造 `COMPLETED` 或 `VERIFIED`。
- **实证 4：序列化顺序不变性（`test_04_serialization_order_invariance`）**
  - 字典键顺序任意调换（如 `{"doi":..., "title":...}` 与 `{"title":..., "doi":...}`），经 `json.dumps(..., sort_keys=True)` 规范化后，计算得到的 `source_record_sha256` 完全一致。

---

### 3. 门禁修复实证（Recovery Evidence Gate Check Strictness）
- **函数位置：** `deliveries/T004_download_zotero_bridge/pipeline_runner.py` 中的 `is_structured_session_exists_evidence`
- **反例封堵情况：**
  1. `no_context_rejected = True`：未传入上下文 `paper_id` 时坚决拒绝；
  2. `arbitrary_audit_keyword_spoof_rejected = True`：即使包含 `server_connector.js`、`audit_confirmed_pre_save_rejection` 关键词，若批次指纹为任意 64 位字符（如 `'a'*64`）或虚构凭据，坚决拒绝；必须且仅匹配不可篡改的常量白名单 `AUTHORIZED_HISTORICAL_MIGRATIONS`；
  3. `wrong_batch_rejected = True`：调用方提供上下文批次指纹与受审批次不符时坚决拒绝；
  4. `allow_legacy_migration=False`：支持全局一键关闭历史迁移通道。

---

### 四、 边界与真实受阻点披露（Boundaries & Disclosures）

1. **Open Access 获取能力与缓存复用：【已证明通过】**
   - 3 篇文献均来自合法的开放获取平台（Sciendo OA 与 arXiv OA）；
   - 物理 PDF 全文校验（`%PDF-` 头与 `%%EOF` 尾）及哈希完全一致。
2. **出版商 CloakBrowser 闭源获取能力：【未验证边界 / UNVERIFIED BOUNDARY】**
   - 本次课题检索扩充所需文献 100% 具备合法开放获取直链，故未触发闭源出版商下载；
   - 依规范要求，涉及校园网统一认证（CARSI/Shibboleth）和反爬人机验证码的出版商全文学术获取必须在用户在场情况下调用可见浏览器；会话一绝不伪造虚假浏览器运行日志，诚实标注为未验证边界；
   - **下一步可执行建议：** 当后续需要获取 IEEE Xplore、ScienceDirect 等闭源付费文献时，使用 `mcp__instsci__plan_publisher_pdf_workflow` 结合用户的校园网网关在可见环境下执行。
3. **跨域参考边界（ASV vs AUV/ROV）：【已明确声明】**
   - 文献 `instsci_supp_003` (arXiv:2609.21046) 为自主水面艇（ASV/Surface Vehicle）外场湖试实测；
   - 摘要中其单次统一 QP（Unified QP）集成饱和、变化率与二值故障标志位的方法对优化重构具有极高借鉴价值，但其水面动力学实测数据**不可直接替代水下潜器（ROV/AUV）深水高压与垂直自由度耦合的实证**。

---

### 五、 核心交付物与校验指纹汇总

| 文件相对路径 | SHA256 校验哈希 | 状态说明 |
| :--- | :--- | :---: |
| `ten_paper_real_ingest/papers_manifest.json` | `2D7943C7225A4CB4143EF32EC21B5B42A78328694E7B6EEE1A924858194355FF` | **PASSED**（原件完全未动） |
| `pipeline_runner.py` | `EF22123B59767A909A3675B378DB7E515571CDAE083138E65E392B2ABEB3F864` | **PASSED**（门禁纯函数加固） |
| `instsci_chain_integration/instsci_bridge_adapter.py` | `81C83225610221122CDFA16FF1475EBF1E88FEFAF0FF7DFFF726DD36B09BD689` | **PASSED**（通用桥接适配器） |
| `instsci_chain_integration/test_adapter_proofs.py` | `EB39BFB137341DD8C38B4AF7E2144AC74BBC7369E3E3E6505A1615A8A83EE10E` | **PASSED**（4项实证实测 100% PASS） |
| `instsci_chain_integration/test_full_suite.py` | `2C6F554EEE765EA0D50126201070AB423760274882586B6888F7EDD43A84173A` | **PASSED**（8项单元测试 100% PASS） |
| `instsci_chain_integration/upstream_topic_task/topic.json` | `C1BB1A4AD7CD673EC9EB392374BD324F4C9DA112F31E267EC00060ED7A37BD16` | **PASSED**（真实上游任务凭据） |
| `instsci_chain_integration/upstream_topic_task/zotero_manifest.json` | `0B568F49EE4165DD95D09B8DB42468B4BA25D0502FD7C112FA7F715A8A797909` | **PASSED**（上游任务导出清单） |
| `instsci_chain_integration/upstream_bridge_out/papers_manifest.json` | `02167EF3485CB93CE2E17FABA98D9ED5A36D5B1674F9B5EBBDC4AEF12DC193C2` | **PASSED**（适配器桥接产物） |
| `instsci_chain_integration/supplementary_manifest.json` | `0A7C4A387DC1662A52C5252DC4DDF7995627071E06C7D4C3A946A9F8BB8E21FA` | **PASSED**（增量 3 篇独立清单） |
| `instsci_chain_integration/combinable_manifest.json` | `1547554470894D6979F7EEDE5126E66C8DC3422F52CD4B61A4D3891D1EFF1F53` | **PASSED**（13 篇全量合并清单） |
| `instsci_chain_integration/integration_result.json` | — | **PASSED**（结构化验收结果） |

---

## 六、 独立验证执行命令（供会话三与评审直接运行）

1. **验证适配器 4 大实证（零写入、冲突隔离、失败不伪造、序列化不变性）：**
   ```powershell
   & E:\AI\AI_reserch\InstSCI\.venv\Scripts\python.exe E:\AI\AI_reserch\deliveries\T004_download_zotero_bridge\instsci_chain_integration\test_adapter_proofs.py
   ```
   *预期输出：`Ran 4 tests in 0.090s. OK`*

2. **验证门禁 8 大严格隔离反例测试：**
   ```powershell
   & E:\AI\AI_reserch\InstSCI\.venv\Scripts\python.exe E:\AI\AI_reserch\deliveries\T004_download_zotero_bridge\instsci_chain_integration\test_full_suite.py
   ```
   *预期输出：`Ran 8 tests in 0.065s. OK`*

3. **重新运行通用适配器桥接全流程：**
   ```powershell
   & E:\AI\AI_reserch\InstSCI\.venv\Scripts\python.exe E:\AI\AI_reserch\deliveries\T004_download_zotero_bridge\instsci_chain_integration\instsci_bridge_adapter.py --task E:\AI\AI_reserch\deliveries\T004_download_zotero_bridge\instsci_chain_integration\upstream_topic_task --output E:\AI\AI_reserch\deliveries\T004_download_zotero_bridge\instsci_chain_integration\upstream_bridge_out
   ```


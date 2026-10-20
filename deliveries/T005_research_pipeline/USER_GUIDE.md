# T005 通用学术研究流水线用户持续使用指南 (Production User Guide)

2026-10-18 Codex 收尾校正：留出样例仍拒答、核心回归为 10 项；工程通过不等于所有主题必答或无需维护。原指南及原哈希保存在 `reviews/20261018_0600_codex_audit/`，本次只修改文档。

本指南面向科研人员与自动化批处理运维，提供基于 Windows 环境与 PowerShell 7 的端到端生产使用、新主题/新文献接入、凭据安全引用、输入预检拦截、断点恢复、增量计算、后端配置以及错误排查的完整实操手册。

---

## 1. 核心架构与支持边界

### 1.1 支持的功能与输入边界
- **输入支持**：
  - 本地 PDF 原始文件（只读原件，支持深层物理页码分块与 Tantivy 稀疏倒排索引构建）；
  - 标准 InstSci 统一结构化清单（JSON 格式，包含 `paper_id`、`title`、`authors`、`year`、`pdf_path`、`sha256`、`zotero_parent_key` 等元数据）；
  - 任意学术领域的自然语言研究问题（通过清单配置或 CLI `--question` 参数传入）。
- **流程支持（9 阶段有向无环图 DAG）**：
  1. `manifest_and_corpus`：清单规范化与 Tantivy 稀疏倒排索引构建（内建全缺失/空语料拦截门禁）；
  2. `retrieval_and_qa`：PaperQA2 原生异步上下文检索与受控问答；
  3. `single_paper_cards`：正文多级目录导航、通用分类补检与逐篇结构化方法卡提炼；
  4. `cross_paper_matrix`：跨文献多维对比矩阵与互补空白归纳；
  5. `hypotheses_generation`：严谨物理量纲与可证伪候选假设生成（含质量门禁拦截）；
  6. `external_search_critique`：arXiv / CrossRef / GitHub 动态同行评议与反例挑战；
  7. `verification_plan`：三阶段递进式学术验证实验方案书；
  8. `evidence_cross_check`：检索事实反偏与原文交叉核验（支持通用段落校准）；
  9. `synthesis_report`：学术综合报告与机读交付契约 (`run_result.json`) 生成。
- **不支持与明确边界（科学诚信底线）**：
  - **物理硬件实测**：本流水线输出严密的“前置学术研究与实验设计提案（PRE-EXPERIMENTAL_RIGOROUS_PROPOSAL）”，水池试验、嵌入式 MCU 硬件在环实测按设计严格后置为未来工作，工具链绝不伪造已完成的实测数据；
  - **全球首创性断言与网络故障区分**：严格区分外部检索传输状态、元数据获取、代码核验与审查结论。当外部网络接口（arXiv/CrossRef/GitHub）全部遭遇网络中断/超时时，流水线如实标定阶段状态为 `partial`，机读契约标定为 `PARTIAL` 与 `UNVERIFIED_DUE_TO_EXTERNAL_SEARCH_FAILURE`，退出码为 1。**严禁在网络全失败情况下妄断“已完成基线核对”或“未发现同构工程”**；
  - **跨域边界如实标注**：水面艇（ASV）算法不可直接冒充为水下潜器（ROV/AUV）实测，流体与高压环境差异由系统显式标定；
  - **拒答忠实表达**：当输入语料证据不足时，模型拒答（Refusal）作为诚实结果记录，系统如实级联为 `execution_status: FAILED` 与 `evidence_status: UNVERIFIED_DUE_TO_EXECUTION_FAILURE`，严禁伪造回答或假冒全流程成功；
  - **空语料早停防御**：当输入清单全部 PDF 文件缺失或入库有效语料为空时，阶段 1 立即触发 `FATAL_PRECHECK` 致命错误并返回退出码 1，严禁盲目向后执行昂贵大模型问答、无意义外部网络检索或无语料报告生成。

---

## 2. 环境准备与凭据引用配置 (Credential Reference)

### 2.1 Python 运行环境
使用已配置的 Python 3.12 虚拟环境：
```powershell
$PYTHON = "E:\AI\AI_reserch\deliveries\T003_paperqa_bridge\.venv\Scripts\python.exe"
```

### 2.2 大模型后端配置与真实凭据加载机制 (`configs/gemini_proxy_backend.yaml`)
系统采用安全的**凭据引用机制 (`api_key_ref`)**，绝不在配置文件或日志中硬编码真实 API Key，亦不使用虚假凭据：
```yaml
# Gemini 本地反代后端配置 (OpenAI 协议兼容)
provider: gemini_proxy
api_base: http://127.0.0.1:8317/v1
model: gemini-3.8-flash-high
api_key_ref: ANTIGRAVITY_LOCAL_API_KEY
temperature: 0.3
max_tokens: 4096
timeout: 60
embedding: sparse
```

#### 凭据加载器 (`get_credential_by_ref`) 的真实解析逻辑：
1. **优先读取系统环境变量**：系统首先尝试通过 `os.environ.get(ref_name)` 读取环境变量（例如 `$env:ANTIGRAVITY_LOCAL_API_KEY` 或 `$env:DEEPSEEK_API_KEY`）；
2. **回退读取本地安全凭据文件**：若环境变量未设置，系统自动定位并解析用户主目录下的安全凭据文件 `~/.dsh/.credentials.yaml`（提取 `refs[ref_name]` 对应密钥）；
3. **安全审计脱敏**：在 CLI 输出、中间件 JSON 与终稿报告中，所有凭据均被屏蔽，严防泄漏；
4. **后端与模型选择**：如需切换模型，请显式提供合法后端配置或指定 `--backend`，系统保持当前主力模型 `gemini-3.8-flash-high`，**严禁静默降级为其他低质模型**。

---

## 3. 标准操作指南 (Playbook)

### 3.1 运行既有代表性 5 篇基准任务 (同输入有效缓存续跑)
**条件说明**：仅在“代码实现 AST 指纹、提示词、后端配置模型与文献物理哈希完全一致”时，已完成且有效的阶段缓存可复用，退出码为 0：
```powershell
& "E:\AI\AI_reserch\deliveries\T003_paperqa_bridge\.venv\Scripts\python.exe" `
  "E:\AI\AI_reserch\deliveries\T005_research_pipeline\research_runner.py" `
  --manifest "E:\AI\AI_reserch\deliveries\T005_research_pipeline\configs\representative_instsci_manifest.json" `
  --output "E:\AI\AI_reserch\deliveries\T005_research_pipeline\runs\instsci_representative_5_run"
```

### 3.2 运行留出样本任务 (2 篇补充 arXiv 预印本：历史拒答与修复说明)
**背景说明**：
- 在 2026-10-18 01:00 早期运行中，该留出样本曾因输入语料证据不足，受控问答模型如实触发拒答（Refusal，返回 `I cannot answer.`），当时流水线真实产生 `status: "partial"`、`execution_status: "FAILED"`，现场完整保存在 `runs/held_out_supp_2_run_audit_0100_refusal_archive/`；
- 当前签收运行 `3868c71a8ce285a7` 仍是合法拒答：`raw_answer/model_answer` 为 `I cannot answer.`，`status: failed`、`execution_status: FAILED`、原生引用数 0，退出码为 1。此样例用于验证证据不足时的诚实失败与可恢复机制，不是完整成功样例；完整成功样例见 3.1。以下命令会复用适用缓存，不能预期强行得到成功回答：
```powershell
& "E:\AI\AI_reserch\deliveries\T003_paperqa_bridge\.venv\Scripts\python.exe" `
  "E:\AI\AI_reserch\deliveries\T005_research_pipeline\research_runner.py" `
  --manifest "E:\AI\AI_reserch\deliveries\T005_research_pipeline\configs\held_out_supp_manifest.json" `
  --output "E:\AI\AI_reserch\deliveries\T005_research_pipeline\runs\held_out_supp_2_run"
```

### 3.3 创建全新研究主题与新文献批次
用户无需编写代码，只需准备两项内容：
1. **新建清单文件**（如 `configs/my_new_study_manifest.json`）：
```json
{
  "schema": "instsci.unified_analysis_manifest.v1",
  "schema_version": "1.0.0",
  "topic": "您的研究主题名称",
  "question": "您想要流水线重点探究的核心理论问题或技术对比？",
  "items": [
    {
      "paper_id": "my_paper_001",
      "title": "论文标题",
      "authors": ["作者1", "作者2"],
      "year": "2025",
      "pdf_path": "C:\\path\\to\\your\\paper1.pdf",
      "sha256": "<文件SHA256>",
      "zotero_parent_key": "XXXXXXX",
      "status": "VERIFIED"
    }
  ]
}
```
2. **在 PowerShell 中启动生产执行**：
```powershell
& "E:\AI\AI_reserch\deliveries\T003_paperqa_bridge\.venv\Scripts\python.exe" `
  "E:\AI\AI_reserch\deliveries\T005_research_pipeline\research_runner.py" `
  --manifest "E:\AI\AI_reserch\deliveries\T005_research_pipeline\configs\my_new_study_manifest.json" `
  --output "E:\AI\AI_reserch\deliveries\T005_research_pipeline\runs\my_new_study_run" `
  --config "E:\AI\AI_reserch\deliveries\T005_research_pipeline\configs\gemini_proxy_backend.yaml"
```

### 3.4 动态覆盖研究问题 (无须修改清单)
通过 `--question` 参数直接注入新问题，流水线会自动检测问题哈希演进，保留昂贵的单篇方法卡，仅增量重算问答、对比与报告：
```powershell
& "E:\AI\AI_reserch\deliveries\T003_paperqa_bridge\.venv\Scripts\python.exe" `
  "E:\AI\AI_reserch\deliveries\T005_research_pipeline\research_runner.py" `
  --manifest "E:\AI\AI_reserch\deliveries\T005_research_pipeline\configs\my_new_study_manifest.json" `
  --output "E:\AI\AI_reserch\deliveries\T005_research_pipeline\runs\my_new_study_run" `
  --question "在推进器完全失效极端工况下，统一二次规划与分层自抗扰分配的动态切换稳定性如何证明？"
```

---

## 4. 退出码规范、输入预检与跨运行污染治理

### 4.1 CLI 退出码契约 (Exit Code Contract)
- **退出码 `0` (COMPLETED)**：全 9 个阶段均已成功执行完毕，产物完备落地，各阶段输入依赖哈希严格对齐，报告与契约处于完整核实状态；
- **退出码 `1` (PARTIAL / FAILED)**：流水线执行结束，但存在未完全成功阶段（如全部/部分输入 PDF 缺失导致空语料拦截、受控问答因证据不足拒答、阶段解析失败、网络中断超时或存在未消解证据缺口）；
- **退出码 `2` (CLI_ARG_ERROR)**：命令行参数错误（如缺失必要参数、指定未识别选项等）；
- **其他非零 (FATAL_CRASH)**：未捕获异常中断。

### 4.2 真实输入预检门禁与部分缺失源完整度治理
- **全部缺失/空语料拦截 (All Missing -> Early Fatal Rejection)**：
  若输入清单中所有候选 PDF 均不存在，或有效入库文献数为 0，阶段 1 立即触发致命错误停止，在 `run_result.json` 中记录 `stages_status.manifest_and_corpus: "failed"` 与 `execution_status: "FAILED"`，**严禁发起后续任何昂贵大模型问答、无意义外部网络检索或虚假报告渲染**，命令退出码严格为 `1`；
- **部分缺失隔离容错与对外诚实披露 (Partial Input Gaps -> Honest Partial Disclosure)**：
  若清单中存在部分文献有效、部分文献缺失（如未找到有效 PDF 文件或 SHA256/大小声明冲突）：
  1. **隔离记录到机读契约**：系统将缺失/冲突条目的 `paper_id`、排除原因、检查过的候选路径、统计数量完整持久化到统一契约的 `corpus_integrity` 对象中（包含 `total_input_items`、`valid_papers_loaded`、`source_conflicts` 详细列表），并将阶段 1 标定为 `partial`；
  2. **终稿报告显式披露**：综合报告在 **Section 1.1 (Corpus Integrity Audit)** 与 **Section 1.6 (Source Conflicts & Gaps)** 中逐项列明被排除的文献与原因，明确标注“下游分析仅基于已核实的有效文献展开，严禁冒充原清单全量核验”；
  3. **分层状态与退出码严密对齐**：因输入源不完整，机读契约与报告 Section 7.1 将状态定性为 `execution_status: "PARTIAL"`、`evidence_status: "VERIFIED_WITH_PARTIAL_SOURCE_GAPS"`、`research_maturity: "PROPOSAL_BASED_ON_PARTIAL_INPUT"`，**整体命令退出码严格为 `1`**；
  4. **补齐文件自动失效重算**：输入依赖哈希中对缺失条目显式标注 `FILE_MISSING_OR_INVALID`。一旦用户在原路径补齐有效 PDF，重新启动流水线将自动检测到输入依赖哈希变化，强制使阶段 1 及下游依赖失效重算，绝不被旧 completed 状态覆盖。

### 4.3 报告数据驱动与禁止跨运行污染原则
- **跨运行污染禁止原则**：
  新运行的综合报告（Section 1.4、Section 7 等）严禁出现**非当前语料特有而来自历史旧样本的代码残留文字**（例如在处理纯数学或水面艇任务时，绝对不应出现历史水下推力分配特定的缩写如 `LS-QP`、`SS-QP` 或特定的写死哈希 `sha_cdda352ad3c09f97` 等）；
- **当前合法语料豁免**：
  如果当前研究主题和文献**本身就属于水下推力分配领域**（例如 5 篇基准清单本身包含 `sha_cdda` 论文，且正文包含相应推力分配方法），则报告中合法讨论当前文献的方法属于正常学术归纳，**不受此限**。治理重点在于“严禁将旧任务文本写死在通用生成逻辑中”。

### 4.4 增量计算与缓存治理逻辑
| 触发场景 | 系统行为 | 内部治理逻辑 |
| :--- | :--- | :--- |
| **代码/模型/配置/输入哈希完全相同** | 有效阶段缓存复用 | 校验各阶段 `stage_input_hash` 与运行状态，直接返回现有工件，零 LLM 消耗 |
| **修改研究核心问题** | 检索问答、对比矩阵、假设、方案、报告失效重算；单篇方法卡保持复用 | 细粒度阶段依赖隔离，方法卡与文献内容强绑定，不受主题问题变更影响 |
| **修改单篇文献章节选区 / 修复解析** | 仅受影响的单篇卡重新提炼；其余文献卡片直接复用 | 逐篇卡三因子强绑定 (`sha256` + `plan_fingerprint` + `extractor_sig`) |
| **输入语料全部缺失或无效** | 阶段 1 立即中断，阻止后续所有调用 | 空语料门禁校验，退出码 1，契约标记 FAILED，绝不生成虚假假设 |
| **模型证据不足触发拒答 (Refusal)** | 问答状态标记为 `failed`，整链契约定性为 `UNVERIFIED_DUE_TO_EXECUTION_FAILURE` | 严禁静默吞并错误，机读契约与综合报告如实披露缺口，退出码 1 |
| **外部检索接口全网络故障 (Outage)** | 阶段 6 标记为 `partial`，契约定性为 `UNVERIFIED_DUE_TO_EXTERNAL_SEARCH_FAILURE` | 如实记录传输失败与待核验缺口，退出码 1；网络恢复后断点续跑自动复用上游并重算下阶，退出码 0 |

---

## 5. 结果检查与错误诊断

### 5.1 查看机读交付契约 (`run_result.json`)
每次运行完成后，首先检查输出目录下的 `run_result.json`：
- `execution_status`：
  - `COMPLETED`：必要阶段成功，仍需按报告审阅具体证据和待核实项（退出码 0）；
  - `PARTIAL`：包含来源缺口隔离、外部检索网络中断部分完成等未完全成功阶段（退出码 1）；
  - `FAILED`：阶段拦截（如空语料）、模型拒答、致命执行异常（退出码 1）。
- `evidence_status`：
  - `VERIFIED_WITH_EXPLICIT_BOUNDARIES`：证据已闭环且严格标定了未确认物理资源与前置假说边界；
  - `VERIFIED_WITH_PARTIAL_SOURCE_GAPS`：基于部分输入文献的证据，原清单存在排除缺口；
  - `UNVERIFIED_DUE_TO_EXTERNAL_SEARCH_FAILURE`：因外部网络中断/接口失败导致外部查重与基线核对无法闭环；
  - `UNVERIFIED_DUE_TO_EXECUTION_FAILURE`：因上游阶段失败导致未通过全部验证。
- `research_maturity`：
  - `PRE-EXPERIMENTAL_RIGOROUS_PROPOSAL`：最高成熟度前置提案；
  - `PROPOSAL_BASED_ON_PARTIAL_INPUT`：基于不完整输入文献的前置方案；
  - `INTERMEDIATE_HYPOTHESIS_PENDING_REVIEW`：中间阶段方案，外部查重待决；
  - `INCOMPLETE_DRAFT`：包含未决或失败阶段的草稿状态。

### 5.2 自动化全场景回归套件
随时可运行内置的 10 项核心场景回归测试（覆盖缓存命中、问题变更失效、审计纠偏失效、哈希损坏防御、失败分层级联、拒答退出码与真实驱动、空语料早停防御、部分缺失源完整度传播与补齐失效重算、以及全部外部网络故障与恢复断点续跑重算）：
```powershell
& "E:\AI\AI_reserch\deliveries\T003_paperqa_bridge\.venv\Scripts\python.exe" `
  "E:\AI\AI_reserch\deliveries\T005_research_pipeline\test_all_scenarios.py"
```
预期输出：`=== ALL 10 SCENARIOS FULLY PASSED ===`（退出码 0）。

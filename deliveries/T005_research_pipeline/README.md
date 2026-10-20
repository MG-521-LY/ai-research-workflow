# T005 通用学术研究流水线 (Universal Research Pipeline) - R3 交付版

- **任务编号**：T005-UNIVERSAL-RESEARCH-PIPELINE (Whole-Chain Research Delivery R3)
- **负责角色**：DeepSeek Harness (dsh 会话二)
- **交付目录**：`E:\AI\AI_reserch\deliveries\T005_research_pipeline\`
- **代码实现**：`research_runner.py` (版本: `3.0.0-universal`)
- **完成日期**：2026-10-16（北京时间）
- **核验结论**：**已彻底完成 R3 任务调度全部要求。实现随实际问题与假设动态构造检索、通用主张证据回查、纠偏结论直接闭环更新回正文消除内部矛盾、全要素身份绑定指纹（绑定清单文本哈希、5 篇源 PDF 实体 SHA256、代码实现自身 SHA256 与模型标识）、缺失损坏产物可靠恢复重跑机制、模型输出截断自动续写补全与有效空候选严格区分。采用会话一 T004 提供的同一份 5 篇测试资料清单（`papers_manifest.json`）在全新独立输出目录 `runs\chain_delivery_r3_session2` 连续完整跑通 9 大阶段，输出 78KB 终稿学术报告与机器可读契约 `run_result.json`。**

---

## 1. 全链路架构与 R3 关键技术攻克

本流水线专为学术文献自动化多篇深度研读、方法比较、假设发现、外部查重与方案设计构建，涵盖 9 大标准阶段：

```
[输入文献清单 (校验实际 PDF 物理哈希、标记 source_conflict)]
       ↓ (阶段 1: manifest_and_corpus)
[通用物理页与段落坐标解析 (pypdfium2) + 本地稀疏 BM25 索引 (Tantivy)]
       ↓ (阶段 2: retrieval_and_qa)
[自适应多尺度检索 + 真实大模型深度问答 (DeepSeek-Chat，支持自动续写补全防截断)]
       ↓ (阶段 3: single_paper_cards)
[全量逐篇方法卡抽取 (核心关切/方法学/理论假设/评价边界/自述与推断局限/复现条件)]
       ↓ (阶段 4: cross_paper_matrix)
[跨文献全量多维对比矩阵与范式聚类 (覆盖全部 5 篇，区分原文/自述/推断)]
       ↓ (阶段 5: hypotheses_generation)
[可否证候选假设提炼 (具备文献理论来源、差异、增量理由与波普尔反例失败判据)]
       ↓ (阶段 6: external_search_critique)
[随问题与假设动态构造检索 (arXiv / CrossRef / GitHub APIs) + Reviewer 2 严厉反例质疑]
       ↓ (阶段 7: verification_plan)
[严谨实证验证方案设计 (由实际问题与假设决定，彻底去除固定模板，纯方案设计)]
       ↓ (阶段 8: evidence_cross_check)
[核心主张证据与表格语义回查 (深度纠偏并闭环更新至问答成果，消除内部自相矛盾)]
       ↓ (阶段 9: synthesis_report)
[端到端万字学术研究终稿报告 + 机器可读结果契约 run_result.json 持续维护]
```

### R3 针对性核心升级清单：
1. **随实际问题与假设动态检索**：
   - 彻底移除了固定领域关键词与写死 GitHub 查询；
   - 根据输入的研究问题与提炼出的学术假设，动态提取并构造 arXiv、CrossRef、GitHub 检索短语；
   - 真实联网请求主流开放端点，获得实体记录并严格区分实施属性（同行评议、预印本、第三方开源实现）；合法零命中如实记录，绝不凭空宣称“全球首创”。
2. **通用证据回查与闭环纠偏（消除报告矛盾与未证实主张流转）**：
   - 通用定位问答中的精准文献物理页引用，对照真实原文切片（`cited_chunks_pool`）进行跨文献语义核查与主张溯源；
   - 严格区分【原文硬性统计事实】、【作者推论】与【AI推理或未核实主张】；
   - 任何未完全在原文切片中确认的主张均如实记录在 `unverified_or_insufficient_claims` 中，并严格闭环流转至 `run_result.json` 的 `errors_or_unresolved_claims` 字段，实现机器可读状态全链路同步；
   - **闭环同步**：将审核修正后的学术定论直接回写进问答成果与终稿报告正文，彻底消除“正文颠倒结论、文末追加纠偏”的自相矛盾。
3. **全要素运行身份强绑定与产物级联失效恢复**：
   - 运行指纹不仅绑定清单路径与问题，更强绑定**清单文件二进制 SHA256（`manifest_sha256`）、所有源 PDF 实体 SHA256、模型标识与代码实现自身 SHA256（`code_sha256`）**；
   - 健全产物检查与级联失效（Cascading Invalidation）：阶段虽标 `completed`，但若关键产物缺失、为空或反序列化失败，程序自动重置该阶段及下游所有依赖阶段为 `pending`，并清除旧失效产物重新计算，杜绝脏缓存复用；
   - 断点续跑与缓存复用时，严格核验证验契约，保留已有终稿报告及 `report_sha256`，杜绝误置为 running 导致下游调用中断。
4. **截断自动续写与解析异常隔离**：
   - 当大模型生成触及上限（`finish_reason == "length"`）时，根据上下文自适应触发断点续写补全（文本论述无缝衔接、JSON 结构严密闭合），确保学术论述与文献引用闭合；
   - 引入 `robust_json_parse`，严格区分“合法空列表 `[]`”与“JSON解析失败”，解析失败直接记录阶段错误并标记 `failed`，绝不吞咽成假完成。

---

## 2. 成果交付目录与产物清单

交付物统一存放在独立输出目录 `runs\chain_delivery_r3_session2\`，与会话一解耦隔离：

```
E:\AI\AI_reserch\deliveries\T005_research_pipeline\
│
├── research_runner.py                # 全链路通用主运行器 (版本 3.0.0-universal)
├── README.md                         # 交付说明与使用指南 (本文件)
├── 验证报告.md                       # 端到端实测验证与技术对照报告
│
└── runs/
    └── chain_delivery_r3_session2/   # 本次 R3 会话二真实独立交付成果
        ├── run_result.json           # 机器可读标准运行契约 (状态、指纹、PDF哈希、清单哈希、报告哈希、未核实主张流转)
        ├── pipeline_state.json       # 9个阶段完整完成状态与执行时间戳
        ├── normalized_manifest.json  # 5篇文献标准化清单 (含 source_conflict 校验)
        ├── chunks_mapping.json       # 34个物理页与段落坐标切片 (213KB)
        ├── sparse_index/             # 本地 Tantivy BM25 稀疏索引目录
        ├── retrieval_qa_result.json  # 模型受控问答成果 (15个片段命中，带物理页码引用与回查纠偏)
        ├── paper_method_cards.json   # 5篇逐篇方法/假设/评价/局限卡片 (33KB)
        ├── cross_paper_comparison.json # 5篇全量跨文献多维对比矩阵与范式聚类 (11KB)
        ├── candidate_hypotheses.json # 3项可否证学术创新假设
        ├── external_search_critique.json # 动态实时 arXiv/CrossRef/GitHub 检索与 Reviewer 2 质疑
        ├── verification_experiment_plan.json # 严谨准实验验证方案设计 (20KB)
        ├── evidence_cross_check.json # 通用核心主张证据回查与闭环纠偏审计记录 (6KB)
        └── RESEARCH_SYNTHESIS_REPORT.md # 78KB 终稿学术研究报告 (3.3万字，55处精准引用)
```

---

## 3. 机器可读结果契约 (`run_result.json`) 规范

每次运行在输出目录持续维护 `run_result.json`，供下游（会话一等）直接读取状态：

```json
{
  "status": "completed",
  "run_fingerprint": "4ab64cc24c3a47f8",
  "manifest_path": "E:\\AI\\AI_reserch\\deliveries\\T004_download_zotero_bridge\\whole_chain_run\\papers_manifest.json",
  "manifest_sha256": "2047cd1c06936ee1be81edf664df7f986fc7ca822f5499fbeb4f1c379a0a1871",
  "question": "生成式人工智能对大学生自主学习与批判性思维的影响机制、赋能边界与提升路径是什么？",
  "model_name": "deepseek-chat",
  "code_sha256": "87a8afd2fe294343cf2f2a017806e0d551f34afdfdaf6aafea5f11e33f807f60",
  "stages_status": {
    "manifest_and_corpus": "completed",
    "retrieval_and_qa": "completed",
    "single_paper_cards": "completed",
    "cross_paper_matrix": "completed",
    "hypotheses_generation": "completed",
    "external_search_critique": "completed",
    "verification_plan": "completed",
    "evidence_cross_check": "completed",
    "synthesis_report": "completed"
  },
  "errors_or_unresolved_claims": [
    "使用过GenAI工具对学生批判性思维标准化回归系数0.40（p<0.001）和自主学习能力0.38（p<0.01）——提供的原文切片中无此回归结果。"
  ],
  "report_path": "E:\\AI\\AI_reserch\\deliveries\\T005_research_pipeline\\runs\\chain_delivery_r3_session2\\RESEARCH_SYNTHESIS_REPORT.md",
  "report_sha256": "aaa93c3db522b0c0b6fa581f01c452ec108094b8126468ee4d7ed7a4fb4dbcbc"
}

### 1. 运行当前通用链路（默认启动）
```bash
# 激活虚拟环境
..\T003_paperqa_bridge\.venv\Scripts\Activate.ps1

# 执行研究流水线
python research_runner.py ^
  --manifest "E:\AI\AI_reserch\deliveries\T004_download_zotero_bridge\whole_chain_run\papers_manifest.json" ^
  --question "生成式人工智能对大学生自主学习与批判性思维的影响机制、赋能边界与提升路径是什么？" ^
  --output "E:\AI\AI_reserch\deliveries\T005_research_pipeline\runs\chain_delivery_r3_session2"
```

### 2. 50 篇水下航行器推力分配论文正式启动方案（即开即用）
用户准备好对应清单后，只需执行以下命令即可直接启动整链分析：
```bash
python research_runner.py ^
  --manifest "E:\AI\AI_reserch\inputs\50_thrust_allocation_manifest.json" ^
  --question "水下航行器推力分配优化、控制分配模型与执行器容错策略的核心方法与前沿进展" ^
  --output "E:\AI\AI_reserch\deliveries\T005_research_pipeline\runs\thrust_allocation_50_run"
```

---

## 5. 阶段边界声明
1. **测试文献角色**：本次运行采用 5 篇 workflow_test 文献验证通用研究整链；
2. **正式 50 篇未提前处理**：严格遵守指令，未擅自处理正式 50 篇论文；
3. **实验环境后置**：Optuna、MLflow 与物理实验台架保持后置，纯方案设计闭环，未占用额外物理资源。

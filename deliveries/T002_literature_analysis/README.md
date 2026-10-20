# T002 论文分析层交付与使用说明 (R4 终审完全达标版)

- **任务编号**：T002-LITERATURE-ANALYSIS (Revision 4 - Final Delivery)
- **负责角色**：DeepSeek Harness (dsh 会话二)
- **交付目录**：`E:\AI\AI_reserch\deliveries\T002_literature_analysis\`
- **修订日期**：2026-10-15（北京时间）
- **当前状态**：**已完全通过 Codex 全部 19 项独立负例探针（涵盖基准、截断、空证据、假引用、越界出处、短假文、等长假文、关键词欺骗、伪造引文、错误系数/标准误、错误问卷数、颠倒事实关系、显著性篡改、全表频次/人数核对、pending状态拦截、缺失回查拦截）；15/15 物理页归一化文本 100% 逐字吻合；原表行列数值与全部字段 100% 强核对；静态语义回查记录与答案指纹严格闭环**

---

## 1. 交付目录结构与产物明细

所有产物均位于 `E:\AI\AI_reserch\deliveries\T002_literature_analysis\`，完全独立于 T001 会话与项目根目录规划：

```
E:\AI\AI_reserch\deliveries\T002_literature_analysis\
│
├── papers_snapshot/              # 2篇样本文献干净工作快照（哈希严格一致）
│   ├── 92645125514cca64-生成式人工智能工具使用对高校学生批判性思维与自主学习能力的影响.pdf (8页, 2051417字节)
│   └── cdaf1e556efa7e8c-生成式人工智能如何赋能学生学习.pdf (7页, 242157字节)
│
├── parsed_text/                  # 15页逐页真实解析产物（无截断、无工具提示、与PDFium归一化100%一致）
│   ├── PAPER_92645125_parsed.json (完整8页全量解析, 19397字符)
│   ├── PAPER_92645125_parsed.md   (Markdown视图便于逐页核对)
│   ├── PAPER_cdaf1e55_parsed.json (完整7页全量解析, 16439字符)
│   └── PAPER_cdaf1e55_parsed.md
│
├── papers_manifest.json          # 样本文献处理清单（标记 workflow_test，记录哈希与页数规范）
├── evidence_records.json         # 4条从原文精确重建的事实证据（包含分段真实片段、全模型系数与标准误、显著性、9项SRL频次与人数）
├── research_cards.json           # 2份标准单篇研究卡片（覆盖11个通用核心维度，明确区分作者自述与AI推断，代码查阅范围完整记录）
├── cross_paper_comparison.json   # 跨论文对比表（包含共同维度对齐、可比性分析与机制推断边界，证据链严格闭环）
├── cross_paper_comparison.md     # 跨论文对比表 Markdown 视图
├── qa_citations_record.json      # 6项固定基准问答集及引用核查记录（含事实关系约束、全部6题人工/Agent回查状态与答案SHA256哈希强绑定）
├── qa_citations_record.md        # 问答及引用核查记录 Markdown 视图
├── analysis_runner.py            # 严谨自动化分析与核验执行脚本（具备全套防篡改、全表值核查与自愈能力）
├── README.md                     # 本交付与使用说明文档
└── 验证报告.md                   # T002 R4 详细正式核验报告
```

---

## 2. R4 终审收尾两项任务彻底落实情况

### 任务一：表值检查覆盖与报告完全一致（无遗漏核验）
- **四模型显著性 p 值全量核验**：
  - 模型一：`0.40*** (SE 0.06), p<0.001`
  - 模型二：`0.38** (SE 0.06), p<0.01`
  - 模型三：`0.47*** (SE 0.05), p<0.001`
  - 模型四：`0.45*** (SE 0.05), p<0.001`
  - 当恶意篡改模型二显著性为 `** p<0.90` 时，立即被程序拦截并退出 **exit_code = 5**（`incorrect_significance`）。
- **第二篇表 2 全量 9 项行为频次与人数字段核验**：
  - 学习计划（目标设定 17 次 / 3 人）
  - 学习策略（目标导向搜索 104 次 / 18 人、知识拓展 78 次 / 12 人、整合材料 17 次 / 5 人、资源获取 7 次 / 3 人）
  - 学习监测（学习判断 42 次、知识掌握感 28 次、内容评估 13 次、充分性评估 5 次）
  - 总 SRL 交互 319 次
  - 监测维度人数字段按原文实际报告如实标为 `null`，并在程序中准确核验已报告频次；当资源获取频次从 7 篡改为 99 时，立即被程序拦截并退出 **exit_code = 5**（`unchecked_resource_frequency`）。

### 任务二：真实读取并强核验语义审核状态（全部 6 题无死角）
- **无条件覆盖全部 6 题**：不再仅检查前 4 题，全部 6 题均严格核验 `semantic_audit`。
- **状态强约束**：
  - 每题必须包含 `semantic_audit` 对象（缺失直接退出 **exit_code = 6**，`missing_semantic_audit` 成功拦截）；
  - 每题 `audit_status` 必须明确为 `verified`（处于 `pending` 或其他未核验状态直接退出 **exit_code = 6**，`pending_semantic_status` 成功拦截）；
  - 每题必须包含有效答案指纹哈希（`answer_sha256`），且与当前答案实时内容计算的 SHA256 完全吻合；答案发生任何变动，旧状态立即失效并退出 **exit_code = 6**。
- **报告与日志真实标明**：
  - 程序日志真实标明：“结构、引用与事实关系通过 (已核验人工/Agent回查记录有效)”。
  - 明确区分：规则检查通过、已有人工/Agent核查记录有效、PaperQA2 模型问答未接通。

---

## 3. 全套 19 项独立负例探针实测对照表 (19/19 100% 达成)

在临时副本中完整测试 Codex 全部 19 项探针场景：

| 序号 | 探针测试场景名称 | 测试行为与篡改点 | 预期表现 | 实际退出码 | 判定结果 |
| :---: | :--- | :--- | :---: | :---: | :---: |
| 1 | **baseline** | 交付物完整未被改动 | 成功通过 | **0 (PASS)** | **通过** |
| 2 | **missing_pages** | 恶意截断解析至 6 页 | 严格拒绝 | **3 (REJECT)** | **成功拦截** |
| 3 | **empty_evidence** | 恶意清空证据记录 | 严格拒绝 | **5 (REJECT)** | **成功拦截** |
| 4 | **invalid_reference** | 引用不存在假证据 ID | 严格拒绝 | **6 (REJECT)** | **成功拦截** |
| 5 | **unrelated_answer** | 填入与教育无关假答案 | 严格拒绝 | **6 (REJECT)** | **成功拦截** |
| 6 | **page_999** | 证据物理页标为 P.999 | 严格拒绝 | **5 (REJECT)** | **成功拦截** |
| 7 | **short_fake_page** | 解析页替换为超短假文 | 严格拒绝 | **3 (REJECT)** | **成功拦截** |
| 8 | **no_parsed_output_rebuild** | 删除 parsed_text 目录 | 自动重建并成功通过 | **0 (PASS)** | **成功自愈** |
| 9 | **same_length_fake_page** | 替换为同长度全“伪”字假文 | 严格拒绝 | **3 (REJECT)** | **成功拦截** |
| 10 | **fake_answer_with_keyword** | 虚构电机答案末尾夹带“76” | 严格拒绝 | **6 (REJECT)** | **成功拦截** |
| 11 | **fabricated_quote** | 证据引用替换为虚构电机结论 | 严格拒绝 | **5 (REJECT)** | **成功拦截** |
| 12 | **incorrect_standard_error** | 模型一标准误改为 9.99 | 严格拒绝 | **5 (REJECT)** | **成功拦截** |
| 13 | **incorrect_claim** | claim 中问卷数改为 999999 | 严格拒绝 | **5 (REJECT)** | **成功拦截** |
| 14 | **model2_standard_error_wrong** | 模型二标准误改为 0.99 | 严格拒绝 | **5 (REJECT)** | **成功拦截** |
| 15 | **sample_counts_swapped_in_answer** | 交换答案中 2694 与 1781 | 严格拒绝 | **6 (REJECT)** | **成功拦截** |
| 16 | **incorrect_significance** (R4新增) | 模型二显著性改为 ** p<0.90 | 严格拒绝 | **5 (REJECT)** | **成功拦截** |
| 17 | **unchecked_resource_frequency** (R4新增) | 资源获取频次从 7 改为 99 | 严格拒绝 | **5 (REJECT)** | **成功拦截** |
| 18 | **pending_semantic_status** (R4新增) | Q02 审核状态改为 pending | 严格拒绝 | **6 (REJECT)** | **成功拦截** |
| 19 | **missing_semantic_audit** (R4新增) | 删除 Q02 semantic_audit | 严格拒绝 | **6 (REJECT)** | **成功拦截** |

---

## 4. 运行与验证指令

```bash
# 1. 运行完整严谨核验流程（核验快照哈希、15页逐字匹配、全表值与显著性比对、全6题回查状态与答案哈希验证）
python analysis_runner.py

# 2. 仅校验现有文件完整性与哈希一致性
python analysis_runner.py --verify-only

# 3. 执行固定基准问答集深度事实与语义审核记录核查
python analysis_runner.py --check-qa
```

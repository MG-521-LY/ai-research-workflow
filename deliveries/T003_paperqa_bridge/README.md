# T003 PaperQA2 接入层与离线衔接交付 (T003A 阶段 - R4 终审达标版)

- **任务编号**：T003-PAPERQA-BRIDGE (Phase A - Final Delivery)
- **负责角色**：DeepSeek Harness (dsh 会话二)
- **交付目录**：`E:\AI\AI_reserch\deliveries\T003_paperqa_bridge\`
- **完成日期**：2026-10-16（北京时间）
- **当前阶段状态**：**已彻底完成 R3 审核指出的唯一剩余收尾任务（从源 PDF 核对应有非空物理页覆盖）；已实现对删除非空页并同步伪造统计元数据的严格物理源推导拦截；通过交付目录内 31 项独立回归测试（31/31 100% 达成）；客观陈述阶段边界，保留全部已通过成果**

---

## 1. 交付目录结构与产物明细

所有文件均严格位于 `E:\AI\AI_reserch\deliveries\T003_paperqa_bridge\`，对 T001、T002、Zotero 及审查目录严格保持只读：

```
E:\AI\AI_reserch\deliveries\T003_paperqa_bridge\
│
├── .venv/                        # 专用虚拟环境 (Python 3.12.14, 已安装 paper-qa 2026.8.12, tantivy, litellm, pypdfium2)
├── inputs/
│   ├── papers/                   # 2篇样本文献干净工作副本 (SHA256及字节数严格校验)
│   │   ├── 92645125514cca64-生成式人工智能工具使用对高校学生批判性思维与自主学习能力的影响.pdf (8页, 2051417字节)
│   │   └── cdaf1e556efa7e8c-生成式人工智能如何赋能学生学习.pdf (7页, 242157字节)
│   └── reference_parsed/         # T002 真实逐页解析参照 (仅作坐标核对，不进入模型上下文)
│       ├── PAPER_92645125_parsed.json
│       └── PAPER_cdaf1e55_parsed.json
│
├── storage/
│   ├── chunks/                   # 物理页与段落切分映射 (chunks_mapping.json，含 mapping_fingerprint 深度绑定)
│   └── sparse_index/             # 本地 Tantivy 0.26.2 离线稀疏索引 (含 mapping_fingerprint.fp 版本绑定)
│
├── input_manifest.json           # 输入清单与元数据映射表 (包含 Zotero Item Key、附件目录、真实字节数及页码)
├── config_template.json          # 统一配置模板 (配置驱动，参数与执行严格一致，显式禁止外部网络模型计费)
├── capability_report.json        # 接口预检能力清单 (脱敏记录真实观察来源、字段存在性、协议及Python可用性，无推断过度声明)
├── requirements_locked.txt       # pip freeze 完整依赖锁定记录 (82个包及版本)
├── bridge_runner.py              # 核心运行器 (源PDF非空页覆盖强核验、全入口统一配置、带事实定位断言)
├── test_offline_bridge.py        # 交付目录内 31 项独立回归测试套件 (覆盖基准、非空页缺失拦截、篡改防御、配置生效等)
├── README.md                     # 本交付与使用说明文档
└── 验证报告.md                   # T003A 阶段正式核验报告
```

---

## 2. R4 终审收尾整改核心落实明细

审查组在 [R3复核结论与单项收尾.md](E:/AI/AI_reserch/reviews/T003_paperqa_bridge/R3复核结论与单项收尾.md) 中指出：指纹与配置故障已完全修复，唯一剩余项为**“从源 PDF 核对应有非空正文物理页的覆盖完整性”**。

### 1. 唯一剩余项落实：源 PDF 非空物理页覆盖核查
- **底层机制实现**：
  - 在 `bridge_runner.py` 的 `validate_input_corpus_common` 与 `offline-check` 中，直接遍历底层源 PDF 的每一页并提取真实文本；
  - 动态推导源 PDF 实际包含有效正文字符的所有非空物理页集合（`expected_nonempty_pages`，允许真实空白页合法无片段）；
  - 提取当前映射中该文献实际覆盖的物理页集合（`covered_pages`），断言：`missing_pages = expected_nonempty_pages - covered_pages` 必须为空集合；
  - 若存在非空物理页未被映射覆盖，立即拦截并退出 **exit_code = 2**（`EXIT_INTEGRITY_ERROR`）。
- **攻克恶意攻击场景**：
  - 当攻击者从映射中删除第一篇物理第 2 页（含 2197 字符正文），并同时修改 `chunks_count`、`summary.total_chunks` 与 `summary.total_characters` 试图伪装完整性时，程序不再盲信元数据，直接从源 PDF 检验出缺少物理第 2 页，报错并退出 **exit_code = 2**（`missing_nonempty_page` 成功拦截！）。

### 2. 阶段边界与客观声明
- **已完成事项 (T003A 阶段)**：
  1. 专用 Python 3.12.14 虚拟环境与依赖版本锁定（`paper-qa==2026.8.12`、`tantivy==0.26.2` 等）；
  2. 2 篇样本文献干净工作副本、哈希与大小校验、确定性逐页段落分块与 15 个片段的坐标映射；
  3. 从源 PDF 推导非空页覆盖完整性，防御非空正文页丢失；
  4. PaperQA2 官方公开受控接口实测（`Docs.aadd_texts` 与 `SparseEmbeddingModel`）以及样本检索事实断言；
  5. 真实配置来源脱敏核对记录；
  6. 本地第三方库离线环境变量门禁（零网络请求）；
  7. 交付目录内 31 项独立测试套件全量通过。
- **未完成事项 (留待后续阶段)**：
  1. **T003B 真实端到端模型问答**：目前处于“待明确现有服务调用方式及范围”状态，未发生模型实际计费请求；
  2. **远程 Dense 向量模型**：未发现配置线索，保持待核实，未配置外部嵌入服务；
  3. **未处理 50 篇推力分配论文**：本阶段严格锁定在 2 篇教育学样本文献进行工具链与接入层核验。

---

## 3. 31 项独立回归测试实测对照表 (31/31 100% 达成)

执行 `python test_offline_bridge.py`：

| 序号 | 场景名称 | 测试行为与篡改点 | 预期退出码 | 实际退出码 | 判定结果 |
| :---: | :--- | :--- | :---: | :---: | :---: |
| 1 | **baseline_fresh** | 全新初始状态执行全流程 | 0 (PASS) | **0** | **通过** |
| 2 | **repeat_full_pipeline** | 重复执行全流程 (指纹安全复用) | 0 (PASS) | **0** | **通过** |
| 3 | **missing_pdf_prepare** | prepare 缺失输入 PDF 文件 | 2 (FAIL) | **2** | **成功拦截** |
| 4 | **missing_pdf_standalone_check** | 单独 offline-check 缺失 PDF | 2 (FAIL) | **2** | **成功拦截** |
| 5 | **corrupt_pdf_prepare** | prepare 输入 PDF 注入脏字节 | 2 (FAIL) | **2** | **成功拦截** |
| 6 | **corrupt_pdf_standalone_check** | 单独 offline-check PDF 损坏 | 2 (FAIL) | **2** | **成功拦截** |
| 7 | **wrong_recorded_size_prepare** | prepare 清单字节数声明为 1 | 2 (FAIL) | **2** | **成功拦截** |
| 8 | **wrong_recorded_size_standalone_check** | 单独 offline-check 字节数不符 | 2 (FAIL) | **2** | **成功拦截** |
| 9 | **empty_manifest_prepare** | prepare 输入清单置空为 [] | 1 (FAIL) | **1** | **成功拦截** |
| 10 | **empty_manifest_offline_check** | 单独 offline-check 输入清单置空 | 1 (FAIL) | **1** | **成功拦截** |
| 11 | **restricted_corpus_standalone_check** | 清单削减为仅第 1 篇执行离线检查 | 0 (PASS) | **0** | **通过 (无残留)** |
| 12 | **wrong_pid_check** | 映射片段 paper_id 篡改为假 ID | 2 (FAIL) | **2** | **成功拦截** |
| 13 | **wrong_page_check** | 映射片段 physical_page 篡改为 99 | 2 (FAIL) | **2** | **成功拦截** |
| 14 | **wrong_offset_check** | 映射片段偏移篡改为超大越界值 | 2 (FAIL) | **2** | **成功拦截** |
| 15 | **wrong_sha_check** | 映射片段 pdf_sha256 篡改为全 0 | 2 (FAIL) | **2** | **成功拦截** |
| 16 | **wrong_content_check** | 映射片段追加虚构文本 | 2 (FAIL) | **2** | **成功拦截** |
| 17 | **missing_config_preflight** | preflight 缺少配置文件 | 1 (FAIL) | **1** | **成功拦截** |
| 18 | **missing_config_standalone_check** | 单独 offline-check 缺少配置文件 | 1 (FAIL) | **1** | **成功拦截** |
| 19 | **unsafe_config_preflight** | preflight 网络开关改为 true | 3 (FAIL) | **3** | **成功拦截** |
| 20 | **unsafe_config_standalone_check** | 单独 offline-check 网络开关为 true | 3 (FAIL) | **3** | **成功拦截** |
| 21 | **custom_config_preflight** | 自定义配置文件传入并成功生效 | 0 (PASS) | **0** | **通过** |
| 22 | **prefix_unsafe_config** | 前置 --config unsafe.json 参数门禁 | 3 (FAIL) | **3** | **成功拦截** |
| 23 | **suffix_unsafe_config** | 后置 --config unsafe.json 参数门禁 | 3 (FAIL) | **3** | **成功拦截** |
| 24 | **binding_raw_id** | 片段 raw_id 篡改为 OTHER_SOURCE | 2 (FAIL) | **2** | **成功拦截** |
| 25 | **binding_zotero_key** | 片段 zotero_item_key 改为 WRONGKEY | 2 (FAIL) | **2** | **成功拦截** |
| 26 | **binding_dropped_document** | 映射删去第二篇文献致集合不相等 | 2 (FAIL) | **2** | **成功拦截** |
| 27 | **binding_incorrect_count** | chunks_count 篡改为 9999 | 2 (FAIL) | **2** | **成功拦截** |
| 28 | **binding_valid_slice_stale_index** | 合法微调切片后自动触发索引重建 | 0 (PASS) | **0** | **通过并同步** |
| 29 | **valid_slice_index_synced** | 校验新索引内容确已同步为新文本 | 0 (PASS) | **0** | **同步核验吻合** |
| 30 | **missing_nonempty_page** (R4新增) | 删去物理第2页并同步篡改统计元数据 | 2 (FAIL) | **2** | **成功拦截** |
| 31 | **duplicate_chunk_id** (R4新增) | 出现重复 chunk_id 异常 | 2 (FAIL) | **2** | **成功拦截** |

---

## 4. 运行与验证指令

```bash
# 激活独立虚拟环境
.\.venv\Scripts\Activate.ps1

# 1. 运行核心预检与全量离线流水线
python bridge_runner.py

# 2. 支持前置与后置配置参数形式
python bridge_runner.py --config config_template.json offline-check
python bridge_runner.py offline-check --config config_template.json

# 3. 运行交付目录内 31 项完整回归测试套件
python test_offline_bridge.py
```

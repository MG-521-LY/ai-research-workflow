# T001 文献入库交付使用说明 (R1 修订版)

## 1. 任务概述与当前状态

- **任务编号**：T001-ZOTERO-INGEST (Revision: R1)
- **执行角色**：DeepSeek Harness (dsh)
- **完成日期**：2026-10-15（北京时间）
- **交付状态**：
  - **当前5篇题录与附件实际入库状态**：已完整入库并核验通过（保留原有 5 条题录 Key 及存储附件，15/15 哈希一致）。
  - **自动化执行程序与健壮性状态**：R1 四项返修要求已全部修订并完成隔离测试（映射持久化与去重、工作副本完整性检验与安全重建、退出码传递、分类/标签对象语义区分）。

---

## 2. 交付目录结构

交付目录位于：`E:\AI\AI_reserch\deliveries\T001_zotero_ingest\`

- `ingest_runner.py`：核心执行脚本（R1 修订版，支持材料生成、哈希核验、工作副本重建、导入映射原子化持久化、严谨去重及精准退出码）。
- `standardized_items.json`：5 篇文献结构化元数据（含清洗后的作者列表、可追溯 RawID 与 SHA256）。
- `import_materials.ris`：符合 Zotero 原生导入规范的 RIS 交换格式（含独立 AU 标签、工作副本绝对路径、Extra 追溯字段）。
- `import_materials.bib`：标准的 BibTeX 交换格式。
- `papers_work_copy/`：PDF 附件工作副本目录（原始历史目录与文件完好保留未改动）。
- `zotero_mapping.json`：原始采集记录、PDF 校验值与 Zotero 内部条目 Key/附件存储路径的完整映射表。
- `验证报告.md`：包含 R1 审查意见逐项响应、隔离测试数据及正式结论的核验报告。

---

## 3. 程序执行方式与参数说明

本工具使用 Python 3 编写，无需额外第三方外部依赖（仅使用标准库）：

```bash
# 1. 仅生成入库材料与核验工作副本（不向 Zotero 写入）
python ingest_runner.py --prepare-only

# 2. 正常运行（自动完成材料准备、工作副本校验、已有映射与哈希去重检测后导入）
python ingest_runner.py

# 3. 强制重新导入（忽略已有映射文件，强制重新提交）
python ingest_runner.py --force

# 4. 仅执行导入步骤（要求目录下已存在 standardized_items.json 和 import_materials.ris）
python ingest_runner.py --import-only
```

### 退出码说明
- `0`：执行成功，或检测到当前批次全量记录已成功入库且哈希匹配而安全跳过 (`EXIT_SUCCESS` / `EXIT_ALREADY_COMPLETED`)。
- `1`：输入错误，如原始 CSV 清单或必要导入文件不存在、格式损坏 (`EXIT_INPUT_ERROR`)。
- `2`：完整性错误，如原始 PDF 文件缺失或哈希校验失败 (`EXIT_INTEGRITY_ERROR`)。
- `3`：网络或接口连接错误，如 Zotero 监听端口未开放、网络不可达 (`EXIT_NETWORK_ERROR`)。
- `4`：接口响应错误，如 HTTP 500、返回非法非 JSON 格式、状态码非 201 (`EXIT_RESPONSE_ERROR`)。
- `5`：状态持久化或部分返回错误，如条目部分返回、映射构建异常 (`EXIT_STATE_ERROR`)。
- `6`：附件核验错误，如条目缺少附件或附件父子挂接不匹配 (`EXIT_ATTACHMENT_VERIFY_ERROR`)。
- `7`：未决/待核对状态安全阻断，如上一次处于提交中、部分返回或附件未核验 (`EXIT_PENDING_BLOCKED`)。
- `8`：完成状态与映射损坏/缺失冲突阻断，检测到批次曾标记 COMPLETED 但映射不可用，强制阻断以防重复导入 (`EXIT_CONFLICT_BLOCKED`)。

---

## 4. R1 返修项关键机制说明

1. **R1-1 映射原子化持久化与去重校验**：
   - 首次在空目录下运行并成功导入后，程序自动从接口响应中匹配条目、检索本地 storage 附件目录，核对 SHA256 后通过原子替换（`tempfile` + `os.replace`）写入 `zotero_mapping.json`。
   - 再次运行时，去重逻辑不仅检查映射文件是否存在，更逐项校验当前输入与映射中的 RawID、题名、SHA256、Zotero Key 及 `hash_verified` 标记；只有完全一致才判定为已完成并拦截网络写入。
2. **R1-2 工作副本完整性校验与安全重建**：
   - 在准备导入材料时，脚本对 `papers_work_copy/` 中的现有文件进行字节数与 SHA256 校验。
   - 若副本缺失或内容损坏（如大小不符、哈希不匹配），脚本会自动报警并从原始下载目录安全重新拷贝覆盖损坏副本，绝不修改原始文件。
3. **R1-3 错误状态码传递**：
   - 所有异常退出点均显式调用 `sys.exit(CODE)`，错误信息输出至 `stderr`，上游自动化管道可准确感知执行成败。
4. **R1-4 专用测试分类与标签语义**：
   - 题录均打上专用测试标签 `AI科研链条_入库验证_T001` 与 `extra` 追溯信息。
   - 在 Zotero 桌面端，用户可随时在左侧导航栏新建分类（Collection），将带标签的条目拖入该测试分类中进行归类。

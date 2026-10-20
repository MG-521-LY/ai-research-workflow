# cnki-topic-library 增强副本与适配说明

## 1. 来源与哈希记录
- **原始技能路径**：`C:\Users\lamost\.codex\skills\cnki-topic-library`
- **原始 library_jobs.py SHA256**：`07d65b8519c3f4f8ba95da0c783a3dbac18c7dd39e41923c876671db3eaca0f6`
- **原始 T001 ingest_runner.py SHA256**：`2ec661d9390aaed5d97f449c9c7f815efe139acbeac5a1b341690b7ca05275e6`

## 2. 增强改动说明
1. **下载后自动衔接**：新增 `pipeline_runner.py record-download` 包装入口，在下载完成并通过原版校验后，无缝进入 Zotero 入库队列。
2. **已有任务批量同步**：提供 `sync-downloads`，可扫描已有下载任务目录中的 `manifest.json`，批量恢复待入库队列。
3. **独立状态与台账**：将采集状态（`downloaded`）与入库状态（`VERIFIED`, `QUEUED`, `TITLE_MISSING`, `PENDING_VERIFY`, `FAILED`）严格解耦；入库未决或失败绝不撤销已核验的下载成果。
4. **增量串行保护**：按单篇维护独立的材料目录与 `batch_state.json`，复用 T001 的父子归属核验、防重入阻断与退出码规范。
5. **分析层衔接**：每次更新台账自动导出 `papers_manifest.json`，无缝供给 T002/T003 分析层使用。

## 3. 安装/部署说明
- 本增强版副本当前完整交付于：`E:\AI\AI_reserch\deliveries\T004_download_zotero_bridge\enhanced_skill\`
- 本轮遵循隔离原则，**未覆盖**用户系统已安装技能目录 `C:\Users\lamost\.codex\skills\cnki-topic-library\`；
- 如需全局升级，只需将 `enhanced_skill/` 覆盖至该安装路径即可。

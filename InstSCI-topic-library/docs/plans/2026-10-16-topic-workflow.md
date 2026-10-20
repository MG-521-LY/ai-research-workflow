# InstSci 主题文献增强实施计划

目标：将 cnki-topic-library 的主题检索、语义筛选、可调目标和失败补位接入已安装 InstSci，并部署到 Codex 与 DSH。

架构：复用原项目的去重、评审与排名函数；新增层只持久化研究需求、候选、评审、来源和下载批次预约。下载状态每次从 InstSci 原始清单读取并复核文件。没有第二套可手动写入的“已下载”状态。

1. 固定原任务管理器源码与许可证，记录 SHA-256；复用可测试函数。
2. 提供 init/search/ingest/review/set-target/status/plan/acquire/retry/close-batch/export 命令。批次先预约，执行只调用 InstSci CLI；中断不能盲目重跑。
3. DOI 清单使用上游实际支持的逐行TXT，完整题录保存在相邻JSON；中文条目使用 record_id/title/first_author，交给原有 CNKI/Wanfang 可见浏览器流程。
4. 读取原始 manifest，按批次映射标识；核对成功状态、证据类别、PDF结构、SHA-256及重复内容。需要登录/验证时暂停整队列；损坏清单及未决批次阻止新增。
5. 生成 CSV、研究报告和仅包含合格、核验成功文献的 Zotero 衔接清单；不修改真实文献库。
6. 用合成 PDF 与 InstSci 真实清单格式验证完整衔接、补位、重启、目标调整和坏状态保护；做一次真实多来源元数据查询。
7. 独立代码审查后部署增强 Skill 到两个宿主；为原 instsci Skill 加入主题任务入口，保留备份。用 DSH 真实 Skill provider 验证发现与资源加载。

边界：本次不选择用户正式研究题目，不发起订阅论文下载，不配置账号或写入 Zotero。模型负责理解主题、生成检索式及阅读摘要；程序不使用关键词计数冒充语义评审。

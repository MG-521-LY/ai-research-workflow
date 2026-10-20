# 任务协议

使用本技能的 `scripts/topic_library.py` 或 `instsci-topic`。参数值通过正确引用的独立参数传入，不将用户主题或题名拼成可执行 shell 代码。程序本身不调用模型。

```text
instsci-topic init --task <绝对任务目录> --topic <完整研究需求> --count 20 --plan <search-plan.json>
instsci-topic search --task <任务目录> --query <实际检索式> --limit 30
instsci-topic search --task <任务目录> --query <检索式> --sources openalex,crossref --year 2020-
instsci-topic ingest --task <任务目录> --input <题录.json>
instsci-topic review --task <任务目录> --input <评审.json>
instsci-topic status --task <任务目录>
instsci-topic plan --task <任务目录> --limit 1
instsci-topic acquire --task <任务目录> --batch <plan返回的批次ID>
instsci-topic set-target --task <任务目录> --count 30
instsci-topic set-target --task <任务目录> --add 20
instsci-topic close-batch --task <任务目录> --batch <ID> --reason <实际检查结果>
instsci-topic retry --task <任务目录> --id <候选ID> --reason <已解决的具体原因>
instsci-topic export --task <任务目录>
```

`plan`只预约，不下载；`acquire`才调用 InstSci。DOI批次按上游识别的出版社分组，未知出版社逐篇尝试；输入使用当前上游实际支持的逐行DOI TXT，完整题录保存在相邻JSON。执行中的批次不能释放预约。`status`不自动重试；它可能因为清单正在写入而暂时报告JSON解析错误，此时稍后重读，不能清空状态。日志与原始清单保存在各批次的 acquisition 目录中。

## 检索计划

`init --plan`接受对象，其中 `queries` 必须是数组，其他字段用于记录研究问题和硬性条件。例如：

```json
{"queries":["实际英文检索式","实际中文检索式"],"inclusion":["用户实际要求"],"exclusion":["用户实际排除项"]}
```

限制条件由 Agent 在检索和评审中执行。脚本不会把自然语言纳入条件自动转成可靠语义判断。

## 题录与评审

题录接受数组、`{"papers":[...]}` 或 InstSci 的 `{"results":[...]}`。至少有观察到的 `title`；其他字段按实际资料填写：

```json
{"papers":[{"title":"实际题名","authors":["第一作者","第二作者"],"year":2024,"doi":"实际DOI或空字符串","abstract":"实际摘要","source":"期刊","field":"教育学","doc_type":"期刊","portal":"cnki","url":"实际详情页URL","citations":null,"downloads":null,"query":"实际检索式"}]}
```

`portal`只能是空字符串、cnki或wanfang；国际 DOI 记录无需 portal。无 DOI、也无中文数据库来源的记录保留为不可自动路由，需继续补元数据或另行处理。作者数组必须保留原顺序，第一作者可显式用 `first_author` 提供。支持 `zotero_item_key` 保留已有 Zotero 映射。

`sources` 和 `citation_counts` 保存不同来源及各自被引量。DOI相同会合并；不同且非空的DOI不会仅凭题名合并。摘要、题名等影响筛选的信息变化会撤销原评审，需要重新评审；已有文件不会删除。

评审输入是数组：

```json
[{"id":"导入返回的候选ID","decision":"include","relevance":88,"evidence":"abstract","reason":"根据实际摘要说明满足哪些研究对象、机制或方法条件"}]
```

`decision`为 include/exclude；`evidence`为 title/abstract/fulltext。abstract必须存在真实摘要字段，fulltext需要实际阅读记录 `fulltext_note`。默认相关性门槛65；热度默认不参与排名，初始化时可显式设置0—1的 `--heat-weight`。

## 清单与恢复

`topic.json`保存候选、评审、检索来源、批次预约和文件首次观察哈希，不保存另一份可随意更改的下载成功状态。`.topic-owner.json`保存任务归属标识，状态丢失或不匹配时拒绝覆盖重建。批次输入保存SHA-256和身份快照，变化时要求检查后重新预约。每次计数按批次的原始 InstSci manifest 重新确认：

- 必须同时是 `file_status=success`、`standard_status=success` 和已接受的获取证据。
- DOI批次按规范DOI匹配；中文批次按稳定record_id、题名和当前请求第一作者匹配，并要求browser_verified/title_match。更正第一作者后不能继续认领旧身份的PDF。
- PDF存在且可解析；所有页面均尝试读取，SHA-256没有变化；相同文件内容只计一次。
- 当前评审依然include且达到门槛。只生成了清单或只下载了不相关文献均不计入目标。

原始清单可能被 InstSci 的 Zotero sync 补充映射字段，程序会每次重新读取。向Zotero导出时，从已按身份关联的当前候选补充中文原始清单缺失的DOI/已有条目Key，保持原始获取清单不变。不要人工伪造其成功、身份匹配或证据字段。报告中的核验只在可信原始清单基础上增加文件复查，不能用来认证来源不明的手写清单。

已开始的批次中断时先检查进程和文件。即使进程退出，缺失/不完整清单也不能自动解释成“没有下载过”。确认实际情况后关闭该批次；相同候选需要显式retry才能再尝试。已成功或正在执行的候选不会因补位被再次自动下载。

# InstSci 主题文献增强

从自然语言研究主题开始，检索中英文文献、阅读摘要筛选，按成功篇数补位，并生成 Zotero 交接清单。支持 Codex 与 DeepSeek Harness（DSH）。

```text
研究需求 → 多来源检索与去重 → Agent 阅读摘要评审
       → 按目标缺口预约批次 → InstSci 获取与文件复核
       → 失败补位 → 报告与 Zotero 交接
```

本项目将 [cnki-topic-library](https://github.com/MG-521-LY/cnki-topic-library) 的主题筛选与数量管理接到 [instsci-workflow](https://github.com/deathcats4/instsci-workflow) 的检索和全文获取能力上。摘要语义判断由宿主 Agent 执行，不需要另配模型 API。检索默认使用 Semantic Scholar、OpenAlex、Crossref；中文题录可通过用户有权限的知网、万方页面补充。

增强层维护主题、候选、评审、批次预约和文件观察哈希。成功篇数每次从 InstSci 原始清单与实际文件核验，不提供手工标记“已下载”的接口。支持总目标调整、追加篇数、断点恢复、失败补位和可选的样本内热度排序。

## 安装

已验证环境：Windows、Python 3.12、InstSci 0.2.0a2，兼容验证基于上游提交 `3c3bb4f4da5c73d23b198a791ce1068c5112b92d`。安装脚本面向 Windows；其他平台可手动复制技能，并使用已有 InstSci 环境的 Python 执行脚本。

1. 按 [InstSci 上游说明](https://github.com/deathcats4/instsci-workflow) 安装 Python 环境、浏览器和基础 `instsci` 技能。将基础技能安装到要使用的宿主用户技能目录。MCP 连接沿用上游配置；本次验证使用 `mcp==1.30.0`。
2. 克隆本仓库，使用**已有 InstSci 环境的 Python**运行安装脚本。下面假设两个仓库并列，InstSci 的虚拟环境在其 `.venv` 目录中：

```powershell
git clone https://github.com/MG-521-LY/instsci-topic-library.git
cd instsci-topic-library
& ..\InstSCI\.venv\Scripts\python.exe scripts\install.py

# 只安装到其中一个宿主时
& ..\InstSCI\.venv\Scripts\python.exe scripts\install.py --hosts codex
```

安装脚本备份已有技能，为基础技能添加主题入口，将增强技能复制到用户的 `.codex/skills`、`.dsh/skills`，并生成 `.local/bin/instsci-topic.cmd`。若命令目录尚未在 PATH 中，可直接使用启动器完整路径。备份保存在本仓库的 `runtime/installation-backups`，不会上传。

DSH 需要支持 filesystem Skill 的版本，并按上游要求配置 InstSci MCP。本次验证使用 DSH 0.2.0-rc.2 的官方 filesystem Skill provider 与 MCP client；安装脚本不修改 DSH 的模型或 MCP 配置。保存工作后在新对话中加载技能。

## 使用

> 使用 instsci-topic-library，帮我检索【研究主题】的中英文文献，阅读摘要按【纳入与排除条件】筛选，下载20篇。失败时用下一篇合格候选补足，保留筛选理由和来源。

可以继续说“总目标改成30篇”或“再下载20篇”。未指定篇数时默认100篇；默认按相关性排序，热度权重由用户选择。默认结果目录在用户目录的 `.instsci/topics`，也可指定其他位置。

详细命令、输入格式及恢复规则见 [任务协议](skills/instsci-topic-library/references/task-protocol.md)。源码在 `skills/instsci-topic-library/scripts`。

## 验证与边界

```powershell
& ..\InstSCI\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

29项本地测试通过，使用 InstSci 真实输入解析器、清单写入器和明确标记的合成 PDF。已验证真实元数据查询和 DSH 技能发现；详情见 [验证报告](验证报告.md)。本仓库不包含运行记录、浏览器配置、登录凭据或论文文件。

全文获取依赖用户已有访问权限、上游支持与各来源的实际可用性。遇到机构登录、验证码或限额会暂停。生成 Zotero 交接清单不等于已经入库；没有 DOI 的中文文献还需要已有条目 Key 才能自动附加文件。本次没有由桌面模型完成“主题→真实批量 PDF→Zotero”的端到端验证。

## 许可与来源

MIT，来源与复用文件的校验值见 [NOTICE.md](NOTICE.md)。InstSci 为独立安装的上游项目，其许可证和访问规则仍适用。

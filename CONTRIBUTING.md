# 贡献指南

欢迎提交 Issue 或 Pull Request 改进这一开源工作流。

## 报告问题

请说明操作系统/Python版本、所用模块、最少复现命令、期望行为、实际退出码及去敏后的错误。模型标识、语料是否缺失、是否调用真实外部服务应分别注明。不上传密钥、Cookie、机构授权、Zotero数据库或受版权保护的论文原文。

## 提交修改

1. 从当前主分支创建自己的分支，每次聚焦一个问题。
2. 修改代码时同步更新受影响的使用文档和适当测试。
3. 不把 `partial`、拒答或失败改写为成功；保持状态与证据边界。
4. 变更文件后执行 `python scripts/update_source_manifest.py --write` 更新发布摘要。已跟踪和未忽略的新文件都会登记，保留原始快照摘要及来源；清单不包含它自身，不读取忽略的本机输出。
5. 执行离线检查：

```powershell
python scripts\verify_repository.py
python scripts\update_source_manifest.py
python -m unittest discover -s tests -v
python workflow.py demo
python scripts\check_empty_corpus.py
```

6. 在 PR 中说明改了什么、如何验证、哪些外部服务或硬件条件没有测试。

默认检查不写入真实Zotero、不投递DSH任务、不调用模型，也不下载论文。历史环境相关测试需要各自的数据和授权，不能把缺失条件伪报为已通过。

## 许可与来源

贡献者应有权提交相应内容。对本项目集成代码和文档的贡献按根目录 MIT 许可提供；涉及第三方组件时保留原许可、署名与来源。完整说明见 `NOTICE.md`。

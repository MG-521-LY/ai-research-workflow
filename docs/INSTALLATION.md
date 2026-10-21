# 安装与配置

## 1. 环境要求

参考环境是 Windows PowerShell 与 Python 3.12。本地入口和离线回归同时纳入Windows/Linux CI；不据此宣称机构认证、真实模型、文库写入或DSH跨平台验证。InstSCI自身声明Python 3.10以上；其他Python版本需按实际依赖安装验证。DSH本机Controller桥接使用Windows进程和端口检测。

按用途准备：

| 用途 | 需要的外部条件 |
| --- | --- |
| 查看源码与做静态检查 | Git、Python |
| 解析自己的 PDF | PyMuPDF、合法取得的 PDF |
| 机构全文获取 | InstSCI、可见 CloakBrowser、自己的有效机构权限 |
| Zotero 写入 | 已启动的 Zotero、自己的文库位置和正常授权 |
| 模型推理 | 明确选择的后端、模型、有效凭据与可用网络 |
| PaperQA 原生检索问答 | 额外安装 PaperQA 及其兼容依赖 |
| T006 会话调度 | 已安装 DSH、受信任的本机控制接口、显式授权会话 |

## 2. 克隆与基础安装

```powershell
git clone https://github.com/MG-521-LY/ai-research-workflow.git
Set-Location ai-research-workflow
$repo = (Get-Location).Path
py -3.12 -m venv .venv
$py = Join-Path $repo '.venv\Scripts\python.exe'
& $py -m pip install --upgrade pip
& $py -m pip install -r .\requirements.txt
& $py workflow.py doctor --profile research
& $py workflow.py demo
& $py scripts\verify_repository.py
```

基础依赖包含实际解析/索引使用的PyMuPDF、pypdfium2与Tantivy；不必为本地文件处理先安装完整PaperQA锁定环境。`doctor`只检查当前解释器中的包是否存在；`demo`使用临时合成PDF验证真实T005第一阶段，拦截网络，后续推理阶段保持pending。Linux命令及PDF清单教程见[本地快速开始](LOCAL_QUICKSTART.md)。

需要主题检索与机构全文获取时，另外执行：

```powershell
& $py -m pip install -e .\InstSCI
```

这些命令安装代码与依赖，不登录机构、不调用模型、不上传论文。浏览器运行时若需要额外下载，按InstSCI的安装说明操作；不要把浏览器缓存提交到仓库。

## 3. PaperQA 依赖

`deliveries/T003_paperqa_bridge/requirements_locked.txt` 保存了原环境的精确版本快照，其中包含 Windows 相关依赖。它用于追溯，不代表任意新环境都能一次安装成功。

需要重建原环境时，可在独立虚拟环境尝试：

```powershell
py -3.12 -m venv .venv-paperqa
$pqa = Join-Path $repo '.venv-paperqa\Scripts\python.exe'
& $pqa -m pip install -r .\deliveries\T003_paperqa_bridge\requirements_locked.txt
```

若平台或包源不支持其中某个版本，保存安装错误，按实际 PaperQA API 兼容性调整；不要悄悄改用其他模型或把基础依赖安装成功称为原生问答已通过。使用哪个解释器执行 T005，就在该解释器中安装相应依赖。

## 4. 模型配置与密钥

T005 接受 `--backend paperqa_native|deepseek|gemini_proxy` 和 `--config`。仓库保留两份不含密钥的原配置模板：

- `deliveries/T005_research_pipeline/configs/deepseek_backend.yaml`
- `deliveries/T005_research_pipeline/configs/gemini_proxy_backend.yaml`

复制到忽略的本地配置文件，核对 `api_base`、`model`、`api_key_ref`、超时、上下文和嵌入方式：

```powershell
Copy-Item .\deliveries\T005_research_pipeline\configs\deepseek_backend.yaml .\config.local.yaml
# 用编辑器填写自己的服务地址和实际模型标识。
```

密钥通过当前进程环境变量或受控本地凭据解析。`.env.example` 只是变量名说明，程序不会因为复制为 `.env` 就自动加载。避免把实际密钥写入脚本、命令历史或配置仓库。模型在 DSH 中的显示名称和下游 API 接受的模型 ID 可能不同，需要分别确认，不能照搬会话显示名。

## 5. Zotero 配置

如果选择“本校图书馆官网→数据库”获取方式，先安装 `InstSCI` 并按[学校图书馆路线](LIBRARY_PORTAL.md)填写自己学校的官方地址。配置机构名/网址不是自动认证；登录和验证码由本人在可见浏览器中完成。

先正常启动 Zotero，核实本机端口、文库路径及存储目录。可在当前 PowerShell 中设置非秘密路径：

```powershell
$env:ZOTERO_DB = Join-Path $env:USERPROFILE 'Zotero\zotero.sqlite'
$env:ZOTERO_STORAGE = Join-Path $env:USERPROFILE 'Zotero\storage'
```

上述仅为常见位置，不证明你的文库就在这里。T004 不同入口的选项不同：直接 `pipeline_runner.py` 支持 `--storage-dir`、`--db-path`；默认工作流还从环境读取位置。先查看各入口 `--help`，不要凭空添加不支持的参数。

## 6. T006：可选的 DSH 会话控制

T006 发布副本的项目根目录已改为当前克隆目录，也可用 `AI_RESEARCH_ROOT` 显式指定。授权会话列表默认是空的；在本机创建被 Git 忽略的：

```text
deliveries/T006_dsh_mcp_control/state/managed_sessions.json
```

内容是你实际获得用户授权的会话 ID 到用途的映射：

```json
{
  "session-REPLACE_WITH_AUTHORIZED_ID": "资料核读会话"
}
```

同时核对 `DSH_EXECUTABLE` 指向已安装、受信任的 DSH 可执行文件。不要复制原机授权状态。T006 保留了一些特定部署时期的历史兼容逻辑与集成测试，使用前检查；默认教程不调用历史 `dsh_create_reviewer` 或真实会话集成测试。发布副本已移除仅为某一天接替任务使用的 DeepSeek 建会话辅助函数。

## 7. 首次自检

```powershell
& $py scripts\verify_repository.py
& $py -m unittest discover -s tests -v
& $py workflow.py demo
& $py deliveries\T005_research_pipeline\research_runner.py --help
& $py deliveries\T004_download_zotero_bridge\pipeline_runner.py --help
```

静态检查通过只说明源码与仓库结构满足这些检查，不代表出版商登录、Zotero 写入、模型权限或完整研究结论已经验证。下一步使用[完整教程](TUTORIAL.md)处理自己的最小资料批次。

# 首次本地运行：不需要密钥、Zotero或DSH

统一入口是仓库根目录的 `workflow.py`。这些子命令只处理本机文件；不调用模型、不下载论文、不写文库、不投递会话。所有报告以JSON输出，方便人工查看和其他工具读取。

## 1. 安装与检查

在Windows PowerShell中执行：

```powershell
git clone https://github.com/MG-521-LY/ai-research-workflow.git
Set-Location ai-research-workflow
py -3.12 -m venv .venv
$py = Join-Path (Get-Location) '.venv\Scripts\python.exe'
& $py -m pip install -r requirements.txt
& $py workflow.py doctor --profile research
& $py workflow.py demo
```

Linux/macOS对应命令：

```bash
git clone https://github.com/MG-521-LY/ai-research-workflow.git
cd ai-research-workflow
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python workflow.py doctor --profile research
.venv/bin/python workflow.py demo
```

Python 3.12为本次验证版本。CI覆盖Windows/Linux；macOS命令供部署参考，未列为已验证平台。DSH控制仍是Windows本机功能。

`doctor`无需第三方包即可启动。它报告当前解释器能否找到依赖，不执行安装、导入服务、连接探测或读取密钥。选择配置范围：

| profile | 检查范围 |
| --- | --- |
| `local`（默认） | 本地PDF清单与校验所需的PyMuPDF |
| `research` | T005基础解析、索引及配置依赖 |
| `paperqa` | 基础研究依赖及可选原生PaperQA |
| `dsh` | 可选MCP包与Windows平台；不证明DSH应用/授权已就绪 |

`demo`临时生成一份明确标记为**合成测试资料**的PDF，再调用真实T005第一阶段，完成PDF解析、片段生成和Tantivy索引。网络连接被拦截，临时目录执行后清理。正常结果包含：

```json
{
  "ok": true,
  "scope": "actual_T005_corpus_only",
  "synthetic_fixture": true,
  "valid_papers": 1,
  "total_chunks": 1,
  "downstream_executed": false,
  "network": "blocked",
  "network_attempts": 0,
  "persistent_outputs": false
}
```

它验证本地数据处理链，不生成模型答案或研究结论。模型、机构获取、Zotero授权和DSH需要另行配置，见[安装与配置](INSTALLATION.md)。

## 2. 为自己的资料生成分析清单

只使用自己有权处理的PDF，将路径替换成实际目录：

```powershell
$papers = 'C:\research-data\my-study\papers'
$manifest = 'outputs\my-study\analysis_manifest.json'
& $py workflow.py manifest --input-dir $papers --output $manifest `
  --question '在这些资料中，方法A相对方法B的适用条件是什么？' `
  --topic '自己的研究主题'
& $py workflow.py validate --manifest $manifest
```

`manifest`递归扫描`.pdf`（扩展名不区分大小写），实际解析文件，计算SHA256和尺寸，按**相同字节**去重。不同版本/扫描件不因题名相同自动合并。ID由文件内容生成；不会创建Zotero key。

输出题名/作者/年份保持未知，另记原文件名和 `metadata_status: needs_review`。文件名不会被当作已核对题名。需自行补齐真实题录和版本，回查原文后再研究；不要机械清除未知/冲突标签来骗过检查。

默认生成相对于清单位置的PDF路径；Windows不同盘符时保留绝对路径。移动任务或换电脑后重新执行 `validate`。若目录有不可读/加密/损坏文件，本次不发布部分清单，先处理报错；**不覆盖已有输出**。输入变更使用新清单文件名，保留旧记录。

`validate`只读检查实际路径、字节摘要、尺寸、ID/内容重复、可解析性，以及T005明确拒收的状态。扫描件无文字会提示OCR；题录待核会提示人工确认。`ok: true`只表示这些文件检查通过，不能证明文献身份、指定版本、方法精读或创新成立。

这个清单可以直接交给T005，不必先做Zotero导入。需要获取/入库/研究完整链时，继续[完整教程](TUTORIAL.md)；T004扫描导入清单与本入口的分析清单用途不同，不混用导入参数。

## 3. 读取正式研究状态

按完整教程配置后端并运行T005后：

```powershell
& $py workflow.py status --output 'outputs\my-study\research'
```

它读取 `run_result.json`，显示执行状态、证据状态、研究成熟度和各阶段状态。只有记录为完成、九阶段一致、执行已结束，且登记的报告实际SHA256匹配时，才返回 `execution_success: true`。不会重跑、修写台账或把失败改成完成。状态文件不存在时，先确认目录或运行任务，不创建空的完成记录。

这是执行记录快照，不证明进程仍活着，也不代替全输入/全部证据的独立验收。关键结论仍须逐项回查原文。

## 退出码

| 子命令 | `0` | `1` | `2` |
| --- | --- | --- | --- |
| `doctor` | 所选范围依赖存在 | 缺依赖/不支持的平台 | 命令参数错误 |
| `manifest` | 清单已创建 | 空输入/坏文件/已有输出等 | 命令参数错误 |
| `validate` | 文件检查通过（可能有警告） | 缺失/冲突/无效输入 | 命令参数错误 |
| `status` | 完成记录一致、报告字节匹配 | 未完成/失败/记录冲突/缺报告 | 命令参数错误 |
| `demo` | 真实解析索引演练通过 | 缺依赖/演练失败 | 命令参数错误 |

程序参数帮助用 `python workflow.py --help` 或 `python workflow.py manifest --help`。依赖安装失败、OCR、路径或服务错误参阅[故障排查](TROUBLESHOOTING.md)。

# 完整使用教程：从资料到研究方案

本教程使用自己的研究问题和资料。示例命令不会替你提供论文、模型密钥或机构权限。每次更换主题建立新目录；同一主题的恢复继续使用原目录。

## 0. 初始化本次任务

完成[安装](INSTALLATION.md)后，在仓库根目录执行：

```powershell
$repo = (Get-Location).Path
$py = Join-Path $repo '.venv\Scripts\python.exe'
$work = Join-Path $repo 'outputs\my-study'
New-Item -ItemType Directory -Force $work | Out-Null
$question = '请在此写出具体研究问题、比较对象、资源条件与需要反驳的假设'
```

如果使用独立 PaperQA 环境，将 `$py` 改为对应解释器。`outputs/` 被 Git 忽略；真实输入和产物保存在自己的本地数据目录，不提交论文原文。

## 1. 准备资料：选已有 PDF 或新检索

### 路线 A：已有本地 PDF

先核实每份 PDF 的题名、作者、年份、版本与真实内容。文件名看起来正确不代表 PDF 属于同一篇论文。

下面只扫描已有文件，生成待审核清单，不执行真实导入：

```powershell
$papers = 'C:\research-data\my-study\papers' # 改为自己的目录
& $py deliveries\T004_download_zotero_bridge\pipeline_runner.py scan-dir `
  --input-dir $papers `
  --output (Join-Path $work 'ingest') `
  --manifest-out (Join-Path $work 'scanned_manifest.json')
```

检查生成清单：文件路径与 SHA256 是否对应实际 PDF；题录是否正确；同一内容是否被重复计入；摘要、题录或讲稿是否被误称指定版本全文。扫描是文件入口，不替代原文身份和方法核读。

### 路线 B：从主题获取必要全文

使用默认 InstSCI 主题工作流，首次仅演练，不提交 Zotero：

```powershell
$flow = 'deliveries\T004_download_zotero_bridge\instsci_default_workflow\workflow_runner.py'
& $py $flow run `
  --task (Join-Path $work 'topic-task') `
  --output (Join-Path $work 'ingest') `
  --topic '自己的明确研究主题' `
  --count 3 `
  --manifest-out (Join-Path $work 'analysis_manifest.json') `
  --no-commit
```

`--no-commit` 不代表不联网：主题检索、获取仍可能发生；它用于避免真实文库提交。检索后可能停在 `awaiting_agent_review`，宿主需要根据摘要和主题填写评审输入，而不是自动把搜索结果全部下载。具体协议见[主题库说明](../InstSCI-topic-library/README.md)。

遇到学校登录、验证码或访问限制，在可见浏览器中按自己的真实权限操作。默认目标数量不等于已取得数量；只把实际合法取得、唯一且可解析的全文计入。题录、摘要、未获回执和精读方法页分开记录。

## 2. Zotero：先演练，再真实提交

已有清单可先用直接桥接入口检查计划：

```powershell
& $py deliveries\T004_download_zotero_bridge\pipeline_runner.py import-local `
  --manifest (Join-Path $work 'scanned_manifest.json') `
  --output (Join-Path $work 'ingest')
```

直接桥接没有 `--commit` 时不执行正式提交。确认文件、身份、父条目复用、文库位置、授权与计划正确后，同一命令增加 `--commit` 才进行真实导入：

```powershell
& $py deliveries\T004_download_zotero_bridge\pipeline_runner.py import-local `
  --manifest (Join-Path $work 'scanned_manifest.json') `
  --output (Join-Path $work 'ingest') `
  --commit
```

默认工作流与直接桥接的开关不同：`workflow_runner.py` 使用 `--no-commit`，不要混用。提交后独立核对：

- 父题录的题名、作者、年份、DOI；
- 附件是否挂在正确父条目，重复资料是否复用；
- 附件实际 PDF 字节与原文件 SHA256、尺寸；
- 分类树及映射是否存在，未决/失败项有没有被错误标为完成。

如果上传或授权状态不明确，先查台账与真实文库，不直接再次上传。专项旧脚本不是这一步的默认入口。

## 3. 导出分析清单

经默认工作流处理的桥接目录可导出统一分析清单：

```powershell
& $py $flow export `
  --bridge-dir (Join-Path $work 'ingest') `
  --output (Join-Path $work 'analysis_manifest.json')
```

不同入口的台账结构可能不同，以该入口实际生成的 `papers_manifest.json` 或统一分析清单为准；不要为了使导出通过而手改状态。没有 Zotero 的纯本地研究也可以提供自己的分析清单，但不能伪造 Zotero 映射。

统一清单的结构参考：

```json
{
  "schema": "instsci.unified_analysis_manifest.v1",
  "schema_version": "1.0.0",
  "topic": "自己的研究主题",
  "question": "自己的具体研究问题",
  "items": [
    {
      "paper_id": "paper_001",
      "title": "核对过的真实论文标题",
      "authors": ["真实作者"],
      "year": "2025",
      "pdf_path": "C:/research-data/my-study/papers/paper_001.pdf",
      "sha256": "替换为该文件真实的64位SHA256"
    }
  ]
}
```

上面是待填写模板，不能原样运行。可用 `Get-FileHash -Algorithm SHA256` 计算文件摘要。不要给尚未核对的条目填“已验证”，也不要给未入库条目编造 Zotero key。

## 4. 执行研究流水线

先依据[安装与配置](INSTALLATION.md)准备自己的 `config.local.yaml`，再明确传入清单、问题和独立输出目录：

```powershell
& $py deliveries\T005_research_pipeline\research_runner.py `
  --manifest (Join-Path $work 'analysis_manifest.json') `
  --question $question `
  --output (Join-Path $work 'research') `
  --backend paperqa_native `
  --config .\config.local.yaml
```

这个步骤可能调用付费模型、外部检索和嵌入服务；开始前核实后端实际路由、模型与预算。不会因为你在 DSH 中选了某个模型，就自动让 PaperQA 或外部检索使用同一个模型。

期望产物包括语料状态、单篇方法卡、带原文定位的问答、跨论文比较、候选假设、近工作反驳、资源条件、验证方案和综合报告。输出的完整程度以本次状态和实际文件为准，不以“生成了一个报告”判断成功。

## 5. 阅读结果的顺序

```powershell
Get-Content -Raw -Encoding UTF8 (Join-Path $work 'research\run_result.json')
```

1. 先查 `execution_status`、各阶段状态、语料冲突与缺失；
2. 再看 `RESEARCH_SYNTHESIS_REPORT.md`，区分原文事实、推断和待检验假说；
3. 抽查关键公式、物理页、段落原文与引用定位；
4. 核查最近工作是否真正读到方法，不能用题录/摘要假装全文；
5. 核对资源条件、算法输入、单位、对照、消融、反例和停止条件。

常见退出码：`0` 表示程序认为必要阶段成功；`1` 表示部分完成、拒答或执行失败；`2` 常为参数错误。即使退出码为0，也不保证科学创新、全局新颖性或物理性能成立。

## 6. 恢复、增量和换问题

网络恢复或补齐缺失输入后，重跑**同一命令、同一输出目录**。有效阶段缓存会按输入/问题/模型/配置/实现指纹判断能否复用。

更换问题时可以保留同主题目录并显式传入新 `--question`；系统会按依赖失效规则重算相关阶段。切换研究主题使用新目录。强制刷新使用 `--no-resume`，没有必要添加不存在的 `--resume` 参数。

桥接中断先检查：

```powershell
& $py $flow status --dir (Join-Path $work 'ingest')
& $py $flow recover --bridge-dir (Join-Path $work 'ingest')
```

活进程仍持锁时等待，不删锁、不重启应用。映射损坏时保留现场并核实真实父子关系，不能清空台账后批量重导入。

## 7. 可选：使用 DSH 组织协作

完成[授权会话配置](INSTALLATION.md#6-t006可选的-dsh-会话控制)后，可通过 fresh stdio MCP 查询和投递：

```powershell
& $py deliveries\T006_dsh_mcp_control\mcp_client.py dsh_list_sessions

$query = @{session='session-REPLACE_WITH_AUTHORIZED_ID'; after_seq=0} | ConvertTo-Json -Compress
& $py deliveries\T006_dsh_mcp_control\mcp_client.py dsh_read_progress --arguments $query
```

任务文档放在本仓库 `dispatches/` 或 `reviews/` 下；它应完整说明输入、写入范围、已通过证据、待修问题与输出契约。每次新任务使用未消耗的稳定 `request_id`。提交前查询回执，投递不确定时先查收件箱和进度，不换ID盲重发。

```powershell
$task = @{
  session = 'session-REPLACE_WITH_AUTHORIZED_ID'
  task_path = (Join-Path $repo 'dispatches\my-study\task.md')
  request_id = 'my-study-method-audit-001'
  mode = 'queue'
} | ConvertTo-Json -Compress
& $py deliveries\T006_dsh_mcp_control\mcp_client.py dsh_submit_task --arguments $task
```

`accepted`、真实工具执行、产物存在、独立验收是四个不同阶段。资料核读、方案撰写和独立审核可以分角色，但写入模块要互斥；同一稿必须按冻结hash审核。

## 8. 一个适合交给执行会话的任务模板

> 围绕我明确给出的研究问题，先复用已有PDF与文库，必要时按现有权限补具体方法缺口。每个主题建立独立输出目录，核对题录、版本、文件摘要和物理页。生成带出处的方法卡、真实近工作差异、完整因果链、最低模型输入、对照消融、单位指标、反例与资源退路。明确模型和实际路由，保留拒答和缺失，不以生成文本替代原文核读。实体实验和训练执行另行安排。

历史更详细的参数解释见 [T005 原指南](../deliveries/T005_research_pipeline/USER_GUIDE.md)；其中原机路径和测试样本仅供追溯，不是新部署默认数据。

# 从自己的学校图书馆进入数据库获取全文

这是一条正式的**用户/Agent 协作获取路线**：

**本校图书馆官网 → 数据库导航 → 选择数据库 → 按学校要求认证 → 定位文章 → 下载 PDF → 核身份与字节 → 后续入库/分析。**

它与公开全文、出版社直接机构登录并列。学校不是默认中科大；使用者必须填写自己的机构与经核实的官方图书馆链接。中科大仅是文档中明确标注的示例。

该入口用同一个可见 CloakBrowser 保存图书馆、数据库及登录上下文；程序接收原生浏览器下载，核对 PDF 首页面题名/可见 DOI，记录尺寸、SHA256、MD5 和私有截图。数据库选择与站内检索由用户或宿主 Agent 在浏览器里完成，学校登录/验证码只由用户操作。**目前没有通用于所有学校的全自动菜单导航或认证能力。**

## 1. 安装与填写输入

在仓库根目录、已安装 Python 3.12 的环境中执行：

```powershell
py -3.12 -m venv .venv
$py = Join-Path (Get-Location) '.venv\Scripts\python.exe'
& $py -m pip install -r requirements.txt
& $py -m pip install -e .\InstSCI
```

若已有环境，直接使用自己的 Python。`library-papers` 需要额外安装 InstSCI，只有顶层本地依赖时不能调用该命令。

在仓库外的私人任务目录保存 `library.json`，字段如下：

```json
{
  "institution": "中国科学技术大学",
  "library_homepage": "https://lib.ustc.edu.cn/",
  "database_directory": "https://lib.ustc.edu.cn/category/e-resource/database/"
}
```

这是中科大示例，可直接参考 [示例配置](../examples/library_portal.ustc.json)。其他学校须替换为自己学校官网与数据库导航的真实公开链接，不能只改机构名。`database_directory` 可省略，省略时从图书馆首页自行进入导航。入口要求不带账号密码、查询串或登录 token 的公开 HTTPS 地址；不要把认证回跳 URL 当图书馆官网。

中科大官方资料：[图书馆](https://lib.ustc.edu.cn/)、[数据库导航](https://lib.ustc.edu.cn/category/e-resource/database/)、[校外访问指南](https://lib.ustc.edu.cn/电子资源/校外访问电子数据库资源指南/)。链接在 2026-10-21 核对；学校改版后重新核查。

另存 `records.json`，提供需要取得的**准确题名与 DOI**：

```json
[
  {
    "doi": "10.1109/48.838980",
    "title": "Modeling and Identification of Open-Frame Variable Configuration Unmanned Underwater Vehicles"
  }
]
```

这只演示输入格式，不等于新建了该论文的下载任务。已有全文先复用；不同版本要核实各自身份，不能把会议版和扩展版冒充同一指定全文。无 DOI 的中文文献使用原有知网/万方入口及精确题名、第一作者核验，本入口目前要求 DOI。

## 2. 先离线查看计划

```powershell
$work = Join-Path $env:USERPROFILE 'ResearchRuns\library-demo'
& $py -m instsci.library_portal `
  --library-config (Join-Path $work 'library.json') `
  --records (Join-Path $work 'records.json') `
  --output (Join-Path $work 'download-run') --plan-only
```

计划不联网、不启动浏览器、不读取全局凭据、不写下载目录。返回 `planned_not_downloaded`，不表示已登录或已取得权限。输入文件须由你事先放到上述位置。

## 3. 在同一可见浏览器内下载

去掉 `--plan-only` 运行；也可使用等价的安装后命令 `instsci library-papers`：

```powershell
& $py -m instsci.library_portal `
  --library-config (Join-Path $work 'library.json') `
  --records (Join-Path $work 'records.json') `
  --output (Join-Path $work 'download-run')
```

1. 程序打开学校图书馆首页并保存截图；若配置导航地址，会在同一页进入数据库导航并再保存截图。
2. 用户或宿主 Agent 根据页面实际链接选择 ScienceDirect、IEEE、知网等目标数据库。校内可能按校园网 IP 识别；校外根据本校官方说明选择机构登录、CARSI、OpenAthens、WebVPN 或学校提供的其他合法路径。不会把 WebVPN 当所有学校的默认，也不会自动更改系统 VPN/代理。
3. 需要登录时，你在该浏览器中手动完成。不要在聊天里发密码或验证码；普通 Chrome 中已登录不等于这个浏览器已登录。
4. 在数据库中按 DOI/准确题名定位文章，检查题名、作者和版本，然后使用 PDF 下载按钮。不要仅在内嵌阅读器里打开；本入口需要一个原生浏览器下载事件。只出现 CAJ 或不能下载的阅读器时，保留未获取并改用相应既有入口，不能把 HTML/CAJ 改名成 PDF。
5. 程序接收下载，只有可解析且首页面题名匹配、可见 DOI 不冲突的目标 PDF 才进入 `complete/pdfs/`。参考文献中出现目标题名/DOI不会被当成功；扫描件/题名排版不确定或多义匹配会保留待人工核验。

不会离开学校访问链主动跳到裸 DOI URL，也不会导出 cookie 交给 HTTP 下载器冒充学校授权。浏览器内的菜单、登录、文章页与 PDF 控件仍须由用户/Agent按实际页面目视核对。`route_chain_verified=false` 如实表示程序没有自动审定每一步跳转；仅打开官网或原生下载成功不证明订阅全库可用。

学校规则及人工访问范围照常适用；自动知网/万方批量获取仍走原有配额台账入口，不能借本入口自动点击绕过下载计数、访问限制或验证码。

## 4. 等待、中断与恢复

默认等待 900 秒后保存未完成状态并**保持浏览器继续等待**；不会把没有下载解释成“认证失败”或“等待你登录”。关闭浏览器或 Ctrl+C 会保留已收 PDF。只有明确传 `--close-on-timeout` 才在时限到达时关闭本次浏览器。

恢复时用相同配置、相同文章清单、相同输出目录，加 `--resume`。会重读已有 PDF 字节与身份，匹配的成功项跳过；输入机构/清单变动或文件篡改会拒绝恢复。浏览器私有 profile 可复用，但不保证学校登录状态永远有效，尤其 WebVPN 活会话可能需要重建。

一次只运行一个写入进程。发现 `.library_run.lock` 时先核查是否仍有活进程；不能删锁或重启活浏览器来重复执行。已确认崩溃留下的锁需人工核对后处理。

## 5. 输出及后续流程

| 输出/状态 | 意义 |
| --- | --- |
| `library_state.json` | 完整进度、指定机构、请求列表、已核验项、待获取项的依据 |
| `complete/manifest.json` | 已通过身份/字节核验的下载清单，带 DOI/题名/文件/hash/来源路线 |
| `complete/pdfs/` | 通过核验的 PDF；不代表已精读或已入 Zotero |
| `candidates/` | 重复、冲突或未核验下载；保持隔离，不计成功 |
| `private-evidence/` | 官网、导航、下载截图，仅本地私有；不要公开上传 |
| `browser_start_failed` | 浏览器启动失败；不能写成等待学校登录 |
| `library_navigation_failed` | 官网或导航打开失败；不证明数据库拒绝订阅 |
| `capture_timeout_not_login_verdict` | 时限内没收齐，不作登录/权限结论 |
| `completed_pdf_identity_only` | 收齐且文件身份核验通过，学术方法、版本及创新结论仍需独立核读 |

`--plan-only` 退出码 0 是计划可读；执行只有收齐目标身份文件才返回 0，未完成返回 2。缺失请求可由 `records` 减去成功项得到，不补造成功行。

接入原工作流：从 `complete/pdfs/` 运行 T004 `scan-dir` 生成待核题录的导入清单，核对 DOI/题名和已有 Zotero 父子项，再使用正常入库授权；纯本地分析可从该目录运行 `workflow.py manifest`，核回 `complete/manifest.json` 的真实题录后再交 T005。具体步骤见[完整教程](TUTORIAL.md)。**不要把下载清单直接当 T005 分析清单或 Zotero 已入库证明。**

给 DSH 的提示示例：

> 使用本工作流的 library-papers 路线，从我指定学校的官方图书馆首页和数据库导航进入对应数据库。先复用已有全文，再补我的待得清单；不要猜学校或网关。保持同一可见 CloakBrowser，上网身份、密码和验证码由我本人完成。根据实际页面选择数据库并查准确题名/DOI，下载后核原件、版本、首页面身份、字节 hash；没打开浏览器、没到登录页、未获订阅、未捕获 PDF 分别记录。自动中文库任务沿用既有配额台账，不绕限制。不重复父条目，不把下载成功说成入库或精读完成。

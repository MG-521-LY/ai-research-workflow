# 所有来源统一采用 Zotero Connector 优先

默认获取策略现在是：

**检索/定位论文页 → 实际 Zotero Connector 保存题录和可得 PDF → 正常 Local API 读回核验 → 插件失败或只有题录时，按原来源补全文 → 复用原父条目 → 分析。**

Semantic Scholar、OpenAlex、Crossref 继续负责发现候选；它们的 API JSON 不是可以按插件按钮下载的论文页面。进入获取阶段后统一走上述策略，不是只对学校图书馆来源使用插件。已有且核验过的本地全文/文库条目直接复用，不重新保存。

## 启用与安装边界

1. 安装并正常打开 Zotero 桌面程序，启用自己正常的本地 API 访问。
2. 使用[官方 Zotero Connector](https://www.zotero.org/download/connectors)；[官方源码与构建说明](https://github.com/zotero/zotero-connectors)。
3. 本工作流的自动化浏览器使用自己的 profile，不会读取普通 Chrome/Edge 的个人 profile 或其凭据。需要为该浏览器提供**独立、未修改的官方解包扩展目录**，目录中应有构建完成的 `manifest.json`。源码根目录不等于已构建扩展。官方构建产物按其开源许可单独使用，不随本 MIT 仓库捆绑发行。
4. 设置目录（下面路径只是占位示例，应替换为自己的实际路径）：

```powershell
$env:ZOTERO_CONNECTOR_EXTENSION = 'D:\Tools\zotero-connector-extension'
```

也可以给统一入口传 `--connector-extension <目录>`。常规本机端口是 `http://127.0.0.1:23119`；自定义端口可用 `ZOTERO_LOCAL_API_URL`，只接受无凭据/查询串的 loopback HTTP 地址。若正常本地 API 要求访问 key，可通过自己的 `ZOTERO_LOCAL_API_KEY` 环境变量提供；只在内存中用于本地读回，不读取 Zotero 内部 key 表、SQLite 凭据或浏览器 cookies，不写日志。

**下载仓库不会自动安装 Zotero 或 Connector。** 扩展没配置、没加载，或桌面程序/正常读回不可用，会记录 `connector_unavailable` 后进入现有获取路线，不会假称按过插件按钮。2026-10-21 本机已验证官方 Connector 5.0.217 的 MV3 后台在 CloakBrowser 中真实加载，且存在实际 translator 保存方法；这不是对所有浏览器/插件版本或所有数据库保存成功的保证。

## 统一命令

先准备一个 JSON 文章列表（真实任务先复用已存在的 PDF）：

```json
[
  {
    "doi": "10.1109/48.838980",
    "title": "Modeling and Identification of Open-Frame Variable Configuration Unmanned Underwater Vehicles",
    "url": "https://doi.org/10.1109/48.838980"
  }
]
```

在安装了本仓库 InstSCI 的环境中执行：

```powershell
instsci acquire records.json --output D:\ResearchRuns\my-topic
```

统一入口首先检查实际扩展与正常 Zotero 读回，然后在可见浏览器打开观察到的论文 URL（或解析 DOI 对应页面），等目标页面/translator 准备好，驱动**扩展自己的** `saveWithTranslator` 保存。它不伪造 `/connector/saveItems` 请求，也不把手写题录导入说成浏览器插件执行。

主题助手的 DOI、知网和万方任务现在均计划为 `instsci acquire … --fallback <来源>`。插件入口失败后，原 `papers`、`cnki-batch`、`wanfang-batch` 等实现仍作为备用路径使用；直接运行这些低层命令属于显式备用/诊断操作，正式主题任务使用统一入口。SDK/MCP 的普通 DOI/URL fetch、OA 获取和出版社文章页面也有 Connector 前置检查。

非 DOI 中文文献需要 `title`、`first_author` 和 `portal: "cnki"` 或 `"wanfang"`，可以保留同一库内实际观察到的 URL。身份核验使用精确题名与第一作者，不能拿后续共同作者代替第一作者。插件自动保存前仍要预约该中文库的同一配额台账；插件尝试与备用下载分别记录，不借插件绕过次数/验证码/机构限制。

## 成功、回退与暂停

| 插件/文库结果 | 后续动作 |
| --- | --- |
| 已有对应父条目与真实 PDF | 读实际附件文件，核首页身份、尺寸、SHA256，复用父子 key；不新建或重下 |
| 插件新保存题录与真实 PDF | 正常读回核验后完成获取；下游复用其父子 key |
| 只有对应题录，没有匹配 PDF | 保留父 key，原来源只补全文；后续正常同步挂到原父条目 |
| 未配置/未加载插件，Zotero 未运行或无法正常读回 | 明确记录不可用，转原获取路线 |
| 页面无可用 translator、明确保存失败 | 保存真实回执后转原来源 |
| 保存仍在进行、调用后读回失败/结果不确定 | 暂停，不立刻再保存或另建父条目；先检查进行中的任务/文库 |
| 多个同 DOI 父条目或身份冲突 | 保留冲突待处理，不擅自挑一个、删条目或再新建 |
| 只有 HTML/网页快照/题录 | 不算全文成功 |

插件返回不等于附件完成。本地 API 读取 `/file` 的正常 302 `file://` 登记位置时，会核附件 key、文件名和实际字节；不把正常本地登记跳转误判为缺失后重复导入。无法证明归属的路径保持未核验。

学校认证/验证码必须由本人在可见浏览器完成。插件不是订阅权限的替代，也不会让每一个数据库都可访问。学校图书馆入口仍用于建立数据库访问上下文，见[学校路线](LIBRARY_PORTAL.md)。学校路线在目标文章页面先尝试插件，只有插件不可用/失败才回到原生 PDF 下载接收；用户自行手动下载的行为不能被宣传成插件成功。

## 输出、去重与接续

- `acquisition_state.json`：逐项进度与请求绑定；`strategy=connector_first`。
- `item_*/connector/connector_receipt.json`：真实插件结果、读回情况、是否允许回退。
- `complete/manifest.json`：实际获取结果及 `zotero_item_key`、`zotero_attachment_key`（确实存在才填写）、PDF/hash/证据层级。
- `item_*/fallback/`：仅需要补取的文章进入原路径，保留该来源的台账。

`local_verified_reuse` 表示复用核验过的文库文件；只有本轮确实驱动扩展并有浏览器证据时才标记浏览器插件获取。复用、新获取、仅题录、失败分别计数。

同一请求可加 `--resume`；会核已完成文件字节并跳过，明确失败项才再次处理。若留有不确定/正在进行的保存标记，必须先只读核查，不自动重发；不删锁或覆盖历史来重置状态。只有全部文章文件通过核验时命令才以 0 退出，否则以 2 退出或保持明确暂停。

T004 的 `--no-commit` 演练向获取子进程传递 `INSTSCI_CONNECTOR_READ_ONLY=1`，只复用已核文库文件，不让插件新建条目；其余下载仍可能联网。正常 `instsci acquire` 和主题获取会执行真实插件写入。不要把普通获取当成入库前的无写入预览。

下游 `instsci zotero sync <manifest>` 对已有父子 key 可直接复用，不需要再创建写入 backend；只有父 key 时走正常附件同步，写入前重新读现有附件并比较实际字节；出现冲突或读回不确定时暂停。T004 在发现原父条目而附件尚未核实时会保持 `PENDING_EXISTING_PARENT_ATTACHMENT`，避免旧 RIS 导入另建父条目；需经正常同步完成附件或读回后再继续。**不会把这个待办冒充完整入库。**

## 给 DSH 的约定

> 对所有来源采用 Connector 优先。先定位准确文章页并检查实际 Zotero 插件与桌面端状态，使用扩展自身保存；读回父条目、PDF 附件和真实字节核验。插件保存成功就复用，不再调用原导入路径；只保存题录时保留原父 key、只补缺失全文。明确失败/不可用才调用 InstSci、学校图书馆、知网/万方等原下载路径；保存进行中或结果不确定时先查回执和文库，禁止盲重试。搜索 API 仍用于发现候选，不把题录或网页快照计为全文。验证码/学校认证由本人完成，旧文献不重复获取，原件不覆盖。

## 本次真实测试边界

2026-10-21 在 Windows 上，以官方 Connector 5.0.217、可见 CloakBrowser 和用户已打开的 Zotero，实际保存了一份明确标记为合成测试的 HTML 题录及 PDF。正常 Local API 独立读回父子 key 和登记文件，PDF 字节 SHA256 与合成原件一致；重跑只复用文件，父条目仍为一个。截图与测试资料留在本机，不随开源仓库发布。这证明该环境的真实插件保存及幂等复用可行，不代表所有出版社、中文数据库或学校订阅均已验证。

备用路径沿用现有下载器的覆盖范围；任意作者页面或不支持的网站仍可能需要人工在可见浏览器定位合法 PDF，不能因为进入备用路线就宣称下载或入库成功。

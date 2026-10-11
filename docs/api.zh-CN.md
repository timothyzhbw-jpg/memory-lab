# 公开 Python API

[English](api.md) | 中文

从 `memcore`、`memfalsify`、`orgmemory`、`memlab_mcp` 包根导入。`memcore` 的发行包名为
`memlab-core`，`memlab_mcp` 为 `memlab-mcp`。每个包通过明确的 `__all__` 列出公开名称，
wheel 中包含 `py.typed`。支持 Python 3.10+。

## 稳定性与语义化版本

公开 Python API 是各包 `__all__` 中的名称、文档中的签名、模型公开字段与方法及文档中的异常。
继承的 `model_dump`、`model_validate`、`model_dump_json` 等方法遵循 Pydantic 自己的契约。
新代码使用包根导入。原定义模块路径继续可用，引用同一对象。各发行包独立拥有 `__version__`。

这是拟定的 1.0 API。0.x 次版本仍可能改变行为，补丁版本做兼容修复。
从 1.0 开始，各发行包独立遵循语义化版本：补丁版本兼容修复、次版本兼容新增、不兼容修改要求
主版本升级。1.0 冻结公开名称、签名与默认值、模型字段名／类型、序列化枚举值、文档中的返回类型
与异常、只读／仅新建的保证。模型回答、模型可用性、生成报告的具体措辞不在冻结范围。

公开名称、参数或字段在删除或不兼容替换前至少经历一个完整次版本的弃用期。
弃用的可调用对象在使用时发出 `DeprecationWarning`，名称与字段在访问／使用时适用地发出警告。
发行说明与此文档会说明替代方案。例如在 1.2 弃用的 API 在 1.3 仍可用，删除要求 2.0。
此次修改没有新增弃用项。

明确内部实现：不在 `__all__` 中的名称、下划线辅助函数、CLI 模块／app 与选项枚举、
`build_verifier`、`preselection`、默认模型常量、报告标题与格式化函数、分词／排序、Git 对象存储
辅助函数、GitHub 映射上下文／锁／游标／apply 内部流程、MCP 工作区与工具注册类。
原有路径保留，但无稳定性承诺。CLI 与 MCP 协议接口另有文档。
偶然暴露的子模块属性不是公开导出；原定义模块导入为 `from memfalsify.audit import audit`，
推荐的可调用函数导入为 `from memfalsify import audit`。

## 使用约定

安装所需包后从仓库根目录运行示例。文件路径用 `Path`，组织记忆时间戳必须带时区。
证据偏移以 Unicode 字符计数，哈希覆盖原始字节；完整性不能证明来源真实。
`confined_reads` 需要 POSIX 支持，不支持时抛出 `ValueError`，上下文外保持普通读取行为。
`make_span` 信任给定文本／摘要；`locate_quote`、`verify_entry`、`load_manifest` 读取并检查文件。

记忆 `audit` 默认词法验证、`follow_links=True`、`stale_days=180`、`max_candidates=3`、
`min_coverage=0.5`、`max_judged=None`，无排除项。`exclude=("private/**",)` 排除证据。
`audit_claims` 要求 `memory_file`、`files_read`、`memory_paths` 关键字，可用 `only` 限定输出。
只有人工 `Review` 能确定支持／反对，模型仍是候选。自定义 `Verifier` 提供 `name: str` 和
`judge(claim: str, passage: str) -> Judgment`。包导入不导入 `anthropic` 或 `typesafe_sdk`；
构造模型验证器需要 `[llm]`／`[jev]` 扩展与凭据或注入客户端。`DualVerifier` 保留分歧；
`CascadeVerifier` 在首个判断置信度不足时升级。模型审计显式传入 `min_coverage=0.2, max_judged=8`
与 CLI 预筛选一致。模型失败为 `VerifierError`；证据错误为 `memcore.EvidenceError`；
无效输入为 `ValueError`（包括 Pydantic 验证错误），文件 I/O 可能抛出 `OSError`。
审计不修改记忆。

`memory_diff` 默认比较 base 与工作区，仅用当前证据审计新增／修改项，支持 audit 的时间／
覆盖率／判断次数选项，不含 `max_candidates`／`only`。`memory_history` 默认沿第一父提交链
扫描 100 个提交，报告浅克隆／截断，不跟随文件重命名。`rollback_patch` 默认跟随链接，仅生成
字节或 None，不应用补丁。Git 错误为 `HistoryError`／`GitError`。

`Dataset` 构造时验证引用。`Dataset.load` 可指定 directory、events、policy、evidence 路径，
默认目录 `.`，核验完整清单，包括 as_of 之后的记录。`replay` 遵守权限，未授权事件保留在轨迹中。
组织审计默认 `stale_days=14`，参考时间依次取 as_of、now、当前 UTC。验证错误为 `ValueError`
或 `memcore.EvidenceError`，I/O 可能抛出 `OSError`。回放、解释与审计不写文件。

GitHub `convert` 是纯映射，无 I/O：原始抓取字典、已有事件 ID 映射、证据 ID 集合、`GithubConfig`
输入，`Plan` 输出。计划中的 `Capture` 是渲染后的源文件，不是原始抓取字典。
`run_convert` 默认写入，默认时钟取抓取开始时间；`run_sync` 用提供的 `Client` 抓取并导入，
默认增量同步、当前 UTC 时钟。full、since、issues 互斥。两者返回 `Summary`，须检查 complete，
暂时停止可能产生部分导入。导入仅新建、不修改策略。`dry_run=True` 跳过数据集写入；显式指定
save_capture 仍保存抓取，无写入预览时省略它。永久抓取失败为 `GitHubError`，无效输入为
`ValueError`，证据错误为 `memcore.EvidenceError`，I/O 为 `OSError`。默认传输仅向 GitHub API
来源发送凭据并拒绝重定向，自定义 `Transport` 须保持该边界。`Client.get/get_json/pages`
是低层请求方法，其中 `Stopped` 表示暂时停止。

`create_server` 注册只读工具，不启动传输。需要安装 `memlab-mcp`、现有根目录与 POSIX 支持，
否则抛出 `ValueError`。返回服务器的服务 API 由 MCP SDK 定义。

## 示例

### 证据与限制读取

```python
from pathlib import Path
from memcore import confinement_supported, confined_reads, locate_quote, read_file_text

root = Path("examples/memfalsify/evidence").resolve()
span = locate_quote(root, "docs/database.md", "PostgreSQL 16")
print(span.location, span.sha256)
if confinement_supported():
    with confined_reads(root):
        print(read_file_text(root / "docs/database.md"))
```

### 记忆审计

```python
from datetime import date
from pathlib import Path
from memfalsify import LexicalVerifier, audit, parse_memory, to_markdown

memory = Path("examples/memfalsify/MEMORY.md")
claims, files = parse_memory(memory)
report = audit(memory, Path("examples/memfalsify/evidence"),
               reviews=Path("examples/memfalsify/reviews.jsonl"),
               verifier=LexicalVerifier(), as_of=date(2026, 10, 10))
print(to_markdown(report))
print(report.model_dump_json(indent=2))
```

### Git 历史与回滚（需要本地仓库）

```python
from pathlib import Path
from memfalsify import diff_to_markdown, memory_diff, memory_history, rollback_patch

# Requires a Git repository containing this memory file and revision.
memory = Path("MEMORY.md")
changes = memory_diff(memory, base="HEAD~1", evidence_root=Path("evidence"))
print(diff_to_markdown(changes))
history = memory_history(memory, max_commits=50)
# Substitute a claim ID from the memory/history report.
restoration = rollback_patch(memory, claim="m-1a2b3c4d", to="HEAD~1")
# restoration.patch is UTF-8 bytes or None; review before applying externally.
```

### 组织记忆

```python
from pathlib import Path
from orgmemory import Dataset, audit, audit_markdown, explain, replay, validate

dataset = Dataset.load(Path("examples/orgmemory/feature-a"))
validate(dataset.events, dataset.evidence)
result = replay(dataset)
print(explain(result, "Feature A"))
print(audit_markdown(audit(dataset)))
```

### 离线转换（需要自己的抓取与现有目录）

```python
from pathlib import Path
from orgmemory import run_convert

# Use a capture you recorded yourself and an existing dataset directory.
preview = run_convert(Path("dataset"), Path("capture.json"), dry_run=True)
print(preview.lines())
# Omit dry_run=True for a create-only import.
```

### GitHub 同步（执行时联网）

```python
from pathlib import Path
from orgmemory import Client, PersistentTransport, run_sync, token_from_env

# This example performs network requests when executed.
with PersistentTransport() as transport:
    client = Client(transport, token_from_env(), max_wait=60)
    summary = run_sync(Path("dataset"), "owner/repo", client, dry_run=True)
    print(summary.complete, summary.events_appended)
```

### 构建 MCP 服务器

```python
from pathlib import Path
from memlab_mcp import create_server

server = create_server(root=Path("examples"), allow_model_verifiers=False)
# Start serving separately through the installed MCP SDK or CLI.
```

## 导出参考

下表列出所有公开名称。模型字段以类型定义为准，可通过 Pydantic `model_fields` 查看；
Change、Rollback、Capture、Plan、Summary 使用 dataclass 字段。完整函数签名列在表后。
签名中的 _now 表示当前 UTC，是内部默认实现，不是公开名称。


### `memcore`

| API | 用途 |
|---|---|
| `memcore.EvidenceError` | 证据缺失、被修改或越界时抛出的 ValueError。 |
| `memcore.EvidenceSpan` | 不可变的精确引用：路径、SHA-256、字符偏移、行号和可选的来源信息。 |
| `memcore.ManifestEntry` | 经过验证的 evidence.jsonl 记录，包含 ID 和必填的来源信息。 |
| `memcore.__version__` | 已安装发行包的版本字符串。 |
| `memcore.append_jsonl` | 将一个字典以 UTF-8/LF JSONL 追加写入；父目录须已存在。 |
| `memcore.confined_reads` | 固定根目录、在读取时防止符号链接竞态的上下文管理器；不支持的平台抛出 ValueError。 |
| `memcore.confinement_root` | 返回当前限制读取的根目录；无上下文时返回 None。 |
| `memcore.confinement_supported` | 检查 POSIX O_NOFOLLOW 与 dir_fd 是否可用。 |
| `memcore.line_of` | 返回 Unicode 字符偏移所在的行号，从 1 开始。 |
| `memcore.load_json` | 从 str 或 bytes 解析一个 JSON 值；格式错误、对象键重复、非 UTF-8 字节和嵌套过深都抛出 ValueError。 |
| `memcore.load_manifest` | 验证全部记录并返回 ID → EvidenceSpan；路径相对于清单所在目录。 |
| `memcore.locate_quote` | 定位根目录下 UTF-8 文件中的首个逐字匹配引用；可选来源参数传给 EvidenceSpan。 |
| `memcore.make_span` | 用文本、摘要及 [start, end) Unicode 偏移构建引用；不读取文件或计算哈希。 |
| `memcore.open_text` | 只读文本上下文管理器；默认 UTF-8，遵守 confined_reads。 |
| `memcore.read_file_bytes` | 从 Path 读取字节，遵守当前 confined_reads 上下文。 |
| `memcore.read_file_text` | 从 Path 读取文本；默认编码 UTF-8，遵守 confined_reads。 |
| `memcore.read_jsonl` | 迭代返回（从 1 开始的行号、字典）；忽略空行，拒绝无效记录。 |
| `memcore.read_text` | 读取根目录内的相对证据路径，返回（原始字节的 SHA-256、UTF-8 文本）。 |
| `memcore.resolve_inside` | 解析根目录内的相对路径；拒绝绝对路径与符号链接越界。 |
| `memcore.sha256_bytes` | 返回字节的十六进制 SHA-256 摘要。 |
| `memcore.verify_entry` | 在根目录下核验 ManifestEntry 并返回验证后的引用。 |
| `memcore.walk_files` | 返回（目录、可修改的子目录列表、文件列表）；支持剪枝与 OSError 回调。限制读取时目录为 Path，普通 os.walk 时为字符串。 |

```python
append_jsonl(path: Path, record: dict) -> None
confined_reads(root: Path) -> Iterator[None]
confinement_root() -> Path | None
confinement_supported() -> bool
line_of(text: str, offset: int) -> int
load_json(data: str | bytes) -> Any
load_manifest(manifest: Path) -> dict[str, EvidenceSpan]
locate_quote(root: Path, relative: str, quote: str, **provenance: Any) -> EvidenceSpan
make_span(relative: str, digest: str, text: str, start: int, end: int, **provenance: Any) -> EvidenceSpan
open_text(path: Path, encoding: str='utf-8') -> AbstractContextManager[TextIO]
read_file_bytes(path: Path) -> bytes
read_file_text(path: Path, encoding: str='utf-8') -> str
read_jsonl(path: Path) -> Iterator[tuple[int, dict]]
read_text(root: Path, relative: str) -> tuple[str, str]
resolve_inside(root: Path, relative: str) -> Path
sha256_bytes(data: bytes) -> str
verify_entry(entry: ManifestEntry, root: Path) -> EvidenceSpan
walk_files(path: Path, onerror: Callable[[OSError], None] | None=None) -> Iterator[tuple[str | Path, list[str], list[str]]]
```

### `memfalsify`

| API | 用途 |
|---|---|
| `memfalsify.ATTENTION` | 严格审计计数的冻结状态集合：challenged、conflicted、candidate_counterevidence、disputed。 |
| `memfalsify.AuditReport` | 只读审计结果，包含发现、状态计数、跳过的证据及局限。 |
| `memfalsify.CascadeVerifier` | 构造参数为 first、second、accept_at=0.8；首个验证器置信度不足时升级。 |
| `memfalsify.Change` | 不可变比较结果：类型、新旧 Claim、相似度及移动标记。 |
| `memfalsify.Claim` | 解析后的记忆，包含稳定 ID、文本、路径、行号、章节及重复位置。 |
| `memfalsify.ClaimChange` | 可序列化的差异项，包含新旧 ClaimVersion 与可选审计状态。 |
| `memfalsify.ClaimHistory` | 当前记忆的引入提交、最后修改及可选的被替换版本。 |
| `memfalsify.ClaimVersion` | 某修订中的记忆文本与位置；of(claim) 从 Claim 构建。 |
| `memfalsify.CommitInfo` | 用于溯源的提交标识、时间、作者及主题。 |
| `memfalsify.DiffReport` | 修订比较及新增／修改记忆的审计；attention 统计严格状态。 |
| `memfalsify.DualVerifier` | 用 first 与 second 构造；不一致时返回 disputed 并保留双方意见。 |
| `memfalsify.EvidenceFinding` | 候选或人工确认的关系、理由、各验证器意见及证据引用。 |
| `memfalsify.Finding` | 单条记忆的状态、证据、问题及建议操作。 |
| `memfalsify.GitError` | HistoryError 子类，带经过清理的 summary 与底层诊断消息。 |
| `memfalsify.HistoryError` | Git 历史操作无效或不可用时的 ValueError。 |
| `memfalsify.HistoryReport` | 当前及已删除记忆的来源、已扫描提交和扫描局限。 |
| `memfalsify.Issue` | 措辞、时间、冲突或重复问题，包含类型与详情。 |
| `memfalsify.JevVerifier` | 构造参数为 model、可选 SDK client、threshold=0.5；仅构造时导入 typesafe_sdk。 |
| `memfalsify.Judgment` | 关系、理由、验证器名称、可选置信度及嵌套意见。 |
| `memfalsify.LLMVerifier` | 构造参数为 model、可选 client、effort="low"、fallbacks=None；仅构造时导入 anthropic。 |
| `memfalsify.LexicalVerifier` | 离线验证器；覆盖阈值 related=0.6、support=0.75。 |
| `memfalsify.Proposal` | 建议操作及原因；replacement 始终为 None。 |
| `memfalsify.Relation` | Literal 类型：supports、contradicts、unrelated、disputed。 |
| `memfalsify.RemovedClaim` | 已删除记忆版本及删除该记忆的提交。 |
| `memfalsify.Review` | 人工确认输入：记忆 ID、关系、路径、引用、审核人及可选备注。 |
| `memfalsify.Revision` | 修订标签、解析后的提交（工作区为 None）及读取的文件。 |
| `memfalsify.Rollback` | 补丁结果：字节或 None，以及消息；不应用补丁。 |
| `memfalsify.Skipped` | 被跳过的证据路径及原因。 |
| `memfalsify.Status` | 七种审计状态值的 Literal 类型。 |
| `memfalsify.Verifier` | 协议：name: str 及 judge(claim: str, passage: str) → Judgment。 |
| `memfalsify.VerifierError` | SDK、凭据不可用或模型判断失败时的 RuntimeError。 |
| `memfalsify.__version__` | 已安装发行包的版本字符串。 |
| `memfalsify.audit` | 解析记忆并用可选的证据与人工审核记录进行审计；不修改记忆。 |
| `memfalsify.audit_claims` | 审计已解析的记忆；only= 限定输出，但审核与冲突检查仍使用全部记忆。memory_paths 不作为证据。 |
| `memfalsify.claim_id` | 返回规范化文本的记忆 ID。 |
| `memfalsify.compare` | 按 ID、章节及词语相似度确定性比较两组 Claim。 |
| `memfalsify.diff_to_markdown` | 将 DiffReport 渲染为 Markdown。 |
| `memfalsify.history_to_markdown` | 将 HistoryReport 渲染为 Markdown。 |
| `memfalsify.load_reviews` | 根据记忆 ID 映射验证人工审核记录及其逐字引用；传入 skipped= 时，被输入路径策略排除的审核证据记录到该列表而不是抛出异常。 |
| `memfalsify.memory_diff` | 比较 base 与 head 或工作区，仅以当前证据审计新增／修改的记忆。 |
| `memfalsify.memory_history` | 沿 HEAD 第一父提交链溯源记忆；max_commits 默认 100。 |
| `memfalsify.parse_memory` | 返回（去重后的记忆、已读文件）；follow_links=True 跟随记忆目录内的本地 Markdown 链接。传入 skipped= 时，被输入路径策略排除的记忆文件或链接文件记录到该列表而不读取。 |
| `memfalsify.parse_text` | 解析给定文本与显示路径，返回（记忆、链接目标），不读取文件。 |
| `memfalsify.resolve_commit` | 在仓库目录内将经过验证的修订解析为提交 ID。 |
| `memfalsify.rollback_patch` | 生成恢复 to 修订中记忆措辞的补丁；接受旧 ID 或当前 ID。 |
| `memfalsify.to_markdown` | 将 AuditReport 渲染为 Markdown。 |

```python
audit(memory_file: Path, evidence_root: Path | None=None, reviews: Path | None=None, verifier: Verifier | None=None, as_of: date | None=None, stale_days: int=180, follow_links: bool=True, max_candidates: int=3, min_coverage: float=0.5, max_judged: int | None=None, exclude: Iterable[str]=()) -> AuditReport
audit_claims(claims: list[Claim], *, memory_file: str, files_read: list[str], memory_paths: Iterable[Path], evidence_root: Path | None=None, reviews: Path | None=None, verifier: Verifier | None=None, as_of: date | None=None, stale_days: int=180, max_candidates: int=3, min_coverage: float=0.5, max_judged: int | None=None, exclude: Iterable[str]=(), only: Iterable[str] | None=None) -> AuditReport
claim_id(text: str) -> str
compare(base: list[Claim], head: list[Claim]) -> list[Change]
diff_to_markdown(report: DiffReport) -> str
history_to_markdown(report: HistoryReport) -> str
load_reviews(path: Path, evidence_root: Path | None, claims: dict[str, Claim], *, skipped: list[Skipped] | None=None) -> dict[str, list[EvidenceFinding]]
memory_diff(memory_file: Path, base: str, head: str | None=None, *, follow_links: bool=True, evidence_root: Path | None=None, reviews: Path | None=None, verifier: Verifier | None=None, as_of: date | None=None, stale_days: int=180, min_coverage: float=0.5, max_judged: int | None=None, exclude: Iterable[str]=()) -> DiffReport
memory_history(memory_file: Path, max_commits: int=100, follow_links: bool=True) -> HistoryReport
parse_memory(memory_file: Path, follow_links: bool=True, *, skipped: list[Skipped] | None=None) -> tuple[list[Claim], list[Path]]
parse_text(text: str, display: str) -> tuple[list[Claim], list[str]]
resolve_commit(directory: Path, rev: str) -> str
rollback_patch(memory_file: Path, claim: str, to: str, follow_links: bool=True) -> Rollback
to_markdown(report: AuditReport) -> str
```

### `orgmemory`

| API | 用途 |
|---|---|
| `orgmemory.Action` | 八种事件操作的 Literal 类型。 |
| `orgmemory.AuditFinding` | 决策流程发现，包含类型、严重程度、议题、事件及引用。 |
| `orgmemory.AuditReport` | 只读决策流程报告，包含回放计数、发现及严重程度计数。 |
| `orgmemory.Capture` | 渲染后的源文件字节、相对路径及清单项；不是原始抓取字典。 |
| `orgmemory.CitedEvent` | 事件元数据及其精确证据引用。 |
| `orgmemory.Client` | 用 transport 与 token 构造；可选 max_wait=3600、sleep、clock、progress。get/get_json/pages 发起限制来源的请求。 |
| `orgmemory.Conflict` | 记录同刻决策、决策反转或无效替代。 |
| `orgmemory.Dataset` | 由事件、Policy 及已验证证据构造；load(directory=...) 读取并验证三个数据集文件。 |
| `orgmemory.Decision` | 授权决策、参与者角色、时间及证据引用。 |
| `orgmemory.Event` | 已验证的事件，包含带时区时间、参与者、操作、议题及证据 ID。 |
| `orgmemory.FetchProgress` | 可选的节流进度报告器，支持注入 clock 与 stream；update/rate_limit 报告进度。 |
| `orgmemory.GitHubError` | GitHub 永久失败，带可选 HTTP 状态。 |
| `orgmemory.GithubConfig` | github.json 标签到操作的配置；key(name) 不区分大小写查找标签。 |
| `orgmemory.PendingChange` | 被忽略的未授权决策尝试及原因。 |
| `orgmemory.PersistentTransport` | 可复用 HTTPS 传输／上下文管理器；timeout=30，支持注入 connection_factory/env，close() 释放资源。 |
| `orgmemory.Plan` | 纯转换结果，包含事件、抓取文件、引用、漂移、跳过项及计数。 |
| `orgmemory.Policy` | 参与者角色及操作权限；roles/allows/decision_roles 提供权限检查。 |
| `orgmemory.ReplayResult` | 议题状态及事件审计轨迹，可通过 as_of 限制截止时间。 |
| `orgmemory.Risk` | 风险事件、参与者及备注。 |
| `orgmemory.Stopped` | 暂时停止抓取；高层 sync 可能返回不完整的 Summary。 |
| `orgmemory.SubjectState` | 当前授权状态、风险、依赖、待批准项、决策及冲突。 |
| `orgmemory.Summary` | GitHub 导入的抓取／计划、追加计数、游标及完整性；lines() 返回显示文本。 |
| `orgmemory.TrailEntry` | 单个回放事件的结果及前后状态。 |
| `orgmemory.Transport` | 可调用类型，输入 URL 与请求头，返回（HTTP 状态、响应头、字节）。 |
| `orgmemory.__version__` | 已安装发行包的版本字符串。 |
| `orgmemory.audit` | 通过回放审计 Dataset；as_of/now 选择参考时间，stale_days 默认 14。 |
| `orgmemory.audit_markdown` | 将决策流程 AuditReport 渲染为 Markdown。 |
| `orgmemory.convert` | 将原始抓取字典、已有事件／证据 ID 及 GithubConfig 纯映射为 Plan；无 I/O。 |
| `orgmemory.explain` | 从 ReplayResult 渲染某个议题的状态与决策来源。 |
| `orgmemory.load_events` | 解析事件 JSONL，验证错误带行号。 |
| `orgmemory.replay` | 按权限策略确定性回放已验证 Dataset，可指定带时区的 as_of 截止时间。 |
| `orgmemory.run_convert` | 离线应用保存的抓取结果，仅新建；dry_run 预览而不写入。 |
| `orgmemory.run_sync` | 通过提供的 Client 抓取并导入 GitHub；支持 full/since/issues、save_capture 与 dry_run。 |
| `orgmemory.to_markdown` | 将 ReplayResult 渲染为 Markdown。 |
| `orgmemory.token_from_env` | 从 env 读取首个非空 GITHUB_TOKEN 或 GH_TOKEN；省略 env 时读取 os.environ。 |
| `orgmemory.urllib_transport` | 构建限制来源的传输；默认 opener 拒绝重定向，timeout=30。 |
| `orgmemory.validate` | 在完整日志中检查事件 ID 唯一性、证据引用及替代目标。 |

```python
audit(dataset: Dataset, as_of: Optional[datetime]=None, stale_days: int=14, now: Optional[datetime]=None) -> AuditReport
audit_markdown(report: AuditReport) -> str
convert(capture: dict, existing: dict[str, Event], evidence_ids: set[str], config: GithubConfig) -> Plan
explain(result: ReplayResult, subject: str) -> str
load_events(path: Path) -> list[Event]
replay(dataset: Dataset, as_of: datetime | None=None) -> ReplayResult
run_convert(dataset: Path, capture_path: Path, *, dry_run: bool=False, now: Callable[[], datetime] | None=None) -> Summary
run_sync(dataset: Path, repo: str, client: Client, *, full: bool=False, since: datetime | None=None, issues: list[int] | None=None, save_capture: Path | None=None, dry_run: bool=False, now: Callable[[], datetime]=_now) -> Summary
to_markdown(result: ReplayResult) -> str
token_from_env(env: Mapping[str, str] | None=None) -> str | None
urllib_transport(opener: urllib.request.OpenerDirector | None=None, timeout: float=30) -> Transport
validate(events: list[Event], evidence: dict[str, EvidenceSpan]) -> None
```

### `memlab_mcp`

| API | 用途 |
|---|---|
| `memlab_mcp.__version__` | 已安装发行包的版本字符串。 |
| `memlab_mcp.create_server` | 构建只读 MCPServer，root="."、allow_model_verifiers=False；不启动服务。要求支持 POSIX 限制读取。 |

```python
create_server(root: Path | str='.', allow_model_verifiers: bool=False) -> MCPServer
```

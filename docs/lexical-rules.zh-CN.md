# 词法验证器会标记什么

`lexical` 是默认验证器：离线、确定、不用模型。它每次读一条记忆和一段证据，比较其中的词语。本页把它的规则写全，方便你判断一个标记的含义，以及什么情况下不该指望它标记。一个标记的意思是“读一下这两段原文”。只有人的审核才能把一条记忆标记为 challenged 或 supported。

## 什么算一条记忆

记忆文件按 Markdown 读，一块一块地读，每一块再切成句子。

- 段落或列表项的多行读成一行，记忆的位置是它开始的那一行。上一行在句子中间停下时，下一行才算接着它：缩进在列表项下面，以逗号或虚词结尾，方括号、圆括号或代码片段没有闭合，或者长到明显是折行。只有一个词加句点的末行（`3.12.`）也算接着上一行。没有标记、也没有句号的短行（`Project: acme` 下面是 `Language: Python`）各算各的，以冒号结尾的短行、有硬换行的行也一样。没有句号、又都以大写字母开头的长行，各自算一句。
- `e.g.`、`i.e.`、`vs.`、`cf.` 这类缩写后面的句点不算句子结束；`etc.` 后面还有句子的部分、`no. 5` 也一样。证据文字也按这个规则切。
- 表格的一行算一条。表头和分隔行不算记忆。
- 这些不算记忆：代码块、注释、标题、只有 HTML 标签的行、标签里只包着一个词的（`<b>Note</b>`）、链接定义，以及以冒号结尾的标签：三个词以内（`Run ty:`），或者只有从句、没有主句（`When writing comments:`、`For a full build from the repository root:`）。带 `must`、`should`、`never`、`always` 或 `do not` 的行是指令，即使以冒号结尾也算（`Never use yarn:`）。
- `---` 上面的一行算标题，除非它以句点或分号结尾：那样它写的是句子，这些横线只是分隔线。`===` 上面的一行总是标题。
- 链接到本地 `.md` 文件的句子是指向记忆的，本身不算。以这种链接开头的一块是索引项，整块都不算。

这些规则宁可多留。中文一律保留，读起来像句子的也保留，比如 `Tests use pytest.` 或 `Prefer <Link> over <a>.`。列表里只写事实的项（`pnpm workspaces`）也保留，分号之间的每一段也都保留。一条记忆至少要有两个实词，这一点没有变，所以单独一个 `Python 3.12` 不算。

Markdown、MDX、reStructuredText 和纯文本格式的证据，折行也读成一行，所以记忆里折行的句子能对上文档里折行的句子，引文也可以跨行。Markdown 报告按行显示这样的引文；JSON 报告和标记下面的那一行保存它的原始字节。代码和数据文件仍然逐行读。

记忆的 ID 由文字算出，所以按常见宽度重新折行的段落，ID 几乎总是不变；修正一行的读法则会得到新 ID。指向旧 ID 的审核会让审计以 `unknown memory_id` 停下；如果是排除记录，则列在 `Skipped evidence` 里。重新折行并非没有风险，因为判断短行的含义要靠猜：以 `- ` 开头的行会读成列表项，大写字母前面的短行会读成单独一行。这个仓库的文档里有 79 个散文段落，重新折到 100、80、72 列时，分别有 1、1、2 段的 ID 变了，折到 60 列时有 10 段。常见原因是出现了不到 30 列的行，所以折得越窄，变的 ID 越多。

列表上方的标题不会改变列表项的读法：`## Do NOT` 下面的 `Use npm in this package.` 按写出来的样子比较，标题只显示在报告里它的旁边。在测过的仓库里，大约 3% 的记忆处在这样的标题或引导语下面。这种列表里，一项的第一句是被否定的，下一句却常常是普通陈述（`Use an optional group.`），把整个列表当成否定来读就会读错这些句子。

## 什么时候它认为一段证据可能与记忆矛盾

规则按下面的顺序执行，第一条命中的规则给出报告里的理由。

| 报告里的理由 | 规则 |
|---|---|
| `same setting max_steps: claim 100 vs source 500` | 两段文字声明同一个键时写了不同的值。 |
| `set differently timeout: claim 30 vs source 60` | 同上，但有一边只是设置这个键（`key=30`、`key = 30`、`--key 30`）。 |
| `opposing terms: claim 'approv…' vs source 'reject…'` | 主题相同的两段文字用了意思相反的词，且这些词不在名称或代码片段里。 |
| `different units: claim 2 minutes vs source 2 seconds` | 主题相同的两段文字给同一个数字配了不同的时间或容量单位。 |
| `different values: claim ['100'] vs source ['60']` | 几乎相同的两段文字在同一个词旁边写了不同的数字或日期，而且不是各自接着说自己的情形（`2 spaces in YAML files`、`4 spaces in Python files`）。 |
| `opposite modality: claim '所有' vs source '仅'` | 只用于中文：两边分别站在“所有/仅”或“必须/无需”的两端。 |
| `same subject, but only one side is negated` | 主题相同的两句话里，一边否定了另一边正面陈述的词。 |
| `source says what the claim names was removed` | 记忆的所有词（至少四个）都出现在证据里“is removed”或“was removed”（或 deleted、dropped、retired）之前。或者，记忆（至少四个词）提到了“was removed”之前的全部内容（至少三个词），还对它多说了些什么。记忆自己在说移除某物时，不算。 |

一段证据要共享记忆的大部分词才会被查看（一半才会被考虑，60% 才算相关）。例外是声明了同一个键的证据：不管其他词共享多少，都会比较。

### 具名的值

- 键要长得像标识符：`snake_case`、`kebab-case`、`camelCase`、`dotted.name`、`--flag`，或写在反引号里的名称。键按原样匹配，或忽略大小写匹配。`Note: 3 things` 不绑定任何东西。
- 这些写法会绑定键：`key: 100`、`key = 100`、`key=100`、`key (default: 100)`、`key: int = 180`、`key: float = Field(default=0.1, ...)`、`key defaults to 100`、`` `key` is 100 ``、`"key": 5`、`| key | 100 |`、`--flag 100`。
- 值可以是数字、布尔值和带引号的短字符串。`None` 和 `null` 表示这里没有值。`100`、`100.0`、`1_000` 按数字比较，`-1` 和 `1` 是两个值。`500ms` 和 `1s` 不比较，因为单位不做换算；而 `512MB` 和 `512GB`、`30m` 和 `30s` 是同样的数字配上同类的另一个单位，所以算不同。
- 在某处设置的值（`key=100`、`key = 100`、`--flag 100`）不和声明的默认值（`key (default: 3)`、`key: int = 3`）比较。一次调用传 100，说明不了默认值是多少。和其他写法比较时，只是设置的值分量也轻一些：这个理由排得靠后，来源不能是测试、示例、变更日志或源代码，证据也要算得上相关。
- 同一个键在一段文字里最多保留八个不同的值。比这更长的是日志或表格，不是一句陈述。

### 主题相同

两段文字互相覆盖对方足够多的实词，就算主题相同：否定要 60%，且至少共享三个；数字和单位要 50%，相反词要 40%，且至少共享两个。门槛不同，是因为一个 `not` 本身说明不了多少，同一个词旁边的数字说明得多一些，相反词又多一些。比较时既看整句，也逐个分句看，所以 `A, and only if B` 可以只凭 `A` 匹配。两段短文字除了相反词或 `not` 之外用词完全相同时，不管共享多少词，都算主题相同：`Telemetry is disabled.` 对 `Telemetry is enabled.`，`Redis is required.` 对 `Redis is not required.`。

否定词只管到它所在分句后面的三个词，`--no-cache`、`not_found` 这样的名称不算否定词。两边否定同一个词算一致。行首的 `Do not ...`、`Never ...` 和 `Skip ...` 是指令，不是对现状的陈述，所以只是用词相同的一行不构成矛盾。只有去掉 `not` 后说法几乎相同的行才算矛盾：互相覆盖 80%，至少共享两个词，而且那一行要用自己的词说出点什么，不能只有名称或以冒号结尾的标签，比如 `Do not use npm in this repository.` 对 `Use npm in this repository.`。这个门槛很严，所以 `Do not run migrations in production.` 对 `Run migrations in production after every deploy.` 会漏掉。句中带 `always`、`every` 或 `never` 的陈述，一个反例就能反驳，所以只要求证据覆盖记忆。

相反词指的是状态，比如 approve/reject、enable/disable、allow/block、include/exclude、public/private、required/optional、supported/deprecated、pass/fail、true/false、increase/decrease。add/remove、start/stop、open/close 不在其中，因为它们是先后发生的动作。

### 不算数的数字

issue 和 pull request 编号（`#7326`）；名称、路径和版本号里的数字（`40_process.js`、`pep-0723`、`vercel-ai-v2.0.6`）；时间和端口（`14:21`、`localhost:5420`）；`lines.length + 1` 这样的运算数；绑定在键上的数字，它们由具名值规则比较。`0723` 和 `723` 是同一个值，`1,000` 和 `1000` 也是。`18.x` 和 `20.x` 不同，而 `18.x` 包含 `18` 和 `18.20`。`100/min` 这样的速率按它的数字算。日期只和日期比较。

### 中文文本

含 CJK 字符的文本保留原来的按分句否定、相反词和情态规则。真实中文仓库没有标注数据可以用来调整，所以这些规则没有收紧。数字和单位规则同样适用。两个汉字之间的换行不算空格，记忆和证据都是这样，所以折到两行的一个词仍是一个词。以 `，` 或 `、` 结尾的行接着下一行。

## 证据所在的文件类型

| 文件 | 能构成矛盾的规则 |
|---|---|
| 测试：`test/`、`tests/`、`__tests__/` 下的代码和数据，`testing/`、`spec/`、`specs/`、`e2e/` 下的代码，以及 `test_*.py`、`*.test.ts` 等 | 都不能。测试只展示一个情形。 |
| 变更日志（`CHANGELOG*`、`changelog/`，文件名恰好是 `Releases.md`、`HISTORY.rst`、`NEWS`、`CHANGES.txt` 等） | 声明的值（`same setting`），以及说明某物已被移除。变更日志记录的是过去。 |
| 示例（`examples/`、`demos/`、`examples.md` 等） | 声明的值（`same setting`）；在代码里只算真正声明的值。 |
| 源代码（`.py`、`.ts`、`.rs`、`.go` 等） | 只有声明的值（`x: int = 5`、`X = 5`）。`x = 5` 只是众多赋值之一，`foo({ x: 5 })` 只是一个对象的字段，代码里的词语和其他数字大多是控制流和错误信息。 |
| 其他文件 | 全部规则。 |

这些文件仍然可以支持一条记忆。这一条只适用于词法验证器：模型验证器读同样的证据，自己判断。

类型由路径决定，所以文档不管放在哪里都有发言权：`tests/README.md`、`specs/rate-limits.md`、`spec/api.yaml`、`docs/testing/setup.md`、`docs/history-retention.md` 都按“其他文件”算。这样被放在一边的证据不会出现在报告里。想知道一个测试或一处调用对记忆说了什么，请用模型验证器，或者自己读那个文件。

## 扫描目录时不读的内容

扫描不把下面这些当作证据，并在报告的 *Skipped evidence* 里列出：

- 名为 `vendor`、`third_party`、`thirdparty`、`testdata`、`fixtures`、`__snapshots__` 的目录，每个列一次，不列里面的文件。
- `*.min.js`、`*.min.css`、`*.snap`、`*.svg`、`*.patch`、`*.lock`、`package-lock.json`、`pnpm-lock.yaml`、`npm-shrinkwrap.json`、`go.sum`。
- 超过 1,000 个字符的行：压缩过的代码、内嵌数据，也包括写在一行里的长段落。文件的其他部分照常读取。把段落折行就能读到。

这一条适用于所有验证器和所有会扫描目录的入口：带或不带文件名的 `memfalsify audit`、GitHub Action、网页和 `memlab-mcp`。路径相对于证据根目录，所以把 `--evidence` 指向这样的目录就会读取它。按文件名排除的文件没有这样的入口：`.patch`、`.lock`、`.svg`、`.snap` 不管根目录在哪里都不读，所以 `--evidence patches` 指向只有 `api.patch` 的目录时什么也找不到，并把这个文件列为已跳过。想让它被读取，就换个名字复制一份。审核可以引用证据根目录里的任何文件。

## 报告的顺序

同一状态内，记忆按最强的理由排序：具名值，然后是单位、数字、移除、只是设置的值、相反词、情态和否定，理由相同的按文件顺序。同一条记忆内，矛盾排在前面，也按这个顺序，然后是共享词最多的证据。同样的输入给出同样的顺序。

## 该多信任一个标记

在 15 个公开仓库的智能体文件上，这些规则把被标记的配对从 1,719 对降到 47 对，被标记的记忆从 794 条降到 29 条；仍被标记的标注配对有 16 对，其中 9 对是真的（按否定的抽样比例换算后是 0.33）。`same subject, but only one side is negated` 是最弱的一类：在那里读下来没有一个是真的。[真实仓库基准](../benchmarks/memfalsify/wild/README.zh-CN.md) 给出了数字，也说明了它们可能怎样误导；[`benchmarks/memfalsify/RESULTS.md`](../benchmarks/memfalsify/RESULTS.md) 是合成用例的结果。

现在标记本来就少，所以一份干净的报告说明不了太多。这个验证器仍然会漏掉：

- 换一种说法的表述。“Requires Python 3.9” 和 `requires-python = ">=3.10"` 没有共同的键。
- 标题写明已移除、下面逐行列出的条目：每一行是单独读的。
- 代码和指令里的大部分否定。把每个 `not` 都当成矛盾的旧规则，在真实仓库抽样的 260 对上全都错了，所以它现在很少触发。

这些情况请用[模型验证器](local-setup.zh-CN.md)。

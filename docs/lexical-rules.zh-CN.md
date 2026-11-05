# 词法验证器会标记什么

`lexical` 是默认验证器：离线、确定、不用模型。它每次读一条记忆和一段证据，比较其中的词语。本页把它的规则写全，方便你判断一个标记的含义，以及什么情况下不该指望它标记。一个标记的意思是“读一下这两段原文”。只有人的审核才能把一条记忆标记为 challenged 或 supported。

## 什么时候它认为一段证据可能与记忆矛盾

规则按下面的顺序执行，第一条命中的规则给出报告里的理由。

| 报告里的理由 | 规则 |
|---|---|
| `same setting max_steps: claim 100 vs source 500` | 两段文字给同一个键绑定了不同的值。 |
| `opposing terms: claim 'approv…' vs source 'reject…'` | 主题相同的两段文字用了意思相反的词，且这些词不在名称或代码片段里。 |
| `different units: claim 2 minutes vs source 2 seconds` | 主题相同的两段文字给同一个数字配了不同的时间或容量单位。 |
| `different values: claim ['100'] vs source ['60']` | 几乎相同的两段文字在同一个词旁边写了不同的数字或日期。 |
| `opposite modality: claim '所有' vs source '仅'` | 只用于中文：两边分别站在“所有/仅”或“必须/无需”的两端。 |
| `same subject, but only one side is negated` | 主题相同的两句话里，一边否定了另一边正面陈述的词。 |
| `source says what the claim names was removed` | 记忆的所有词（至少四个）都出现在证据里“was removed”（或 deleted、dropped、retired）之前。 |

一段证据要共享记忆的大部分词才会被查看（一半才会被考虑，60% 才算相关）。例外是绑定了同一个键的证据：不管其他词共享多少，都会比较。

### 具名的值

- 键要长得像标识符：`snake_case`、`kebab-case`、`camelCase`、`dotted.name`、`--flag`，或写在反引号里的名称。键按原样匹配，或忽略大小写匹配。`Note: 3 things` 不绑定任何东西。
- 这些写法会绑定键：`key: 100`、`key = 100`、`key=100`、`key (default: 100)`、`key: int = 180`、`key: float = Field(default=0.1, ...)`、`key defaults to 100`、`` `key` is 100 ``、`"key": 5`、`| key | 100 |`、`--flag 100`。
- 值可以是数字、布尔值和带引号的短字符串。`None` 和 `null` 表示这里没有值。`100`、`100.0`、`1_000` 按数字比较。`500ms` 和 `1s` 不比较，因为单位不做换算。
- 在某处设置的值（`key=100`、`key = 100`、`--flag 100`）不和声明的默认值（`key (default: 3)`、`key: int = 3`）比较。一次调用传 100，说明不了默认值是多少。

### 主题相同

两段文字互相覆盖对方足够多的实词，就算主题相同：否定要 60%，且至少共享三个；数字和单位要 50%，相反词要 40%，且至少共享两个。门槛不同，是因为一个 `not` 本身说明不了多少，同一个词旁边的数字说明得多一些，相反词又多一些。比较时既看整句，也逐个分句看，所以 `A, and only if B` 可以只凭 `A` 匹配。

否定词只管到它所在分句后面的三个词，`--no-cache`、`not_found` 这样的名称不算否定词。两边否定同一个词算一致。行首的 `Do not ...`、`Never ...` 和 `Skip ...` 是指令，不是对现状的陈述，所以不会构成矛盾。句中带 `always`、`every` 或 `never` 的陈述，一个反例就能反驳，所以只要求证据覆盖记忆。

相反词指的是状态：approve/reject、enable/disable、allow/block、include/exclude、public/private、required/optional、supported/deprecated、pass/fail、true/false、increase/decrease。add/remove、start/stop、open/close 不在其中，因为它们是先后发生的动作。

### 不算数的数字

issue 和 pull request 编号（`#7326`）；名称、路径和版本号里的数字（`40_process.js`、`pep-0723`、`vercel-ai-v2.0.6`）；时间和端口（`14:21`、`localhost:5420`）；`lines.length + 1` 这样的运算数；绑定在键上的数字，它们由具名值规则比较。`0723` 和 `723` 是同一个值。日期只和日期比较。

### 中文文本

含 CJK 字符的文本保留原来的按分句否定、相反词和情态规则。真实中文仓库没有标注数据可以用来调整，所以这些规则没有收紧。数字和单位规则同样适用。

## 证据所在的文件类型

| 文件 | 能构成矛盾的规则 |
|---|---|
| 测试（`tests/`、`test_*.py`、`*.test.ts` 等） | 都不能。测试只展示一个情形。 |
| 变更日志（`CHANGELOG*`、`changelog/`、`Releases.md` 等） | 具名的值，以及说明某物已被移除。变更日志记录的是过去。 |
| 示例（`examples/`、`demos/`、`examples.md` 等） | 具名的值。 |
| 源代码（`.py`、`.ts`、`.rs`、`.go` 等） | 具名的值、数字和单位。代码里的相反词和否定大多是控制流和错误信息。 |
| 其他文件 | 全部规则。 |

这些文件仍然可以支持一条记忆。这一条只适用于词法验证器：模型验证器读同样的证据，自己判断。

## 扫描目录时不读的内容

扫描不把下面这些当作证据，并在报告的 *Skipped evidence* 里列出：

- 名为 `vendor`、`third_party`、`thirdparty`、`testdata`、`fixtures`、`__snapshots__` 的目录，每个列一次，不列里面的文件。
- `*.min.js`、`*.min.css`、`*.snap`、`*.svg`、`*.patch`、`*.lock`、`package-lock.json`、`pnpm-lock.yaml`、`npm-shrinkwrap.json`、`go.sum`。
- 超过 1,000 个字符的行：压缩过的代码、内嵌数据，也包括写在一行里的长段落。文件的其他部分照常读取。把段落折行就能读到。

这一条适用于所有验证器和所有会扫描目录的入口：带或不带文件名的 `memfalsify audit`、GitHub Action、网页和 `memlab-mcp`。路径相对于证据根目录，所以把 `--evidence` 指向这样的目录就会读取它。审核可以引用证据根目录里的任何文件。

## 报告的顺序

同一状态内，记忆按最强的理由排序：具名值，然后是单位、数字、移除、相反词、情态和否定，理由相同的按文件顺序。同一条记忆内，矛盾排在前面，也按这个顺序，然后是共享词最多的证据。同样的输入给出同样的顺序。

## 该多信任一个标记

在 14 个公开仓库的智能体文件上，这些规则把被标记的配对从 1,371 对降到很小一部分，标注样本里剩下的大约一半是真的。[真实仓库基准](../benchmarks/memfalsify/wild/README.zh-CN.md) 给出了数字，也说明了它们可能怎样误导；[`benchmarks/memfalsify/RESULTS.md`](../benchmarks/memfalsify/RESULTS.md) 是合成用例的结果。

现在标记本来就少，所以一份干净的报告说明不了太多。这个验证器仍然会漏掉：

- 换一种说法的表述。“Requires Python 3.9” 和 `requires-python = ">=3.10"` 没有共同的键。
- 标题写明已移除、下面逐行列出的条目：每一行是单独读的。
- 代码和指令里的大部分否定。把每个 `not` 都当成矛盾的旧规则，在真实仓库上 99% 都是错的，所以它现在很少触发。

这些情况请用[模型验证器](local-setup.zh-CN.md)。

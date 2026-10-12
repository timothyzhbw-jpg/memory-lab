"""English and Simplified Chinese wording for the UI.

The page language is per request: ``?lang=zh`` shows Chinese, anything else English. ``_`` looks a message up
by its English text and fills ``{placeholders}``; ``n_`` picks the singular or plural English text by count
(Chinese has one form). Messages are plain text unless the caller escapes their values and uses them as HTML,
so no message holds markup of its own. File contents, names and library details are never translated.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

LANGS = ("en", "zh")
_LANG: ContextVar[str] = ContextVar("memlab_ui_lang", default="en")


def current() -> str:
    return _LANG.get()


@contextmanager
def language(lang: str) -> Iterator[None]:
    token = _LANG.set(lang if lang in LANGS else "en")
    try:
        yield
    finally:
        _LANG.reset(token)


# English messages a Chinese page asked for but ZH lacks; the tests render every page and expect none.
MISSING: set[str] = set()


def _translated(text: str) -> str:
    template = ZH.get(text)
    if template is None:
        MISSING.add(text)
        return text
    return template


def _(text: str, **values: object) -> str:
    template = _translated(text) if _LANG.get() == "zh" else text
    return template.format(**values) if values else template


def n_(count: int, one: str, many: str, **values: object) -> str:
    """``one`` or ``many`` by ``count``, with ``{n}`` set to it. The Chinese text is keyed by ``many``."""
    if _LANG.get() == "zh":
        template = _translated(many)
    else:
        template = one if count == 1 else many
    return template.format(n=count, **values)


ZH: dict[str, str] = {
    # shell
    "Home": "首页",
    "Check an agent file": "检查 Agent 文件",
    "Team decisions": "团队决策",
    "Main": "主导航",
    "Skip to content": "跳到正文",
    "Project folder": "项目文件夹",
    "Read-only: nothing here changes a file.": "只读：这里不会修改任何文件。",
    "Language": "语言",
    "Topics": "主题",
    "Back to the start": "回到首页",
    # home
    "Is your AI assistant's memory still true?": "你的 AI 助手记住的东西还对吗？",
    "AI coding assistants follow instruction and memory files such as CLAUDE.md, AGENTS.md and MEMORY.md. "
    "Memory Lab checks each statement in them against the rest of your project and shows the ones that may "
    "no longer be true; it can also show which decisions in a team's decision log really count. Nothing is "
    "changed and nothing leaves this computer.":
        "AI 编程助手会遵循 CLAUDE.md、AGENTS.md、MEMORY.md 这类指令与记忆文件。Memory Lab 把其中每一条陈述与"
        "项目里的其他文件对照，找出可能已经不再成立的内容；它还能显示团队决策日志里哪些决定真正有效。"
        "不会修改任何文件，也不会有任何内容离开这台电脑。",
    " Model checks are switched on for this session: they send text to an outside service, but only when you "
    "pick one under More options.":
        "本次会话已开启模型检查：只有在“更多选项”里选择它时，才会把文本发送给外部服务。",
    "Which file should I check?": "要检查哪个文件？",
    "Check": "检查",
    "Compared with the whole project. Nothing is changed and nothing leaves this computer.":
        "与整个项目对照。不会修改任何文件，也不会有任何内容离开这台电脑。",
    "No agent files here yet": "这里还没有 Agent 文件",
    "This folder has no CLAUDE.md, AGENTS.md, GEMINI.md, MEMORY.md or agent rule files. Start memlab-ui with "
    "--root set to a project that has one.":
        "这个文件夹里没有 CLAUDE.md、AGENTS.md、GEMINI.md、MEMORY.md 或 Agent 规则文件。请用 --root 指向一个"
        "包含这些文件的项目，重新启动 memlab-ui。",
    "Check {name}": "检查 {name}",
    "See team decisions": "查看团队决策",
    "Suggestions": "建议",
    "Start here": "从这里开始",
    "Pick a file.": "选一个文件。",
    "Choose the agent file you want to check.": "选择要检查的 Agent 文件。",
    "Press Check.": "点“检查”。",
    "Each statement is compared with your project.": "每一条陈述都会与项目对照。",
    "Look at what's flagged.": "看被标出的内容。",
    "Read the quotes side by side and decide.": "并排阅读两边的原文，再做决定。",
    "See which statements in CLAUDE.md, AGENTS.md or MEMORY.md your project still backs up.":
        "看看 CLAUDE.md、AGENTS.md 或 MEMORY.md 里哪些陈述仍有项目文件支持。",
    "See what was decided, who could decide it, and which messages did not count.":
        "看看决定了什么、谁有权决定，以及哪些消息不算数。",
    "Agent files in this project": "本项目中的 Agent 文件",
    "{n} file found by name; none of them was changed.": "按文件名找到 {n} 个文件；都没有被修改。",
    "{n} files found by name; none of them was changed.": "按文件名找到 {n} 个文件；都没有被修改。",
    "File": "文件",
    "Agent": "Agent",
    "Statements": "陈述",
    "Action": "操作",
    "can't read": "无法读取",
    "None found.": "没有找到。",
    "Agent memory": "Agent 记忆",
    # check: form
    "Pick a file and press Check. Each statement in it is compared with your project, and anything that seems "
    "to disagree is shown side by side.":
        "选一个文件，然后点“检查”。文件里的每条陈述都会与项目对照，看起来有出入的地方会并排显示。",
    "Agent file": "Agent 文件",
    "Choose a file": "选择文件",
    "Compare against": "对照范围",
    "The whole project (recommended)": "整个项目（推荐）",
    "Only {path}/": "仅 {path}/",
    "Only {path}": "仅 {path}",
    "More options": "更多选项",
    "Only statements changed since": "只看此后改动的陈述",
    "A git branch, tag or commit, such as main": "git 分支、标签或提交，例如 main",
    "\"Changed since\" works when the project folder is a git repository.":
        "“只看此后改动”需要项目文件夹是一个 git 仓库。",
    "Treat today as": "把今天当作",
    "Old dates in the file are judged from this day": "文件中的旧日期以这一天为准判断",
    "Reviews file": "审阅文件",
    "None": "不使用",
    "Reviews a person recorded; they must point into the folder you compare against":
        "由人记录的审阅；其中的引用必须位于对照范围内",
    "How to compare": "对照方式",
    "Model checks send your text to an outside service": "模型检查会把文本发送给外部服务",
    "Quick word match (offline, free)": "快速词语匹配（离线、免费）",
    "Claude (sends text to Anthropic; costs money)": "Claude（文本发送给 Anthropic，需付费）",
    "Jev (sends text to TypeSafe AI; costs money)": "Jev（文本发送给 TypeSafe AI，需付费）",
    "Claude and Jev (sends text to both; costs money)": "Claude 与 Jev（文本发送给两者，需付费）",
    "Jev, then Claude when unsure (sends text to both; costs money)":
        "先用 Jev，不确定时再用 Claude（文本发送给两者，需付费）",
    # check: errors
    "This check is switched off": "此检查已关闭",
    "We couldn't run this check": "无法完成这次检查",
    "Pick how to compare from the list.": "请从列表中选择对照方式。",
    "Model checks are switched off. They send your files' text to an outside service and cost money, so they "
    "work only when memlab-ui is started with --allow-model-verifiers.":
        "模型检查已关闭。它们会把文件文本发送给外部服务并产生费用，因此只有在用 --allow-model-verifiers "
        "启动 memlab-ui 时才可用。",
    "\"Treat today as\" must be a date like 2026-10-10.": "“把今天当作”必须是日期，例如 2026-10-10。",
    "\"Changed since\" works only when the project folder is a git repository.":
        "“只看此后改动”只在项目文件夹是 git 仓库时可用。",
    "\"Changed since\" must be a branch, tag or commit name that does not start with '-'.":
        "“只看此后改动”必须是不以“-”开头的分支、标签或提交名。",
    "The model check failed. Check that its optional SDK is installed, the API key is set and the service is "
    "reachable.":
        "模型检查失败。请确认已安装可选 SDK、已设置 API 密钥，并且服务可以访问。",
    # check: results
    "Possible conflict": "可能冲突",
    "Confirmed conflict": "已确认冲突",
    "Confirmed both ways": "两方面都有确认",
    "Checkers disagree": "检查器意见不一",
    "Confirmed by a reviewer": "已由审阅者确认",
    "Can't tell": "无法判断",
    "Look at these": "需要看一下",
    "Something in your project seems to disagree.": "项目里似乎有内容与之不符。",
    "Nothing in your project talks about it.": "项目里没有提到它。",
    "A reviewer confirmed the source is right: update or remove this line in {file}.":
        "审阅者已确认来源是对的：请更新或删除 {file} 中的这一行。",
    "Reviewers found sources on both sides: decide which one is right, then update {file}.":
        "审阅者在两边都找到了来源：请判断哪一方正确，再更新 {file}。",
    "If the source is right, update this line in {file}; if it is about something else, you can ignore this.":
        "如果来源是对的，请更新 {file} 中的这一行；如果它说的是别的事，可以忽略。",
    "The two checkers disagree about this source: read both and decide.":
        "两个检查器对这个来源意见不一：请阅读两边后自行判断。",
    "They use opposite words.": "两边用了意思相反的词。",
    "The numbers are different.": "两边的数字不同。",
    "One says it must (or always) happen, the other says it need not (or only sometimes).":
        "一边说必须（或总是）如此，另一边说不必（或只是有时）。",
    "Only one of them says \"not\".": "只有一边用了否定。",
    "{who} confirmed this in a review.": "{who} 在审阅中确认了这一点。",
    "A reviewer": "一位审阅者",
    "Why: {why}": "原因：{why}",
    "Your docs say": "你的文档写道",
    "Your project says": "你的项目写道",
    "Your agent file says": "你的 Agent 文件写道",
    "at {where}": "位于 {where}",
    "{n} more source": "另外 {n} 个来源",
    "{n} more sources": "另外 {n} 个来源",
    "Nothing in the folder you compared against mentions this. That means unchecked, not wrong.":
        "对照范围内没有提到这一点。这表示没有核实，并不表示错误。",
    "Statement ID {id}": "陈述 ID {id}",
    "Uses a word like \"always\" or \"never\"; rules like that often have exceptions.":
        "用了“总是”“从不”这类词；这样的规则常有例外。",
    "Uses a relative time such as \"next week\" without a date, so it goes stale silently.":
        "用了“下周”这类相对时间却没有日期，过时了也不会被发现。",
    "Mentions a date long ago; it may be out of date.": "提到的日期很久以前了；可能已经过时。",
    "Sounds like a guess (\"probably\", \"seems\").": "听起来像是猜测（“可能”“似乎”）。",
    "May disagree with another statement in the same file.": "可能与同一文件中的另一条陈述矛盾。",
    "Says the same thing as another statement.": "与另一条陈述重复。",
    "Summary": "概览",
    "Show the {n} other statement": "显示其余 {n} 条陈述",
    "Show the {n} other statements": "显示其余 {n} 条陈述",
    "{n} file was not read": "{n} 个文件未读取",
    "{n} files were not read": "{n} 个文件未读取",
    "Files left out of the comparison": "未参与对照的文件",
    "Why": "原因",
    "the whole project": "整个项目",
    "{n} statement": "{n} 条陈述",
    "{n} statements": "{n} 条陈述",
    "{n} linked file": "{n} 个链接文件",
    "{n} linked files": "{n} 个链接文件",
    "{statements} from {file}{linked}, compared with {against} · as of {date} · {method} · nothing was changed":
        "{file}{linked} 中的 {statements}，与{against}对照 · 基准日期 {date} · {method} · 未修改任何内容",
    " and {linked}": " 及 {linked}",
    "{n} thing in {name} may be out of date": "{name} 中有 {n} 处可能已过时",
    "{n} things in {name} may be out of date": "{name} 中有 {n} 处可能已过时",
    "No statements were found in this file.": "这个文件里没有找到陈述。",
    "About this check: the quick check compares words. It can miss a source that says the same thing in other "
    "words, and it can flag a sentence that only looks like a disagreement. A flag means \"read this\", not "
    "\"this is wrong\". Only a person can confirm a statement, by recording a review.":
        "关于这次检查：快速检查比较的是词语。它可能漏掉换了说法的来源，也可能标出只是看起来矛盾的句子。"
        "被标出意味着“请读一读”，而不是“这是错的”。只有人才能通过记录审阅来确认一条陈述。",
    "About this check: a model judged passages that share words with each statement. Models can be wrong, and "
    "passages that share few words were never shown to it. Only a person can confirm a statement, by recording "
    "a review.":
        "关于这次检查：模型只判断与陈述有共同词语的段落。模型可能出错，共同词语太少的段落不会交给它。"
        "只有人才能通过记录审阅来确认一条陈述。",
    "New": "新增",
    "Changed": "已改动",
    "Since {rev}: {added} added, {changed} changed, {removed} removed, {unchanged} unchanged (not checked again)"
    " · as of {date} · nothing was changed":
        "自 {rev} 以来：新增 {added}，改动 {changed}，删除 {removed}，未变 {unchanged}（未重新检查）"
        " · 基准日期 {date} · 未修改任何内容",
    "No statement was added or changed, so nothing was checked.": "没有新增或改动的陈述，所以没有检查任何内容。",
    "It used to say": "原来写的是",
    "Now it says": "现在写的是",
    "{n} changed statement: before and after": "{n} 条改动的陈述：改动前后",
    "{n} changed statements: before and after": "{n} 条改动的陈述：改动前后",
    "{n} statement was removed": "{n} 条陈述已删除",
    "{n} statements were removed": "{n} 条陈述已删除",
    "Removed": "已删除",
    "was at {where}": "原位于 {where}",
    # workspace messages
    "The agent file": "Agent 文件",
    "The folder to compare against": "对照文件夹",
    "The reviews file": "审阅文件",
    "The decision log": "决策日志",
    "{what} is empty": "{what}为空",
    "{what} contains a NUL character": "{what}包含 NUL 字符",
    "{what} is too long": "{what}太长",
    "{what} is outside the folder this page can read. Pick one from the list.":
        "{what}不在本页面可读取的文件夹内。请从列表中选择。",
    "{what} cannot be found inside the folder this page can read": "在本页面可读取的文件夹内找不到{what}",
    "{what} is not read: {reason}": "{what}不会被读取：{reason}",
    "{what} does not exist": "{what}不存在",
    "{what} is not a file": "{what}不是文件",
    "{what} is not a folder": "{what}不是文件夹",
    "it is in a hidden folder or is a hidden file": "它位于隐藏文件夹中，或本身是隐藏文件",
    "it is in a folder that is never read (node_modules, __pycache__, venv)":
        "它位于从不读取的文件夹中（node_modules、__pycache__、venv）",
    "it is a broken link": "它是一个失效的链接",
    "null character in path": "路径中含有空字符",
    "The decision log folder has no {name}": "决策日志文件夹中没有 {name}",
    "A reviews file needs a folder to compare against.": "审阅文件需要一个对照文件夹。",
    "No readable files are left in the folder you compare against.": "对照文件夹中没有可读取的文件。",
    "A quote, hash or file named in the reviews or the decision log does not match the files. Reviews must "
    "point into the folder you compare against.":
        "审阅或决策日志中的引文、哈希或文件与实际文件不符。审阅必须指向对照文件夹内的文件。",
    "A file has invalid data: check its required fields, types and values.":
        "某个文件的数据无效：请检查必填字段、类型和取值。",
    "A file named in the reviews or the decision log does not exist. Reviews must point into the folder you "
    "compare against.":
        "审阅或决策日志中提到的文件不存在。审阅必须指向对照文件夹内的文件。",
    "The files cannot be read. Check that they are readable, regular files.":
        "无法读取文件。请确认它们是可读的普通文件。",
    "A file has invalid JSON at line {line}, column {column}.": "某个文件第 {line} 行第 {column} 列的 JSON 无效。",
    "The input files are not valid. Check the memory, reviews and decision log files.":
        "输入文件无效。请检查记忆文件、审阅文件和决策日志。",
    # decisions
    "Approved": "已批准",
    "Rejected": "已拒绝",
    "Waiting": "待定",
    "asked for it": "提出了请求",
    "asked to reconsider": "请求重新考虑",
    "raised a risk": "提出了风险",
    "said it depends on": "说明它依赖于",
    "approved it": "批准了它",
    "rejected it": "拒绝了它",
    "withdrew the earlier decision": "撤回了先前的决定",
    "commented": "发表了评论",
    "approve": "批准",
    "reject": "拒绝",
    "withdraw decisions": "撤回决定",
    "make requests": "提出请求",
    "ask to reconsider": "请求重新考虑",
    "raise risks": "提出风险",
    "add dependencies": "添加依赖",
    "comment": "评论",
    "Approved by": "批准人",
    "Rejected by": "拒绝人",
    "The earlier decision was withdrawn by": "撤回先前决定的人",
    "Important": "重要",
    "Worth a look": "值得一看",
    "Minor": "次要",
    "Decided": "已决定",
    "Started waiting for a decision": "开始等待决定",
    "Got the decision it was waiting for": "等到了决定",
    "Someone asked to reconsider": "有人请求重新考虑",
    "The request to reconsider was answered": "重新考虑的请求已得到答复",
    "A risk was raised": "有人提出了风险",
    "A dependency was added": "添加了依赖",
    "Decisions conflicted": "决定之间有冲突",
    "Not counted: someone tried to decide without the right role": "不算数：有人在没有相应角色的情况下尝试决定",
    "{what} must be a date like 2026-10-07.": "{what}必须是日期，例如 2026-10-07。",
    "no role": "无角色",
    "Counted": "算数",
    "Noted": "已记录",
    "Not counted: it withdraws a decision that is no longer the current one": "不算数：它撤回的不是当前有效的决定",
    "Not counted: {roles} can't {action}": "不算数：{roles}无权{action}",
    "someone without a role": "没有角色的人",
    "{who} tried {times} to do something their role does not allow ({actions}) on {topics}. It was not counted.":
        "{who} 曾 {times} 尝试做其角色不允许的事（{actions}），涉及 {topics}。这些都不算数。",
    "{n} time": "{n} 次",
    "{n} times": "{n} 次",
    "Someone": "有人",
    "a topic": "某个主题",
    "Someone asked to reconsider {s}, and no decision has followed for more than {days} days.":
        "有人请求重新考虑 {s}，已超过 {days} 天仍未作出决定。",
    "{s} has been waiting for a decision for more than {days} days.": "{s} 已等待决定超过 {days} 天。",
    "The decision on {s} was reversed. Make sure everyone knows the latest answer.":
        "{s} 的决定被推翻了。请确认大家都知道最新的结论。",
    "Two different decisions on {s} have the same time, so the order in the file decided which counts.":
        "{s} 有两个时间相同的不同决定，由文件中的先后顺序决定哪个算数。",
    "Someone tried to withdraw a decision on {s} that was no longer the current one; nothing changed.":
        "有人尝试撤回 {s} 上一个已不再有效的决定；没有任何改变。",
    "A risk was raised after {s} was approved.": "{s} 批准后又有人提出了风险。",
    "{s} was approved while a risk was on record. Check that the risk was handled.":
        "{s} 在有风险记录的情况下获批。请确认风险已处理。",
    "{s} is approved, but something it depends on is not approved yet.": "{s} 已获批，但它依赖的内容尚未获批。",
    "{a} asked for {s} and then approved it themselves.": "{a} 先提出了 {s}，然后自己批准了它。",
    "{a} later lost the role that let them decide {s}. The decision still counts.":
        "{a} 后来失去了决定 {s} 的角色。该决定依然有效。",
    "{verb} {who} on {when}. It is based on:": "{verb} {who}，时间 {when}。依据：",
    "No one with the right role has decided yet.": "还没有具备相应角色的人作出决定。",
    "Waiting for": "等待",
    "a decision from {roles}.": "{roles} 的决定。",
    " Someone asked to reconsider.": "有人请求重新考虑。",
    "Open risks": "未解决的风险",
    "raised by {who}": "由 {who} 提出",
    "Depends on": "依赖",
    "Needs attention": "需要注意",
    "How we got here": "事情经过",
    "A filled dot counted toward the decision; a hollow dot was noted or not counted.":
        "实心点表示计入决定；空心点表示仅记录或不算数。",
    "{n} event in this period; {topics} changed.": "这段时间内有 {n} 个事件；{topics}有变化。",
    "{n} events in this period; {topics} changed.": "这段时间内有 {n} 个事件；{topics}有变化。",
    "{n} topic": "{n} 个主题",
    "{n} topics": "{n} 个主题",
    "Nothing changed in that time.": "这段时间内没有变化。",
    "new": "新的",
    "{who} {action} on {when}": "{who} 于 {when} {action}",
    "Decision log": "决策日志",
    "This folder": "此文件夹",
    "Choose a decision log": "选择决策日志",
    "A folder with events.jsonl, policy.json and evidence.jsonl": "包含 events.jsonl、policy.json 和 evidence.jsonl 的文件夹",
    "Open": "打开",
    "Look at it as it was at the end of": "查看截至这一天结束时的状态",
    "A day in UTC; leave empty for everything": "UTC 日期；留空表示全部",
    "After the end of": "从这一天结束后",
    "Up to the end of": "到这一天结束为止",
    "A day in UTC": "UTC 日期",
    "Show changes": "显示变化",
    "Pick a decision log to see what was really decided, who could decide it, and which messages did not count.":
        "选择一个决策日志，看看真正决定了什么、谁有权决定，以及哪些消息不算数。",
    "No decision log was found in this folder.": "这个文件夹里没有找到决策日志。",
    "We couldn't open this decision log": "无法打开这个决策日志",
    "The events or the policy are not valid.": "事件或策略无效。",
    "Unknown topic": "未知主题",
    "That topic is not in this decision log. Pick one from the list.": "这个决策日志中没有该主题。请从列表中选择。",
    "{n} thing needs attention.": "有 {n} 处需要注意。",
    "{n} things need attention.": "有 {n} 处需要注意。",
    "See what": "查看详情",
    "as it was at {when}": "截至 {when}",
    "up to the last event": "截至最后一个事件",
    "{events} on {topics}, {cutoff}. Only events from someone whose role allows the action change a status. "
    "Nothing was changed.":
        "{topics}共 {events}，{cutoff}。只有角色允许该操作的人发出的事件才会改变状态。未修改任何内容。",
    "{n} event": "{n} 个事件",
    "{n} events": "{n} 个事件",
    "This decision log has no events yet.": "这个决策日志还没有事件。",
    "These are leads for a person to look at, not verdicts.": "这些是供人查看的线索，不是结论。",
    "What changed between two dates": "两个日期之间的变化",
    "We couldn't compare these dates": "无法比较这两个日期",
    "Fill in both dates.": "请填写两个日期。",
    "The first date must not be after the second.": "第一个日期不能晚于第二个。",
    "\"Look at it as it was\"": "“查看截至”",
    "\"After the end of\"": "“从这一天结束后”",
    "\"Up to the end of\"": "“到这一天结束为止”",
    "{n} more quote": "另外 {n} 段引文",
    "{n} more quotes": "另外 {n} 段引文",
    # server messages
    "Not available": "无法访问",
    "This page answers only at http://127.0.0.1 or http://localhost on the port memlab-ui printed. Open that "
    "address instead.":
        "本页面只在 memlab-ui 打印出的端口上通过 http://127.0.0.1 或 http://localhost 访问。请改用那个地址。",
    "Not possible here": "这里不能这样做",
    "This page only shows things: it never changes or uploads anything.": "本页面只用于查看：不会修改或上传任何内容。",
    "Bad request": "请求无效",
    "Page not found": "找不到页面",
    "There is no such page. Use the links on the left.": "没有这个页面。请使用左侧的链接。",
    "Something went wrong": "出了点问题",
    "The page could not be built. Check that the files are readable and valid.":
        "无法生成页面。请确认文件可读且有效。",
    "The address is not a path on this server.": "这个地址不是本服务器上的路径。",
    "The address has too many fields.": "地址中的字段太多。",
    "Each field may appear only once in the address.": "每个字段在地址中只能出现一次。",
    "A field in the address is too long.": "地址中的某个字段太长。",
    "The request could not be understood.": "无法理解这个请求。",
    # added after the review
    "Similar text found": "找到相似说法",
    "Your project has a passage that looks alike. It's a word match, not proof.":
        "项目里有相似的句子。只是字词相近，不代表一定正确。",
    "A model judged a passage to agree. That is not proof.": "模型认为有段落与之一致，但这不代表一定正确。",
    "No contradictions found in {name}": "{name} 里没有发现矛盾",
    " \u00b7 {n} statement couldn't be checked": "，另有 {n} 条无法核对",
    " \u00b7 {n} statements couldn't be checked": "，另有 {n} 条无法核对",
    "A reviewer confirmed": "审阅者已确认",
    "Similar text": "相似的说法",
    "{n} more similar passage.": "另有 {n} 段相似的文字。",
    "{n} more similar passages.": "另有 {n} 段相似的文字。",
    "Statement ID": "陈述 ID",
    "Statement": "陈述",
    "Details": "详情",
    "Media file": "媒体文件",
    "Source": "来源",
    "Event ID": "事件 ID",
    "larger than 2 MB": "大于 2 MB",
    "not read: this page's reading budget was used up": "未读取：本页面的读取额度已用完",
    "not text (not UTF-8)": "不是文本（不是 UTF-8）",
    "an image, audio or video file without a transcript or OCR text next to it":
        "图片、音频或视频文件，旁边没有转写稿或 OCR 文本",
    "in a hidden folder, or a hidden file": "位于隐藏文件夹中，或本身是隐藏文件",
    "in a folder that is never read": "位于从不读取的文件夹中",
    "a linked folder, which is not followed": "链接的文件夹，不会跟随",
    "a link to outside the folder": "指向文件夹之外的链接",
    "a broken link": "失效的链接",
    "not a regular file": "不是普通文件",
    "an agent file that could not be read": "无法读取的 Agent 文件",
    "This file has more than {n} statements, too many to compare here. Check it without \"Changed since\", or "
    "run memfalsify diff.":
        "这个文件有超过 {n} 条陈述，太多了，无法在这里比较。请不用“只看此后改动”来检查，或运行 memfalsify diff。",
    "Only the first {checked} of {n} statements were checked.": "只检查了 {n} 条陈述中的前 {checked} 条。",
    "Something in the decision log needs a look.": "决策日志里有需要查看的地方。",
    "This request came from another website, so it was not run. Open the page from the address memlab-ui "
    "printed.":
        "这个请求来自其他网站，因此没有执行。请从 memlab-ui 打印出的地址打开本页面。",
    "Busy": "正忙",
    "Memory Lab is busy with other pages. Wait a moment and try again.": "Memory Lab 正在处理其他页面。请稍等后再试。",
    "This project is large, so only part of it was read. Results may be incomplete.":
        "这个项目很大，只读取了其中一部分。结果可能不完整。",
    "A file is too large for this page. Use the memfalsify or orgmemory command for it.":
        "有文件太大，无法在本页面读取。请改用 memfalsify 或 orgmemory 命令。",
    # round 3
    "review records": "审阅记录",
    "decision log": "决策日志",
    "report generated by Memory Lab": "Memory Lab 生成的报告",
    "Only the first {checked} statements were checked: the file has more than {n}.":
        "只检查了前 {checked} 条陈述：这个文件的陈述超过 {n} 条。",
    "This check uses a model, which sends your text to an outside service and costs money. Press Check to run "
    "it.":
        "这次检查使用模型，会把文本发送给外部服务并产生费用。点“检查”开始运行。",
}

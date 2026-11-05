# Security policy

Please report security problems privately, not in a public issue.

中文用户可以直接用中文写报告，同样请走下面的私密渠道，不要开公开 issue。

## How to report

Use GitHub's private reporting: [open a private report](https://github.com/timothyzhbw-jpg/memory-lab/security/advisories/new). Only you and the repository's maintainers can read it. If that page is not available to you, open an issue that asks for a private contact and leaves the details out.

Please include:

- the package and version (`pip list` shows it) or the commit, and your operating system;
- the commands and the smallest synthetic input that show the problem;
- what an attacker gets from it, and what they need first, such as write access to an evidence folder or the ability to open a pull request.

Leave real secrets and private evidence out of the report. A made-up key or file shows the same thing.

Memory Lab is a research project with one maintainer, so replies are best effort and come with no promised time. Please allow time for a fix before you publish details, and say so if you would like to be credited.

## Supported versions

Fixes land on `main` and ship in the next release. Older releases get no backports.

A security fix comes with a regression test that replays the attack, and an entry under "Security" in [CHANGELOG.md](CHANGELOG.md).

## What counts

Each of these is something the docs promise, so a way around one is a security report:

- **Reading out of bounds.** A command reads, or quotes in a report, a file outside the directory it was given, through a symlink, `..` or a crafted path. This includes memlab-mcp and memlab-ui with `--root`.
- **Untrusted text that acts.** Text from a memory file, evidence, a decision log or a GitHub or Jira capture reaches a terminal, an Action log or a report as anything more than inert text: terminal escapes, bidi or zero-width characters that hide content, Markdown or HTML that runs or misleads, GitHub workflow commands.
- **Credentials.** An API key or token appears in output, a report, a log or a diagnostic, or goes anywhere other than the configured endpoint.
- **Writes the docs rule out.** MemFalsify changes a memory or evidence file, or `orgmemory replay` or `orgmemory audit` changes anything.
- **Authority.** An event from an actor the policy gives no permission changes orgmemory's current state.
- **The GitHub Action.** An input injects a shell command, code from the audited repository runs, or a hash-checked install can be bypassed.
- **The local web page.** A website or another user of the computer starts a model check on memlab-ui without the key it prints in the terminal.

## What is not a security report

- **A false flag or a missed contradiction.** Lexical and model candidates are leads, not findings. Please use the [issue form](https://github.com/timothyzhbw-jpg/memory-lab/issues/new?template=false_flag.yml).
- **A limit the docs already state.** [Trust boundaries and limitations](README.md#trust-boundaries-and-limitations) lists them: for example, actor IDs and `policy.json` are trusted input, and a hard link cannot be told from the original file.
- **A model verifier sending text to its provider.** That is what the option does, and it is off by default.
- **Evidence text that sways a model's verdict.** A verdict stays a lead until a person records a review in `reviews.jsonl`. It becomes a security report when the text makes the tool do more than change a candidate, such as leaking a key or reading another file.
- **memlab-ui's work limits.** As [docs/ui.md](docs/ui.md) says, they are best-effort robustness against your own project's content, not a security boundary.

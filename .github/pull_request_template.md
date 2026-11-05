<!-- 中文用户可以直接用中文写。Feel free to write in Chinese. CONTRIBUTING.md explains the checks below. -->

## What changed and why

## How you checked it

- [ ] `python3 -m pytest` passes.
- [ ] If this writes files or changes CLI output: `python3 -m pytest --simulate-windows-newlines` passes.
- [ ] If this changes docs or `AGENTS.md`: the strict dogfood audit passes.
- [ ] If users can see this: `CHANGELOG.md` is updated, and both languages of any doc pair.
- [ ] New fixtures are synthetic, with no private evidence or credentials.

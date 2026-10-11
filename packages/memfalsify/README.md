# memfalsify

Part of [Memory Lab](https://github.com/timothyzhbw-jpg/memory-lab#readme). The `memfalsify` command
audits an agent memory file (`MEMORY.md`, `CLAUDE.md`, `AGENTS.md`, ...) against a directory of evidence,
and for memory kept in git shows what a revision changed (`diff`), where each claim came from
(`history`) and a patch to restore a claim (`rollback`). It never modifies memory. See the root README
for usage and the [changelog](https://github.com/timothyzhbw-jpg/memory-lab/blob/main/CHANGELOG.md) for
changes.

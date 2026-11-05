# MemFalsify claim history

- Memory: `MEMORY.md` (1 file(s): `MEMORY.md`)
- Scanned: 4 commit(s) on the first-parent line of HEAD (`b17a3549d0ee`), back to the first one that touched these files, plus the working tree
- Mode: **read_only** (nothing was modified)

## Current claims (4)

### `m-4966c315`

> Refund requests over 500 dollars need a second reviewer.

`MEMORY.md:3` · `Memory`

- Introduced: `5e952cedae02` 2026-10-05 · lin · Remember the refund rule (approved-by: zhou)
- Last changed: same as introduced

### `m-f0a6035e`

> The main database runs PostgreSQL 15.

`MEMORY.md:4` · `Memory`

- Introduced: `66b9cc9e87db` 2026-10-06 · lin · Remember the database version (approved-by: zhou)
- Last changed: same as introduced

### `m-9efe3be5`

> The public API rate limit is 100 requests per minute.

`MEMORY.md:5` · `Memory`

- Introduced: `7ed0c4c7f4b1` 2026-10-07 · agent-7 · Remember the API rate limit (approved-by: wu)
- Last changed: same as introduced

### `m-314c50bc`

> The nightly export job finishes before 04:00 UTC.

`MEMORY.md:6` · `Memory`

- Introduced: `ca1ca91a0789` 2026-10-08 · agent-7 · Remember when the nightly export finishes
- Last changed: same as introduced

## Limitations

- History follows the first-parent line of HEAD: changes merged with a merge commit are attributed to it.
- Commits are compared as whole snapshots of the memory file and the files it links to; a renamed memory file is not followed, so its claims appear in the commit that created the new name.
- Claims are identified by ID (normalized text); an edit is followed to its earlier version only when \`diff\` would pair them (same file and section, term similarity at least 0.5).

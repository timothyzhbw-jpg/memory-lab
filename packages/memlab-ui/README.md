# memlab-ui

Part of [Memory Lab](https://github.com/timothyzhbw-jpg/memory-lab#readme). A local, read-only web page for
trying MemFalsify and Organizational Memory without learning the command line:

```bash
memlab-ui --root /path/to/your/project
```

Then open the address it prints (`http://127.0.0.1:8765/` by default). The page checks an agent instruction or
memory file against the project and shows a team's decision log, and it never changes a file. It serves only
on `127.0.0.1`, refuses paths outside `--root` (files hard-linked into it are read like any other), and keeps
model verifiers off unless started with
`--allow-model-verifiers`. See [docs/ui.md](https://github.com/timothyzhbw-jpg/memory-lab/blob/main/docs/ui.md)
for the pages and the safety rules, and the
[changelog](https://github.com/timothyzhbw-jpg/memory-lab/blob/main/CHANGELOG.md) for changes.

The typed public Python API is documented in the
[API reference](https://github.com/timothyzhbw-jpg/memory-lab/blob/main/docs/api.md).
Import library symbols from the package root; `__all__` defines the supported surface.

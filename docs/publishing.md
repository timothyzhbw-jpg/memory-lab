# Publishing to PyPI

English | [中文](publishing.zh-CN.md)

Publishing is one manual workflow, [`.github/workflows/publish.yml`](../.github/workflows/publish.yml). It uses PyPI
[Trusted Publishing](https://docs.pypi.org/trusted-publishers/): PyPI trusts this repository's workflow directly, so
no password or API token is stored anywhere. Steps 1 to 3 are done once, by the repository owner, because they need
his PyPI account. Step 4 is the button.

## Before you start: the name `memory-lab`

PyPI refuses a new name that equals an existing one after it drops `-`, `_` and `.` and treats `l` and `i` as `1` and
`o` as `0`. The project [`memorylab`](https://pypi.org/project/memorylab/) exists (one release, 0.0.1, March 2025), so
for `memory-lab` the form in step 3 will answer "This project name is too similar to an existing project". This comes
from PyPI's source code and its project list as of 2026-11-04; nobody has tried the form, because that needs the
account. `memlab-core`, `memfalsify`, `orgmemory`, `memlab-mcp` and `memlab-ui` have no such collision. TestPyPI has
no `memorylab`, so a dry run there succeeds and says nothing about this.

Pick one before step 3:

- **Publish five packages and leave `memory-lab` out.** Choose `stop-after: memlab-ui` in step 4. Nothing else
  changes. The install line becomes `pip install memfalsify orgmemory`, and `memlab-ui` and `memlab-mcp` install by
  their own names.
- **Rename the meta-distribution.** `memlab` has no collision as of 2026-11-04. Change `name` in
  `packages/memory-lab/pyproject.toml`, then the name in that directory's README, `scripts/dists.py`,
  `scripts/rehearse_install.py`, `publish.yml`, this page and `tests/test_packaging.py`;
  `tests/test_publish_workflow.py` fails on any place you miss.
- **Ask for the name.** Write to the owner of `memorylab`, or file a [PEP 541](https://peps.python.org/pep-0541/)
  request at [pypi/support](https://github.com/pypi/support). This takes weeks or more, and the outcome is theirs to
  decide. The first option works in the meantime.

## 1. PyPI account (once)

1. Create an account at <https://pypi.org/account/register/> and confirm the email address. PyPI accepts a pending
   publisher only from a verified address.
2. Open <https://pypi.org/manage/account/two-factor/> and add an authenticator app or a security key. PyPI then shows
   recovery codes: save them.

## 2. GitHub environment (once)

Open <https://github.com/timothyzhbw-jpg/memory-lab/settings/environments> and press **New environment**.

1. Name: `pypi`. Press **Configure environment**.
2. Under **Deployment branches and tags**, choose **Selected branches and tags**, then **Add deployment branch or tag
   rule**, and enter `main`.
3. Optional: tick **Required reviewers** and add yourself. Each upload then waits for your approval, so you can read
   the list of built files first.
4. Press **Save protection rules**.

## 3. Pending publishers on PyPI

A pending publisher tells PyPI that this workflow may create the project on its first upload. Open
<https://pypi.org/manage/account/publishing/>, go to **Add a new pending publisher**, and choose the **GitHub** tab.
Fill in the form once per project and press **Add**:

| Field | Value |
|---|---|
| Project Name | the project, from the rounds below |
| Owner | `timothyzhbw-jpg` |
| Repository name | `memory-lab` |
| Workflow name | `publish.yml` |
| Environment name | `pypi` |

PyPI allows three pending publishers per account at a time, and each one stops counting once it has been used. So the
first publication takes two rounds:

- Round 1, now: `memlab-core`, `memfalsify`, `orgmemory`.
- Round 2, after the first run in step 4: `memlab-mcp`, `memlab-ui`, `memory-lab` (see the note on that name above).

A pending publisher does not reserve the name, so go on to step 4 in the same sitting.

## 4. The button

Open <https://github.com/timothyzhbw-jpg/memory-lab/actions/workflows/publish.yml> and press **Run workflow**.

| Field | First run | Second run (after round 2) | Later releases |
|---|---|---|---|
| Use workflow from | `main` | `main` | `main` |
| confirm | the version in `packages/memory-lab/pyproject.toml` | the same | the new version there |
| target | `pypi` | `pypi` | `pypi` |
| stop-after | `orgmemory` | `memory-lab`, or `memlab-ui` if you left that name out | `memory-lab` |

Press the green **Run workflow**. The run then does this:

1. **Build and check every package** builds an sdist and a wheel of all six packages, runs `twine check --strict`, and
   compares the files with this checkout and with your `confirm` value. A `confirm` that differs from the built
   memory-lab version stops the run here, before any upload. The job's summary lists each file with its SHA-256.
2. If you added a required reviewer, press **Review deployments**, then **Approve and deploy**.
3. **Upload to PyPI** uploads in dependency order: `memlab-core`, `memfalsify`, `orgmemory`, `memlab-mcp`, `memlab-ui`,
   `memory-lab`, stopping after the package you chose.

After the first run, add round 2 and press the button again. The three packages from the first run are skipped,
because a version already on the index is skipped and never replaced.

## 5. Check the result

```bash
python3 -m venv /tmp/memlab-check
/tmp/memlab-check/bin/pip install memfalsify orgmemory
/tmp/memlab-check/bin/pip index versions memfalsify
/tmp/memlab-check/bin/memfalsify --help
/tmp/memlab-check/bin/orgmemory --help
```

Then open <https://pypi.org/project/memfalsify/> and the other project pages. The README should render, and the
sidebar should list the repository under **Verified details** and name `publish.yml` under **Provenance**. Once
`memory-lab` is on PyPI, also try `pip install "memory-lab[ui]"` and `memlab-ui --help`.

Last, change the Install section of the root README to the published form.

## Dry runs

**Build only.** Choose `target: build-only`, from any branch. It needs no account: it does everything in the build job
above and uploads nothing to an index. The files stay on the run's page as the artifact `dists`. The same check runs
on your machine, including a clean install from the wheels:

```bash
python3 scripts/rehearse_install.py
```

CI runs this on every push (the `packaging` job).

**TestPyPI.** A separate site with separate accounts. It proves the workflow, the publisher settings and the
attestations end to end. It does not show whether PyPI will accept a name.

1. Create an account and 2FA at <https://test.pypi.org/account/register/>.
2. Create a second GitHub environment named `testpypi`, as in step 2, leaving out the branch rule so that you can
   rehearse from a branch.
3. Add the pending publishers at <https://test.pypi.org/manage/account/publishing/>: the same fields and rounds as
   step 3, with Environment name `testpypi`.
4. Press the button with `target: testpypi`. The first run also takes `stop-after: orgmemory`.
5. Install from it:

```bash
python3 -m venv /tmp/memlab-testpypi
/tmp/memlab-testpypi/bin/pip install --index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ memory-lab
/tmp/memlab-testpypi/bin/memfalsify --help
```

TestPyPI keeps versions too: a second run of the same version is skipped, so try a changed build under a new version.

## Later releases

1. Bump the versions that changed and move the pins between packages (each pin admits one minor version; see the top
   of [CHANGELOG.md](../CHANGELOG.md)). `memory-lab` gets a new version every release, because its version names the
   release.
2. Merge to `main` and wait for CI.
3. Press the button with the new version, `target: pypi` and `stop-after: memory-lab`.

## When something goes wrong

| What you see | What it means |
|---|---|
| `the confirm input differs from the built memory-lab version` | You typed another version, or `main` holds a different one. Nothing was uploaded. The message names the built version. |
| `target pypi works from main only` | **Use workflow from** pointed at another branch or tag. Nothing was uploaded. |
| `invalid-publisher` in an upload step | PyPI has no publisher matching this run. Compare the four values in step 3 for that package with the claims the step prints, or add the package's publisher if its round has not come yet. |
| `This project name is too similar to an existing project` on the PyPI form | See [the name `memory-lab`](#before-you-start-the-name-memory-lab). |
| A run stops halfway | The packages before that step are on PyPI and stay there. Fix the cause and press the button again: they are skipped. |
| A published version is broken | Files on PyPI cannot be replaced or reused. Use **Yank** on the release's page in the project's settings on PyPI, fix it, and publish a new version. |

## What keeps this safe

- The workflow's only trigger is `workflow_dispatch`; a push, a tag or a release never starts it.
- The upload jobs hold the only `id-token: write` permission and run nothing from the repository: they unpack the files
  the build job checked and hand them to `pypa/gh-action-pypi-publish`, pinned by commit SHA like every other action.
- The build tools come from the hash-locked `scripts/publish-requirements.txt`, including the build backend.
- `tests/test_publish_workflow.py` and `tests/test_action.py` check all of this, on every push.

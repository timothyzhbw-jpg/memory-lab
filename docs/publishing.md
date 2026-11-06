# Publishing to PyPI

English | [中文](publishing.zh-CN.md)

Publishing is one manual workflow, [`.github/workflows/publish.yml`](../.github/workflows/publish.yml). It uses PyPI
[Trusted Publishing](https://docs.pypi.org/trusted-publishers/): PyPI trusts this repository's workflow directly, so
no password or API token is stored anywhere. Steps 1 to 3 are done once, by the repository owner, because they need
the owner's PyPI account. After that, each release is one press of the button in step 4.

## The name `memlab`

The package that installs everything is called `memlab`, not `memory-lab`. PyPI refuses a new name that equals an
existing one after it drops `-`, `_` and `.` and treats `l` and `i` as `1` and `o` as `0`, and the project
[`memorylab`](https://pypi.org/project/memorylab/) exists (one release, 0.0.1, March 2025). This comes from PyPI's
source code and its project list as of 2026-11-04; nobody has tried the form with that name, because that needs the
account. `memlab`, `memlab-core`, `memfalsify`, `orgmemory`, `memlab-mcp` and `memlab-ui` have no such collision.

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

## 3. First publication: six rounds (once)

A pending publisher tells PyPI that this workflow may create a project on its first upload. PyPI holds one pending
publisher per combination of owner, repository, workflow and environment, and all six projects share one combination.
So you add one, use it, and only then add the next: it becomes a normal publisher when used, and that one publisher
then serves every project it created.

For each round below:

1. Open <https://pypi.org/manage/account/publishing/>, go to **Add a new pending publisher** and choose the **GitHub**
   tab. Fill in the form and press **Add**:

   | Field | Value |
   |---|---|
   | Project Name | the project of this round |
   | Owner | `timothyzhbw-jpg` |
   | Repository name | `memory-lab` |
   | Workflow name | `publish.yml` |
   | Environment name | `pypi` |

2. Press the button (step 4) with `stop-after` set to that same project.
3. Wait for the run to turn green. Packages from earlier rounds are skipped, because a version already on the index is
   skipped and never replaced.

| Round | Project Name | stop-after |
|---|---|---|
| 1 | `memlab-core` | `memlab-core` |
| 2 | `memfalsify` | `memfalsify` |
| 3 | `orgmemory` | `orgmemory` |
| 4 | `memlab-mcp` | `memlab-mcp` |
| 5 | `memlab-ui` | `memlab-ui` |
| 6 | `memlab` | `memlab` |

Round 6 needs the decision above about that name. A pending publisher does not reserve a name, so go straight from
adding it to pressing the button.

## 4. The button

Open <https://github.com/timothyzhbw-jpg/memory-lab/actions/workflows/publish.yml> and press **Run workflow**.

| Field | Value |
|---|---|
| Use workflow from | `main` |
| confirm | the version in `packages/memlab/pyproject.toml` |
| target | `pypi` |
| stop-after | during step 3, the project of the round. Later, `memlab` |

Press the green **Run workflow**. The run then does this:

1. **Build and check every package** builds an sdist and a wheel of all six packages, runs `twine check --strict`, and
   compares the files with this checkout and with your `confirm` value. A `confirm` that differs from the built
   memlab version stops the run here, before any upload. The job's summary lists each file with its SHA-256.
2. If you added a required reviewer, press **Review deployments**, then **Approve and deploy**.
3. **Upload to PyPI** uploads in dependency order: `memlab-core`, `memfalsify`, `orgmemory`, `memlab-mcp`, `memlab-ui`,
   `memlab`, stopping after the package you chose.

## 5. Check the result

```bash
python3 -m venv /tmp/memlab-check
/tmp/memlab-check/bin/pip install memfalsify orgmemory
/tmp/memlab-check/bin/pip index versions memfalsify
/tmp/memlab-check/bin/memfalsify --help
/tmp/memlab-check/bin/orgmemory --help
```

Then open <https://pypi.org/project/memfalsify/> and the other project pages. The README should render, and the
sidebar should list the repository under **Verified details**. Under **Download files**, **View provenance details**
should name `publish.yml` for each file. Once `memlab` is on PyPI, also try `pip install "memlab[ui]"` and `memlab-ui --help`.

Last, change the Install section of the root README to the published form.

## Dry runs

**Build only.** Choose `target: build-only`, from any branch. It needs no account: it does everything in the build job
above and uploads nothing to an index. The files stay on the run's page as the artifact `dists`. The same check runs
on your machine, including a clean install from the wheels:

```bash
python3 scripts/rehearse_install.py
```

On macOS, start it from Python 3.11 or newer: one of the locked tools has no macOS wheel for 3.10. `--python` picks
another version for the install itself. CI runs this on every push (the `packaging` job).

**TestPyPI.** A separate site with separate accounts. It proves the workflow, the publisher settings and the
attestations end to end. It does not show whether PyPI will accept a name.

1. Create an account and 2FA at <https://test.pypi.org/account/register/>.
2. Create a second GitHub environment named `testpypi`, as in step 2, leaving out the branch rule so that you can
   rehearse from a branch.
3. Go through the rounds of step 3 at <https://test.pypi.org/manage/account/publishing/>, with Environment name
   `testpypi`, and press the button with `target: testpypi`.
4. Install from it:

```bash
python3 -m venv /tmp/memlab-testpypi
/tmp/memlab-testpypi/bin/pip install --index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ memlab
/tmp/memlab-testpypi/bin/memfalsify --help
```

TestPyPI keeps versions too: a second run of the same version is skipped, so try a changed build under a new version.

## Later releases

1. Bump the versions that changed and move the pins between packages (each pin admits one minor version; see the top
   of [CHANGELOG.md](../CHANGELOG.md)). `memlab` gets a new version every release, because its version names the
   release.
2. Merge to `main` and wait for CI.
3. Press the button with the new version, `target: pypi` and `stop-after: memlab`. There is no form to fill in:
   the publisher from step 3 serves all the projects.

## When something goes wrong

| What you see | What it means |
|---|---|
| `the confirm input differs from the built memlab version` | You typed another version, or `main` holds a different one. Nothing was uploaded. The message names the built version. |
| `target pypi works from main only` | **Use workflow from** pointed at another branch or tag. Nothing was uploaded. |
| `This project name is too similar to an existing project` on the PyPI form | See [the name `memlab`](#the-name-memlab). |
| `already been registered for a different project name` on the PyPI form | The previous round's pending publisher has not been used yet. Press the button for it first, or delete it on the same page. |
| `Non-user identities cannot create new projects` in an upload step | The run reached a project whose pending publisher you have not added yet, usually because `stop-after` was left past the current round. Everything before that step is fine. Add the next publisher, or lower `stop-after`, and press again. |
| `invalid-publisher` in an upload step | PyPI has no publisher matching this run. Compare the four values you entered in step 3 with the claims the step prints. |
| A run stops halfway | The packages before that step are on PyPI and stay there. Fix the cause and press the button again: they are skipped. |
| A published version is broken | Files on PyPI cannot be replaced or reused. Use **Yank** on the release's page in the project's settings on PyPI, fix it, and publish a new version. |

## What keeps this safe

- The workflow's only trigger is `workflow_dispatch`; a push, a tag or a release never starts it.
- The upload jobs hold the only `id-token: write` permission and run nothing from the repository: they unpack the files
  the build job checked and hand them to `pypa/gh-action-pypi-publish`, pinned by commit SHA like every other action.
- The build tools come from the hash-locked `scripts/publish-requirements.txt`, including the build backend.
- `tests/test_publish_workflow.py` and `tests/test_action.py` check all of this, on every push.

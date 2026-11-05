# 发布到 PyPI

[English](publishing.md) | 中文

发布只有一个手动运行的工作流：[`.github/workflows/publish.yml`](../.github/workflows/publish.yml)。它使用 PyPI 的
[Trusted Publishing](https://docs.pypi.org/trusted-publishers/)：PyPI 直接信任本仓库的这个工作流，所以任何地方都不存密码或
API token。第 1 到 3 步只需做一次，必须由仓库所有者本人来做，因为要用他的 PyPI 账号。第 4 步就是那个按钮。

## 开始之前：`memory-lab` 这个名字

PyPI 判断新名字时，会先去掉 `-`、`_`、`.`，再把 `l` 和 `i` 当作 `1`、把 `o` 当作 `0`，结果与已有项目相同就拒绝。
[`memorylab`](https://pypi.org/project/memorylab/) 这个项目已经存在（只有一个版本 0.0.1，2025 年 3 月），所以第 3 步的表单
对 `memory-lab` 会回答 “This project name is too similar to an existing project”。这个结论来自 PyPI 的源代码和
2026-11-04 的项目列表；表单本身没有人试过，因为那需要账号。`memlab-core`、`memfalsify`、`orgmemory`、`memlab-mcp`、
`memlab-ui` 都没有这样的冲突。TestPyPI 上没有 `memorylab`，所以在那里试运行会成功，但说明不了这个问题。

第 3 步之前，从下面选一个：

- **发布五个包，先不发 `memory-lab`。** 第 4 步选 `stop-after: memlab-ui`，其他都不用改。安装命令变成
  `pip install memfalsify orgmemory`，`memlab-ui` 和 `memlab-mcp` 用各自的名字安装。
- **给这个元包改名。** 截至 2026-11-04，`memlab` 没有冲突。先改 `packages/memory-lab/pyproject.toml` 里的 `name`，再改
  该目录的 README、`scripts/dists.py`、`scripts/rehearse_install.py`、`publish.yml`、本文和 `tests/test_packaging.py` 里的名字；
  漏改的地方 `tests/test_publish_workflow.py` 会报错。
- **申请这个名字。** 联系 `memorylab` 的所有者，或者到 [pypi/support](https://github.com/pypi/support) 提交
  [PEP 541](https://peps.python.org/pep-0541/) 申请。这要几周甚至更久，结果也由对方决定。在此期间可以先用第一种做法。

## 1. PyPI 账号（一次）

1. 在 <https://pypi.org/account/register/> 注册账号并确认邮箱。PyPI 只接受已验证邮箱添加 pending publisher。
2. 打开 <https://pypi.org/manage/account/two-factor/>，添加验证器应用或安全密钥。PyPI 接着会给出恢复码，请保存好。

## 2. GitHub environment（一次）

打开 <https://github.com/timothyzhbw-jpg/memory-lab/settings/environments>，点 **New environment**。

1. Name 填 `pypi`，点 **Configure environment**。
2. 在 **Deployment branches and tags** 下选 **Selected branches and tags**，点 **Add deployment branch or tag rule**，填 `main`。
3. 可选：勾选 **Required reviewers** 并添加你自己。这样每次上传都会等你批准，你可以先看一眼构建出的文件列表。
4. 点 **Save protection rules**。

## 3. 在 PyPI 上添加 pending publisher

pending publisher 的意思是：告诉 PyPI，这个工作流第一次上传时可以创建这个项目。打开
<https://pypi.org/manage/account/publishing/>，找到 **Add a new pending publisher**，选 **GitHub** 标签页。每个项目填一次表单，
然后点 **Add**：

| 字段 | 填写 |
|---|---|
| Project Name | 项目名，见下面的两轮 |
| Owner | `timothyzhbw-jpg` |
| Repository name | `memory-lab` |
| Workflow name | `publish.yml` |
| Environment name | `pypi` |

PyPI 规定每个账号同时最多有三个 pending publisher，用过一次之后就不再占名额。所以第一次发布要分两轮：

- 第一轮，现在添加：`memlab-core`、`memfalsify`、`orgmemory`。
- 第二轮，第 4 步第一次运行之后添加：`memlab-mcp`、`memlab-ui`、`memory-lab`（这个名字见上面的说明）。

pending publisher 不会替你保留名字，所以添加之后请接着做第 4 步。

## 4. 那个按钮

打开 <https://github.com/timothyzhbw-jpg/memory-lab/actions/workflows/publish.yml>，点 **Run workflow**。

| 字段 | 第一次运行 | 第二次运行（第二轮之后） | 以后的发布 |
|---|---|---|---|
| Use workflow from | `main` | `main` | `main` |
| confirm | `packages/memory-lab/pyproject.toml` 里的版本号 | 同上 | 那里的新版本号 |
| target | `pypi` | `pypi` | `pypi` |
| stop-after | `orgmemory` | `memory-lab`；如果不发这个名字，选 `memlab-ui` | `memory-lab` |

点绿色的 **Run workflow**。之后会发生这些事：

1. **Build and check every package** 为六个包各构建一个 sdist 和一个 wheel，运行 `twine check --strict`，再把这些文件和当前代码、
   你填的 `confirm` 对比。`confirm` 与构建出的 memory-lab 版本不同，运行就在这里停下，什么都不会上传。这个任务的摘要会列出每个文件和它的
   SHA-256。
2. 如果你加了 required reviewer，点 **Review deployments**，再点 **Approve and deploy**。
3. **Upload to PyPI** 按依赖顺序上传：`memlab-core`、`memfalsify`、`orgmemory`、`memlab-mcp`、`memlab-ui`、`memory-lab`，到你选的那个包为止。

第一次运行之后，添加第二轮，再点一次按钮。第一次已经发布的三个包会被跳过：索引上已有的版本只会跳过，绝不会被替换。

## 5. 检查结果

```bash
python3 -m venv /tmp/memlab-check
/tmp/memlab-check/bin/pip install memfalsify orgmemory
/tmp/memlab-check/bin/pip index versions memfalsify
/tmp/memlab-check/bin/memfalsify --help
/tmp/memlab-check/bin/orgmemory --help
```

然后打开 <https://pypi.org/project/memfalsify/> 和其他项目页面。README 应该能正常显示；侧栏的 **Verified details** 下应该列出本仓库，
**Provenance** 下应该写着 `publish.yml`。`memory-lab` 发布之后，再试一下 `pip install "memory-lab[ui]"` 和 `memlab-ui --help`。

最后，把根目录 README 的安装一节改成发布后的写法。

## 试运行

**只构建。** 选 `target: build-only`，任何分支都可以。它不需要账号：执行上面构建任务的全部步骤，不向任何索引上传。文件以 `dists` 这个 artifact
留在运行页面上。同样的检查也可以在本机运行，还包括从 wheel 全新安装：

```bash
python3 scripts/rehearse_install.py
```

CI 每次推送都会运行它（`packaging` 任务）。

**TestPyPI。** 这是另一个网站，账号也是独立的。它能从头到尾验证工作流、publisher 设置和 attestation，但不能说明 PyPI 是否接受某个名字。

1. 在 <https://test.pypi.org/account/register/> 注册账号并开启 2FA。
2. 按第 2 步再建一个名为 `testpypi` 的 GitHub environment，不加分支规则，这样可以从分支试运行。
3. 在 <https://test.pypi.org/manage/account/publishing/> 添加 pending publisher：字段和分轮与第 3 步相同，Environment name 填 `testpypi`。
4. 选 `target: testpypi` 点按钮。第一次运行同样选 `stop-after: orgmemory`。
5. 从那里安装：

```bash
python3 -m venv /tmp/memlab-testpypi
/tmp/memlab-testpypi/bin/pip install --index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ memory-lab
/tmp/memlab-testpypi/bin/memfalsify --help
```

TestPyPI 同样保留版本：同一个版本第二次运行会被跳过，所以修改过的构建要换一个新版本来试。

## 以后的发布

1. 给有改动的包升版本，并调整包之间的版本约束（每个约束只允许一个 minor 版本，见 [CHANGELOG.md](../CHANGELOG.md) 开头）。`memory-lab` 每次发布都要升版本，
   因为它的版本号就是这次发布的编号。
2. 合并到 `main`，等 CI 通过。
3. 填新版本号，选 `target: pypi` 和 `stop-after: memory-lab`，点按钮。

## 出问题时

| 你看到的 | 含义 |
|---|---|
| `the confirm input differs from the built memory-lab version` | 你填了别的版本，或者 `main` 上是另一个版本。什么都没有上传。提示里写着构建出的版本。 |
| `target pypi works from main only` | **Use workflow from** 选了其他分支或标签。什么都没有上传。 |
| 上传步骤出现 `invalid-publisher` | PyPI 找不到与这次运行匹配的 publisher。把这个包在第 3 步填的四个值与该步骤打印的 claims 对比；如果它的那一轮还没到，就先添加它的 publisher。 |
| PyPI 表单显示 `This project name is too similar to an existing project` | 见[这个名字的说明](#开始之前memory-lab-这个名字)。 |
| 运行中途停了 | 那一步之前的包已经在 PyPI 上，并且会一直留在那里。解决问题后再点一次按钮，它们会被跳过。 |
| 发布的版本有问题 | PyPI 上的文件不能替换，也不能重复使用。在 PyPI 项目设置里该版本的页面上使用 **Yank**，修好之后发布一个新版本。 |

## 为什么这样是安全的

- 工作流唯一的触发方式是 `workflow_dispatch`；push、打标签、创建 release 都不会启动它。
- 只有上传任务持有 `id-token: write` 权限，它们不运行仓库里的任何代码：只解开构建任务检查过的文件，交给
  `pypa/gh-action-pypi-publish`，和其他 action 一样按 commit SHA 固定版本。
- 构建工具（包括 build backend）来自按哈希锁定的 `scripts/publish-requirements.txt`。
- `tests/test_publish_workflow.py` 和 `tests/test_action.py` 在每次推送时检查以上所有内容。

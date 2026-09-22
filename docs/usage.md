# 操作指南

一般使用只需在 Codex 中调用 `$codex-dsh-workflow`。本页说明底层命令，便于审查、手动诊断与复现；以下路径均需替换成自己的绝对路径。

## 1. 准备工作区和任务

目标必须是有初始提交的 Git 工作区根目录。干净项目默认使用 `codex/<task-id>` 分支的独立 worktree；已有用户修改时先判断与任务是否相关。相关修改不能在隔离时丢失，不相关修改也不能覆盖或清理。

任务和证据位于项目外，例如：

```text
~/.codex/dsh-tasks/example/
  task.md
  checks/
  attempt-01/
  attempt-02/
```

下面是任务单示例，不是可直接运行的通用修复任务。函数名、输入、路径和测试命令均需根据项目真实调查替换：

```markdown
# 修复已定位的输入处理错误

## 目标
说明具体输入、当前行为和预期行为。

## 工作上下文
工作区：<绝对路径>；分支：<分支>；基准HEAD：<真实提交>。
原有修改：<git状态与相关性>。
项目规则：<适用AGENTS.md及必须传递的约束>。

## 调查
复现命令与实际输出：<已执行证据>。
根因：<已确认位置和原因>；假设：<尚未确认项>。

## 工具与检索策略
工具：<已核实的codegraph/解释器入口及工作目录>。
索引：<状态>；已定位：<符号与文件>；待确认：<问题>。
复用已有调查，仅针对剩余问题查询。

## 范围
只允许修改：<实现文件及相关测试>。
不得改动：公共API、数据格式、无关模块和任务验收输入。

## 验收
AC1：<正常输入> → <可观察结果>。
AC2：<引发bug的输入> → <正确结果>。
AC3：<必要边界/反例> → <正确结果>。
AC4：相关回归通过，修改未超出允许范围。

## 验证
<已核实解释器、依赖、命令和工作目录>。
<既有失败及基线；没有证据时不预判为历史失败>。
外部输入及依赖：<逐个列出，传入--input>。
内容取证范围：<传入--capture的文件/目录>。
```

完整约定见 [handoff.md](../codex-dsh-workflow/references/handoff.md)。小任务只保留有用内容，避免复制整本通用说明。

## 2. 执行和自测

```sh
python3 /absolute/skill/scripts/dsh_run.py run \
  --workspace /absolute/project \
  --task /absolute/tasks/example/task.md \
  --output /absolute/tasks/example/attempt-01 \
  --capture src/target.py \
  --capture tests/test_target.py \
  --input /absolute/tasks/example/oracle.json \
  --timeout 900
```

参数说明：

| 参数 | 作用 |
|---|---|
| `--workspace` | Git工作区根目录，runner在此启动dsh |
| `--task` | UTF-8任务文件 |
| `--output` | 尚不存在、位于工作区外的证据目录 |
| `--capture` | 可重复；保存选定相对路径的修改前后内容，包括预计新增文件的范围 |
| `--input` | 可重复；冻结外部或Git忽略的验收文件及其副本，没有则省略 |
| `--timeout` | 本轮执行上限，默认900秒 |
| `--feedback` | 返修时附加上一轮审查文件 |

`--capture` 是取证选择，不是写入白名单。范围外变化仍记录路径和哈希，但没有完整内容审阅；Codex必须补查，不能据此认定无问题。不能选择仓库根或 `.git`；单文件上限2 MiB，每轮前/后捕获各20 MiB，外部输入合计20 MiB。超限应缩小范围或明确专项取证方法。

`--input` 不自动解析fixture或overlay依赖，须逐个列出。输入原文件及副本在本轮冻结；需要新测试时使用新版本并在下一轮登记。

## 3. 独立验收和记录

先读取 `result.json`，确认dsh已停止；逐项查看 `delta.json` 和 `changes.patch`。不要用dsh自述或退出码代替验收。

```sh
python3 /absolute/skill/scripts/dsh_run.py check \
  --output /absolute/tasks/example/attempt-01

python3 /absolute/skill/scripts/dsh_run.py record \
  --workspace /absolute/project \
  --candidate /absolute/tasks/example/attempt-01 \
  --output /absolute/tasks/example/checks/verify-01 \
  -- python3 -m unittest tests.test_target

python3 /absolute/skill/scripts/dsh_run.py check \
  --output /absolute/tasks/example/attempt-01
```

将示例测试换成项目实际命令。`record` 默认超时300秒，可用 `--timeout` 调整；只执行 `--` 后的参数数组，不自动解释管道、重定向或shell变量。测试基线也可用不带 `--candidate` 的 `record` 留档。

`check` 只检查候选、任务和已登记输入是否漂移，不测试业务逻辑、不生成PASS；绑定候选的 `record` 在验证前后检查漂移。指纹覆盖Git可见文件及登记输入，不冻结系统环境、数据库或外部服务。当前不支持submodule条目。

在attempt目录写 `review.md`，包含候选fingerprint、task摘要、每条AC结论、独立验证记录、范围审查及最终结论：

- `PASS`：必要AC全部满足，独立验证完成，最后一次候选检查通过。
- `REWORK`：指明位置、实际/预期、证据、对应AC及修复验证方式。
- `BLOCKED`：必要信息、权限、凭证、服务或超范围决策确实缺失；复杂或代码错误本身不是阻塞。

## 4. 返修

```sh
python3 /absolute/skill/scripts/dsh_run.py run \
  --workspace /absolute/project \
  --task /absolute/tasks/example/task.md \
  --feedback /absolute/tasks/example/attempt-01/review.md \
  --output /absolute/tasks/example/attempt-02 \
  --capture src/target.py \
  --capture tests/test_target.py \
  --input /absolute/tasks/example/oracle.json
```

每轮创建新目录并重新登记必要输入，保留历史证据。每次headless是独立调用，交接必须自包含。同一问题两轮返修仍未解决，Codex重新调查根因并修订任务，不能无限重复同一提示。

## 证据文件

| 文件 | 含义 |
|---|---|
| `task.md`、`prompt.txt`、可选 `feedback.md` | 本轮任务副本、实际提示词和返修输入 |
| `execution.md`、`stderr.log` | dsh标准输出和标准错误 |
| `result.json` | 执行状态、退出码、哈希和候选指纹；不是验收结论 |
| `before.json`、`after.json` | Git可见状态的前后摘要 |
| `delta.json`、`changes.patch` | 本轮增量清单及选定范围的审阅差异；不保证可直接git apply |
| `blobs/` | 所选范围的内容快照，包括二进制和符号链接目标字符串 |
| `inputs/` | 登记的验收文件副本 |
| `review.md` | Codex撰写的独立验收结论 |
| `checks/<检查>/` | `record`产生的命令、stdout/stderr、退出码和指纹 |

输出目录权限为0700，但不等于加密、OS沙箱或秘密自动检测。不要捕获凭证和大数据目录，也不要把本地执行证据直接公开。

## 常见问题

**dsh没有输出，是否卡住？** headless可能在结束时才产生最终stdout。先检查本轮stderr和实际状态，等待受控超时，不因为安静就反复启动。

**为什么退出码0还没完成？** `EXECUTED` 只说明进程成功结束；实现正确性由Codex独立验收。失败、超时、中断和取证失败同样不能成为PASS。

**为什么check报候选变化？** 代码、Git状态、任务或登记输入可能在运行后改变，某些测试也会重写文件。先审查变化并重做受影响验收，不删除证据绕过校验。

**codegraph能保证找全测试吗？** 不能，动态调用或未索引代码可能遗漏。它帮助定位，不替代源码判断、项目规则或验收命令。

**可以并行、提交或部署吗？** 当前流程默认串行，dsh不得自行再委派、提交、推送或部署。独立验收通过也不自动授权发布。并发备忘仅为历史设计，不是已实现功能。

**如何卸载？** 先确认没有任务使用该skill，备份自己的定制后移除已安装的 `codex-dsh-workflow` 目录；这不会卸载dsh、codegraph，也不会删除外部任务证据。

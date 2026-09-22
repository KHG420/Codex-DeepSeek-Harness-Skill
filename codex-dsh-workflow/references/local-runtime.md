# dsh 运行环境与诊断

此文件提供可移植的检查方法。实际路径、模型、权限和凭证由使用者的环境决定，不能照搬维护者机器的配置。

## 入口和兼容性

发布准备时（2026-09-22）检查的 dsh 版本为 `0.1.5-rc.2`，入口来自 npm 包 `@deepseek-ai/dsh`。这是已检查版本，不是自动锁定版本或对其他版本的兼容保证。

```sh
command -v dsh
dsh --version
dsh --profile headless --help
command -v codegraph
codegraph --help
```

当前 runner 使用 `dsh --profile headless <任务文本>`，通过子进程 cwd 设置工作目录；任务作为单个参数传入，不经 shell 展开。该版本 headless help 未列出 `--cwd`、`--resume` 或 stdin 任务接口，不套用其他 profile 的参数。launcher 的 `--profile`、`--patch` 位于应用任务参数之前。

先按 DeepSeek Harness 上游说明安装 dsh 并配置模型、凭证及 headless profile。本 skill 不安装 dsh，不分发 API key，也不固定提供商或模型。升级后核实当前实际入口，不默认重建另一个源码目录能够修复 PATH 中的安装。

## 工具可用性

- 在实际工作目录确认 codegraph CLI 和索引。已有索引用 `codegraph status` 检查，按变化需要执行 `codegraph sync`；缺失时 `codegraph init`。初始化可能产生索引及辅助文件，检查后不要纳入产品提交。
- `explore`、`node`、`callers`、`impact`、`affected` 可通过 dsh 的 Bash 工具调用，不要求另接 MCP。参数以当前版本 `--help` 为准。
- 维护环境的 headless 配置包含 Bash、文件读写、文件搜索及 Web 插件。其他安装必须单独核实，不能假设继承 Codex 的工具或授权。
- 需要诊断插件装配时可检查 `dsh --profile headless --dump-config`，只提取必要插件名称；完整配置可能包含敏感信息，不直接粘贴进任务或公开日志。
- 区分“命令/help 可用”“配置中有插件”和“模型实际调用成功”。前两项不证明第三项，也不证明外部服务、索引内容或访问权限正常。

## 凭证和权限

沿用用户现有 dsh 配置，不输出密钥，不将密钥放进命令参数、任务单或报告。不要为 headless 自动化静默切换到更宽松权限。worktree、cwd 和提示词范围不是 OS 沙箱。

若需要交互批准但 headless 无法完成，依据实际错误报告所需权限，不能把权限拒绝解释为普通工具缺失并绕过。浏览器任务应沿用任务单中的实例复用、登录状态和资源收尾要求。

## 分层排错

1. `command -v`、版本和 help：区分 PATH 问题、入口错误和插件装配失败。
2. 首次使用、模型/凭证变化或请求失败时，在任务外临时目录做最小真实调用：`dsh --profile headless 'Do not use tools or modify files. Reply exactly DSH_READY.'`。这会调用配置的模型，可能产生费用；成功只证明请求链路，不证明代码工具可用。
3. 必要时在有初始提交的小型 Git 仓库验证读取、修改和测试，用 runner 设置超时，并由 Codex 独立检查结果。不要拿生产目录作环境探测。
4. 长任务没有即时 stdout 不等于卡死；先看本轮 stderr、结果状态和文件变化。限定到本轮证据，不倾倒其他会话数据，不无依据重跑或换模型。

## 本仓库验证边界

`scripts/test_dsh_run.py` 使用 fake-dsh 做离线行为回归，验证取证、差异、冻结输入、失败与超时等路径，不调用模型。它不能证明真实模型遵循任务约定，也不保证当前账户、额度、网络或所有工具可用。

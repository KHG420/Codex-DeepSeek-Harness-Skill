# 任务与验收交接约定

任务目录位于 Git 工作区外，避免报告自身改变候选指纹或进入产品提交。以下是内容提纲，按真实项目填写，不要求小任务生成大量空字段。

优先写“已确认事实 + 允许改哪里 + 输入→输出”，不重复长篇通用约束。仅列影响本任务的环境、禁区与反例。

## task.md（Codex 写）

```markdown
# <任务编号与目标>

## 目标
用户要求、触发场景、当前行为 → 预期行为。

## 工作上下文
工作区绝对路径、分支、基准 HEAD。
用户原有修改及其与本任务的关系。
适用 AGENTS.md 路径和必须传给 dsh 的会话级约束。

## 调查
复现命令、实际输出、已确认根因；未确认内容标为假设。

## 工具与检索策略
只列本任务需要且已核实的工具入口、工作目录、用途和必要参数；区分命令/help可用、配置存在与真实调用成功。
codegraph索引状态、已查询的符号/文件和仍需调查的问题；索引不可用时的实际错误。
可复用的定位结果与证据位置；其他必要CLI或已配置MCP的使用方式，不假设继承Codex工具。

## 范围
允许修改的模块/文件，必要测试范围。
不得改变的 API、格式、行为；依赖及外部操作限制。
发现范围之外的必要修改先提交事实，不擅自扩展。

## 验收
AC1：具体输入/动作 → 可观察的结果。
AC2：相关旧行为保持成立。
AC3：改动范围和必要验证。

若涉及数值/分支，逐项给出关键正反例：
| 输入情形 | 预期结果 | 原因/限制 |
|---|---|---|
| 正常值 | 明确数值/公式 | 单位与容差 |
| 真正零值 | 0 或业务定义结果 | 不与缺失混淆 |
| 缺失 | null/错误/既定策略 | 原因标记 |
| 非法输入 | 拒绝或既定策略 | 不等同于合法负结果 |
只保留本任务相关行；如负差值合法，单独说明。

## 验证
已实测的解释器/依赖、命令、工作目录、成功判据、record证据位置。
历史失败的准确位置/原因及基线，未确认项标为未确认。
关键正反例文件及其fixture/overlay依赖（--input逐一登记）；--capture范围。
哪些条件需要 Codex 自己观察。

## 实现建议
最小正确实现方向，说明哪些仅为建议。
```

## execution.md（dsh 最终输出，助手保存）

要求有实际变更、原因、验证命令和退出结果、AC 自检、偏离规划及未完成项。简述关键工具查询、定位结果与失败回退原因；给出必要命令/符号/路径，不重复粘贴大段源码或全部日志。dsh 自检是线索，不替代 Codex 独立证据。

## 工具选择与上下文控制

- 复用已确认调查；仍需阅读代码时优先codegraph。已有索引先看状态，仅有相关变化时同步，缺失时初始化，不在每轮任务或返修中无条件重建。索引及初始化产生的其他文件要检查，不混入产品修改。
- 按问题选择查询，不机械执行整套命令。例如 `codegraph explore "问题或模块" --max-files 3` 定位区域，`codegraph node <符号>` 查看源码和关系，`callers`/`impact` 检查调用影响。以本机 `--help` 为准。
- 查询已返回的当前完整源码不立即重复读取；需要补充时限定文件、符号或行段。精确字符串、配置和未索引内容用 `rg` 补充，不把全仓源码或超长查询结果一次性塞入上下文。结果不足时围绕缺口扩大范围。
- codegraph不可用先尝试 `codegraph init`，保留准确失败原因后回退；不循环重试同一失败，不自行安装依赖或放宽权限。权限拒绝仍遵守任务边界。
- 可用 `codegraph affected <改动文件>` 辅助定位测试，但图谱可能漏掉动态调用及未索引测试，不能代替任务单规定的验证或据此删减验收。
- 其他工具按任务选择：文件读写/搜索、Git差异、编译器、测试器、已配置的外部查询等。CLI足够时无需额外MCP接入；工具可调用不代表获准执行其所有动作。浏览器任务显式传递用户的实例复用和资源收尾要求。
- 以减少无关输出、重复调查和返修为目标，不以工具调用次数衡量效率；没有可比的实际用量记录，不承诺节省比例。

## review.md（Codex 写）

```markdown
# 验收：PASS / REWORK / BLOCKED（三选一）

候选：result.json 中的 candidate_fingerprint
任务：result.json 中的 task_sha256
工作区：绝对路径
执行状态：result.json 中的 status

| 条件 | 结论 | 独立证据 |
|---|---|---|
| AC1 | PASS / FAIL / NOT_VERIFIED | 文件、命令、实际结果 |

## 代码与范围审查
实际变更、根因判断、相关回归、原有修改处理。

## 独立验证
实际命令与退出结果，record生成的各验证目录/result.json和stdout/stderr位置。
验证前后 candidate check 结果。
历史失败与本次新增失败的区分；缺失取证范围或未验证依赖不得写PASS。

## 需要修复的问题（存在时）
R1：对应 AC、位置、证据、实际/预期、修复验证方式。

## 外部阻塞（存在时）
缺失条件、错误证据、剩余验收项、解除条件。
```

旧 attempt 的 task.md 和 review.md 保留当时事实；重新规划先保留任务旧版再更新源 task.md，新建 attempt。源任务变化会让旧 attempt 的 check 失败，这是预期的失效提示。

## 调用和取证

```sh
python3 <skill>/scripts/dsh_run.py run --workspace <repo> --task <task.md> --output <attempt-01> --capture src/module.py --capture tests/module_test.py --input <oracle.json>
python3 <skill>/scripts/dsh_run.py check --output <attempt-01>
python3 <skill>/scripts/dsh_run.py record --workspace <repo> --output <checks/preflight-01> -- /verified/python -c 'import yaml; print(yaml.__version__)'
python3 <skill>/scripts/dsh_run.py record --workspace <repo> --candidate <attempt-01> --output <checks/verify-01> -- go test ./internal/monitoring
python3 <skill>/scripts/dsh_run.py run --workspace <repo> --task <task.md> --feedback <attempt-01/review.md> --output <attempt-02> --capture src/module.py --capture tests/module_test.py --input <oracle.json>
```

替换示例路径；没有外部输入时省略--input。record只执行`--`之后的参数数组，不自动使用shell，不推断业务PASS；非零退出、启动失败、超时如实记录。测试若改写候选，带--candidate的record会失败并保留原命令退出码。record与run共用工作区锁，不能用record在同一run仍执行时测试；dsh自行测试直接运行命令即可。

## 本轮取证与冻结边界

- `before.json` / `after.json`：完整Git可见状态摘要；`delta.json`：仅本轮发生变化的文件和元数据；`changes.patch`：选择范围内的before→after文本审阅差异（不是保证可直接git apply的补丁）。未改的原有脏内容不混入差异。
- `--capture`为经人工审查的相对文件或目录，可重复；未来新增文件也要在选择范围内。未选择内容不复制，仍在delta中显式标记。二进制及链接保存准确字节到按哈希命名的`blobs/`；不跟随链接目标。删除/新增空文件、模式变化可通过delta审查。
- 不允许捕获仓库根或.git；Git ignored文件不自动捕获。单文件2 MiB、每个前/后快照20 MiB上限，超限报错，不自动扩大。不要选择含凭证的目录；工具没有可靠的秘密检测能力，必要大数据使用专项hash/抽样验证并明确限制。
- `--input`复制外部或ignored验收文件到attempt/inputs并记录源路径/副本hash，合计上限20 MiB。输入与依赖按轮次冻结；执行结束及check校验原路径与副本。复制的overlay不会自动重写路径，所以其引用目标也必须登记，不能只登记overlay本身。
- 校验针对文件最终状态，不保证阻止瞬时改动或同权限恶意篡改；冻结仍依靠协作纪律。新增独立测试不能原地改旧输入，应版本化、验证fixture前提，再加入下一轮。
- 命令记录包含argv和输出，不保存完整环境变量。不要在命令行或日志打印密钥。证据目录0700只保护本地目录访问，不等于加密；证据留在本机，不随产品发布。
- 环境预检按任务必要性选择，记录实际可用解释器/版本/import结果；只有环境或相关依赖变更才重新调查。全仓已知失败必须有可对比原始证据，不能预先忽略某类失败或声称全绿。

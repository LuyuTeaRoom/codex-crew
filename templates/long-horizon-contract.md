# codex-crew 0.5.3 简报与验证记录

本页供主协调者组装简报和核对交接。Worker 的必读格式使用 [Worker 短契约](worker-handoff.md)，无需加载本页的协调流程；Luna 交付的语义验收由 [Sol 审计](sol-audit.md)承担，审计规则不传给 Luna。

只有需要委派或跨阶段交接时才创建简报。以下 JSON 是最小的尚未执行任务示例；空列表表示目前没有已知事实或验证结果，不代表检查通过。

```json
{
  "task_id": "parser-fix",
  "revision": 1,
  "scope": ["src/parser.py", "tests/test_parser.py"],
  "do_not_touch": ["公共接口", "无关文件"],
  "expected_result": "修复空输入异常，保留现有合法输入行为",
  "acceptance": {"A1": "空输入返回空列表；现有合法输入测试通过"},
  "context": {
    "goal": "定位并修复空输入异常",
    "verified_facts": [],
    "artifact_index": [],
    "decisions": ["保持现有公共接口"],
    "open_items": ["读取实现及相关测试，确认触发条件"],
    "validation": []
  }
}
```

Task Contract 的顶层字段和 Context Packet 的六个字段固定；JSON 任意层级不能出现重复键。goal 和预期结果必须非空，scope 至少一项，acceptance 包含验收编号及完整要求。非委派任务不需要此包装。用户限制放入 do_not_touch 或 decisions，不能因压缩遗漏。

工作角色及其操作边界放入本次 `context.decisions`，交付和停止条件放入 `expected_result`、`acceptance` 或 `context.open_items`，不得新增顶层字段。停止条件应具体，例如原因已证实、任务完成或出现需协调者裁决的需求冲突。现有任务状态另记角色、模型、强度策略和实际调用路径；不重复简报正文。主协调者强度记录为跟随当前对话，具体值仅在有当前依据时记录。

同 Worker 调查转实现时，在 decisions 中明确新的工作角色、已有授权和操作边界，并按目标、范围、输入或验收的实际变化更新版本。发送经重新校验的完整简报，保留 scope、do_not_touch 和完整验收；增量交接指来源与进度只补必要变化，不是发送缺字段的 JSON。一般进度消息无需重发简报或增加版本。新增简报不清除 Worker 历史；独立审查仍需不同上下文。

## 首次读取

Worker 首次执行必要的读取时，先读经校验的完整原简报，确认 scope、do_not_touch 和每项完整验收，再按索引读当前任务需要的材料。同时确认三个问题：所需输入是否可访问；是否存在会实质改变正确性的缺失决定；验收要求是否足以执行。摘要和进度信息不能替代原简报。

能从材料和已有授权解决的常规选择自行处理并继续工作。只有真实缺口才在现有 open_items 或返回的 unfinished 中说明其影响、已完成部分和最小待决事项；依赖该缺口的工作暂停，独立工作继续。无需单独的 READY 消息、预检 Worker 或额外用户确认。这次实际读取同时用于现有路径确认。

## 来源与已执行检查

artifact_index 中每条来源包含 `id`、`path`、`range`、`version`。path 是本地证据文件，相对路径基于校验命令的 --base-dir；range 指明相关行、页或其他范围。version 使用 `sha256:<文件内容摘要>`，可由 policy_contract.py 的 file_version(Path(...)) 生成，只处理实际索引的文件，不扫描整个项目。工作区未提交修改也会改变该版本。

外部资料将已读取的必要片段保存为本地证据文件，并在可选的 source_url 中保留原网址。校验器不联网。计划生成的文件写入 expected_result，不能用不存在的路径充当证据。

verified_facts 每项采用 `{"text": "事实", "sources": ["source-id"]}`。decisions、open_items 为字符串列表。存在来源不代表来源支持该结论，验证者仍需核对原文。

validation 记录本任务当前验收项的结果：

```json
{
  "task_id": "parser-fix",
  "revision": 1,
  "criterion": "A1",
  "status": "PASS",
  "method": "python3 -m unittest tests.test_parser",
  "inputs": ["parser", "parser-tests"],
  "input_versions": {"parser": "sha256:<实际摘要>", "parser-tests": "sha256:<实际摘要>"},
  "evidence": ["test-log"],
  "conditions": {"cwd": "项目目录", "python": "实际运行版本"}
}
```

inputs 和 evidence 引用 artifact_index 中的 ID。PASS/FAIL 必须有方法、输入版本、证据和对应任务版本；input_versions 必须是执行检查时的版本，不能为了匹配新文件更新旧记录。NOT_RUN/BLOCKED 使用 reason 说明原因，不伪造执行记录。同一验收项只保留当前结论，旧日志放入索引；上游其他任务的结果可以作为来源，不能充作本任务 PASS。

## 检查与边界

```bash
python3 -B <skill目录>/scripts/policy_contract.py <brief.json> --base-dir <工作区>
```

返回 packet_valid、brief_version 和错误字段；退出码 0 表示简报有效，1 表示存在问题。task_completion_verified 始终为 false，因为这个脚本不验收业务语义。发送给 Worker 的内容必须与已检查简报一致，修改后重验。实际发出的 scope、不能修改内容和验收要求应完整保留。

默认上限是整个简报 12000 个 JSON 字符（非 token），同时检查原始文件和紧凑序列化后的内容，避免多余空白或转义放大实际交接。先缩小任务或改为引用；必要时说明理由并用 --max-chars 增大上限，不能截断约束或反复压缩到失真。Worker 摘要以 2000 字符为目标，是表达约定，不是原生输出硬限制。

## 返回结果校验

主协调者保留实际发送的原简报及其 brief_version；Worker 返回同一指纹，不得用自己的摘要、删减后的约束或修改后的验收替换它。返回沿用上述 artifact_index 和 validation 格式，顶层固定为：

```json
{
  "task_id": "parser-fix",
  "revision": 1,
  "brief_version": "sha256:<派工校验输出的完整简报摘要>",
  "status": "BLOCKED",
  "summary": "必要输入暂不可访问",
  "actual_changes": [],
  "artifact_index": [],
  "validation": [{"criterion": "A1", "status": "BLOCKED", "reason": "指定实现文件暂不可访问"}],
  "unfinished": ["恢复指定输入后执行 A1"]
}
```

每个原验收编号必须恰好出现一次。PASS/FAIL 检查还须记录非空 conditions；未执行项使用 NOT_RUN/BLOCKED 和 reason。整体 PASS 要求每项为 PASS 且 unfinished 为空；PARTIAL/FAIL/BLOCKED 必须说明未完成事项。实际修改和摘要仍需对照文件核实。

派工消息给出 Worker 短契约的本地路径、原简报路径、已校验的 brief_version、结果输出路径及实际校验命令，保证 fork_turns="none" 的 Worker 能取得返回契约。不要把指纹写回原简报，也不要仅给文件名而依赖父会话历史补全格式。详细构建报告、专项状态和排查过程另存为证据，由 artifact_index 引用；结果顶层不得换成自定义字段或自定义成功状态。

```bash
python3 -B <skill目录>/scripts/policy_contract.py <原brief.json> --result <result.json> --base-dir <工作区>
```

主协调者使用自己保存的原简报运行该命令。result_valid=true 只表示返回契约、覆盖和当前证据版本通过；还需核对证据是否支持原验收。task_completion_verified 仍为 false，有效的 PARTIAL/BLOCKED 也可退出 0。返回索引记录修改后的实际文件版本；不重新检查原简报中的修改前文件摘要。原简报的内容指纹则必须一致，执行时的 input_versions 不得改写以迎合新文件。

Worker 提交前运行该命令；主协调者收取后独立运行并保留命令、退出码及 JSON 输出，再按原验收核对关键证据。缺少这次返回校验时，不宣称已完成契约验收；结束 Worker 执行与接受其交接是两件事。校验输出可保存在交接旁，不要将会随结果变化的回执反向索引进同一结果造成循环依赖。

Luna 返回的内容须由 Sol 核对实际证据并形成审计结论，逐任务绑定原简报与当前结果文件摘要。使用主 Agent 现有任务记录或结果旁的审计文件，不在 Worker 结果中追加字段，也不要求 Luna 为 Sol 预写审计报告。只有受影响的缺陷条目与修复条件退回 Luna；没有需求变化时不重新注入完整历史或审计过程。

拒收缺项、旧版本或缺证据的返回后，定向补齐受影响部分；先检查 Worker 已经产生的实际修改，按当前要求处理，不自动回滚、重派、重建或重测。仅修复交接格式时，保留原始报告和已有产物，继续使用实际发送的原简报及版本，不为了让旧结果通过而放宽验收。主协调者可以据现有证据整理结果，但须在 summary 或索引的修复说明中标明整理者、来源和实际检查范围。复用的历史构建/测试证据保留原执行条件，当前只读核对另记方法；不能把历史命令写成刚重新执行。校验器不执行测试、不撤销文件，也不拦截原生工具。

## 验证复用与结果版本

主协调者在同任务中复用 PASS 前，核对任务版本、实际输入内容、检查方法、相关条件以及证据仍有效。条件只包含这次检查真正依赖的运行因素；不知道依赖范围就重做相关检查。

现有辅助函数 result_is_current 检查任务和版本；validation_reusable 比较已知输入版本、方法和条件，并要求 dependencies_known=True。调用者必须先核验当前证据，辅助函数不探测全环境、不自动停止 Worker。旧版本返回拒收后，仍需检查其实际文件修改。

## 可取得的用量

有实际 usage 时记录调用标识、task_id、阶段、模型、input/output 和耗时；不可得为 unknown。reasoning 通常属于 output，cached 和工具结果通常属于 input，packet/retry 是交叉分类，均不能再次加进总量。同一调用只统计一次，计入失败和重试调用。

用量记录不承担调度或计费功能。主协调者的任务状态和证据索引用于恢复核对，不能清除模型历史；不承诺任务总 token 硬预算。

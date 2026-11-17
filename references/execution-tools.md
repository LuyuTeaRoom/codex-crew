# 单项执行与本地用量计量

单项执行 CLI 依赖外部 `task_process.py`，可用 `--manager` 指定；计量 CLI 可独立读取本地 rollout。这两个标准库工具不创建队列，也不会调用模型。单项工具每次只启动一条显式命令；下一项是否放行仍由主协调者判断。

主模型先读本项摘要及必要产物，作出验收决定；在同一决策轮保存决定，再调用 `run` 启动已放行的下一项。可将本项启动、等待、结果摘要读取合并为一次工具往返。完整结果和日志留原位，回传状态、退出码、关键产物、异常及来源；需要核对内容时再定向读取。

## 启动一项工作

`scripts/single_item.py run` 要求给出工作目录、chat/thread ID、turn ID、job 名、进程期限、预期 JSON 结果路径、job 索引和命令。命令以 argv 形式传入，不经过 shell：

```sh
python3 -B scripts/single_item.py run \
  --cwd /tmp/task-a \
  --task THREAD_ID --turn TURN_ID \
  --name task-a --timeout 300 --wait 10 \
  --result /tmp/task-a/result.json \
  --job-index /tmp/task-a/jobs.json \
  -- python3 -B /path/to/task.py --input /path/to/input.json
```

`--timeout` 是进程管理器的执行上限；`--wait` 是本次调用等待并轮询 job 的最长时间。等待结束仍在运行时，输出保留原 job ID、`RUNNING` 状态、退出码（尚无时为 `null`）、日志路径和 job 索引路径。使用同一个索引轮询，不会再启动任务：

```sh
python3 -B scripts/single_item.py status \
  --job-id JOB_ID --job-index /tmp/task-a/jobs.json
```

可用 `--manager /path/to/task_process.py` 覆盖管理器位置，也可设置 `CODEX_PROCESS_MANAGER`；默认位置是 `${CODEX_HOME:-~/.codex}/process_manager/task_process.py`。若当前会话设置了 `CODEX_THREAD_ID`，`--task` 必须与其相同，这样后续 `status` 才能查询同一个 chat 的 job。结果文件缺失或不是 JSON 时，`result.state` 分别为 `missing` 或 `invalid_json`，进程状态与退出码仍单独返回。`PROCESS_FAILED` 不会因结果文件中出现某个状态字段变成成功。

## 计量本地 rollout

单来源可直接给路径、thread、turn 和角色：

```sh
python3 -B scripts/measure_usage.py \
  --rollout /path/to/rollout.jsonl \
  --thread-id THREAD_ID --turn-id TURN_ID --role coordinator \
  --output /path/to/usage.json
```

多来源可使用 JSON 文件。相对 rollout 路径以该 JSON 文件所在目录为基准：

```json
{
  "sources": [
    {"path": "coordinator.jsonl", "thread_id": "THREAD_A", "turn_id": "TURN_A", "role": "coordinator"},
    {"path": "worker.jsonl", "thread_id": "THREAD_B", "turn_id": "TURN_B", "role": "worker"}
  ]
}
```

```sh
python3 -B scripts/measure_usage.py \
  --source-json /path/to/sources.json --output /path/to/usage.json
```

每个来源优先读取带原生 response ID 的响应 usage，并按 ID 去重；同一来源没有这类数据时，使用 `token_count.info.total_token_usage` 的累计差分。一个来源不会把响应 usage 和累计差分相加。累计差分没有 response ID，因此不会伪造 ID，报告也会说明这类来源无法跨来源按响应 ID 去重。重复配置的同一 rollout/thread/turn 只计一次。

报告按 coordinator 和 worker 分列。`cached_input_tokens` 是 `input_tokens` 的子集，`reasoning_output_tokens` 是 `output_tokens` 的子集；违反这两项关系的输入会被拒绝。总量不会把子集或 `total_tokens` 再加一次。`non_cached_input_tokens` 仅在 input 与 cached 值都已知时计算。缺失计量写为 `null`。只有观察到目标 turn 的结束事件才标为 `complete`；进行中的 turn 是 `snapshot`，未找到目标 turn 则为 `not_found`。不完整来源不会被标成完整总额。

计量只读取已有 rollout，不重放或重新执行工作。它统计可观察到的 usage，不补造尚未写入日志的尾部计量，也不判断任务内容是否正确或是否通过主协调者验收。

评测开始时确定统一的截止点；记录一次任务验收与进程收尾，复用本工具生成一份用量结果。进行中的回合交付一次带截止时间的快照；回合结束后需要完整总量时离线补齐。报告按“实际结果、直接原因、优化动作”组织，用响应次数与平均输入、缓存/非缓存、输出来解释 token 变化，用准备、执行、验收和收尾区间解释耗时。

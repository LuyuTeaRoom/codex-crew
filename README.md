# codex-crew 0.5.3

用于 Codex 的轻量协作 skill：按完整交付物分工，由主模型持续推进、验收和接续。简单任务直接完成；有界工作优先交 Luna，修订复用原 Worker。计量、详细报告和额外审查按需使用。

## 模型与职责

| 责任 | 模型 | 推理强度 |
|---|---|---|
| 协调、关键判断、整合与验收 | 当前 GPT-6 Astra 或 GPT-6.1 Sol | 用户当前选择 |
| 有界调查、整理、实现 | GPT-6 Luna | max |
| 必要的复杂独立工作或行为审查 | GPT-6.1 Sol | xhigh |

启动证据与条件见 [SKILL.md](SKILL.md)。不自动切换主模型或档位，不增加常驻协调层。完整流程集中在[运行契约](templates/AGENTS.md)。

## 部署

将仓库放入本机技能目录，或在 `~/.agents/skills/codex-crew` 建立指向仓库的符号链接。先检查已有入口，避免重复安装。

需要 Worker 时，按 [agent-luna.toml](templates/agent-luna.toml) 和 [agent-sol.toml](templates/agent-sol.toml) 检查现有角色的实际模型和强度；已有角色优先复用。项目配置参考 [project-config.toml](templates/project-config.toml)，只合并实际需要的字段和规则，保留原权限及用户配置。修改前备份。

Worker 只读原简报、必要来源与[短契约](templates/worker-handoff.md)。结果由主协调者核对实际产物；Luna 结果经 [Sol 审计](templates/sol-audit.md)。

## 按需工具

- `scripts/current_session.py`：需要派工但配置证据不足时，读取本线程当前回合。
- `scripts/policy_contract.py`：校验原始简报与固定九字段返回契约。
- `scripts/single_item.py`：在 macOS/Linux 上启动或查询一项已放行的命令；依赖外部 `task_process.py`，可用 `--manager` 指定。此仓库不安装该管理器。
- `scripts/measure_usage.py`：独立离线读取已有 Codex rollout，按响应ID去重、区分缓存/非缓存和快照/完整回合。

调用方法见[工具说明](references/execution-tools.md)。任务接续仍由主模型或已明确授权的固定执行链负责。

## 检查

```sh
python3 -B scripts/validate.py
python3 -B -m unittest discover -s tests -v
```

外部进程管理器未安装时，仅相关集成用例跳过，其余单元测试照常执行。检查方法见[验证指南](references/verification-guide.md)，本版变化见[0.5.3说明](RELEASE-0.5.3.md)。

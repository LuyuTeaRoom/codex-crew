---
name: codex-crew
description: Bounded collaboration for a current GPT-6 Astra or GPT-6.1 Sol primary session at the user-selected effort. Separate investigation, implementation and review roles from Luna max or Sol xhigh execution; use compact evidence-backed handoffs.
license: MIT
metadata:
  version: 0.5.3
  author: codex-crew contributors
---

# codex-crew 0.5.3

轻量分工、完整交付。主模型负责完整目标、关键判断和最终结果；简单任务直接完成，确定性工作用现有程序。只有完整工作包可独立交付且委派有收益时才启动 Worker。`templates/` 维护协作细则；本 skill 不接管 Codex runtime。

## 先判断是否委派

先确定交付物、输入、验收和写入归属。有界调查、整理和实现优先交 Luna；主模型保留复杂判断与整合。不按任务名称机械选模型，也不拆成读取、实现、测试三个 Worker。同一成品修订优先复用原 Worker 的 follow-up，Worker 负责实现、必要验证、交接和进程收尾。

## 启动条件

主会话允许 `gpt-6.1-sol`（不另设强度门槛），或 `gpt-6-astra`（不另设强度门槛）。主模型始终沿用用户选择的强度，不切模型、不另起协调者；速度不参与判断。Worker 不再次委派，维护本 skill 不受委派启动门槛限制。

配置证据顺序：当前运行时 → 本线程进行中回合的 turn_context → 用户对当前设置的明确说明。已知不符合时停止查找；部分证据不被低优先级冲突值覆盖。仅在需要委派且证据不足时运行 `python3 -B scripts/current_session.py`，检查 `crew_start_allowed`；未知则继续普通任务，不追问或猜测设置。脚本退出0不代表获准。参数只传已知的 runtime/user model 与 effort。

同一回合证据复用；新回合或设置变化后，在下次委派前重新确认。条件不再满足时停止新增派工，先核对已有写入与收尾。无后台档位监控。

## 工作角色与模型选择

| 责任 | 实际路径 | 强度 |
|---|---|---|
| 编排、简单任务、复杂判断、整合与验收 | 当前 Astra 或 GPT-6.1 Sol 主会话 | 用户当前选择 |
| 可独立验收的有界工作 | `codex_luna_max_worker` / `gpt-6-luna` | `max` |
| 确需独立执行的复杂工作或行为审查 | `codex_sol_xhigh_worker` / `gpt-6.1-sol` | `xhigh` |

通常0–1个 Worker；多个独立交付物可并行，遵守已有并发上限和写入归属。Sol 主会话承担协调、复杂判断及 Luna 结果审计，不默认再开 Sol 或调用 Astra。默认模型仅为上述三种；其他模型须用户明确授权，Spark 不设默认验收门槛。

调用前核对工具暴露的精确模型/档位，自定义角色配置优先；旧同名角色不算已升级。不匹配时仅使用实际支持且获准的显式路径，并记录路径差异。

## 必要的委派流程

1. 读取[运行契约](templates/AGENTS.md)，按[简报格式](templates/long-horizon-contract.md)保存完整任务和约束，用 `policy_contract.py <brief> --base-dir <workspace>` 校验；仅发送 `packet_valid=true` 的原件。派工给出原 brief、返回路径、校验所得 `brief_version`、[Worker短契约](templates/worker-handoff.md)和实际返回校验命令，使用 `fork_turns="none"`。指纹不写回原 brief。
2. 原 Worker 首次读取完整 brief 和必要输入后直接执行；这次读取同时验证实际路径，无需空跑或另报READY。Worker 不加载协调/路由/审计全文，只按本任务读取必要技能与来源。
3. Worker 自检后返回固定九字段结果。主模型以保存的原 brief 运行 `policy_contract.py <brief> --result <result> --base-dir <workspace>`，保存回执并核对实际产物；Luna 结果按[Sol审计](templates/sol-audit.md)验收。格式问题只补交接，保留产物；完成或失败后结束 Worker 执行。

主模型持续推进整条已授权队列。需要判断的项验收后再放行，受阻只暂停依赖部分；执行、复用、修订和停止边界见[队列约定](templates/AGENTS.md#已授权队列的持续推进)。已有运行任务沿用冻结契约。

## 按需资料与检查

普通任务只交付结果、必要证据与遗留事项。计量、详细报告和额外独立审查按用户要求或实际风险启用，沿用现有记录。工具调用见[执行与计量](references/execution-tools.md)，故障时读[排查说明](references/TROUBLESHOOTING.md)，部署见[README](README.md#部署)。

维护时按改动做格式、配置和引用检查；实现或行为有变化再做相关回归。具体方法见[验证指南](references/verification-guide.md)，不默认启动全部测试或模型对照。版本说明见[0.5.3记录](RELEASE-0.5.3.md)。

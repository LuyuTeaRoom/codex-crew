# 长程 Token 策略的外部依据

本文件解释为何采用阶段映射、结构化交接与使用量计量；不替代 `templates/AGENTS.md` 的运行契约。

| 来源 | 可复用结论 | 未直接采用部分 |
|---|---|---|
| [OpenAI Model Guidance](https://developers.openai.com/api/docs/guides/latest-model) | 比较代表性任务的质量、总 token、延迟与成本；多代理适用于可独立分解的工作流 | 不将官方 API 功能或价格推导为 Codex Desktop 的已启用能力 |
| [Aider architect/editor](https://github.com/Aider-AI/aider/blob/main/aider/website/docs/usage/modes.md) | 规划与编辑可分为明确阶段 | 双模型会增加请求与成本，不能默认用于小任务 |
| [CoACT](https://github.com/THU-Agent/CoACT) | 压缩应保留下一步行动所需信息，并评估总 token 与任务有效性 | 不引入其训练压缩器、模型服务或研究基准作为生产依据 |
| [DeerFlow](https://github.com/bytedance/deer-flow/blob/main/CHANGELOG.md) | 预算、委派上限、结构化 token 记录和总结状态是长程运行时的重要控制面 | 不引入其运行时、数据库或中间件 |
| [MTRouter](https://github.com/ZhangYiqun018/MTRouter) | 多轮路由应考虑当前历史和阶段，而非只看第一轮 prompt | 不引入训练路由器、外部 provider 或其 benchmark 数字 |

GitHub 项目的结果只构成设计线索。codex-crew 的 token 节省结论必须由本地代表性任务的实际 usage、质量、验证完备性、重试数和墙钟时间复核。

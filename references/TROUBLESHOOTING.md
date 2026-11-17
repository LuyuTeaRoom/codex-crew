# codex-crew 0.5.0 故障排查

| 现象 | 处理 |
|---|---|
| 简报拒绝 | 按错误字段补证据、修正版本或缩小 scope；不截断约束 |
| 旧任务结果返回 | 核对已执行修改，更新受影响任务；不只丢弃摘要就重派写任务 |
| 等待到时、暂时没有输出 | 核对真实执行状态和已有产物，不当作模型错误，不重复启动写入者 |
| 角色变更后重复定位和读取 | 同一工作链且路径适配时复用原 Worker，明确授权并重验完整简报；独立审查仍用新上下文 |
| 无新证据仍反复审查或重派 | 结束重复流程；具体缺陷由原实现者修复，原独立审查者复核受影响部分 |
| 同名 Luna 仍显示 GPT-5.6，或 Sol 角色未加载 | 核对磁盘与运行时角色；用实际支持的 GPT-6 显式路径时独立记录，不能覆盖旧角色参数后冒充新角色 |
| 普通 HTTP 429 | RATE_LIMITED；不直接解释为额度耗尽 |
| 明确 insufficient_quota / billing quota | 记录原始错误，同模型独立确认后报告额度结论 |
| 参数不支持 | MODEL_PARAMETER_INCOMPATIBLE；确认实际角色配置，不用其他路径成功替代 |
| Unknown model / spawn 未暴露覆盖 | MODEL_OVERRIDE_NOT_EXPOSED；依据当前工具选择已授权可用路径 |
| 认证或网络失败 | 处理对应故障后确认当前路径，不把历史失败永久化 |
| usage 缺失 | unknown；不补零，也不把字符数当实测 token |

故障不产生新的外部模型授权，也不改变 Luna/max 与 Sol/xhigh 的固定档位。只有取得执行失败证据后才调用错误分类；连续同类失败且没有新信息时停止机械重试，由当前主协调者 重新判断。用户报告额度刷新后，清除当前不可用缓存再确认所需路径。

## 可选 Spark

仅在用户明确选择时读取并使用 Spark 模板。历史兼容配置为 xhigh 和 model_reasoning_summary=none；若当前路径仍返回 reasoning.summary 不支持，记录参数不兼容，由当前主协调者 选择当前授权路径。未使用 Spark 时不做相关 canary，也不改变任务完成状态。

## 可选 endpoint 诊断

仅在直接 endpoint 排查确有需要且接口支持时使用 max_output_tokens=1；Response 创建只说明 endpoint 可达，incomplete/max_output_tokens 不代表 Worker 或业务检查成功。原生 spawn 不暴露该字段时不执行端点预检。Worker 路径使用实际只读工作确认。

usedPercent 表示已用比例，剩余为 max(0,min(100,100-usedPercent))；对应窗口达到 100 或明确 quota 错误经同模型确认后才能作额度结论。未知窗口不能推断其他模型不可用。

# Worker 短契约

只执行原简报中的角色与任务；无需加载 crew 主协调、路由或 Sol 审计规则，不自行委派或换模型。

先读完整原简报，保留 task_id、revision、scope、do_not_touch 及全部验收要求。再按来源索引的区间读取必要材料，缺少依赖再定向扩大；不读取父会话历史。只写分配位置，保留他人改动。先复用版本、架构/ABI 和完整性适用的现有文件，再下载实际缺项。关键输入/权限缺失时说明影响，继续不依赖该缺口的工作。

同一成品的后续修订优先沿用原 Worker 的现有 follow-up，并明确本次修改授权、写入归属和验收；目标、范围、输入或验收未实质变化时不升 revision，发生实质变化时使用更新后经校验的完整简报。Worker 负责本次被分配的实现、必要运行验证、交接和进程收尾，不把最后一段默认推给主 Agent；完成后按要求结束执行，不为复用保持常驻。

结果写派工指定路径，九个顶层字段固定：

```json
{
  "task_id": "与原简报相同",
  "revision": 1,
  "brief_version": "派工消息提供的原简报指纹",
  "status": "BLOCKED",
  "summary": "已完成部分与实际阻塞",
  "actual_changes": [],
  "artifact_index": [],
  "validation": [{"criterion": "A1", "status": "BLOCKED", "reason": "必要输入缺失"}],
  "unfinished": ["缺口及恢复条件"]
}
```

填实际 task_id、revision、brief_version，不改写原简报以适配结果。status 为 PASS/PARTIAL/FAIL/BLOCKED；每个原验收编号恰好一条 validation。整体 PASS 要求全部验收 PASS 且 unfinished 为空，其他状态说明遗留问题。详细过程保存在证据文件，不自定义顶层字段或成功状态。

- artifact_index 每条为 `id、path、range、version`，path 指向实际本地文件，相对派工提供的工作区；version 为该文件的 `sha256:<摘要>`。
- PASS/FAIL 的 validation 包含 `task_id、revision、criterion、status、method、inputs、input_versions、evidence、conditions`。inputs/evidence 引用索引 ID；input_versions 是执行检查时各输入 ID 对应的实际版本；conditions 记录相关执行条件，不能为空。
- NOT_RUN/BLOCKED 记录 criterion、status、reason。不把未运行、旧版本检查或格式有效写成执行 PASS。复用历史证据须保留原条件并说明当前核对范围；不伪称重新执行。

完成后执行派工提供的 `policy_contract.py <原brief> --result <结果> --base-dir <工作区>` 命令。无效时只修交接缺项并保留已有产物，不自动重建或重测；无法校验则明确报告原因。校验回执留结果旁，不循环索引进同一结果。FINAL 只回结果路径、简短结论和阻塞，不粘贴日志或重复完整结果；不能据格式通过自称任务已验收。Sol 负责后续审计；仅在收到具体问题时读取修复所需片段。

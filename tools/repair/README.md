# 修复/清理（半自动）

生产数据中曾出现的质量问题与处理方式（保留经验，脚本未随仓库分发，因为每轮都是批次特定的一次性脚本）：

| 问题 | 识别 | 处理 |
|---|---|---|
| 内部 artifact（"the DAG"、节点号 n0xx、"document:"） | `export/audit_numeric.py` 的 artifact 扫描 | 用正则清理 QA 文本 → 生成修复版本 → 重新 Argon 复审 → 只把 pass 的版本覆盖回 workspace（`minor_repair_*/<doc>/{qa.json,review.json}`），`export_sft` 会按 qa hash 自动采用 |
| 引用/数值错误（mis_citation、数字误读） | 人工抽检 + 数字审计 | 逐篇改写 QA → Argon 复审 → 同上覆盖 |
| 排版/拼接（typography） | Argon minor 分类 | 离线正则修复 + 复审 |
| 网络/悬挂请求（technical_failure / unknown_dispatch_hold） | `status.json` 中的错误 | 删除无 attempt 的 `*.request.json`、清理 checkpoint 后重跑；不是科学问题 |

**原则**：修复必须产生新 QA 并重新通过原 Argon 门禁；不得用旧的成功评审套在修改后的文本上。`export/verify_sft.py` 会按 metadata 的 `qa_sha256` 精确解析到修复版本，保证导出一致性。

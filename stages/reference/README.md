# 上游阶段参考实现（非核心库）

这些脚本来自生产批次（node032, 2026-11），**代码可用但硬编码了批次路径/环境**，复现时需按 RUNBOOK 调整：

| 文件 | 作用 | 依赖 |
|---|---|---|
| `acquire.py`, `intake.py` | bioRxiv S3 (requester-pays) 抓取 JATS XML | boto3、AWS 凭证 |
| `preflight.py`, `workflow.py` | 抓取前检查 / 批处理编排 | lxml |
| `prepare_fulltext.py` | JATS XML → source blocks / evidence 目录 | lxml |
| `screen.py`, `budget_local.py` | 全文适用性筛选（screening 模型） | 模型网关、aiohttp |
| `select_papers_example.py`, `build_selection_b.py` | 分层抽样（domain→subdomain→topic） | 无 |
| `setup_batch_example.py` | 早期 workspace 构建示例（`pipeline/setup_workspace.py` 的通用版） | lxml |

核心生产/导出/验证链路在 `pipeline/` 与 `export/`。

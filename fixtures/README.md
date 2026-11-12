# Fixtures

`fixtures/synthetic/` 是完全合成的一篇"论文"（无版权内容），用于离线 e2e 测试：

- `evidence/biorxiv_10_9999_2026_01_01_000001_v1/`：`article.xml`（最小 JATS）、`blocks.json`、`source_validation.json`
- `selection.json`：一篇文档的分层分类记录
- `accepted/`：模拟已通过门禁的 `qa_formatted.json` 与 `argon_final.json`（供 export/verify 测试）

`tests/test_smoke.py` 会用它完成：构建 workspace → 导出 SFT → 验证 → 数字审计。

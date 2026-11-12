# bio-pipeline — bioRxiv 论文 → 证据 DAG → 长文推理 QA（SFT 数据）

从 bioRxiv 论文全文出发，构建**证据图（DAG）**、生成**基于原文证据的长文推理 QA**，经**多模型科学评审门禁**后导出可直接训练的 SFT 数据集（多格式、family-safe 切分、可复现验证）。

> 本项目是 2026 年 bioRxiv QA 生产线的工程化整理版：**核心科学约定、提示词、schema、tokenizer 全部冻结并随仓库分发**，生产/导出/验证链路已用 1000+ 篇真实论文验证（v10：1032 条，0 hash/token mismatch、0 artifact、split 干净）。

## 仓库结构

```
frozen/                    冻结科学契约（prompts, schema, node/edge types, tokenizer, 校验库）
pipeline/                  生产链路核心
  setup_workspace.py       由 evidence 目录构建 QA workspace
  runner.py                QA 生产 runner（v5：64k 输出预算、JSON 恢复、结构重试、预算/并发控制）
  run_workflow_v5.py       单篇执行 workflow（extract → DAG 校验 → generate → Argon verify → format）
  finalize.py              导出前完整性校验 + chat template 渲染 + metadata
export/                    SFT 导出与验证
  export_sft.py            workspace → 多格式 SFT（messages/sharegpt/swift/alpaca/text，train/val，ctx32k）
  verify_sft.py            逐行全量验证（hash/token/artifact/引用/split）
  audit_numeric.py         全量数字-引用审计（派生感知）
tools/                     数据集工具（add_ids, distribution, repair 经验脚本）
stages/reference/          上游阶段参考实现（下载/全文/筛选/抽样；见该目录 README 的状态说明）
fixtures/synthetic/        合成小样本（离线 e2e 测试用，无版权文本）
tests/test_smoke.py        离线冒烟测试（frozen hash + 构建 workspace + 导出 + 验证 + 审计）
docs/                      设计、数据格式、验收标准、安全说明
```

## 快速开始（离线，无需 API key）

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python tests/test_smoke.py        # frozen hash 校验 + e2e 冒烟（约 2 秒）
```

## 端到端流程（生产）

```bash
# 1) 论文全文 → evidence 目录（或直接使用你已有的 JATS XML + blocks.json）
# 2) 构建 QA workspace（输入: evidence/, selection.json, 配置文件）
python pipeline/setup_workspace.py --evidence-dir /data/evidence --selection /data/selection.json \
       --out /data/workspace --config configs/config.example.json --budget-cny 300

# 3) 生产 QA（需要模型网关 API key；runner 从 stdin 读取 key）
export BIO_QA_WORKSPACE=/data/workspace
printf '%s' "$YUNJINTAO_API_KEY" | python pipeline/runner.py
#   → 每篇文档产出 tasks/<doc>/papers/<doc>/{dag,qa,qa_formatted,argon_final}.json
#   → 结束时 finalize() 写出 training/{final_messages,final_train,metadata}.jsonl

# 4) 导出 + 校验
python export/export_sft.py --out /data/sft_export --workspaces /data/workspace
python export/verify_sft.py --export-dir /data/sft_export --workspaces /data/workspace
python export/audit_numeric.py --export-dir /data/sft_export --workspaces /data/workspace

# 5) 加 bioRxiv 编号字段 + 分类分布（可选）
python tools/add_ids.py --export-dir /data/sft_export
python tools/distribution.py --export-dir /data/sft_export --workspaces /data/workspace
```

完整步骤、依赖、验收标准见 **[RUNBOOK.md](RUNBOOK.md)**；配置项见 `configs/config.example.json`。

## 各阶段状态（如实说明）

| 阶段 | 代码 | 状态 | 外部依赖 |
|---|---|---|---|
| 论文发现/下载 | `stages/reference/acquire.py` 等 | **参考实现**（生产使用过，需适配路径） | bioRxiv/AWS requester-pays、boto3 |
| 全文重建（XML→blocks） | `stages/reference/prepare_fulltext.py`、`screen.py` | **参考实现** | lxml、aiohttp、深度求索 screening 模型 |
| 适用性筛选 | `stages/reference/screen.py` | **参考实现** | 模型网关 |
| 分层抽样 | `stages/reference/select_papers_example.py` | 可移植（改路径即可） | 无 |
| **QA 生产** | `pipeline/` | **生产验证** | 模型网关（extract/generate/verify 三个模型） |
| **SFT 导出** | `export/export_sft.py` | **生产验证** | 无（离线，tokenizer 冻结） |
| **验证/审计** | `export/verify_sft.py`、`audit_numeric.py` | **生产验证** | 无（离线） |
| 修复/清理（artifact/minor） | `tools/repair/` 经验脚本 | 半自动，需人工判断 | 模型网关（复审） |

## 科学约定（冻结）
- 单篇流程：**证据 DAG 提取 → 结构/引用校验 → QA 生成 → Argon 评审（verdict=pass 且无 major）→ 格式化与格式守恒校验 → 固定 tokenizer/模板导出**
- `schema.json`、`node_types.json`、`edge_types.json`、`prompts.json`、`tokenizer/` 一律以 `frozen/` 为准，**仓库测试会校验其 sha256**（`frozen/MANIFEST.sha256`）
- 校验函数来自 `frozen/pipeline/run.py`：`graph_check` / `qa_check` / `review_check` / `normalize`
- 允许 Argon minor，但会计数并保留；不得用未授权的额外门槛替代原门禁

## 数据与成本
- 生产规模参考：1660 篇（两批）→ 1032 条 SFT；约 ¥7800（含网络故障损耗，仅供参考）
- 单篇成本取决于模型单价、源文长度、重试次数；`config.example.json` 中 `budget_cny` 是硬上限（budget_guard 强制执行）
- 数据集许可：论文来自 bioRxiv，遵循每篇原文 license（见 metadata `license`）；**本仓库不含论文全文**，`fixtures/` 为合成内容

## 已知限制
- 上游下载/筛选代码是从生产批次整理的参考实现，硬编码路径需要按 RUNBOOK 调整
- 模型名是某一供应商网关 ID（`gpt-6-astra` / `claude-opus-5-5` / `gemini-4-argon`），换网关需在 config 中替换
- 不能宣称 SFT 数据零语义错误：残余 Argon minor（引用/排版/措辞级）按门禁策略保留并计数
- 未包含训练脚本；`configs/` 提供 LLaMA-Factory / ms-swift 模板

## License
代码 MIT（见 LICENSE）。论文内容版权归原作者/期刊，遵循原文许可。

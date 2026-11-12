# 设计说明

## 目标
把一篇 bioRxiv 论文的全文证据转成**可核查、长链推理的长文 QA**，并作为 SFT 监督数据交付。
质量由"结构 + 引用 + 科学评审"三重门禁保证，而不是只靠 LLM 自评。

## 数据流

```
bioRxiv 论文 (JATS XML)
   │  prepare_fulltext: 抽取正文段落/图表标题/公式 → 带 XML xpath 的 source blocks
   ▼
evidence/<doc>/{article.xml, blocks.json, source_validation.json}
   │  setup_workspace: 生成 production manifest + 输入哈希 + 冻结资产链接 + 预算配置
   ▼
QA workspace
   │  runner → run_workflow_v5（每篇）:
   │    1. extract:  源文 blocks + node/edge 类型 → 证据 DAG
   │    2. graph_check: schema + 节点/边/引用/无环校验（失败 → 一次结构重试）
   │    3. generate:  DAG + 源文 → 长文 QA（带 [doc:bNNNN] 引用）
   │    4. qa_check:  引用合法、reasoning_dependencies 成链
   │    5. verify:    Argon 科学评审 → verdict=pass 且 major_issues 为空
   │    6. format:    固化格式（空格、引用括号、标点）并做格式守恒校验
   ▼
tasks/<doc>/papers/<doc>/{dag,qa,qa_formatted,argon_final}.json + status.json
   │  finalize: chat template 渲染 + tokenizer 编码校验 + metadata
   ▼
training/{final_messages,final_train,final_text,metadata}.jsonl
   │  export_sft: family-safe 切分 + 5 种格式 + ctx32k + 统计/哈希
   ▼
SFT export ── verify_sft / audit_numeric ──► 交付包
```

## 关键约束（冻结）
- `frozen/schema.json`、`frozen/node_types.json`、`frozen/edge_types.json`、`frozen/pipeline/prompts.json`
- `frozen/tokenizer/{tokenizer.json, chat_template.jinja, tokenizer_manifest.json}`
- `frozen/pipeline/run.py` 中的 `graph_check` / `qa_check` / `review_check` / `normalize`
- 任何修改冻结资产都会使 `tests/test_smoke.py` 失败；升级必须显式 bump manifest 并在 docs 记录

## 幂等与断点续跑
- 每篇文档的每个阶段结果保存为 JSON；重跑时复用已成功的请求（含请求 hash 校验）
- `run_workflow_v5.execute` 用 checkpoint 指纹（doc + blocks + prompts + models + schema + runtime）保证输入变化时不会复用错误缓存
- 预算由 `budget_guard` 持久化在 workspace 的 `budget.sqlite`，硬上限

## 质量策略
- 科学通过 = Argon `verdict=pass` 且 `major_issues` 为空；minor 允许但计数
- 技术失败 ≠ 科学拒绝：网络/解析失败单独重试，不自动降级
- 导出前 `finalize` 强制：review 与 QA 版本 hash 对应、格式守恒、tokenizer 往返一致
- `verify_sft` + `audit_numeric` 是全量核查（不是抽样）；人工抽检作为语义层补充

# 数据格式

## Workspace（QA 生产目录）
```
workspace/
  config.json                 # 模型/预算/并发/runtime（见 configs/config.example.json）
  production_manifest.json    # 文档记录：document_id, doi, version, sources, split_family, license...
  production_input_hashes.json
  protected_external_hashes.json
  import_hashes.json
  frozen -> <repo>/frozen
  inputs/<document_id>.json   # source blocks
  status.json                 # runner 运行状态与每篇结果
  tasks/<doc>/
    result.json               # 状态: ready / scientific_reject / technical_failure / budget_hold ...
    papers/<doc>/
      dag.json                # 证据 DAG（schema.json）
      qa.json                 # 原始 QA
      qa_formatted.json       # 格式固化后 QA（含最终引用）
      argon_final.json        # 最终 Argon 评审
  training/                   # finalize 输出（messages/text/metadata）
  budget.sqlite
```

## source blocks（`inputs/<doc>.json`）
```json
[{"id": "<document_id>:b0001", "section": "Abstract|Results|...",
  "text": "…", "xml_xpath": "/article/body/sec[1]/p[2]",
  "char_start": 0, "char_end": 900}]
```

## SFT 导出
- `metadata_unified.jsonl`（每行）：`document_id, doi, version, publication_year, license, split_family,
  batch, source_sha256, qa_sha256, review_sha256, serialized_tokens, answer_tokens, text_sha256,
  minor_count, repaired, [classification]`
- `split_manifest.json`：`{train: [...], val: [...]}`（按 DOI family 切分，无泄漏）
- 格式文件（`train`/`val` + `_ctx32k` 后缀）：
  - `messages_*`：`{messages:[{role,content}], document_id, doi, version}`（OpenAI/ms-swift）
  - `sharegpt_*`：`{conversations:[{from,value}], system, document_id, doi, version}`（LLaMA-Factory）
  - `swift_*`：同 messages 结构
  - `alpaca_*`：`{instruction, input, output, system, document_id, doi, version}`
  - `text_*`：`{text, document_id, doi, version}`（chat template 已渲染）
- `stats.json` / `hashes.json` / `llama_factory_example.yaml` / `swift_example.yaml`

## 注意
- `text_*` 已渲染 Qwen chat template，训练时**不要重复套模板**
- 32k 训练用 `*_ctx32k`；更长上下文用 full 并按框架截断策略处理
- `messages/sharegpt/swift/alpaca` 中额外字段（document_id/doi/version）一般被训练框架忽略，可用于溯源

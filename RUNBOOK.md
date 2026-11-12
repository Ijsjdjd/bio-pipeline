# RUNBOOK — 从论文到 SFT 数据集的完整操作手册

> 本手册描述如何从零重建流程。核心 `pipeline/` + `export/` 是生产验证过的；上游 `stages/reference/` 是从生产批次整理的参考实现，路径/服务需按实际环境调整。

## 0. 环境

```bash
python3.12 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt          # jsonschema, tokenizers, jinja2
pip install lxml aiohttp boto3           # 仅上游阶段需要
python tests/test_smoke.py               # 必须先通过（frozen 契约 + 离线 e2e）
```

模型网关（OpenAI 兼容）：设置环境变量 `YUNJINTAO_API_KEY`（或你自建网关的 key），
在 `configs/config.example.json` 里改 `api_base` 和三个模型 ID。

## 1. 获取论文（evidence 目录）

两种方式：
- **已有 JATS XML**：组织为 `evidence/<document_id>/{article.xml, blocks.json, source_validation.json}`，
  参考 `fixtures/synthetic/evidence/`。`blocks.json` 字段：`id/section/text/xml_xpath/char_start/char_end`，
  `source_validation.json` 至少含 `{"pass": true, "xml_sha256": "<article.xml sha256>"}`。
- **从 bioRxiv 抓取**：参考 `stages/reference/acquire.py`（S3 requester-pays）与 `prepare_fulltext.py`。

## 2. 选文与 selection.json

```json
{"documents": [{"document_id": "...", "doi": "...", "version": "1", "title": "...",
                "source_dir": "/data/evidence/<id>", "xml_sha256": "...",
                "classification": {"domain_id": "D01", "domain": "...", "subdomain_id": "SD01",
                                   "subdomain": "...", "topic_id": "T001", "topic": "..."}}]}
```

分层抽样可参考 `stages/reference/select_papers_example.py`（domain→subdomain→topic 近比例 + 每 topic 至少 1 篇 + 固定 seed）。

## 3. 构建 QA workspace

```bash
python pipeline/setup_workspace.py \
  --evidence-dir /data/evidence --selection /data/selection.json \
  --out /data/workspace --config configs/config.example.json --budget-cny 300
```
产出：`inputs/`、`production_manifest.json`、`production_input_hashes.json`、
`protected_external_hashes.json`、`import_hashes.json`、`config.json`、`frozen -> repo/frozen`。

## 4. 生产 QA

```bash
export BIO_QA_WORKSPACE=/data/workspace
printf '%s' "$YUNJINTAO_API_KEY" | python pipeline/runner.py
```
- 逐篇执行 `extract → DAG 校验（失败一次结构重试）→ generate → Argon verify → format`
- 技术失败与科学拒绝分开记录；预算由 `budget_guard` 强制（`budget_cny`）
- 结束时自动 `finalize()`：按冻结 tokenizer/chat template 渲染并写出 `training/`
- 支持断点续跑：已完成任务不重复调用（`status.json` + `tasks/<doc>/result.json`）

## 5. 导出 SFT

```bash
python export/export_sft.py --out /data/sft_export --workspaces /data/workspace
```
输出 5 种格式 × train/val（另含 ctx32k 子集）、`metadata_unified.jsonl`、`split_manifest.json`、
`stats.json`、`hashes.json`、训练配置模板。

多批合并：`--workspaces /data/ws_a /data/ws_b`（family-safe 切分在合并后统一执行）。

## 6. 验证与审计（必须全绿）

```bash
python export/verify_sft.py    --export-dir /data/sft_export --workspaces /data/workspace
python export/audit_numeric.py --export-dir /data/sft_export --workspaces /data/workspace
```
验收标准（固定，不允许降低）：
- `verification_final.json`：`error_count=0, hash_mismatch=0, token_mismatch=0, artifact_hits=0`，split 无重叠/无 family 泄漏
- `final_audit.json`：`artifact_docs=0`、`unknown_citation_docs=0`、`duplicates=0`；数字未解释候选需人工抽样确认（多为逗号/派生格式）
- 人工抽检建议 ≥10 篇（回源核对数字/引用/推理）

## 7. 编号与分布（可选）

```bash
python tools/add_ids.py --export-dir /data/sft_export
python tools/distribution.py --export-dir /data/sft_export --workspaces /data/workspace
```

## 8. 打包

```bash
cd /data && tar -czf sft_export.tar.gz sft_export
sha256sum sft_export.tar.gz > sft_export.tar.gz.sha256
cd sft_export && python - <<'PY'
import json,hashlib,pathlib
h=json.load(open('hashes.json'))
bad=[f for f,d in h.items() if hashlib.sha256(pathlib.Path(f).read_bytes()).hexdigest()!=d]
print('files',len(h),'bad',bad)
PY
```

## 常见问题
- **`Input/config changed`**：workspace 的 config/runtime 改过但旧 checkpoint 还在 → 新 workspace 或清理对应 `tasks/<doc>/papers/<doc>/checkpoint.json`
- **`unknown_dispatch_hold`**：上次进程被强停留下 `*.request.json` → 删除无对应 attempt 的 request 文件后重跑该篇
- **预算耗尽**：runner 停止并保留 `budget_hold` 状态；提高 `budget_cny` 后重跑会只补未完成部分
- **模型网关报错**：检查 `api_base`、模型 ID、并发/限速（`workers`, `requests_per_minute`）

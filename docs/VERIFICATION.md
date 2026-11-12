# 验收标准与核查方法

## 生产门禁（每篇，固定）
1. `graph_check`：schema 校验 + 节点 ID/证据引用/无环
2. `qa_check`：question/answer 非空、引用 ID 全部存在、reasoning_dependencies 成链
3. `review_check` + Argon：`verdict == "pass"` 且 `major_issues == []`
4. `formatting.validate`：格式化前后内容守恒
5. `finalize`：review/QA hash 对应、tokenizer 往返一致、无内部 artifact

## 数据集验收（离线、全量）
```bash
python export/verify_sft.py --export-dir OUT --workspaces WS
python export/audit_numeric.py --export-dir OUT --workspaces WS
```
必须满足：
- `error_count=0`
- `hash_mismatch=0`（messages/text/sha256 与 accepted QA 一致）
- `token_mismatch=0`（tokenizer 重算一致）
- `artifact_hits=0`（无 DAG/node-ID/占位符/道歉语等内部语言）
- `unknown_citation_docs=0`（引用 ID 均在 source blocks 中）
- `duplicates=0`
- train/val 无重叠、无 family 泄漏

## 允许的残余
- Argon `minor_issues`（引用粒度/排版/措辞）会保留并计数，不是验收失败；但需记录在 `verification_final.json`
- `audit_numeric` 的未匹配数字候选需人工抽样：生产数据中均为同句派生/四舍五入/逗号格式（例如 621/849=73%、2176/12≈181）

## 抽检建议
- 从 train/val 分层随机抽 ≥10 篇，回源核对 2–3 个关键数字/比较声明
- 检查修复过的文档：`repaired=true` 的行应使用修复后 QA 与对应 review

## 禁止
- 不得为了通过而删/改冻结 schema、提示词、校验函数或降低门禁
- 不得把"技术失败"当作"科学拒绝"，也不得对科学拒绝自动无限重试

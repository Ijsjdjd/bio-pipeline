# 安全与合规

## 密钥
- 仓库**不包含**任何 API key、密码、服务器登录信息
- 运行时从环境变量读取 key（默认 `YUNJINTAO_API_KEY`；见 config `api_key_env`）
- `.gitignore` 排除 `.env`、`.secrets/`、`*.log`、workspace 输出
- 日志/错误信息中 key 会被替换为 `[REDACTED]`

## 数据许可
- 论文来自 bioRxiv，每篇 license 记录在 `metadata_unified.jsonl` 的 `license` 字段
- **仓库不分发论文全文**；`fixtures/synthetic/` 是合成内容
- 使用数据时遵循原文许可（多为 CC-BY-NC / CC-BY-NC-ND），尤其不得用于商业用途或再分发全文
- 数据集发布/共享前请核对目标论文的 license 与机构政策

## 模型
- 模型名是网关 ID，不代表已核实底层身份；换网关时在 config 显式配置
- 不要在提示词/输出中注入密钥；本流程的提示词对源文按不可信数据处理

## 运行环境
- 建议在隔离目录运行；`budget_cny` 是硬上限，防止意外超支
- 外部下载（boto3 / bioRxiv）需要你自己的凭证与合规授权

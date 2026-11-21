# Free API Gateway (new-api)

聚合第三方**免费档** AI API（**Numes / OpenRouter / Groq**），暴露统一的 OpenAI 兼容端点，
供 OpenCode 等客户端使用。基于 [QuantumNous/new-api](https://github.com/QuantumNous/new-api) 自托管。

> 🔒 **安全**：真实 API Key 存放在被 `.gitignore` 忽略的 `.env` 中，**绝不提交**；仓库只包含 `.env.example` 占位符。

## 快速开始

完整步骤见 **[SETUP.md](./SETUP.md)**。一句话版：

```bash
cp .env.example .env      # 填入三个上游 Key + NEW_API_TOKEN
source .env
docker compose up -d      # 打开 http://127.0.0.1:3000 走设置向导
# 控制台里添加 Numes / OpenRouter / Groq 三个渠道（字段值见 SETUP.md）
# 生成令牌填回 NEW_API_TOKEN，然后：
./verify.sh               # 验证网关与三条渠道
```

把 [opencode.jsonc](./opencode.jsonc) 合并进 `~/.config/opencode/opencode.jsonc`，
在 `/models` 里选 `gw/<model-id>`。

## 文件

| 文件 | 作用 |
| --- | --- |
| `docker-compose.yml` | new-api 单实例（SQLite，仅绑定 `127.0.0.1:3000`） |
| `.env.example` | 上游 Key 模板（真实值放 `.env`） |
| `opencode.jsonc` | OpenCode 对接配置（V2 格式，17 个模型） |
| `verify.sh` | 网关 / 渠道连通性验证脚本 |
| `SETUP.md` | 完整安装、渠道、令牌、排障步骤 |

## 上游渠道

| 渠道 | 类型 | 免费模型 |
| --- | --- | --- |
| **Numes** | 自定义（OpenAI 兼容） | GLM 5.3 / DeepSeek V4.1 Flash / Kimi K3 / Nemotron 等 9 个（`:free`） |
| **OpenRouter** | 内置预设 | `:free` 模型（`openrouter/auto`、Llama、Gemini、Qwen 等） |
| **Groq** | 自定义（OpenAI 兼容） | Llama 3.3 / 3.1 / Mixtral / Gemma 2 |

## 注意

- 免费档普遍限流、可能返回 429；部分模型会用你的数据改进服务或留存 30 天，**勿发送敏感内容**。
- 上游条款由你遵守；免费额度随时可能变更或下线。
- new-api 自身许可证为 **AGPL-3.0**（个人自托管无碍；对外分发/提供服务时注意 copyleft 义务）。

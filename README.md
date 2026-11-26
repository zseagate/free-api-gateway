# Free API 环境配置 & OpenCode 接入指南

本仓库集中管理多个免费/低成本 AI 推理 API 的环境配置、OpenCode 接入示例、连通性检测脚本。

> ⚠️ **安全**：真实 API Key 存放在被 `.gitignore` 忽略的 `.env` 中，**绝不提交**。仓库只包含 `.env.example` 占位符。

> 🚀 **推荐聚合方案**：优先使用 [free-api-gateway](https://github.com/zseagate/free-api-gateway)（自托管 new-api 网关，聚合 Numes/OpenRouter/Groq，客户端只用一把网关令牌），配置见 `opencode-gateway.jsonc`。下方直连方案保留作备选。

## 仓库结构

```
free-api-config/
├── .env.example                 # 环境变量模板(占位符, 可提交)
├── .env                         # 真实配置(已 gitignore, 不提交)
├── .gitignore
├── api-check.sh                 # 连通性/可用性检测脚本
├── opencode-gateway.jsonc       # OpenCode provider 配置示例: free-api-gateway 网关 (推荐, V2)
├── opencode-numes.jsonc         # OpenCode provider 配置示例: Numes (OpenAI 兼容, V1)
├── opencode-fcc.jsonc           # OpenCode provider 配置示例: free-claude-code (本地反代, V1)
├── opencode-openrouter.jsonc    # OpenCode provider 配置示例: OpenRouter (免费模型, V1)
├── opencode-groq.jsonc          # OpenCode provider 配置示例: Groq (免费模型, V1)
└── README.md
```

## 快速开始

```bash
cp .env.example .env      # 然后编辑 .env 填入真实 API Key
source .env               # 加载环境变量
```

### 检测连通性

```bash
# Numes 免费模型 (9 个)
./api-check.sh                    # 检测全部(串行)
./api-check.sh --parallel         # 并行, 更快
./api-check.sh --watch 60 --parallel --stop-on-ok   # 每 60s 轮询, 恢复后自动退出
./api-check.sh --model z-ai/glm-5.3:free            # 指定模型
./api-check.sh --list-free                          # 列出已知免费模型

# 任意 OpenAI 兼容 API (如 OpenRouter / Groq / 本地反代)
./api-check.sh --base-url https://openrouter.ai/api/v1 --key $OPENROUTER_KEY --model openrouter/auto
./api-check.sh --base-url http://127.0.0.1:8082/v1 --key $NUMES_API_KEY --model nvidia/nemotron-3-ultra-550b-a55b:free
```

结果标记：`OK` 可用 / `TIMEOUT|HANG` 免费档繁忙 / `OVERLOAD` 429 限流 / `NOCREDIT` 余额不足 / `BADKEY` Key 无效。

---

## 支持的免费 API 提供商对比

| 提供商 | 类型 | 免费额度 | OpenCode 配置文件 | 备注 |
|---|---|---|---|---|
| **Numes** | OpenAI 兼容网关 | 9 个免费模型 (GLM, Nemotron, DeepSeek, Kimi 等) | `opencode-numes.jsonc` | 直连 `https://api.numes.ai/api/v1` |
| **free-claude-code (FCC)** | 本地反代网关 | 聚合 NVIDIA NIM / OpenRouter / Groq 等免费额度 | `opencode-fcc.jsonc` | 本地 `http://127.0.0.1:8082/v1`，需先安装 FCC |
| **OpenRouter** | OpenAI 兼容聚合 | 100+ 免费模型 (`:free` 后缀) | `opencode-openrouter.jsonc` | 需 API Key，免费档有限额 |
| **Groq** | OpenAI 兼容 | 高速免费推理 (Llama, Mixtral, Gemma 等) | `opencode-groq.jsonc` | 需 API Key，速度极快 |

---

## 接入 OpenCode

### 0. free-api-gateway (new-api 自托管网关) —— 推荐聚合方案

把多个免费档（Numes / OpenRouter / Groq）聚合到统一 OpenAI 兼容端点，客户端只用一把网关令牌。详见上游 [free-api-gateway](https://github.com/zseagate/free-api-gateway) 的 `SETUP.md`，本地只含对接配置 `opencode-gateway.jsonc`：

```bash
# 1) 进入上游仓库目录后启动网关 (首次走 http://127.0.0.1:3000 设置向导建管理员)
cd free-api-gateway && docker compose up -d
# 2) 控制台添加三个渠道: Numes=Custom(8) / OpenRouter=OpenRouter(20) / Groq=OpenAI(1)
# 3) 「令牌」页生成 sk-... 填入 .env 的 NEW_API_TOKEN
# 4) (推荐) 启动 groq-proxy (见上游 SETUP.md §7); 不用代理则把 opencode-gateway.jsonc 的 baseURL 改为 127.0.0.1:3000/v1
# 5) 把 opencode-gateway.jsonc 的 providers.gw 合并进 OpenCode 配置, 选 gw/<model-id>
```

> 注意：`opencode-gateway.jsonc` 默认 `baseURL` 是 `127.0.0.1:3001/v1`（groq-proxy，转发到 3000 并为 Groq 截断 prompt），**需先启动 groq-proxy**；不跑代理就改成 `127.0.0.1:3000/v1`。

`.env` 需额外填 `OPENROUTER_API_KEY`、`GROQ_API_KEY`、`NEW_API_TOKEN`（模板见 `.env.example`）。完整排障（`tool calling is not supported`、`reasoning_content` 回传、Groq 429 等）见 `AGENTS.md`「free-api-gateway 规则」与上游 `SETUP.md` §9。

### 1. Numes (直连)

参考 `opencode-numes.jsonc`，把 `numes` 段合并进 `~/.config/opencode/opencode.jsonc`。

Key 通过环境变量引用 `{env:NUMES_API_KEY}`，无需在配置里写明文。

> 排障：若报 `reasoning.effort is not supported by any endpoint for this model`，是因为 OpenCode 默认发 `medium`，而 Numes 免费端点不支持。示例里已为每个模型设置 `options.reasoningEffort="high"`（`high` 是唯一被全部免费端点支持的 effort）。若 TUI 里选过 variant（缓存在 `~/.local/state/opencode/model.json`），按 `Ctrl+T` 选为 `high` 或清除该项。
>
> 排障：当前 OpenCode 的 numes provider 指向本地反代 `http://127.0.0.1:8082/v1`（free-claude-code / fcc-server），该服务再转发到 Numes（`https://api.numes.ai/api/v1`）。若 OpenCode 报 `provider response was invalid` / `no provider is available` / 超时，往往是该反代链路或 Numes 后端异常导致。可通过 `curl http://127.0.0.1:8082/v1/models` 探测反代状态；Numes 免费档恢复后（`api-check.sh` 全 OK），反代与 OpenCode 即可恢复。

### 2. free-claude-code (FCC 本地反代) —— 推荐聚合方案

[Free Claude Code (FCC)](https://github.com/Alishahryar1/free-claude-code) 是一个本地反代服务，把多个免费 provider (NVIDIA NIM、OpenRouter、Groq、Cerebras 等) 聚合到一个 OpenAI 兼容端点 `http://127.0.0.1:8082/v1`。

**安装 FCC (macOS/Linux):**

```bash
curl -fsSL "https://raw.githubusercontent.com/Alishahryar1/free-claude-code/main/scripts/install.sh" | sh
# 安装时选择 OpenCode 作为 harness
```

**启动服务:**

```bash
fcc-server
# 访问输出的 Admin UI (通常 http://127.0.0.1:8082/admin)
# 在 Providers 中添加: NVIDIA NIM (免费档 1.3B+ tokens/月) 或 OpenRouter / Groq 等
# 在 Model Config 选择模型 (如 nvidia_nim/nvidia/nemotron-3-ultra-550b-a55b)
```

**OpenCode 直连 FCC (推荐方式，不依赖 `fcc-opencode` 包装):**

参考 `opencode-fcc.jsonc`，合并进 `~/.config/opencode/opencode.jsonc`。

```jsonc
{
  "provider": {
    "fcc": {
      "name": "fcc",
      "npm": "@ai-sdk/openai-compatible",
      "options": {
        "baseURL": "http://127.0.0.1:8082/v1",
        "apiKey": "{env:NUMES_API_KEY}"
      },
      "models": {
        "nvidia/nemotron-3-ultra-550b-a55b:free": {
          "name": "Nemotron 3 Ultra (free)",
          "options": { "reasoningEffort": "high" }
        },
        "nvidia_nim/nvidia/nemotron-3-ultra-550b-a55b": {
          "name": "Nemotron 3 Ultra via NIM (free)"
        }
      }
    }
  }
}
```

> ⚠️ FCC 需要在**与 OpenCode 同一台机器、同一用户环境**下运行。免费额度受 upstream provider 政策影响。

### 3. OpenRouter (免费模型聚合)

参考 `opencode-openrouter.jsonc`。在 OpenRouter 网站获取 API Key，免费模型以 `:free` 后缀标识。

### 4. Groq (高速免费)

参考 `opencode-groq.jsonc`。在 Groq 控制台获取 API Key，适合低延迟场景。

---

## 常见排障清单

| 现象 | 可能原因 | 排查步骤 |
|---|---|---|
| `reasoning.effort is not supported...` | 模型不支持默认 `medium` | 给模型加 `options.reasoningEffort="high"` (Numes 免费) |
| `provider response was invalid` / `Provider response headers timed out` | 上游响应超时/挂起 | 1. `api-check.sh --watch` 确认后端恢复 2. 检查反代链路 (FCC 等) |
| `no provider is available` | 后端/反代不可用 | 同上，确认后端与反代服务均存活 |
| `invalid credentials` / `Invalid token` | Key 未传/失效 | 1. `opencode.jsonc` 里 `options.apiKey` 配对 2. `.env` 与环境变量一致 |
| 首 token 极慢 (>30s) | 免费档冷启动/排队 | 耐心等待；或换 Groq/Cerebras 等高速免费 provider |

---

## 其它 OpenAI 兼容服务通用用法

本检测脚本适用于任意 OpenAI 兼容 API：

```bash
./api-check.sh --base-url https://api.deepseek.com/v1 --key sk-xxx --model deepseek/deepseek-chat
./api-check.sh --base-url http://127.0.0.1:8082/v1 --key $KEY --model nvidia/nemotron-3-ultra-550b-a55b:free
```

---

## 许可证 / 维护

- 仅供个人使用。
- 变更 `.env` 前请确认已 gitignore。
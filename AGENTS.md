# AGENTS.md

本仓库只含配置与文档，无代码构建/测试/CI。所有内容面向 OpenCode + 免费 AI API 接入。

## 安全（最重要）

- 真实 API Key 只放在被 `.gitignore` 忽略的 `.env` 中，**绝不提交、绝不写进任何文档/示例**。
- 对外内容一律用 `.env.example` 的占位符（如 `sk-numes-v1-REPLACE_ME`、`{env:NUMES_API_KEY}`）。
- 修改 `.env` 前先确认已 gitignore；`*.key`、`auth.json` 同样被忽略。

## 关键事实

- **两种 OpenCode 配置格式并存，勿混用**：
  - **V1 直连片段**（`opencode-numes.jsonc` / `-fcc` / `-openrouter` / `-groq`）：是 `provider` 段的片段，合并进用户配置的 `"provider"` 键（singular），用 `npm` + `options.baseURL`。
  - **V2 网关片段**（`opencode-gateway.jsonc`，来自 free-api-gateway，**推荐**）：是 `providers` 段的片段，合并进 `"providers"` 键（plural），用 `package` + `settings.baseURL`。`$schema`/`model` 键仅顶层示例，合并时只取 `providers.gw`。
- **Key 通过环境变量引用**：`options.apiKey: "{env:...}"`，不写明文。
- **Numes 免费端点不支持 `reasoning.effort=medium`**（OpenCode 默认值），必须给每个模型设 `reasoningEffort="high"`，否则报 `reasoning.effort is not supported by any endpoint for this model`。
- **`api-check.sh` 自动加载同目录 `.env`**（`set -a; . .env; set +a`），无需手动 `source`。Key 解析顺序：`--key` > `--key-file` > `NUMES_API_KEY` 环境变量 > `.env` > `~/.local/share/opencode/auth.json`(numes) > `~/.config/numes/key`。
- 常见 API 错误与排查见 README「常见排障清单」。

## free-api-gateway (new-api 网关) 规则 — 推荐聚合方案

完整安装/排障见上游仓库 [zseagate/free-api-gateway](https://github.com/zseagate/free-api-gateway) 的 `SETUP.md`。要点：

- **客户端只用一把网关令牌** `NEW_API_TOKEN` 经 `opencode-gateway.jsonc` 访问网关；上游 Key（Numes/OpenRouter/Groq）只写进网关内部数据库，**不出本机**。OpenCode 选 `gw/<model-id>`。
- **baseURL 默认 `127.0.0.1:3001`（groq-proxy 转发到 new-api 3000）**：Groq 必须经该代理截断 prompt，故推荐先启动 `groq-proxy` 再连 3001；不跑代理则改为 `127.0.0.1:3000/v1`（直连 new-api）。
- **new-api 按请求的 `model` 字段路由**到持有该模型的上游渠道。渠道类型编号（已核对源码）：Numes=**Custom(8)**、OpenRouter=**OpenRouter(20)**、Groq=**OpenAI(1)**；`Models` 字段填上游模型 ID。
- **网关可对瞬时故障自动重试**：数据库 `options` 表写入 `RetryTimes=2`、`AutomaticRetryStatusCodes=429,500,502,503,504`、`AutomaticDisableChannelEnabled=false`（见 SETUP §9）。
- **无工具/非推理模型**（如 Groq `allam-2-7b`）：OpenCode 的 `capabilities.tools/reasoning` 只影响选择器，**不裁剪实际请求体**。需在 `groq-proxy.py` 的 `NO_TOOLS` 集合登记，并在 `opencode-gateway.jsonc` 里把该模型 `capabilities.tools=false`（`reasoning.supported=false`），改后 `systemctl --user restart groq-proxy.service`。否则报 `tool calling is not supported` 或 `property 'reasoning_content' is unsupported`。
- **Groq 免费档输入 7000 tokens/分**（ITPM）：OpenCode 系统提示词约 7K 会超限，靠 `groq-proxy.py`（监听 3001）截断 prompt 缓解；Groq 仅用于短对话，持续任务用 Numes/OpenRouter。
- **同步脚本两套场景**：充值前 `SYNC_MAX_TEST=12 SYNC_TEST_SLEEP=1`（保守），充值后留空/`0`（全测）；偶发 429 时按 `rate` 保留模型，不会误删。
- **License**：new-api 为 AGPL-3.0（个人自托管无碍，对外分发/服务注意 copyleft）。免费档可能用数据改进服务或留存 30 天，**勿发送敏感内容**；端口默认只绑 `127.0.0.1`。

## 常用命令

```bash
cp .env.example .env          # 首次；然后编辑填入真实 Key
./api-check.sh                # 检测默认 9 个 Numes 免费模型（串行）
./api-check.sh --parallel     # 并行，更快
./api-check.sh --watch 60 --parallel --stop-on-ok   # 轮询直到恢复
./api-check.sh --list-free    # 列出已知免费模型
./api-check.sh --base-url <url> --key <key> --model <model>  # 任意 OpenAI 兼容 API
```

- 退出码：`0` 全部可用，`1` 有模型不可用，`2` 无 Key/参数错误。
- 结果标记：`OK` / `TIMEOUT|HANG`（免费档繁忙）/ `OVERLOAD`（429 限流）/ `NOCREDIT` / `BADKEY`。
- 默认免费模型列表在 `api-check.sh` 的 `DEFAULT_MODELS`；若新增模型，需同时更新该数组、`opencode-numes.jsonc` 和 README 中的说明。

## FCC 本地反代要点

- `opencode-fcc.jsonc` 指向本地 `http://127.0.0.1:8082/v1`，需先安装并运行 `fcc-server`，且须与 OpenCode 在同一台机器、同一用户环境。
- 探测反代状态：`curl http://127.0.0.1:8082/v1/models`。
- 当前 OpenCode 的 numes provider 实际也走该反代转发，故 `provider response was invalid` / `no provider is available` / 超时多由反代链路或 Numes 后端异常引起，先用 `api-check.sh` 确认后端恢复。

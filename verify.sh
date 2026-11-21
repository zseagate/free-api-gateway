#!/usr/bin/env bash
# verify.sh — 验证 new-api 网关是否就绪、三个渠道的模型是否可达
# 用法: ./verify.sh  (会自动加载同目录 .env)
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "$SCRIPT_DIR/.env" ]; then
  set -a; . "$SCRIPT_DIR/.env"; set +a
fi

BASE="${NEW_API_BASE_URL:-http://127.0.0.1:3000}"
TOKEN="${NEW_API_TOKEN:-}"

if [ -z "$TOKEN" ] || [ "$TOKEN" = "sk-xxxxx-REPLACE_ME" ] || [ "$TOKEN" = "REPLACE_ME" ]; then
  echo "[x] 请先在 .env 填入 NEW_API_TOKEN（new-api 控制台创建）"; exit 2
fi

echo "== 1) 网关健康 /v1/models =="
if ! curl -sf "$BASE/v1/models" -H "Authorization: Bearer $TOKEN" -o /tmp/na_models.json; then
  echo "[x] /v1/models 失败 —— 检查 new-api 是否在跑、token 是否有效"; exit 1
fi
echo "[OK] /v1/models 可用，可调用模型数: $(grep -o '"id"' /tmp/na_models.json | wc -l)"

echo
echo "== 2) 抽查三条渠道各一个模型（Numes / OpenRouter / Groq）=="
declare -a MODELS=(
  "deepseek/deepseek-v4.1-flash:free"  # Numes
  "openrouter/auto"                     # OpenRouter
  "qwen/qwen3.8-27b"                    # Groq
)
for m in "${MODELS[@]}"; do
  printf "   %-38s " "$m"
  resp=$(curl -s -m 20 -X POST "$BASE/v1/chat/completions" \
       -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
       -d "{\"model\":\"$m\",\"messages\":[{\"role\":\"user\",\"content\":\"ping\"}],\"max_tokens\":16}" 2>/dev/null)
  if echo "$resp" | python3 -c "import sys,json; d=json.load(sys.stdin); c=d.get('choices',[{}])[0]; print('OK' if c.get('message') or c.get('delta') else 'FAIL')" 2>/dev/null | grep -q OK; then
    echo "[OK]"
  elif echo "$resp" | grep -q "model_not_found"; then
    echo "[FAIL] 模型未在 new-api 中注册"
  else
    echo "[FAIL] $(echo "$resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('error',{}).get('message','unknown'))" 2>/dev/null || echo 'no response')"
  fi
done

echo
echo "== 3) 提示 =="
echo "   OpenCode 里选择 gw/<model-id>；或把 opencode.jsonc 的 model 改成目标模型。"

#!/usr/bin/env python3
"""sync-models.py — 定期同步上游免费模型、测试连通性，并写回 new-api + OpenCode。

流程:
  1. 读取 new-api 数据库里的三条渠道 Key
  2. 拉取各上游 /v1/models，筛出免费/可聊天模型
  3. 对新候选模型直接向上游发一次最小请求做连通性测试
  4. 更新 channels.models、tokens.model_limits、opencode.jsonc（及全局配置）
  5. 重启 new-api / groq-proxy 使配置生效（--no-restart 可跳过）

用法:
  ./sync-models.py            # 正常同步
  ./sync-models.py --dry-run  # 只看计划，不改任何文件/数据库
  ./sync-models.py --no-restart
"""

import argparse
import datetime as dt
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(DIR, "one-api.db")
CFG_PATH = os.path.join(DIR, "opencode.jsonc")
GLOBAL_CFG = os.path.expanduser("~/.config/opencode/opencode.jsonc")
LOG_DIR = os.path.join(DIR, "logs")
UPSTREAM_TIMEOUT = 60

# 渠道定义: id -> 上游信息
UPSTREAMS = {
    1: {
        "name": "Numes",
        "base": "https://api.numes.ai/api",
        "free_only": True,
        "exclude": [],
        "output_limit": 4096,
        "context_limit": 131072,
    },
    2: {
        "name": "OpenRouter",
        "base": "https://openrouter.ai/api",
        "free_only": True,
        "exclude": [],
        "output_limit": 512,
        "context_limit": 131072,
    },
    3: {
        "name": "Groq",
        "base": "https://api.groq.com/openai",
        "free_only": False,
        # Groq 免费档 ITPM 很小，只保留聊天模型，排除语音/审核/嵌入
        "exclude": ["whisper", "orpheus", "tts", "guard", "embed", "moderation"],
        "output_limit": 800,
        "context_limit": 131072,
    },
}

# 不带上游 free 标签、但值得保留的路由模型
KEEP_ROUTES = {"openrouter/auto", "openrouter/free"}

# 上游实际能力有缺陷的模型：在这里覆盖 capabilities（其余默认 tools=True）
# 例：Groq 的 allam-2-7b 收到 tools 会直接 400 "`tool calling` is not supported with this model"，
# 且拒绝回传 reasoning_content 字段，需同时关闭 reasoning。
CAPABILITY_OVERRIDES = {
    "allam-2-7b": {"tools": False, "reasoning": {"supported": False}},
}


def load_env():
    env = {}
    path = os.path.join(DIR, ".env")
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def log(msg, lines):
    print(msg, flush=True)
    lines.append(msg)


def curl_json(method, url, headers=None, body=None, timeout=UPSTREAM_TIMEOUT):
    """用 curl 发请求（Go/curl 的 TLS 指纹能过 Cloudflare，Python urllib 会被 403）。"""
    cmd = ["curl", "-sS", "-m", str(timeout), "-X", method, url, "-w", "\n%{http_code}"]
    for k, v in (headers or {}).items():
        cmd += ["-H", f"{k}: {v}"]
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        cmd += ["-H", "Content-Type: application/json", "--data-binary", "@-"]
    p = subprocess.run(cmd, input=data, capture_output=True, timeout=timeout + 10)
    raw = p.stdout.decode("utf-8", errors="replace")
    if "\n" in raw:
        raw, _, code = raw.rpartition("\n")
    else:
        code = "0"
    try:
        code_i = int(code)
    except ValueError:
        code_i = 0
    try:
        parsed = json.loads(raw) if raw.strip() else None
    except json.JSONDecodeError:
        parsed = None
    return code_i, parsed, raw


def fetch_catalog(cid, key, lines):
    up = UPSTREAMS[cid]
    url = up["base"] + "/v1/models"
    code, data, raw = curl_json("GET", url, {"Authorization": "Bearer " + key})
    if code != 200 or not isinstance(data, dict):
        log(f"  [WARN] {up['name']} 模型列表获取失败 HTTP {code}: {raw[:120]}", lines)
        return None
    items = data.get("data") or []
    ids = [m.get("id") for m in items if isinstance(m, dict) and m.get("id")]
    picked = []
    for mid in ids:
        if up["free_only"] and ":free" not in mid:
            continue
        if any(pat in mid.lower() for pat in up["exclude"]):
            continue
        picked.append(mid)
    log(f"  {up['name']}: 上游 {len(ids)} 个模型，命中 {len(picked)} 个候选", lines)
    return ids, picked


def test_upstream(cid, key, model, lines):
    """直连上游做最小请求。返回 ok/rate/billing/fail/auth。"""
    up = UPSTREAMS[cid]
    body = {
        "model": model,
        "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
        "max_tokens": 16,
    }
    code, data, raw = curl_json(
        "POST", up["base"] + "/v1/chat/completions",
        {"Authorization": "Bearer " + key}, body,
    )
    if code == 200 and isinstance(data, dict) and data.get("choices"):
        return "ok", ""
    msg = ""
    if isinstance(data, dict):
        err = data.get("error")
        if isinstance(err, dict):
            msg = str(err.get("message", ""))[:120]
        elif err:
            msg = str(err)[:120]
    if code == 429:
        return "rate", msg or "429 rate limited"
    if code == 402:
        return "billing", msg or "402 insufficient balance"
    if code in (401, 403):
        return "auth", msg or f"{code} auth failed"
    return "fail", msg or f"HTTP {code}: {raw[:100]}"


def test_gateway(base, token, model):
    body = {
        "model": model,
        "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
        "max_tokens": 16,
    }
    try:
        req = urllib.request.Request(
            base + "/v1/chat/completions",
            data=json.dumps(body).encode(),
            headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=60) as r:
            d = json.loads(r.read())
            if d.get("choices"):
                return "ok"
    except urllib.error.HTTPError as e:
        if e.code == 429:
            return "rate"
        return f"fail{e.code}"
    except Exception as e:
        return "fail:" + type(e).__name__
    return "fail"


def read_db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    cur = conn.cursor()
    channels = {}
    for cid, name, models in cur.execute("SELECT id, name, models FROM channels"):
        channels[cid] = {"name": name, "models": [m for m in (models or "").split(",") if m]}
    keys = {cid: key for cid, key in cur.execute("SELECT id, key FROM channels")}
    token = cur.execute("SELECT model_limits, model_limits_enabled FROM tokens WHERE id=1").fetchone()
    conn.close()
    whitelist = set()
    if token and token[0] and token[1]:
        whitelist = {m.strip() for m in token[0].split(",") if m.strip()}
    return channels, keys, whitelist


def write_db(channels, model_list):
    """写入 channels.models 与 tokens 白名单，并重建 abilities 路由表。

    new-api 的运行时路由读的是 abilities 表，而不是 channels.models；
    直接改数据库时必须同步重建，否则 /v1/models 与调用都会漏掉新模型。
    """
    conn = sqlite3.connect(DB_PATH, timeout=60)
    cur = conn.cursor()
    for cid, info in channels.items():
        cur.execute("UPDATE channels SET models=? WHERE id=?", (",".join(info["models"]), cid))

    # 重建受影响渠道的 abilities
    meta = {}
    for cid, grp, prio, weight, tag in cur.execute(
        "SELECT id, \"group\", priority, weight, tag FROM channels"
    ):
        meta[cid] = (grp or "default", prio or 0, weight or 0, tag or "")
    for cid in channels:
        cur.execute("DELETE FROM abilities WHERE channel_id=?", (cid,))
        grp, prio, weight, tag = meta.get(cid, ("default", 0, 0, ""))
        for m in channels[cid]["models"]:
            cur.execute(
                "INSERT INTO abilities(\"group\", model, channel_id, enabled, priority, weight, tag) "
                "VALUES (?,?,?,?,?,?,?)",
                (grp, m, cid, 1, prio, weight, tag),
            )

    whitelist = ",".join(model_list)
    cur.execute(
        "UPDATE tokens SET model_limits_enabled=1, model_limits=?, unlimited_quota=1, remain_quota=0 WHERE id=1",
        (whitelist,),
    )
    conn.commit()
    conn.close()


def backup_db():
    dst_path = DB_PATH + ".sync-bak"
    src = sqlite3.connect(DB_PATH, timeout=30)
    dst = sqlite3.connect(dst_path)
    src.backup(dst)
    dst.close()
    src.close()
    return dst_path


def nice_name(mid):
    base = mid.split("/")[-1]
    base = base.replace(":free", "").replace(":batch", "")
    base = re.sub(r"[-_]", " ", base).strip()
    return " ".join(w.capitalize() if not re.match(r"^v?\d", w) else w for w in base.split())


def build_entry(mid, channel_name, up):
    caps = {"tools": True, "input": ["text"], "output": ["text"]}
    caps.update(CAPABILITY_OVERRIDES.get(mid, {}))
    return {
        "modelID": mid,
        "name": f"{nice_name(mid)} ({channel_name})",
        "capabilities": caps,
        "limit": {"context": up["context_limit"], "output": up["output_limit"]},
    }


def sync_config(gateway_models, channel_of, lines, dry_run):
    cfg = json.load(open(CFG_PATH, encoding="utf-8"))
    models = cfg["providers"]["gw"]["models"]
    changed = False

    # 删除已不存在的模型
    for mid in list(models):
        if mid not in gateway_models:
            log(f"  - 移除模型 {mid}（网关已不可用）", lines)
            del models[mid]
            changed = True

    # 校正已有模型的能力声明（覆盖表里的项以 CAPABILITY_OVERRIDES 为准）
    for mid, over in CAPABILITY_OVERRIDES.items():
        if mid not in models:
            continue
        caps = models[mid].setdefault("capabilities", {})
        for k, v in over.items():
            if caps.get(k) != v:
                caps[k] = v
                log(f"  ~ 校正 {mid} capabilities.{k} = {json.dumps(v)}", lines)
                changed = True

    # 新增模型
    for mid in gateway_models:
        if mid in models:
            continue
        cid = channel_of.get(mid)
        up = UPSTREAMS.get(cid, UPSTREAMS[1])
        models[mid] = build_entry(mid, up["name"], up)
        log(f"  + 新增模型 {mid} （来自 {up['name']}，output={up['output_limit']}）", lines)
        changed = True

    # 默认模型失效则回退到 deepseek，再不行取第一个
    current = cfg.get("model", "")
    if current.replace("gw/", "", 1) not in models:
        fallback = "deepseek/deepseek-v4.1-flash:free"
        if fallback not in models:
            fallback = sorted(models)[0] if models else ""
        if fallback:
            cfg["model"] = "gw/" + fallback
            log(f"  * 默认模型切换为 gw/{fallback}", lines)
            changed = True

    if changed and not dry_run:
        with open(CFG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
            f.write("\n")
        merge_global(cfg, lines)
    return changed


def merge_global(project_cfg, lines):
    if not os.path.isfile(GLOBAL_CFG):
        log(f"  [WARN] 未找到全局配置 {GLOBAL_CFG}，跳过", lines)
        return
    try:
        global_cfg = json.load(open(GLOBAL_CFG, encoding="utf-8"))
    except Exception as e:
        log(f"  [WARN] 全局配置解析失败: {e}", lines)
        return
    old_settings = {
        k: v for k, v in global_cfg.get("providers", {}).get("gw", {}).get("settings", {}).items()
    }
    global_cfg.setdefault("providers", {})["gw"] = project_cfg["providers"]["gw"]
    # 保留全局配置里的真实 apiKey（项目文件用 env 占位符）
    if old_settings.get("apiKey") and "{" not in old_settings["apiKey"]:
        global_cfg["providers"]["gw"]["settings"]["apiKey"] = old_settings["apiKey"]
    global_cfg["model"] = project_cfg["model"]
    with open(GLOBAL_CFG, "w", encoding="utf-8") as f:
        json.dump(global_cfg, f, indent=2, ensure_ascii=False)
        f.write("\n")
    log(f"  * 已同步全局配置 {GLOBAL_CFG}", lines)


def restart_services(lines):
    try:
        subprocess.run(
            ["systemctl", "--user", "restart", "new-api.service", "groq-proxy.service"],
            check=True, capture_output=True, timeout=90,
        )
        log("  * 已重启 new-api / groq-proxy", lines)
        time.sleep(5)
        return True
    except Exception as e:
        log(f"  [WARN] 重启服务失败: {e}（请手动 systemctl --user restart ...）", lines)
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只输出计划，不修改")
    ap.add_argument("--no-restart", action="store_true", help="不重启服务")
    args = ap.parse_args()

    os.makedirs(LOG_DIR, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    lines = []

    env = load_env()
    base = env.get("NEW_API_BASE_URL", "http://127.0.0.1:3000").rstrip("/")
    token = env.get("NEW_API_TOKEN", os.environ.get("NEW_API_TOKEN", ""))
    if not token:
        print("[x] 未找到 NEW_API_TOKEN（.env 或环境变量）")
        return 2

    log(f"== sync-models {stamp} ==", lines)
    channels, keys, whitelist = read_db()

    # 1) 发现上游候选
    catalogs = {}
    for cid in UPSTREAMS:
        if cid not in keys:
            continue
        catalogs[cid] = fetch_catalog(cid, keys[cid], lines) or ([], [])

    # 2) 计算各渠道目标模型
    channel_of = {}
    planned = {}
    added, kept, dropped = [], [], []
    for cid, up in UPSTREAMS.items():
        existing = channels.get(cid, {}).get("models", [])
        all_ids, catalog = catalogs.get(cid, ([], []))
        catalog_set = set(catalog)
        all_set = set(all_ids)
        # 现有模型里仍存在于上游的保留（不受 exclude 影响）；路由模型也保留
        keep = [m for m in existing if m in all_set or m in KEEP_ROUTES]
        # 上游新增的免费候选
        new = [m for m in catalog if m not in existing]

        # 现有但上游已下架的模型：默认移除（--dry-run 只提示）
        for m in existing:
            if m not in keep:
                dropped.append((cid, m))

        # 需要测试的：尚未暴露给 OpenCode 的模型（新候选 + 渠道里已有但未进白名单）
        to_test = [m for m in (keep + new) if m not in whitelist][:12]
        tested = []
        for m in to_test:
            status, detail = test_upstream(cid, keys[cid], m, lines)
            if status in ("ok", "rate"):
                tested.append(m)
                if m in new:
                    added.append((cid, m))
                note = "" if status == "ok" else "（上游限流，仍保留）"
                log(f"  + [{up['name']}] 可用 {m}{note}", lines)
            else:
                log(f"  - [{up['name']}] 不可用 {m}: {status} {detail}", lines)
            time.sleep(1)

        # 已暴露在白名单里的模型默认保留；未通过测试的不加入
        planned[cid] = [m for m in (keep + new) if m in whitelist or m in tested]
        for m in planned[cid]:
            channel_of.setdefault(m, cid)
        kept.extend((cid, m) for m in keep)

    gateway_models = sorted({m for ms in planned.values() for m in ms})

    # 4) 落库 / 配置
    log("-- 变更 --", lines)
    if added:
        log(f"  新增 {len(added)} 个模型: " + ", ".join(m for _, m in added), lines)
    if dropped:
        log(f"  移除 {len(dropped)} 个已下架模型: " + ", ".join(m for _, m in dropped), lines)
    if not added and not dropped:
        log("  渠道模型无变化", lines)

    changed = bool(added or dropped)
    if changed and not args.dry_run:
        bak = backup_db()
        log(f"  * 数据库备份: {os.path.basename(bak)}", lines)
        for cid in UPSTREAMS:
            channels.setdefault(cid, {"name": UPSTREAMS[cid]["name"], "models": []})
            channels[cid]["models"] = planned.get(cid, [])
        write_db(channels, gateway_models)
        log("  * 已写入 channels.models / tokens.model_limits", lines)

    cfg_changed = sync_config(gateway_models, channel_of, lines, args.dry_run)
    if not changed and not cfg_changed:
        log("  配置无变化", lines)

    if (changed or cfg_changed) and not args.dry_run and not args.no_restart:
        restart_services(lines)
        # 重启后确认
        try:
            req = urllib.request.Request(
                base + "/v1/models", headers={"Authorization": "Bearer " + token}
            )
            with urllib.request.urlopen(req, timeout=30) as r:
                n = len(json.loads(r.read()).get("data", []))
            log(f"  * 网关当前可调用模型数: {n}", lines)
        except Exception as e:
            log(f"  [WARN] 重启后检查失败: {e}", lines)
        # 每渠道抽一个模型做端到端连通性测试
        log("-- 重启后端到端抽查 --", lines)
        for cid, up in UPSTREAMS.items():
            sample = [m for m in planned.get(cid, []) if m in gateway_models][:1]
            for m in sample:
                log(f"  [{up['name']}] {m}: {test_gateway(base, token, m)}", lines)

    if args.dry_run:
        log("  (dry-run，未做任何修改)", lines)
    log("== 完成 ==", lines)

    with open(os.path.join(LOG_DIR, f"sync-{stamp}.log"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    shutil.copyfile(
        os.path.join(LOG_DIR, f"sync-{stamp}.log"),
        os.path.join(LOG_DIR, "sync-latest.log"),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

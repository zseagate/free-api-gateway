#!/usr/bin/env python3
"""sync-models-win.py — Windows/Docker 适配版 sync-models。

复用仓库 sync-models.py 的全部逻辑，仅替换 Linux 专属部分：
  - DB 路径: data/one-api.db（Docker volume）
  - 重启: docker restart new-api（替代 systemctl --user restart）
  - 全局配置: ~/.config/opencode/opencode.json（而非 .jsonc）

用法:
  python sync-models-win.py --dry-run   # 只看计划
  python sync-models-win.py             # 正常同步（备份 DB、写回、重启容器）
"""
import importlib.util
import os
import sqlite3
import subprocess
import sys
import time

DIR = os.path.dirname(os.path.abspath(__file__))

# sync-models.py 含连字符，不能用 import 语句 —— 用 importlib 按路径加载
_spec = importlib.util.spec_from_file_location("sync_models", os.path.join(DIR, "sync-models.py"))
sm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sm)

# --- Windows/Docker 适配 ---
sm.DB_PATH = os.path.join(DIR, "data", "one-api.db")
sm.GLOBAL_CFG = os.path.expanduser("~/.config/opencode/opencode.json")

# 仓库假定渠道 ID 为 1/2/3（Numes/OpenRouter/Groq），但我们库里是 2/3/4。
# 按渠道名把 UPSTREAMS 的 key 重映射到真实 channel id，否则会跳过 Numes 并误删其模型。
def _remap_upstreams():
    conn = sqlite3.connect(sm.DB_PATH, timeout=30)
    try:
        id_by_name = {name: cid for cid, name in conn.execute("SELECT id, name FROM channels")}
    finally:
        conn.close()
    new_up = {}
    for orig_cid, up in sm.UPSTREAMS.items():
        real_cid = id_by_name.get(up["name"])
        if real_cid is None:
            print(f"  [WARN] 渠道 {up['name']} 不在数据库中，跳过")
            continue
        new_up[real_cid] = up
    sm.UPSTREAMS = new_up

# sync_config 里 `UPSTREAMS.get(cid, UPSTREAMS[1])` 会立即求值 UPSTREAMS[1] 作为默认值。
# 重映射后 key 1 不存在 → KeyError。补一个无害的兜底条目（真实库里无 channel 1，
# main() 里 `if cid not in keys: continue` 会跳过它；写库时 UPDATE id=1 影响 0 行）。
def _ensure_fallback():
    if 1 not in sm.UPSTREAMS:
        fallback_src = next(iter(sm.UPSTREAMS.values()))
        sm.UPSTREAMS[1] = dict(fallback_src)


_remap_upstreams()
_ensure_fallback()

# --- 容错：上游瞬时故障不应导致误删渠道模型 ---
# 仓库 sync-models.py 在 fetch_catalog 失败时返回空目录，main() 会把该渠道
# 现有模型全部判为"已下架"并删除。对定时任务（每 6h）很危险。做两个加固：
#   1) fetch_catalog 失败时重试；仍失败则回退为"现有模型"，保住渠道不被清空
#   2) 已存在于渠道里的模型不重复打上游（避免瞬时 503 误判为不可用）
_orig_fetch_catalog = sm.fetch_catalog
_orig_test_upstream = sm.test_upstream


def _fetch_catalog_safe(cid, key, lines):
    up = sm.UPSTREAMS[cid]
    for attempt in range(3):
        result = _orig_fetch_catalog(cid, key, lines)
        if result is not None:
            return result
        sm.log(f"  [WARN] {up['name']} 目录获取失败(第{attempt+1}次)，重试...", lines)
        time.sleep(5)
    conn = sqlite3.connect(sm.DB_PATH, timeout=30)
    try:
        row = conn.execute("SELECT models FROM channels WHERE id=?", (cid,)).fetchone()
        existing = [m for m in (row[0] or "").split(",") if m] if row else []
    finally:
        conn.close()
    sm.log(f"  [WARN] {up['name']} 目录不可用，保留现有 {len(existing)} 个模型以防误删", lines)
    return existing, []


def _test_upstream_safe(cid, key, model, lines):
    conn = sqlite3.connect(sm.DB_PATH, timeout=30)
    try:
        row = conn.execute("SELECT models FROM channels WHERE id=?", (cid,)).fetchone()
        existing = set(m for m in (row[0] or "").split(",") if m) if row else set()
    finally:
        conn.close()
    if model in existing:
        return "ok", ""  # 已在渠道里，视为保留，避免上游瞬时故障误删
    return _orig_test_upstream(cid, key, model, lines)


sm.fetch_catalog = _fetch_catalog_safe
sm.test_upstream = _test_upstream_safe

DOCKER = r"C:\Program Files\Docker\Docker\resources\bin\docker.exe"


def restart_services(lines):
    """用 docker restart 替代 systemctl --user restart。"""
    try:
        subprocess.run(
            [DOCKER, "restart", "new-api"],
            check=True, capture_output=True, timeout=90,
        )
        sm.log("  * 已重启 new-api 容器 (docker restart)", lines)
        time.sleep(6)
        return True
    except Exception as e:
        sm.log(f"  [WARN] 重启容器失败: {e}（请手动 docker restart new-api）", lines)
        return False


sm.restart_services = restart_services

if __name__ == "__main__":
    sys.exit(sm.main())

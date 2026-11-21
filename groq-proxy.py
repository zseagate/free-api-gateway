#!/usr/bin/env python3
"""Optional reverse proxy in front of new-api.

Only truncates the prompt for Groq-backed models (ITPM ~7000 free tier),
so OpenCode's long system prompt no longer exceeds Groq's input limit.
Other models pass through unchanged.
"""
import http.server, json, urllib.request, urllib.error, os

UPSTREAM = os.environ.get("UPSTREAM", "http://127.0.0.1:3000")
LISTEN = int(os.environ.get("PORT", "3001"))
GROQ_MODELS = {"qwen/qwen3.8-27b", "openai/gpt-oss-20b", "openai/gpt-oss-120b",
               "openai/gpt-oss-safeguard-20b", "allam-2-7b"}
INPUT_BUDGET_TOKENS = 1200  # conservative; leaves room for tool defs + retries


def est_tokens(text):
    if not text:
        return 0
    if isinstance(text, list):
        return sum(est_tokens(x) for x in text)
    if isinstance(text, dict):
        return sum(est_tokens(v) for v in text.values())
    return max(1, len(str(text)) // 3)


SYS_CAP = 600  # conservative


def truncate_text(text, max_tokens):
    if not isinstance(text, str):
        return text
    max_chars = max_tokens * 4
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n...[truncated]"


def truncate_content(content, max_tokens):
    if isinstance(content, str):
        return truncate_text(content, max_tokens)
    if isinstance(content, list):
        out = []
        used = 0
        for part in content:
            if isinstance(part, dict) and "text" in part:
                remain = max(0, max_tokens - used)
                t = truncate_text(part["text"], remain)
                out.append({**part, "text": t})
                used += est_tokens(t)
            else:
                out.append(part)
        return out
    return content


def truncate_messages(messages, budget):
    if not messages:
        return messages
    system = [m for m in messages if m.get("role") == "system"]
    others = [m for m in messages if m.get("role") != "system"]
    # cap system prompt
    for m in system:
        m = dict(m)
        m["content"] = truncate_content(m.get("content", ""), SYS_CAP)
    system = [ {**m, "content": truncate_content(m.get("content",""), SYS_CAP)} for m in system ]
    sys_tokens = sum(est_tokens(m.get("content", "")) for m in system)
    budget = max(512, budget - sys_tokens)
    # keep last messages within budget, truncating the boundary one if needed
    kept = []
    used = 0
    for m in reversed(others):
        t = est_tokens(m.get("content", ""))
        if used + t > budget and kept:
            # truncate this message to fit remainder
            remain = budget - used
            if remain >= 256:
                m2 = dict(m)
                m2["content"] = truncate_content(m.get("content", ""), remain)
                kept.insert(0, m2)
                used += est_tokens(m2["content"])
            break
        kept.insert(0, m)
        used += t
    if not kept and others:
        m = dict(others[-1])
        m["content"] = truncate_content(m.get("content", ""), budget)
        kept = [m]
    return system + kept


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b""
        path = self.path
        headers = {k: v for k, v in self.headers.items()
                   if k.lower() not in ("host", "content-length", "connection")}
        body = raw
        try:
            data = json.loads(raw) if raw else {}
        except Exception:
            data = None
        if isinstance(data, dict) and data.get("model") in GROQ_MODELS and "messages" in data:
            orig = len(data["messages"])
            tools_tok = est_tokens(data.get("tools", []))
            msg_budget = max(512, INPUT_BUDGET_TOKENS - tools_tok)
            data["messages"] = truncate_messages(data["messages"], msg_budget)
            if len(data["messages"]) != orig:
                sys.stderr.write(f"[groq-proxy] truncated messages {orig} -> {len(data['messages'])} for {data['model']}\n")
            body = json.dumps(data).encode()
        headers["Content-Length"] = str(len(body))
        url = UPSTREAM + path
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                self.send_response(resp.status)
                for k, v in resp.headers.items():
                    if k.lower() not in ("transfer-encoding", "connection", "content-length"):
                        self.send_header(k, v)
                payload = resp.read()
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
        except urllib.error.HTTPError as e:
            payload = e.read()
            self.send_response(e.code)
            for k, v in e.headers.items():
                if k.lower() not in ("transfer-encoding", "connection", "content-length"):
                    self.send_header(k, v)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        except Exception as e:
            msg = json.dumps({"error": {"message": str(e)}}).encode()
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(msg)))
            self.end_headers()
            self.wfile.write(msg)

    def do_GET(self):
        url = UPSTREAM + self.path
        headers = {k: v for k, v in self.headers.items()
                   if k.lower() not in ("host", "connection")}
        req = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                self.send_response(resp.status)
                payload = resp.read()
                for k, v in resp.headers.items():
                    if k.lower() not in ("transfer-encoding", "connection", "content-length"):
                        self.send_header(k, v)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
        except urllib.error.HTTPError as e:
            payload = e.read()
            self.send_response(e.code)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    http.server.ThreadingHTTPServer.allow_reuse_address = True
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", LISTEN), Handler)
    print(f"groq-proxy listening on 127.0.0.1:{LISTEN} -> {UPSTREAM}", flush=True)
    srv.serve_forever()

#!/usr/bin/env python3
"""Walking skeleton：网关 → mock 引擎 → SSE。

演示三件事（对应第 23、24 章）：

1. 租户级 token bucket 限流 —— 背压第一层，超了直接 429；
2. deadline 逐跳递减 —— 客户端给 3s，网关留 200ms，引擎只拿到 2.8s；
3. 客户端断开即停止生成 —— 把取消信号传到引擎，省下的是真金白银。

用法：
    python3 platform/gateway.py --port 8080
    curl -N "http://127.0.0.1:8080/v1/chat?tenant=demo&q=我想退货"
"""
import argparse
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import mock_engine

START = time.time()

# ---------- 限流：每租户一个令牌桶 ----------
class Bucket:
    def __init__(self, rate: float = 3.0, cap: int = 6):
        self.rate, self.cap = rate, cap
        self.tokens = float(cap)
        self.ts = time.time()
        self.lock = threading.Lock()

    def take(self) -> bool:
        with self.lock:
            now = time.time()
            self.tokens = min(self.cap, self.tokens + (now - self.ts) * self.rate)
            self.ts = now
            if self.tokens >= 1.0:
                self.tokens -= 1.0
                return True
            return False


BUCKETS: dict[str, Bucket] = {}
BUCKETS_LOCK = threading.Lock()
STATS = {"requests": 0, "rate_limited": 0, "tokens": 0,
         "cancelled": 0, "ttft_ms_sum": 0, "ttft_n": 0}
STATS_LOCK = threading.Lock()


def bucket_for(tenant: str) -> Bucket:
    with BUCKETS_LOCK:
        if tenant not in BUCKETS:
            BUCKETS[tenant] = Bucket()
        return BUCKETS[tenant]


def bump(key: str, n: int = 1) -> None:
    with STATS_LOCK:
        STATS[key] += n


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # 静音默认日志
        pass

    # ---------- 工具 ----------
    def send_json(self, code: int, obj: dict) -> None:
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def sse_open(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

    def sse_send(self, event: str, data: dict) -> None:
        chunk = f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
        self.wfile.write(chunk.encode())
        self.wfile.flush()

    # ---------- 路由 ----------
    def do_GET(self):
        u = urlparse(self.path)

        if u.path == "/healthz":
            return self.send_json(200, {"ok": True, "uptime_s": round(time.time() - START, 3)})

        if u.path == "/metrics":
            with STATS_LOCK:
                s = dict(STATS)
            lines = [f"gateway_requests_total {s['requests']}",
                     f"gateway_rate_limited_total {s['rate_limited']}",
                     f"gateway_tokens_total {s['tokens']}",
                     f"gateway_cancelled_total {s['cancelled']}"]
            if s["ttft_n"]:
                lines.append(f"gateway_ttft_avg_ms {s['ttft_ms_sum'] / s['ttft_n']:.1f}")
            body = ("\n".join(lines) + "\n").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if u.path != "/v1/chat":
            return self.send_json(404, {"error": "not found"})

        params = parse_qs(u.query)
        tenant = (params.get("tenant") or ["anonymous"])[0]
        prompt = (params.get("q") or ["hello"])[0]

        # 背压第一层：配额
        if not bucket_for(tenant).take():
            bump("rate_limited")
            return self.send_json(429, {"error": "rate_limited", "tenant": tenant,
                                        "retry_after_ms": 300})

        # deadline 逐跳递减：客户端 3s → 网关 2.8s → 引擎 2.6s
        total_ms = int(self.headers.get("X-Deadline-Ms", "3000"))
        budget_ms = max(300, total_ms - 200)

        bump("requests")
        started = time.time()
        sent = 0
        try:
            self.sse_open()
            self.sse_send("session", {"tenant": tenant, "deadline_ms": budget_ms})
            for event, payload in mock_engine.stream_reply(prompt):
                if (time.time() - started) * 1000 > budget_ms:
                    self.sse_send("error", {"code": "deadline_exceeded",
                                            "sent": sent, "budget_ms": budget_ms})
                    break
                if event == "token":
                    sent += 1
                    bump("tokens")
                if event == "first_token":
                    ms = payload["ttft_ms"]
                    with STATS_LOCK:
                        STATS["ttft_ms_sum"] += ms
                        STATS["ttft_n"] += 1
                self.sse_send(event, payload)
        except (BrokenPipeError, ConnectionResetError):
            # 客户端取消：真实系统里这里要通知推理引擎 abort，释放 KV 与 batch
            bump("cancelled")
        finally:
            try:
                self.close_connection = True
            except Exception:
                pass


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8080)
    args = ap.parse_args()
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"gateway listening on http://127.0.0.1:{args.port}")
    print(f'  curl -N "http://127.0.0.1:{args.port}/v1/chat?tenant=demo&q=hi"')
    srv.serve_forever()


if __name__ == "__main__":
    main()

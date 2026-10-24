#!/usr/bin/env python3
"""假引擎：模拟 vLLM 的 TTFT + 逐 token 输出。

真实环境里这里是 vLLM/SGLang 的 /v1/chat/completions（stream=true）。
本文件只保留对网关有意义的两个参数：TTFT 和 TPOT。
"""
import time

TTFT_S = 0.22      # 首 token 延迟
TPOT_S = 0.025     # 每 token 间隔


def stream_reply(prompt: str, max_tokens: int = 60):
    """产出 (event, payload) 二元组，供网关转成 SSE。"""
    t0 = time.time()
    yield "meta", {"model": "mock-200b-a30b", "prompt_tokens": len(prompt) // 2 + 8}
    time.sleep(TTFT_S)
    yield "first_token", {"ttft_ms": round((time.time() - t0) * 1000)}

    for i in range(max_tokens):
        time.sleep(TPOT_S)
        yield "token", {"i": i, "text": f"t{i} "}

    yield "done", {
        "completion_tokens": max_tokens,
        "elapsed_ms": round((time.time() - t0) * 1000),
    }

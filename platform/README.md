# platform —— walking skeleton

最小可跑链路：**网关 → mock 引擎 → SSE**。它不追求功能完整，只把三件容易做错的事先跑通：

| 机制 | 代码位置 | 对应章节 |
|---|---|---|
| 租户级限流（令牌桶） | `gateway.py: Bucket` | 第 24 章 |
| deadline 逐跳递减 | `gateway.py: budget_ms` | 第 24 章 |
| 客户端断开即停止生成 | `gateway.py: except BrokenPipeError` | 第 23 章 |
| SSE 事件协议 | `gateway.py: sse_send` | 第 23 章 |
| 指标暴露 | `gateway.py: /metrics` | 第 37 章 |

## 跑

```bash
python3 platform/gateway.py --port 8080

curl -N "http://127.0.0.1:8080/v1/chat?tenant=demo&q=我想退货"
curl -s localhost:8080/metrics
```

## 接下来要替换的

| 现在 | 以后 |
|---|---|
| `mock_engine.py` | 真实 vLLM `/v1/chat/completions`（stream） |
| 内存令牌桶 | Redis + 集中配额 |
| 文本 SSE | 带 trace_id / usage 的标准事件 |
| 无认证 | OIDC + 租户绑定 |

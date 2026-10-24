# deploy

K8s / Helm / ArgoCD 清单。目前为占位，随第 31–34 章补齐。

计划内容：

```
deploy/
├── base/            # namespace, GPU Operator, device plugin
├── inference/       # vLLM Deployment + Service + HPA/KEDA
├── gateway/         # 网关 Deployment + Ingress + 限流策略
├── autoscaling/     # KEDA ScaledObject（按队列/KV 水位）
├── batch/           # Volcano Queue / Kueue 配额
└── observability/   # OTel Collector, ServiceMonitor
```

原则：**所有清单可 `kubectl apply -k` 直接跑**，不依赖未公开的镜像与 CRD。

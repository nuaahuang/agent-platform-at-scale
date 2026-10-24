# 从 v1 到 v3 的编号映射

v1 是 14 章的初稿（`learn/book/`），v3 重排为 40 章。已迁入本仓库的文件：

| v1 | v3 | 本仓库路径 | 状态 |
|---|---|---|---|
| ch01 请求旅程 | 第 08 章 请求链路 | `book/part-03-inference/08-request-journey.md` | ✅ 已迁 |
| ch02 模型物理 | 附录 A | `book/appendix/A-model-physics.md` | ✅ 已迁 |
| ch02a 并行互联 | 第 09 章 并行与互联 | `book/part-03-inference/09-parallelism.md` | ✅ 已迁 |
| ch03 推理引擎 | 第 10 章 推理引擎 | `book/part-03-inference/10-inference-engine.md` | ✅ 已迁 |
| ch04 PD 分离 | 第 11 章 调度与 PD | `book/part-03-inference/11-pd-disaggregation.md` | ✅ 已迁 |

**注意**：迁移文件里的"第 N 章"是 v1 旧编号，尚未逐处改写；修订该章时顺手改对。

## 尚未成章、但已有素材的 v1 文档

| v1 文档 | 去向 | 用法 |
|---|---|---|
| `theory.md` | 第 18–20 章 | 上下文/记忆/事件的理论底稿 |
| `tech-stack.md` | 第 3 章、第 26 章 | 选型矩阵直接复用 |
| `platform-landing.md` | 第 31 章 | K8s/GPU 调度底稿 |
| `app-engineering.md` | 第 20、25 章 | 上下文与缓存的工程细节 |
| `security-architecture.md` | 第 35 章 | 五道闸门 |
| `industry-baseline.md` | 附录 B | 标准件清单 |
| `labs.md` | `labs/README.md` | 实验索引 |
| `vllm-vs-sglang.md` | 第 10 章 | 引擎选型 |
| `checklist.md` | 第 40 章 | 答辩题库 |
| `reading-list-agents.md` | 附录 C | 延伸阅读 |

**迁入原则**：只搬与章节主题一致的段落，不整篇复制；搬过来的每一段都要过 `make style`。

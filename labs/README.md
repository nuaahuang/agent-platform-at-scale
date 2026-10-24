# labs —— 可复算的数字

全部只用 Python 标准库，clone 下来直接跑。每个脚本对应一章的"数字"小节。

| 脚本 | 对应章节 | 算什么 |
|---|---|---|
| `calc.py` | 13 | 容量与成本：DAU → QPS → 卡数 → 年成本 → 元/百万 token |
| `littles_law.py` | 8 | 排队论：并发、QPS、时延三者关系 |
| `paging_lab.py` | 10 | PagedAttention：块大小与显存浪费 |
| `radix_lab.py` | 10 | 前缀缓存：RadixAttention 命中率 |
| `pd_lab.py` | 11 | PD 分离：TTFT/TPOT 分布对比 |
| `pd_cost_lab.py` | 11、14 | 四种部署架构的卡数/成本/SLO 对比 |

## 用法

```bash
python3 labs/calc.py                     # 基准
python3 labs/calc.py --dau 30m --ctx 3000
python3 labs/calc.py --hit 0.5
python3 labs/pd_cost_lab.py
```

## 约定

- 所有参数集中写在文件顶部，改一个数就能重跑；
- 每个脚本最后打印"口径说明"，避免把假设当结论；
- 数字对不上时，先看口径，再看公式。

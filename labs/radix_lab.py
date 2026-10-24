#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RadixAttention 原理演示：token 序列的基数树（前缀树）。

模拟 3 个用户、每人 3 轮对话：
  每轮 = 公共系统提示(40) + 该用户历史(逐轮增长) + 本轮问题(20)
朴素做法：每次请求存整段 KV；基数树：公共前缀只存一份。

  python3 radix_lab.py
"""

S = list(range(40))          # 系统提示 + 工具 Schema（所有请求相同）


class Node:
    def __init__(self):
        self.children = {}   # token -> Node


class RadixTree:
    def __init__(self):
        self.root = Node()
        self.stored = 0      # 树上存了多少个 token 的 KV（去重后）

    def match_prefix(self, seq):
        """沿树走，返回最长匹配前缀长度（这些 token 的 KV 可直接复用）。"""
        node, n = self.root, 0
        for t in seq:
            if t not in node.children:
                break
            node = node.children[t]
            n += 1
        return n

    def insert(self, seq):
        node = self.root
        for t in seq:
            if t not in node.children:
                node.children[t] = Node()
                self.stored += 1     # 新节点 = 新存一份 KV
            node = node.children[t]


tree = RadixTree()
naive = 0
print(f"{'请求':<14}{'总长':>5}{'复用':>5}{'新算':>5}{'命中率':>8}")
for u in range(3):
    history = []
    for turn in range(3):
        question = [1000 + u * 100 + turn] * 20
        seq = S + history + question
        reused = tree.match_prefix(seq)          # 先查：能复用多少
        tree.insert(seq)                         # 再插：新 KV 挂到树上
        naive += len(seq)
        print(f"用户{u+1} 第{turn+1}轮   {len(seq):>5}{reused:>5}"
              f"{len(seq)-reused:>5}{reused/len(seq):>8.0%}")
        history += question[:]                   # 下一轮带上本轮（多轮增长）

print(f"\n朴素 KV 总量：{naive} 份")
print(f"基数树实际存：{tree.stored} 份（去重后）")
print(f"节省：{1 - tree.stored/naive:.0%}")
print("""
原理要点：
  1. 每个请求的 token 序列是树上的一条路径，公共前缀只存一份 KV；
  2. 新请求沿树找最长公共前缀 → 命中部分跳过 prefill，只算剩余后缀；
  3. 多轮对话历史天然是同一条路径的延长，系统提示是所有人的共同祖先；
  4. 显存不够时从叶子端 LRU 淘汰（和操作系统的页面缓存一个思路）。
""")

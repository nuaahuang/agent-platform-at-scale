# 第 18A 章 · 插件怎么管：代码组织、依赖与语言选择

> 读完你能回答：
> ① 一个"插件"在文件层面到底是什么？
> ② 开一个插件、装一个插件，分别改了哪个文件的哪一行？
> ③ 一个生产级 harness 大概有多少个插件，怎么分组？
> ④ 为什么全用 TypeScript，哪些地方不得不用别的语言？
> ⑤ 这套组织方式的代价是什么？

---

## 1. 一个插件就是一个 npm 包

先说结论：**插件不是框架里的一个对象，是一个独立的 npm 包。** 全仓 101 个依赖包，每个都对应 `packages/` 下的一个目录。

以 dsh 的核心工具包为例，它的清单是这样：

```json
{
  "name": "@deepseek-ai/dsh-tools",
  "type": "module",
  "main": "lib/index.js",
  "exports": {
    ".": { "types": "./lib/types/index.d.ts", "default": "./lib/index.js" }
  },
  "peerDependencies": {
    "@deepseek-ai/cordis": "workspace:~",
    "@deepseek-ai/dsh-session": "workspace:*",
    "@deepseek-ai/dsh-llm": "workspace:*"
  }
}
```

这个清单里有三行值得看（`exports` 一共 5 个子路径，`peerDependencies` 一共 7 项，`dependencies` 只剩 2 项）：

| 字段 | 含义 |
|---|---|
| `type: module` | 纯 ESM，没有 CommonJS 兼容层 |
| `main: lib/index.js` | 发布的是编译产物，`src/` 也一起发（方便调试）|
| `peerDependencies` | 核心包**不打包**，由宿主提供 |

最后一行是关键设计。如果每个插件都把自己的 `dsh-session` 打进产物，装了 20 个插件就会加载 20 份会话日志模块，20 个互不相认的 `ctx.sessions`——内存多占 20 倍，状态却谁也看不见谁。用 `peerDependencies` 声明，宿主只提供一份，**所有插件共享同一个实例**。

这也解释了为什么插件之间没法"私有通信"——它们本来就在同一个进程、同一个 `ctx` 里。

## 2. 管理：一个 profile 就是三个文件

运行时的一切管理动作，最终都落在 profile 目录的三个文件上。这三个文件加起来通常不到 40 行：

| 文件 | 作用 |
|---|---|
| `package.json` | 依赖声明 + `dsh.profile.bundles` 有序列表 |
| `cordis.patch.yml` | 覆盖项：改某个插件的配置或开关 |
| `compatibility.json` | 版本豁免：允许某个包在特定运行时版本下运行 |

下面这张表是"你想做的事 → 实际改了哪"：

| 操作 | 改哪个文件 | 具体怎么改 |
|---|---|---|
| 关掉一个插件 | `cordis.patch.yml` | 找到最后一条匹配的覆盖项，改 `disabled: true` |
| 没有匹配的覆盖项 | `cordis.patch.yml` | 追加一条只含 `id` 和 `disabled` 的条目 |
| 开一个新组合包 | `package.json` | 往 `dsh.profile.bundles` 列表末尾追加 |
| 关一个组合包 | `package.json` | 从列表移除，但依赖保留 |
| 装一个第三方包 | `package.json` + 锁文件 | `pnpm add`，`expected` 校验包名和版本 |
| 允许某包在不兼容版本跑 | `compatibility.json` | 写 `package@version` → 运行时版本列表 |

第二条有个反直觉的细节：**如果组合包层已经让这个插件处于你要的状态，那条只含 `id` 和 `disabled` 的覆盖项会被删掉。**

也就是说，`cordis.patch.yml` 里只记录"与组合包不同的设置"。你开了又关回去，文件会恢复原样，不留垃圾。这个设计让 patch 文件始终保持最小，几个月后还能读懂。

## 3. 装一个包要过几道门

安装不是 `pnpm add` 就完事。dsh 在真正下载之前和之后一共五道检查，任何一道不过就中止或回滚：

| 阶段 | 检查 | 不通过的结果 |
|---|---|---|
| 装之前 | spec 是否指向一个包 | `not-found` / `not-a-package` |
| 装之前 | 是否声明了组合包 | `not-bundle` |
| 装之前 | `engines.dsh` 与 DSH peer 版本 | `incompatible-version` |
| 装之后 | 实际装的包名与版本是否等于预期 | `unexpected-package`，回滚 |
| 装之后 | 构建脚本是否被批准 | `pendingBuilds`，等用户授权 |

第四道最值得说。命令里带 `expected: { name, version }`，装完核对：如果装进来的是别的包、别的版本，或者顺带改动了其他直接依赖，**整个操作回滚**——`package.json`、`pnpm-lock.yaml`、`node_modules` 全部恢复。

这是防供应链投毒的基本动作：你请求的是 `foo@1.2.3`，注册表返回的却是 `foo@1.2.3` 加一个恶意 `postinstall`，回滚能挡住后半段。

注册表也有顺序：先问你指定的，再问配置的，最后问 `fallbackRegistries`（默认 npmmirror）。**但私有源永远不会落到公共源**——如果 pnpm 自己指向的是私有源，只问它自己。这条规则防止内部包名泄露到公网。

## 4. 代码怎么组织：两级目录的 monorepo

源码是一个 pnpm workspace，`pnpm-workspace.yaml` 里 8 类目录，其中 `packages/*/*` 是主体：

```yaml
packages:
  - packages/*/*        # 两级：域/包
  - apps/*
  - vendor/*
  - native/system
  - python/sdk-runtime
```

`packages/*/*` 这个两级结构是刻意的：第一级是**域**，全仓 15 个（core、llm、fs、session、tool、subagent、compaction、spill、storage、web、boot、bundle、client、api、util），第二级是**包**。找东西时先想域，再想包——平均每个域 6.7 个包。找东西时先想域，再想包。

版本策略是**锁步**：所有包同一个版本号（写作时是 `0.2.3-alpha.2`）。101 个包各自独立发版会让 `peerDependencies` 立刻变成地狱，锁步版本牺牲了"单独发一个小修复"的灵活性，换来了"装哪个组合都一致"。

构建链是三件套：

| 环节 | 工具 |
|---|---|
| 库打包 | tsdown（`^0.22.2`）|
| Web 客户端 | Vite（`8.2.2`）|
| 测试 | Vitest（`^4.1.11`）|
| 语言 | TypeScript（`^6.0.3`）|
| 运行时 | Node `^24.22.3 \|\| >=26.13.1`，纯 ESM |

仓库的 `devDependencies` 有 96 项，其中真正参与运行时的不到 5 项。另外还有几个自检脚本，比如 `verify-type-equiv`（校验文档里的类型代码块与源码一致）、`verify-application-entrypoints`（保证所有可执行入口都走 `dsh` 启动器）。这类脚本不产生功能，但缺了文档和代码会在几个月内漂移。

依赖脚本默认全部拒绝（pnpm 11 的 `strictDepBuilds`），`allowBuilds` 只白名单三个：`esbuild`（要下原生二进制）、`node-pty`（PTY 后端）、`lefthook`（git hooks）。**默认拒绝、逐个批准**，这是供应链安全里成本最低的一条。

## 5. 一个生产级 harness 有多少插件

dsh 的 `dsh-base` 组合包依赖 **101 个包**。按职责分组大致是这样：

| 组 | 代表包 | 干什么 |
|---|---|---|
| 循环与状态 | `agent`、`agent-loop`、`session`、`system-prompt`、`tools` 等 5 个 | 主干 |
| 模型接入 | `llm-deepseek-api-key`、`llm-deepseek-account`、`llm-retry` | 适配器与重试 |
| 工具 | `tool-bash`、`tool-fs`、`tool-fs-search`、`tool-web`、`tool-todo` | 模型能调的动作 |
| 执行器 | `tool-executor-native`、`tool-executor-ptc` | 单次调用 / 程序内批量调用 |
| 子 agent | `subagent`、`subagent-fork-in-process`、`subagent-spawn-in-process` | 派生与恢复 |
| 上下文 | `compaction-basic`、`compaction-tool-result-pruner`、`spill-local`、`token-meter` | 四道工序 |
| 会话存储 | `session-persistence-jsonl`、`session-query-sqlite`、`session-projection` | 落盘与查询 |
| 沙箱与审批 | `sandbox-policy`、`bash-sandbox`、`fs-observation-policy`、`user-approval` | 权限边界 |
| 检索与技能 | `skill`、`skill-filesystem`、`mcp-resources` | 按需加载知识 |
| 观测 | `otel`、`session-telemetry-otel`、`api-gateway` | 遥测与网关 |
| 配套 | `commands`、`plan-mode`、`goal`、`plugin-manager`、`hmr` | 命令、计划、目标、自管理 |

十一个组，101 个包，平均一组 9.2 个。最多的"工具"组 13 个，最少的"执行器"组 2 个。

这 101 个包里只有 1 个是组合包（`dsh-base` 本身），剩下 100 个都是能力包。能力包之间不互相 import——它们全部通过同一个 `ctx` 通信，包与包之间的直接依赖是 0 行。真正耦合的是数据形状，不是代码。

所以升级一个包时，你看到的风险通常是"它改了某个事件的 payload"，而不是"它引用了别的包的内部函数"。前者会体现在 1 个类型定义里，后者会散在几十个文件里。这也是为什么 100 个包还能维护得动：耦合面被压缩到 13 种事件和若干 ctx 键上。

这个数字有参考价值：**如果要自研一个同等能力的 harness，起点不是"写一个 loop"，是"准备 100 个左右的模块边界"。** 这也解释了为什么第 18 章说"照着写很难"。

## 6. 语言：96% 是 TypeScript

仓库的语言构成（按字节，一共 14 种语言、84.7 MB）：

| 语言 | 体积 | 占比 | 用途 |
|---|---|---|---|
| TypeScript | 81.1 MB | 95.8% | 全部插件、核心、客户端、适配器 |
| CSS | 1.1 MB | 1.3% | Web 界面样式 |
| Python | 0.93 MB | 1.1% | SDK 薄封装 |
| JavaScript | 0.64 MB | 0.8% | 构建配置、浏览器端胶水 |
| Swift | 0.58 MB | 0.7% | macOS 原生壳 |
| C# / PowerShell / NSIS | 0.55 MB | 0.6% | Windows 安装与壳 |
| C++ / C | 0.17 MB | 0.2% | Landlock 启动器、PTY |
| 其他 | 0.1 MB | 0.1% | Shell、HTML、Makefile |

选 TypeScript 有三个具体原因，不是"生态好"这种空话：

**第一，插件要热加载。** 宿主和插件跑在同一个 Node 进程里，改一个插件文件要能立即生效。跨语言做不到这一点——换语言意味着跨进程或 FFI（Foreign Function Interface，跨语言调用接口），热加载就变成了"重启服务"。

**第二，类型要跨宿主和客户端共享。** 一个工具的参数 schema，服务端要校验、客户端要渲染表单、模型要看到 JSON Schema，三处要是同一份定义。TS 的类型系统和 npm 包边界让这件事只需要写一遍。

**第三，插件作者的门槛。** 插件是给别人写的，语言越主流，愿意写的人越多。TS 在 npm 生态里的位置，决定了"装一个插件"和"装一个 npm 包"是同义词。

少数非 TS 的地方只占 4.2%，而且每一处都是被平台逼的：

| 语言 | 位置 | 为什么不能用 TS |
|---|---|---|
| C++ / C | Landlock 启动器、PTY | 需要系统调用和内核接口 |
| Swift | macOS 壳 | 系统 API 只有原生绑定 |
| C# / PowerShell / NSIS | Windows 安装器 | 安装器要在 Node 装好之前运行 |

Python 那 0.93 MB 只是 SDK 的运行时封装——把 `dsh` CLI 打包成 wheel，让 Python 调用方能 `pip install` 然后起子进程。**它不是第二套实现。**

## 7. 代价

| 代价 | 表现 |
|---|---|
| 包数量 | 101 个包的依赖图，新人第一周基本在认路，第三周才敢改 core |
| 锁步版本 | 改一个包要发全部，不能只发一个补丁 |
| peer 依赖 | 版本不匹配时报错在运行期，不在安装期（`pnpm install` 只给 1 行警告）|
| 启动开销 | 101 个插件初始化，冷启动 200–500ms，比单体慢 3–5 倍 |
| 文档漂移 | 类型定义散在几十个 README 里，要靠脚本校验 |

第三行是个真实的坑：`peerDependencies` 不满足时，`pnpm install` 只警告不报错，等到运行时 `ctx.sessions` 是 `undefined` 才发现。

所以 dsh 加了两道防线：安装前检查 `engines.dsh`，装完核对 `expected`。**两道都通过，才算装好。**

## 8. 对我们的启示

如果自研平台，这四条可以直接用：

| 原则 | 具体做法 |
|---|---|
| 插件边界 = 包边界 | 一个插件一个包，不要一个包里放五个插件 |
| 核心用 peer 依赖 | 会话、模型、工具注册表只允许一份实例 |
| 版本锁步 | 全部包同一个版本号，减少组合爆炸 |
| 配置只记差异 | patch 文件只写与默认不同的部分 |

第一条最容易被违反。常见做法是一个 `plugins/` 目录下放十几个文件，靠约定区分——结果就是"改 A 影响了 B"，因为它们在同一个包里共享模块状态。

拆成包之后，**依赖关系变成显式的**：A 要用 B，要写进 `package.json`，否则构建就失败。这比代码评审可靠。

## 9. 失败模式

| 现象 | 根因 | 怎么发现 | 怎么修 |
|---|---|---|---|
| 两个插件各有一份 session | 核心包被写进 dependencies 而非 peer | 打印 `ctx.sessions` 是否同一引用 | 改成 peerDependencies |
| 装了包但插件没生效 | 组合包没加入 `bundles` 列表 | `listBundles` 看 `enabled` | 追加到列表 |
| 开关改了重启又变回去 | 改的是组合包层不是 patch 层 | 对比 patch 与 bundles | 走 `plugin_manager` |
| 安装成功但启动崩 | peer 版本不匹配 | 看启动日志的 peer 警告 | 加 `engines.dsh` 检查 |
| 私有包名泄露到公网 | 注册表顺序配错 | 查 `registries()` 顺序 | 私有源只问自己 |
| 插件热加载后状态错乱 | 卸载没撤销副作用 | 反复开关后对比状态 | 注册要返回 disposer |

## 10. 验收

1. 能说出"关一个插件"改的是哪个文件的哪个字段。
2. 能解释为什么核心包要用 `peerDependencies`。
3. 能说出 101 个包大致分成哪十一个组。
4. 能判断你的项目该不该拆成多包。

## 11. 复盘卡

```
我原来以为…实际…差在：
```

我原来以为"插件化"就是把代码分文件、加个注册函数，实际差在"包边界即插件边界"——只有拆成独立的包，依赖才是显式的、实例才是唯一的、版本才管得住。

## 12. 延伸

- 第 18 章：插件树怎么组装起来
- 第 19 章：会话日志怎么落盘、怎么重放
- 第 21 章：工具插件怎么写、怎么被把关
- 源码：[deepseek-ai/deepseek-harness](https://github.com/deepseek-ai/deepseek-harness)

小结：插件管理不是"写个管理器"，是三件事同时成立——包边界清晰、依赖关系显式、配置只记差异。缺任何一条，装到第 20 个插件时就会失控。

# AudioEdition 开发文档

> **这是给"要改这个项目的人"看的文档**：架构、模块职责、数据模型、执行链的判据、
> 以及每一条判据**为什么长这样**。
>
> 与仓库里其它文档的分工：
>
> | 文档 | 读者 | 内容 |
> |---|---|---|
> | `README.md`（根） | 使用者 | 功能、界面、怎么用 |
> | `ui-kit/README.md` | 改样式的人 | 组件与令牌契约、层叠顺序的硬约束 |
> | `tests/README.md` | 跑自检的人 | 每个测试脚本验什么、命令、探针的坑 |
> | `docs/README.md`（本文） | 改代码的人 | **架构与实现**，以及现有文档的索引 |
> | 根目录各 `*.md` 方案文档 | 历史决策 | 设计过程与取舍留档（§14 有清单） |
>
> 本文的每一条结论都对应到代码。凡"代码里其实不是这样"的地方，都在 §13 单独列出，
> **不**在正文里照抄。
>
> **快照声明**：§15 的规模数字与 §13 的漂移清单是 **2026-10-07 17:52 这一次核对**的结果
> （Python 3.14.7 / ffmpeg 9.0.2）。这个工作区当时正被另一条工作流并行改动，
> 行数一类的数字会漂；判据与结构不会。

---

## 0. 这个项目是什么

一个**只在本机运行**的音频工具箱 WebUI：

- 后端 Python + FastAPI，前端是**零构建**的纯 HTML/CSS/JS，由同一个后端进程一起托管；
- 所有音频处理交给外部程序（ffmpeg / ffprobe / flac / metaflac），后端负责编排与判定；
- 核心概念是**执行链**：把若干个操作排成一条流水线，每个文件按链序走完；
- 打开即空页面（启动时清空工作区），不需要登录、不需要联网、没有数据库服务。

---

## 1. 快速开始

### 1.1 跑起来

```powershell
# Windows
run.bat
# macOS / Linux
./run.sh
```

两个脚本做的事一样：

1. 找 Python，打印版本；
2. 检查 `ffmpeg` / `ffprobe` / `flac` / `metaflac` 在不在 PATH（**缺了也能启动**，
   只是相关功能会明确报错，不会静默失败）；
3. `python -c "import fastapi,uvicorn,mutagen,multipart"` 探测依赖，缺了就地 `pip install`；
4. 先检查端口是否被占用（**不等 uvicorn 报 10048**，那时浏览器可能已经打开了）。
   占用时报出占用者的**进程名 + PID**，并给出两条出路（换端口 / `taskkill`）；
5. 后台轮询 `/api/health`，**服务真的应答之后**才打开浏览器（可用 `AE_NO_BROWSER` 跳过）；
6. `python -m backend.app`。

默认地址 `http://127.0.0.1:8765`。

### 1.2 依赖

`requirements.txt`（下限，不是硬锁定）：

```
fastapi>=0.141.1
uvicorn[standard]>=0.54.0      # 会带上 websockets，只有 tests/snapshot_drag_real.py 的 CDP 用它
mutagen>=1.48.1
python-multipart>=0.0.32
```

外部程序**不由 pip 安装**：

| 程序 | 装法 | 用在哪 |
|---|---|---|
| `ffmpeg` / `ffprobe` | `winget install Gyan.FFmpeg` | 转码、解码、响度（ebur128）、波形、探测 |
| `flac` / `metaflac` | `winget install Xiph.FLAC` | FLAC 完整性校验、FLAC 标签与封面读写 |

**最低 Python 3.10**（代码里用了 `dict[str, str]`、`X | Y` 这类注解）。

### 1.3 环境变量

全后端**只有 5 个**环境变量，全部在 `backend/config.py` 里读：

| 变量 | 默认 | 语义 |
|---|---|---|
| `AE_CONCURRENCY` | `2` | 队列 worker 数，钳到 `1..16`。**上限 16 是故意压住的** —— 每个 slot 都可能起一个 ffmpeg，8 个同时转码能把整机（含 WebUI）打满，而那正是这条需求要防的事。非数字会在 **import 时**抛 `ValueError`（早点炸） |
| `AE_LONG_SLOTS` | 未设 → 按 `max(1, n-1)` 推导 | 长任务最多占几个 worker。`0` = 关掉限制。**不在这里烤死**，因为测试会 `Queue(workers=4)`，烤死的话它只拿到 1 个长任务槽 |
| `AE_HOST` | `127.0.0.1` | 监听地址 |
| `AE_PORT` | `8765` | 端口 |
| `AE_FRESH` | `1` | `1` = **启动时清空工作区**（需求："每次打开都是空页面"）。想留住上一轮的文件调试就设 `0` |

> ⚠ **`AE_FRESH` 的默认值是"清空"，这是最容易踩的一个坑。**
> 起一次服务就会把 `uploads/` / `outputs/` / `.cache/` 和数据库**移进 `.trash/`**
> （不是 `unlink`，见 §3.4），所以开发/跑测试时请先起服务再跑脚本，
> 或者 `AE_FRESH=0`。
> `tests/README.md` 开头专门写了这条警告。

### 1.4 目录总览

```
audio-edition/
├── run.bat / run.sh              启动脚本（见 §1.1）
├── requirements.txt
├── index.html                    唯一的应用页面（444 行，见 §9.1）
├── _snapdrag.html                拖拽自检用的第二个页面（ui_check.py 也校验它）
├── app.js / api.js               前端全部逻辑（无打包、无框架，见 §9）
├── backend/                      后端（见 §3.1）
│   ├── app.py                    FastAPI 装配 + 生命周期 + 静态挂载
│   ├── config.py                 路径、常量、回收站、命名规则
│   ├── store.py                  SQLite 全部读写（唯一碰数据库的模块）
│   ├── queue.py                  任务队列与调度闸门
│   ├── chain.py                  建链 + 格式流规则 + 链上提示
│   ├── tasks.py                  任务 handler（ffmpeg 调用的实际落点）
│   ├── audio.py                  ffprobe / 标签 / 封面 / 峰值 / 响度时间线 / 报告
│   ├── runner.py                 子进程封装（超时、二进制输出、日志）
│   ├── toolchain.py              外部程序探测与版本缓存
│   ├── logs.py                   内存环形日志缓冲
│   ├── formats.py                容器格式分类（有损/无损）
│   ├── theme.py                  解析 ui/theme.css → SVG 用的实色
│   ├── chart_axis.py             响度图纵轴映射（唯一事实源）
│   ├── chart_layout.py           响度图版面（唯一事实源）
│   ├── loudness_svg.py           响度图整页 SVG 渲染器
│   ├── drp.py                    动态模式（DRP）检测
│   ├── cards/                    卡片系统（6 个模块，见 §6）
│   └── routers/                  HTTP 端点（57 个，见 §8）
├── ui/                           10 个样式表（应用的**源**，见 §10）
├── ui-kit/                       组件库交付产物 + 契约文档 + 画廊（**生成物**）
├── tools/                        build_ui_kit.py（拼样式）、send_to_ae.py
├── tests/                        自检脚本（见 §11）
├── uploads/ outputs/ .cache/     运行时数据（gitignored，启动时清空）
├── .trash/                       删除与清空的**回收站**（gitignored，保留 30 天）
└── audioedition.db               SQLite（gitignored）
```

---

## 2. 总体架构

### 2.1 一次"执行链"请求走到哪

```
浏览器 (app.js)
  │  ① 用户把卡片排成链，选档位（serial/parallel）
  │  ② 前端先用 /api/ops 的同一份字段算一遍可用性，把不可选的卡置灰
  ▼
POST /api/ops/chain  { mode, fileIds, steps, theme, themeMode }
  │
  ▼  backend/routers/ops.py
  ▼  backend/chain.py :: build_chain()
        · validate_chain()       —— 用 boundary.availability() 再算一遍（**边界在后端**）
        · check_format_flow()    —— 仅串行档：有损→无损、有损→有损的码率/采样率升档
        · 逐条 store.create_task()，串行档写死 src_task_id
        · **全部建完之后**才 queue_.submit()
  ▼
backend/queue.py :: Queue
   N 个 worker 线程，每个从队列取一个 task，过 _gate()：
        ① 上游就绪  →  ①b 汇总类全局屏障  →  ①c 长任务槽位  →  ② 同文件租约
   过关才 start_task；没过关走 _requeue()（放回队尾 + 指数退避，**绝不写库**）
  │
  ▼  backend/tasks.py :: h_xxx(ctx)
        · 真正调 ffmpeg / flac / metaflac（经 runner）
        · 有时产出派生产物 → store.add_derived_file() + 回填 src_output
        · 测量值（响度那组）合并进 files.info
  │
  ▼  store（SQLite）+ logs（环形缓冲）+ 内存租约
  │
  ▼
前端 3 条数据通道：轮询 /api/tasks/queue（2s）、轮询 /api/logs（1.5s）、SSE /api/events
```

### 2.2 分层与依赖方向

后端的依赖**单向**，没有环：

```
config          (只依赖标准库)          ← 谁都能依赖它
  ↓
store  logs  runner  formats  theme  chart_axis  chart_layout
  ↓
cards/  (specs → builtin → validate → store → contract → boundary)
  ↓
audio  drp  loudness_svg  toolchain
  ↓
tasks    (handler：唯一真正干活的层)
  ↓
queue    (调度：不认识任何具体 op，只看 task.type)
  ↓
chain    (建链：把 steps 变成 tasks)
  ↓
routers  (HTTP：只做参数整形与错误→状态码)
  ↓
app      (装配)
```

几条**刻意**的设计约束：

- **`store.py` 是唯一碰数据库的模块**。其它模块要数据都经它，所以"状态口径"只有一处。
- **`queue.py` 不认识任何具体 op**。它读 `task.type`（字符串）与 `store` 里的状态，
  所有 op 知识都在 handler 与 `tasks.register_all()` 的注册表里。
- **`chain.py` 不碰 ffmpeg、不碰数据库连接**，只调 `store.create_task`。
- **`cards/` 不依赖 `tasks/`**（`specs.task_params()` 只做参数整形，不执行）。
- **前端不 import 后端**，但**共用判据**：`/api/ops` 把 `needs` / `gives` / `produce` /
  `gives_formats` 原样发给浏览器，前端用同一份字段复算一遍可用性与格式流（§5.14）。

### 2.3 零构建前端的硬约束

这个项目**故意没有任何前端工具链**：没有 `package.json`、没有 `node_modules`、
没有打包器、没有 `@import` 汇总入口、两个 `<script>` 都**没有** `type="module"`。

后果（每一条都是需要遵守的约定）：

1. **不能 `import` / `export`**。`app.js` 与 `api.js` 靠 `<script>` 顺序 + **共享的全局词法作用域**
   协作：`app.js` 顶层的 `const $ = …` / `const FILES = []` / `function renderFiles()`
   后续脚本能直接读到。
2. **顶层 `const` 不是 `window.X`**。所以浏览器控制台里 `window.API` 是 `undefined`。
   要跨文件/跨控制台暴露必须**显式赋值** —— 全项目只有 6 处 `window.applyXxx`
   （`applyServerFiles` / `applyServerQueue` / `applyServerLogs` / `applyServerCards` /
   `applyOffline` / `applyServerHealth`），它们是 `app.js` → `api.js` 的**双向通道**。
3. **改完刷新即生效**，不用重启、不用清缓存（`NoCacheStatic` 保证，见 §3.3）。
   **唯一的例外**是 `ui-kit/audioedition-ui.css` —— 那是生成物，要跑
   `python tools/build_ui_kit.py`。应用本身不读它。
4. **顺序即契约**：`index.html` 里 10 个 `<link>` 的顺序、2 个 `<script>` 的顺序，
   都有测试硬核对（`tests/ui_check.py`）。
5. **没有 lint、没有类型检查**。所以踩坑结论直接写在代码现场当文档 ——
   `app.js` 里最长的一段注释是 `bindFollow` 的抖动分析（约 30 行），
   `ui-kit` 刻意不压缩样式也是这个理由（"这套样式的价值有一大半在注释里"）。
6. **样式顺序会静默走样**。层叠只看声明先后，打乱顺序不报错、只是看起来不对。
   详见 `ui-kit/README.md` §2 与本文 §10.3。

---

## 3. 后端

### 3.1 模块清单

| 模块 | 职责 | 关键入口 |
|---|---|---|
| `app.py` | FastAPI 装配、生命周期、静态挂载 | `app`、`lifespan`、`main` |
| `config.py` | 路径与常量、路径安全、回收站、命名规则 | `safe_join`、`move_to_trash`、`wipe_workspace`、`unique_path` |
| `store.py` | SQLite 全部读写、迁移、租约、链状态解算 | `init_db`、`aggregate_state`、`chain_zip_window` |
| `queue.py` | worker 线程池、调度闸门、defer、长任务槽位 | `Queue.start`、`Queue._gate` |
| `tasks.py` | 任务 handler、ffmpeg 配方、产物落盘 | `register_all`、`h_convert`、`h_loudness` |
| `chain.py` | 建链、格式流规则、链上提示 | `build_chain`、`check_format_flow` |
| `audio.py` | 探测、标签、封面、峰值、响度时间线、md 报告 | `probe`、`peaks`、`loudness_timeline` |
| `runner.py` | 子进程封装 | `run` / `run_bytes`（超时、不吞 stderr） |
| `toolchain.py` | 外部程序探测 + 版本缓存 + 单例 `toolchain` | `probe()` |
| `logs.py` | 内存环形日志 + 序号（供增量拉取） | `log` / `warn` / `err` / `items_since` |
| `formats.py` | 容器 → 有损/无损分类 | `classify`、`is_lossy`、`is_lossless` |
| `theme.py` | 正则解析 `ui/theme.css` → 实色 | `solid`、`normalize`、`ensure_contrast` |
| `chart_axis.py` | 响度图纵轴映射（F/knee） | `frac`、`invert`、`y`、`check` |
| `chart_layout.py` | 响度图版面参数与不变式 | `PLOT`、`BOXES`、`canvas_height`、`check` |
| `loudness_svg.py` | 整页 SVG 渲染 | `render_loudness_svg`、`chart_palette` |
| `drp.py` | 动态模式检测（方案 C） | `patterns`、`extremes` |
| `cards/` | 卡片系统（6 个模块，见 §6） | `OPS`、`BUILTIN_CARDS`、`validate_step`、`task_params` |
| `routers/` | 5 个路由模块，57 个端点（见 §8） | `router` |

### 3.2 启动序列

`backend/app.py` 的 `lifespan`，**顺序有意义**：

```
config.ensure_dirs()                     建 uploads / outputs / .cache / .trash
if FRESH_ON_START:                       默认 True
    config.wipe_workspace()              移进 .trash（不是删）
    config.reset_db()                    数据库文件也移走
store.init_db()                          迁移 → 建表 → 建索引（§4.3 的顺序是硬约束）
print 迁移补列                           迁移**不能静默发生**
store.reset_stale_running()              上次被 Ctrl+C 打断的 running 任务 → failed
store.reset_stale_chain_pending()        中断的链上 pending → failed/skipped
store.release_all_leases()               内存态，重启即空（防热重载残留）
tasks.register_all(q_mod.queue_)         注册 handler
q_mod.queue_.start()                     起 worker 线程
toolchain.probe()                        打印每个外部程序的 OK/FAIL + 版本
```

`finally: queue_.stop()`。注意**清空工作区必须在建表之前**，否则页面首帧可能读到上一轮的文件。

**路由必须先 `include_router`，静态挂载必须最后**（文件里有注释）：
`app.mount("/", StaticFiles(...))` 是个 catch-all，先挂上会把 `/api/*` 全吞掉。

CORS 是 `allow_origins=["*"]` —— 仅本机使用，放开是为了方便 `file://` 调试。

全局异常处理器只挂了一个：`cards_store.CardsFileError` → 500 +
`{"detail": {"message": …, "kind": "cards-file"}}`。**刻意全局注册而不是逐端点包**：
`cards.json` 被 CRUD / 快照 / 预设三条路径读写，漏掉任何一个都等于漏掉一次
"把用户卡片写没"的机会。

### 3.3 静态前端与缓存头

`NoCacheStatic(StaticFiles)`：`StaticFiles` 只发 `ETag` / `Last-Modified`，
**不发 `Cache-Control`**，浏览器于是走"启发式缓存"（新鲜期 ≈ 文件年龄的 10%）——
改完 CSS 刷新页面可能还是旧文件。

- 所有静态资源：`Cache-Control: no-cache, must-revalidate`（仍走 304，开销极小）；
- `*.html` / `*.htm` 额外 `no-store, must-revalidate` + `Pragma: no-cache`。

**为什么入口文件要给 `no-store`**：它引用 `app.js` 和各个 css，一旦它自己被缓存住，
后面引用的是哪一版就完全不由我们决定了。这条真踩过 ——
"后端修好了、后端测试全绿，页面却一直是旧行为"，排查时把缓存当成了逻辑错。

### 3.4 配置、路径安全与回收站

三条安全边界（对应需求 §5）：

1. **用户永远不能指定输出路径**，只由系统在 `outputs/` 下生成；
2. **所有落盘路径必须经 `safe_join` 收敛到受管目录内**，杜绝目录穿越；
3. **上传只允许白名单扩展名**（`AUDIO_EXT` 11 个 / `IMAGE_EXT` 6 个）。
   `AUDIO_EXT` 与 `api.js` 的 `DND.ALLOW_AUDIO`、`tools/send_to_ae.py` 的 `AUDIO_EXT`
   是**三份必须逐项一致**的名单（后端那份权威），加格式时三处都要改；
   它同时经 `ServerInfo.as_dict()` 的 `audioExt` 发到 `/api/config`。
   ⚠ 它是"能收进来"的名单，**不是"能转过去"的名单** —— 后者是
   `formats.AUDIO_FORMATS` / `FORMAT_ARGS`，两者刻意不同（`.m4s` 只在前面那一份里）。

命名与去重：

- `sanitize_name` / `sanitize_relpath`：清洗非法字符（`<>:"/\|?*` 与控制字符）、
  Windows 保留名（`CON` / `PRN` / `COM1..9` / `LPT1..9`）、Unicode 归一化；
- `unique_path`：同名时追加 `-1` / `-2`，**插在扩展名之前**（`sine.loudness-1.md`）。
  所以测试里通配要写 `*.loudness*.md`，不是 `*.loudness.md`；
- `zip_filename`：`upload-<日期>-<卡片名…>.zip`，同名卡片退化成 `名字x2`；
- `run_dir_name`：`upload-<YYYYMMDD>-<chain_id 末 6 位>`。**用 chain_id 而不是
  "当日第几次"** —— 并发建链可能算出同一个序号。

**回收站（`.trash/`）**：任何删除与清空都先**移**进去，保留原目录结构，
`TRASH_KEEP_DAYS = 30` 之后由 `prune_trash()` 真删。改动动机写在 `config.py:27-36`：
原来 `wipe_workspace()` 是 `unlink` / `rmtree`，一次忘了带 `AE_FRESH=0` 的冒烟测试
把 `uploads/` 里 6 个音频（约 311MB，含一个刚导入的 DAW marker wav）永久删掉了。

### 3.5 日志

`logs.py` 是**内存环形缓冲** + 单调递增序号。前端用 `GET /api/logs?since=N` 增量拉取
（默认每 1.5 s），所以日志面板不依赖任何持久化。`DELETE /api/logs` 清空缓冲。

### 3.6 子进程（`runner.py`）与工具链（`toolchain.py`）

`runner` 是**唯一**起子进程的地方，负责：超时（`config.TASK_TIMEOUT = 3600`）、
文本/二进制两种取输出方式（`run_bytes` 必须用于解码音频原始采样）、
以及把命令行原样记进日志（便于用户复制去自己跑）。

`toolchain.probe()` 探测 `ffmpeg` / `ffprobe` / `flac` / `metaflac`，
返回单例 `toolchain`（带版本号，进程内缓存）。启动横幅与 `/api/health` 都读它。
**缺工具不阻止启动**，只是相关 handler 会明确报错。

> ⚠ 已知问题：`tests/dsh-wheel/check_loudness_metrics.py` 里有一段
> `_seed_toolchain()` 绕过探测的 workaround，注释写明原因是
> `toolchain._probe_metaflac()` 在 Python 3.14 上 `TemporaryDirectory(..., ignore_cleanup_errors=True)`
> 的清理会抛 `PermissionError [WinError 5]` 并传出 `__exit__`，
> 导致**任何**走 toolchain 的东西都挂。注释里写着"toolchain 修好后删掉这块"。
> 本次核对**未复现也未修复**。

---

## 4. 数据模型

### 4.1 SQLite 的打开方式

```python
c = sqlite3.connect(str(config.DB_PATH), timeout=30, isolation_level=None)  # autocommit
c.row_factory = sqlite3.Row
c.execute("PRAGMA journal_mode=WAL")
c.execute("PRAGMA foreign_keys=ON")
c.execute("PRAGMA busy_timeout=10000")
```

每**线程**一个连接（`threading.local`）—— sqlite3 连接不可跨线程共享，
而队列有 N 个 worker 线程 + FastAPI 的请求线程。

`isolation_level=None` 是 **autocommit**：每条语句立刻生效，没有隐式事务。
好处是"任务状态变更"这类单语句操作天然原子；代价是**跨多语句的操作要自己保证顺序**
（例如建链是逐条 INSERT，`chain_build_in_progress()` 就是为这个窗口准备的，见 §5.11）。

### 4.2 表结构

两张表，SQL 在 `store.SCHEMA`。

**`files`** —— 受管音频文件（`uploads/` 里的工作副本 + `outputs/` 里的派生产物）

| 列 | 说明 |
|---|---|
| `id` | 主键，`new_id("f_")` |
| `rel_path` | 相对 `uploads/` 的路径。**`UNIQUE`** —— 注意它是**跨 origin 的全局唯一**，所以派生产物绝不能走这条唯一约束（走 `add_derived_file`） |
| `name` | 显示名 |
| `size` / `mtime` | 落库时的快照，用于判断"内容换了" |
| `state` | `uploaded` / `processing` / `done` / `failed` / `deleted` |
| `info` | JSON：`probe` 结果 + 测量值（§4.6） |
| `origin` | `imported` / `derived` |
| `derived_from` | `origin=derived` 时指向产出它的任务 |
| `created_at` / `updated_at` | 秒级浮点 |

索引：`idx_files_state`、`idx_files_origin`。

**`tasks`** —— 一条任务 = 一个 (文件, 操作) 的执行单元

| 列 | 说明 |
|---|---|
| `id` / `type` / `state` / `progress` | `state ∈ pending / running / success / failed / cancelled / skipped` |
| `file_id` | **可为空**（汇总类任务，如 `zip`） |
| `params` / `result` / `error` | JSON / JSON / 文本 |
| `batch_id` | 同一批操作共用一个 id（一次建链里**每一步**一个 batch） |
| `created_at` / `started_at` / `ended_at` | |
| `chain_id` / `step_id` / `step_idx` / `chain_steps` / `chain_mode` | 执行链的全部信息（§5） |
| `src_task_id` | **输入 = 哪条任务的产物**，只有串行档会填 |
| `src_output` | 上游成功后才回填：产物在 `outputs/` 下的相对路径 |

索引：`idx_tasks_file(file_id, created_at DESC)`、`idx_tasks_state`、`idx_tasks_batch`、
`idx_tasks_chain(chain_id, step_idx)`、`idx_tasks_src`。

外键 `FOREIGN KEY(file_id) REFERENCES files(id) ON DELETE CASCADE`
（所以删文件会级联删任务行 —— 测试里"僵尸文件"的坑就是这个，见 §11.4）。

> **为什么 `chain_mode` 要显式存**：不能从 `src_task_id` 反推档位
> （并行档也可能有 `src_task_id`？不 —— 是反过来：串行档的**首步**没有 `src_task_id`，
> 于是"有没有上一步"分辨不出"并行档"与"串行档的第一步"）。

### 4.3 迁移机制（顺序是硬约束）

```python
def init_db():
    migrated = apply_migrations(c)                  # ① 必须先补列
    c.executescript(SCHEMA)                        # ② 再建表/建索引
    c.executescript(";\n".join(MIGRATION_INDEXES) + ";")
```

`MIGRATIONS` 是 9 条 `ALTER TABLE ... ADD COLUMN`（tasks 7 条 + files 2 条），
`MIGRATION_INDEXES` 是 3 条 `CREATE INDEX`。

**为什么顺序不能颠倒**（代码注释里写得很直接）：`SCHEMA` 里有
`CREATE INDEX ... ON files(origin)`，而老库的 `files` 表还没有 `origin` 列 ——
`CREATE TABLE IF NOT EXISTS` 不会给已存在的表补列，于是那句 `CREATE INDEX` 直接抛
`no such column: origin`，**整个服务起不来**。

这个顺序错误**实测踩过**：单测里手工调 `apply_migrations` 全绿，因为那条路绕开了
`init_db()`。所以**测试也必须走 `init_db()` 这条路**。

**加列时两处都要加**：`SCHEMA` 里（给全新库）+ `MIGRATIONS` 里（给老库）。
迁移会记录在 `_MIGRATED` 里，由启动横幅打印 —— 迁移静默发生的话，
出问题时没人知道库被改过。

### 4.4 状态机与聚合口径

单个任务的 6 个状态见上表。**文件的状态**由它所有任务聚合出来
（`store.aggregate_state`），依次判：

| 顺序 | 条件 | 结果 |
|---|---|---|
| 1 | 有 `running` | `processing` |
| 2 | 有 `pending` | `processing` |
| 3 | 有 `success` 且比其它都新 | `done` |
| 4 | 有 `failed` | `failed` |
| 5 | 只有 `skipped` | `failed` |
| 6 | 其余 | `ready` |

第 5 条容易漏：**只有 skipped 也要显示成 failed**。`skipped` 是"上游失败了所以我没跑"，
对用户来说就是"这一步坏了"，显示成"就绪"会让人以为可以点。

前端侧的镜像：`applyServerQueue` 里 `skipped` **必须显式映射成 failed** ——
兜底分支是 `|| 'pending'`，漏了会显示成永远转圈的"排队中"。

### 4.5 文件租约（内存态）

`store._LEASES`：`{file_id: {task_id: op}}`，**只在内存里**（进程重启即空）。

规则：**写者独占、读者共享**。`acquire_file_leases(task)` 的判据经 `_op_of_task_type`
映射到 op 后看 `contract.rewrites_input`：

- 要写 → 必须此时无任何别的持有者；
- 只读 → 与其它读者共存，但不能与写者共存。

`release_all_leases()` 在启动时显式调一次（防热重载残留），`held_leases()` 供测试与调试。

> ⚠ `_requeue()` 里**必须**调 `release_file_leases(tid)`。`_gate` 走到第 ② 步时
> 已经拿到租约了，不放回去的话下次探测会把它自己的租约当成"别人在写"，
> 于是**永远推迟**（实测踩过：任务一直停在 `pending`，看起来像队列卡死），
> 而且会连带挡住整个文件的其它任务。

### 4.6 `info` 的合并语义（`merge_file_info` vs `set_file_info`）

`files.info` 里混着两类东西：

- **`probe` 结果**：格式、时长、采样率、位深、声道、封面有无、标签……（整体重算）；
- **测量值**：`MEASUREMENT_KEYS = ("loudness", "truePeak", "loudnessRange", "samplePeak", "dra", "drp")`
  —— 这些要**跑响度分析**才有，而且很贵。

所以：

- `set_file_info` 是**整体覆盖**，会把这些测量值擦掉；
- `merge_file_info` 是**逐块合并**，并保留测量值。

判据：**"重新探测"或"改个标签"之后测量值必须还在**；只有**内容真的换了**
（`add_file` 时发现 size/mtime 都变了）才调 `_forget_measurements` 丢掉。

这条线上修过两次（"卡片上写着 `undefined LUFS`"）：
① 前端从来没把 `info.loudness` 读进 `FILES`；② 后端测出来的响度压根没落库、
且 `set_file_info` 会把已测出的值擦掉。现在由
`tests/loudness_display_check.py`、`tests/chain_store_check.py` §10、
`tests/smoke_api.py` §19 三处钉住。

### 4.7 派生产物与文件行

`add_derived_file(rel_to_outputs=…, name=…, size=…, mtime=…, derived_from=…)`
在 `files` 表里插一行 `origin='derived'`。它**不占 `rel_path` 的唯一性**那一套
（`rel_path` 是相对 `uploads/` 的，派生产物有自己的寻址方式）。

派生产物的寻址有两个相对基准，**不能混**：

| 字段 | 相对谁 | 谁用 |
|---|---|---|
| `output` | **项目根**（`outputs/waveforms/x.png`） | 前端下载/预览 |
| `relPath` | **`outputs/`**（`waveforms/x.png`） | 后端建派生产物行、交下游当输入 |

`tasks._artifact()` 同时给这两个字段，**写成同一个函数**是为了避免某个 handler 漏给。
前端的 `outputUrl(rel, inline)` 负责剥掉可能多出来的 `outputs/` 前缀并对每段做
`encodeURIComponent`（中文/空格文件名）。

---

## 5. 执行链

这是这个项目最复杂的部分，也是绝大多数 bug 的来源。

### 5.1 名词

| 词 | 含义 |
|---|---|
| **op** | 一个后端操作（`convert` / `tags` / `zip` / `loudness-report` …），共 **15 个** |
| **card** | 功能卡片 = `op` + 一套 `params` 快照 + 图标 + 名字。内置 **71 张**，自定义卡片由用户建 |
| **step** | 链上的一步：`{cardId?, name?, op, params}`。**必须带 `op`** |
| **chain** | 一次提交的整条链，共享一个 `chain_id` |
| **task** | 一条 (文件, step) 的执行单元。串行档下同一文件相邻两步由 `src_task_id` 相连 |

**"每个文件自己的流水线"**是核心语义：同一文件内严格有序、不同文件之间并行。

### 5.2 两个档位

`MODE_SERIAL = "serial"` / `MODE_PARALLEL = "parallel"`，`DEFAULT_MODE = parallel`。
**只有两档** —— 原来的"分段"档已删（它唯一的卖点是"每步都回到原文件"，
而那正是要修的 bug）。

| | 并行（默认） | 串行 |
|---|---|---|
| 因果边 | 不连 | 连：`src_task_id` 指向**同一文件**上一步 |
| 每步读什么 | **原文件** | 上一步的产物（如果那一步产出了可续的东西） |
| 格式流规则（§5.12） | **不适用** | 适用 |
| 前端置灰 | 只按可用性 | 可用性 + 格式流 |

**档位就是在回答"产物流不流"**，所以同一条链在两个档位下合法性可以不同：
`转 MP3 → 转 FLAC` 并行档合法（两步都作用于原文件）、串行档被拦（第 2 步会把
一个 MP3 包装成"无损"）。

### 5.3 接触面契约（`backend/cards/contract.py`）

一行一个 op，**这是边界推导的唯一事实源**，也是新增 op 时必须同步改的地方。

| 字段 | 取值 | 含义 |
|---|---|---|
| `touch` | `in` / `out` 的列表 | 碰哪些文件。`in` = `uploads/` 里那个受管副本（或上游派生品），`out` = `outputs/` 下新生成的文件 |
| `mode` | `read` / `write` | **会不会改变 `in` 指向的那个文件**。`write` 只有 4 个：`tags` / `cover` / `remove-cover` / `rename` |
| `produce` | `in_place` / `derived` / `sidecar` / `none` | 产出形态，决定串行档的输入怎么解析 |
| `obs` | `whole` / `meta` / `audio` / `container` | handler **内部**读/改哪一部分。**也参与规则 ①** |
| `needs` | `audio` / `image` / `archive` / `none` / `any` / `upstream` | 吃得动哪种文件，可多个 |
| `gives` | `audio` / `image` / `archive` / `none` | 交出哪种文件 |
| `consumes` | `True`（默认）/ `False` | 依不依赖上一环递过来的东西。只有 `verify`（判定）与 `zip`（收集）是 `False` |
| `gives_formats` | `same` / `param:format` / `image` / `archive` / `none` | 产出什么容器格式，供格式流推导（§5.12） |

**几个最容易写错的地方**（都在代码注释里）：

- **`convert` / `normalize` 的 `mode` 是 `read`**。它们往 `outputs/` 写**新**文件，
  从不碰 `in`。把它们当 `write` 的后果很具体：
  `转 FLAC` 与 `转 WAV` 会被判成互斥，而它们明明是两个纯读者；
  而且"同文件写者租约"会把它们排成串行，白白慢一倍。
  "会不会产出新文件"由 `produce` 表达，**不要用 `mode` 兼职**。
- **`gives='none'` 不等于"链到此为止"**。它的真正含义是"**没动那个音频文件**"，
  所以下一环照旧读原文件就行。`verify` / `probe` / `peaks` / `loudness` 全是 `gives='none'`。
  混淆这两件事会把"响度分析报告之后的全部卡片"错误置灰（§5.14 有这个坑的完整记录）。
- **`sidecar` 与 `none` 必须分开**：`波形 → 打包` 装的是 PNG，而 `校验 → 打包` 装的是原音频。
- **`gives='archive'` 没有任何 op 的 `needs` 能吃** ⇒ "打包 ZIP 是链尾"是
  **类型系统的推论**，不是写死的特例。
- **`needs` 里慎用 `any`**：它连"图片喂给要音频的卡"也一起放行。
  `zip` 原来写 `any`，现已收紧成 `audio + image + none`。
- **`validate_contract()` 必须在导入 `OPS` 之后调用**（`cards/__init__.py` 里做），
  否则漏登记一个 op 时前端只会"这张卡永远可选"，不报错。

### 5.4 三条规则 → 15×15 矩阵

`boundary.compile_rules()` 用上面那张表推出全矩阵（**不许手抄**）。
四种关系：

| 符号 | 常量 | 含义 |
|---|---|---|
| `∥` | `PARALLEL` | 可并行 |
| `⇉` | `ORDERED` | 同文件按链序串行 |
| `⤫` | `EXCLUSIVE` | 写-写互斥（换顺序也救不了） |
| `⇥` | `BROKEN` | 产物接不上（下一步回到原文件） |

三条规则：

**规则 ①　同文件写者互斥。**
两个都 `rewrites_input` 的步骤不能同时在跑。判据用 `obs` 细分：
两个都是 `meta`（`改标签` / `按标签重命名`）→ **`⇉` 有序**（它们动的是同一个标签字典，
用户要的就是"先改标签、再按新标签改名"；一刀切成互斥会把这件明显合理的事标成冲突）；
其余（如 `嵌封面` 的 `container` vs `改标签` 的 `meta`，
或 `嵌封面` vs `删除封面` 都在动 PICTURE 块）→ **`⤫` 互斥**。

**规则 ②　链序 = 因果。**
链上 `i<j` 且 `j` 会碰 `i` 读过的那个文件，且 `i` 是**就地改写** ⇒ `j` 必须等 `i`。
矩阵里的 `⇉` 就是这条。**方向很关键**：`probe → tags` 不需要顺序（probe 什么都没改），
而 `tags → probe` 需要（probe 不能读到写了一半的标签）。

**规则 ③　输入解析 / 产物断链。**
`b` 要碰文件、而 `a` 交出的东西 `b` 接不住 ⇒ `⇥`。两个分支覆盖两种"喂不到"：
`b` 干脆不吃上一环（`consumes=False`），或者 `b` 吃但 `a` 交的类型它接不住。

> **`⇥` 与 `⇉`/`⤫` 是并列的，不是互斥的。** `probe → peaks` 既没有顺序约束（`∥`），
> 又确实喂不到东西（`⇥`）。`compile_rules()` 里为了画成一张表让 `⇥` 优先显示，
> 所以 **`compile_rules()[a][b]` 与 `handoff_broken(a, b)` 要分别调**
> （后者是"要挂 `⇥` 提示吗"的判据，`a == b` 时恒 `False`）。

**当前矩阵实测**（`compile_rules()`，2026-10-07）：

```
225 格 = ∥ 59 + ⇥ 110 + ⇉ 46 + ⤫ 10
```

`ANCHORS` 里写死了 14 组"语义上必须是这样"的格子（`("tags","rename") == ORDERED`、
`("probe","tags") == BROKEN`、`("cover","remove-cover") == EXCLUSIVE` …），
**放在代码里而不是测试文件里**，是为了让"有意变更"必须改这个具名常量。

### 5.5 卡片可用性（`boundary.availability`）

`availability(tail, op) -> (可选?, 原因/提示文案)`。`tail=None` 表示链为空。
**这里不再有"硬禁"**（原来只有 `archive` 一条硬禁，那条规则的前提
"ZIP 必须在链尾"已经被打包语义的改动推翻了）。

判据顺序**本身就是语义**：

1. 链为空：`first_ok(op)`（`needs` 里有 `upstream` 的不能当第一张）—— 这是唯一会**不可选**的情形；
2. 链尾交的是**压缩包** → 可选 + 提示"它不会被递给下一步，后面的步骤仍作用于当前文件"；
3. `compatible(tail, op)` 为真 → 可选，无提示；
4. 大类错配（交出图片、要的是音频）→ 可选 + 强提示"这一步会回到原文件"；
5. 大类相同只是没传下来 → 可选 + 回落提示。

`compatible()` 的四个"用得上"分支：`op` 接得住 `tail` 交出的类型 /
`tail` 产出了可续的东西（`derived`）/ `tail` 是就地改写（`in_place`）/
`op` 本来就不吃上一环（`consumes=False`）。

**`_hands_something_off(tail)`** = `produces(tail) not in NO_HANDOFF`，
`NO_HANDOFF = ("none", "sidecar")`。

### 5.6 建链（`build_chain`）

```
payload: { mode, fileIds, steps:[{cardId?, name?, op, params}], theme?, themeMode? }
  ① validate_chain()        模式合法 / 非空 / ≤ MAX_CHAIN_STEPS(32) / 逐步 availability
  ② 取文件（丢掉 state=deleted）、非空、≤ MAX_BATCH_FILES(500)
  ③ 串行档才 check_format_flow()
  ④ 逐 step：
       params = task_params(op, step.params)      ← 必须走它，见 §6.6
       if op in THEME_OPS: 注入链级 theme / themeMode（步骤里显式给了就听步骤的）
       params["_step_name"] = step.name or op 的 label
       if _is_aggregate(op):  只建一条任务，params.fileIds = 全部文件
       else:                  每个文件建一条，串行档填 src_task_id = 同文件上一步
       prev_of_file = 这一步的 (file_id → task_id)  ← 供下一步连边
  ⑤ **全部建完之后**才 queue_.submit()（顺序稳定，日志读起来跟链一致）
  返回 { chainId, mode, steps, taskIds, total, notes }
```

**为什么要一次建完**（而不是复用 `submit_batch`）：`src_task_id` 要指向
"同一文件的上一步任务"，那需要先知道每一步的任务 id；串行档的因果边必须在建链时
写死（提交时绑定，不做运行时解算）；链的整体状态要有 `chain_id` 才查得到。

`_is_aggregate(op)` 的判据是 `"none" in needs_of(op)`（"我收下所有东西"），
**不写死 op 名** —— 将来加"解压"之类的汇总类 op 时不用改这里。

`ChainError(message, step_idx)` 携带步号，前端据此把链上那一格标红。

> **注释与代码的一个不一致**（见 §13）：`queue.py:324-331` 描述了
> "zip 可能在建链还没走到它自己那一步之前就被 worker 取走"的竞态窗口，
> 但 `build_chain` 是**先全部 INSERT、再统一 submit**，所以那个窗口在当前实现下不存在。
> 屏障本身仍然必要（理由见 §5.11 的第二个 ⚠）。

### 5.7 调度：队列与闸门

`Queue(workers=N)`，N 由 `AE_CONCURRENCY`（默认 2）决定。

`Queue._gate(task)` —— **开工前的闸门，一行数据库状态都不许改**
（defer 的任务必须留在 `pending`）。判据按代价从低到高：

```
①  上游就绪        store.upstream_settled(task)   ← 并行档恒过（没有 src_task_id）
①b 汇总类全局屏障  _aggregate_barrier_unready()   ← §5.11
①c 长任务槽位      _long_slot_busy()               ← §5.10
②  同文件租约      store.acquire_file_leases()     ← §4.5
→ REASON_READY
```

**顺序**：①c 放在最后是因为它比其他两个"软"（上游没到永远等不到，
而槽位只是"再等等就轮到你"），先判确定性的那两个能少做无用的 defer。

闸门结果的处理（`_run_one`）：

| reason | 处理 |
|---|---|
| `REASON_EXPIRED` / `REASON_CYCLE` | **当场结案**为 failed（"我尝试过但做不了"） |
| `REASON_FAILED_UPSTREAM` | 直接上游自己跑坏 → 本步 `failed`（红着，指出断点）；上游也是级联失败 → 本步 `skipped`（灰着，一路灰到底） |
| 其它 | `_requeue()`（§5.9） |

**"直接上游失败"与"更上游失败"必须分开**：第一版只判"上游是不是 failed"，
于是三步链的第 3 步跟着第 2 步一起变红，用户看到"两个都坏了"而实际只坏了一个。
判据是 `CASCADE_PREFIX` 这个**由调度器写下的稳定前缀** ——
跑坏的 handler 不可能产生它。

**长任务计数必须与 `start_task` 在同一个 `with self._lock` 里加**，
而且只在"过了闸、真的要跑"这一刻加。在 `_gate` 里加就错了：
`_gate` 后面还有租约那一步，租约失败会 `_requeue`（不跑），
那样计数只增不减，长任务槽位很快永久占满。

### 5.8 派生产物的交付（`_publish_derived`）

任务成功后，如果它产出了新文件：

```
_publish_derived(task, artifacts):
    ① **全部**产物都注册成 files 行（origin=derived）—— 这样 zip 才装得到、前端才看得到
    ② 只有 produce == "derived" 的那份才**交接**给下游（回填下游的 src_output）
```

`_handoff_kind` 决定交哪一份。**"注册"与"交接"必须分开**：`sidecar`
（波形 PNG / 响度图 / 响度报告）要能被 `zip` 装走，但**绝不能**被当成下一步的音频输入。

这条修的是一个用户实测的 bug：串行档 `响度报告 → 导出响度分析图`，
第 2 步的输入被换成了 `.md`，于是它拿着一个 markdown 去跑 ebur128。
`tests/loudness_chain_e2e_check.py` §5 钉这条。

`_publish_derived` 里那条"这一步没有下游"的 warn 要 `store.chain_has_later_step(task)`
判一下再报 —— 否则每条链的**最后一步**都会报一次（噪音）。

### 5.9 defer：等待与放弃（`_requeue`）

```python
store.release_file_leases(tid)                    # ← 必须还租约（§4.5）
n = self._defer_count.get(tid, 0) + 1
waited = monotonic() - self._defer_since.setdefault(tid, monotonic())
if waited > DEFER_DEADLINE:      # 按**墙钟**判，不是按次数
    finish_task(ok=False, error=f"依赖无法满足（{…}）")
    return
backoff = min(DEFER_BACKOFF * 2 ** min(n-1, 20), DEFER_BACKOFF_MAX)
self._defer_until[tid] = monotonic() + backoff
self._q.put(tid)                                  # 队尾，不是队首
```

- `DEFER_DEADLINE = 30 * 60`（**墙钟 30 分钟**）。退避指数增长、封顶 `DEFER_BACKOFF_MAX = 1.0` 秒
  —— 等待初期灵敏（上游一结束就接上），长时间等待时不再空转
  （30 分钟最多约 1800 次探测，而不是 36000 次）。
- **为什么用墙钟而不是次数**：原来 `DEFER_LIMIT = 200` 是**按次数**的，
  而 200 次轮询只相当于 10~20 秒。用户日志里看到的是第 4 步在 `17:48:25` 失败，
  而它的上游 `17:48:26` 才成功 —— **差一秒**。
- `_gate` 一行库都不改，所以 defer 的任务**留在 `pending`** —— 这正是它该有的样子。

### 5.10 长任务槽位

动机：`normalize`（loudnorm 两遍法）与 `zip` 一跑就是几十秒到十几分钟。
默认 2 个 slot 时两个长任务一起上就把队列占满，期间**短任务全部排队**，
界面看着像卡死。留一个 slot 给短任务，页面就一直"有反应"。

- `LONG_TASK_TYPES = frozenset({"normalize", "zip", "waveform", "loudness", "verify"})`
  —— **按任务类型列出来，不猜时长**：时长要跑起来才知道，而准入必须在开始前决定。
- `Queue(workers=n)` 里推导 `_long_slots = max(1, n-1)`，除非 `AE_LONG_SLOTS` 显式给了值；
  `workers <= 1` 时限制**自动失效**（"留一个"等于不让长任务跑，那是死锁级错误）。
- 不在名单里的（`convert` / `tags` / `cover` / `probe` / `peaks`…）秒级跑完，
  本来就该优先占槽。
- ⚠ 它**不是**"长任务串行"：`_long_slots = 1` 时确实等于串行，
  但那是默认并发 2 下的结果；`AE_CONCURRENCY=4` 时会自动放到 3。

运行时可从 `GET /api/config` 读到实际值：
`maxConcurrency: 2`、`longTaskSlots: 1`、`longTaskTypes: [...]`。

### 5.11 打包窗口（`chain_zip_window`）

一个打包步骤收集的是**它自己那个窗口**里的产物：
**「上一个打包步骤（不含）」→「本次打包步骤（不含）」**。

`store.chain_zip_window()` 与 `store.chain_prev_zip_step()` 是这条规则的**唯一解算**，
`h_zip` 和队列屏障用的是**同一份**（这是"两处判据必须同源"的一个正面例子）。

于是 `转 FLAC → 打包 → 转 MP3 → 打包` 是合法且有用的链：两个 zip、窗口不重叠。

`_aggregate_barrier_unready(task)` 让汇总类任务等窗口内所有步骤落定，判据：

- 窗口内某前置**还在 `pending`/`running`** → 无条件等；
- 前置行**缺失** → 只在 `chain_build_in_progress()`（建链还在插入）时等；
  建链早结束却仍然缺，说明那一步对这批文件没建任务，等也等不到；
  排除自己（`_gate` 在 `start_task` 之前跑，**它自己此刻就是 `pending`**）；
- 前置**刚成功、产物还没交付**（`src_output` 空、`_produces_derived`、且
  `now - ended_at < ATTACH_GRACE`）→ 等一下那次回填。

**为什么不能只看"紧邻的前一步"**（第一版就是这么写的）：窗口可能跨多步。
并行档下 `波形 → 探测 → 打包` 里 zip 只等"探测"，而波形的 PNG 可能**还没生成**
—— 于是包里静默少一个成员，而且不报错（用户拿到一个"看起来成功"的包）。

### 5.12 格式流规则（仅串行档）

代码在 `chain.check_format_flow()` + `format_token` / `step_output_format` /
`_step_bitrate_kbps` / `_step_sample_rate`。

**规则 A：不允许把有损格式转成无损。**

```
源 = 上一步**产出**的容器格式（不是用户导入时的格式）
违规条件：i > 0 且 is_lossy(current) 且 is_lossless(nxt)
```

三个关键点都不显然：

1. **判据是目标格式，不是源格式**。链的**第一项**可能只是"把源文件转成 MP3"，
   而源文件在 `uploads/` 里可以是任何格式 —— 那条链不该被拦；
2. **只判第 1 步之后**（`i > 0`）：`转 FLAC`（源是 MP3）**放行** ——
   那是"我想塞进无损容器"，单步操作，产物不会假装比源更保真。
   `转 MP3 → 转 FLAC` 才拦；
3. **容器沿用型步骤（`gives_formats='same'`：标准化/改标签/嵌封面/按标签重命名）
   不改变格式**，格式流要**穿过**它们继续往后传，否则
   `转 FLAC → 改标签 → 转 WAV` 会被误判。
   `nxt is None`（图片/压缩包产物）时 `current` **保持不变继续传** ——
   不要清空，那会让 `转 MP3 → 导出波形 → 转 FLAC` 的最后一步漏过检查。

**"未知就不猜"只覆盖源文件本身**：

- `未知源 → 转 FLAC` 放行（第 0 步不判）；
- `未知源 → 转 MP3 → 转 FLAC` **拦下**（第 1 步的产出由它自己的 `format` 参数决定，
  所以第 2 步的输入"是 MP3"是确定的，跟源无关）；
- 只有"输入未知**且**产出未知"时才不判 —— **误拦比漏拦更烦人**（用户没法自己绕过）。

> ⚠ **格式流的 `current` 是「文件后缀」，不是 `probe()` 探测出来的容器名。**
> `_source_format()` 取 `rel_path` 的后缀、`step_output_format()` 取卡片的 `format`
> 参数 —— 两者都**不是** `files.info.format`。这个区分很要紧，因为 ffprobe 对
> mp4 系容器（`.mp4` / `.m4a` / **`.m4s`**）报的 `format_name` 第一段是 `"mov"`
> （`ProbeInfo.as_dict()` 还会把它转成大写 `"MOV"`）。
> 于是：
> - **`.m4a` 源一直是被正确识别的**（后缀 `m4a` → `is_lossy` 为真），
>   别名不是"修复了 m4a 的规则"；
> - **`.m4s` 源在规则 B 第 0 步是「未知 ⇒ 不判」**（`classify("m4s")` 是 `None`），
>   前后端一致（前端的 `sourceAudioOfScope()` 同样取后缀），所以**不会误拦**。
>   别指望 `formats._CONTAINER_ALIASES` 的 `mov → m4a` 能改变这件事 ——
>   它的落点只在"拿探测出的容器名去分类"那条路上，而那条路今天没有调用方
>   （详见 `backend/formats.py` 那个常量上的注释）。

**规则 B：有损 → 有损，但码率/采样率反而调高。**

- 与规则 A 同族（都不会恢复信息，只是把文件变大）；
- **第 0 步也要判**：拦的是"把一个已经是 128k 的文件重新编码成 320k"，
  源文件本身有损时第 0 步就已经在浪费体积了。反过来 `128k MP3 → 转 MP3 96k` 放行；
- **只看能确定的数字**：`bitrate` 没写、源文件没 probe 过 → 不判
  （`_step_bitrate_kbps` 的注释：**不拿编解码器默认值去猜**，猜错会误拦）；
- `_kbps_cn()` 一律取整：源码率来自 ffprobe 的容器平均码率，实测一个"128k"的 MP3
  会报 `132.244 kbps`（含容器开销），把小数丢给用户只会让人困惑。

拒绝文案里**必须给出"切成并行"这条出路**，否则用户会以为这个组合本身非法而卡死。

### 5.13 链上的提示（`chain_notes`）

返回 `[{stepIdx, text}]` —— **只提示，不阻断**。`build_chain` 把它放进响应，
前端写进日志并在非串行档额外弹一条。

五个分支（`app.js` 的 `renderChainNotes` 是同一份规则的镜像）：

1. **并行档下的产物断链**：`_parallel_handoff_gap(a, b)` 为真。
   文案直接给"改成串行即可接上"这个出路（前端那个提示是个**直达按钮**）。
2. **`a == "zip"`**：后面还有步骤 —— 压缩包不会被递下去，
   但 `zip` 什么都没改动，后面的步骤照旧作用于当前文件；顺带提醒
   "本次打包只收集到它为止的产物"。
3. **`relation(a, b) == EXCLUSIVE`**：都会改这个文件，最终结果取决于执行顺序。
4. **`extract-cover → cover`**：`cover` 的图只能来自**参数**（且必须在 `uploads/` 下），
   不会用上一步刚提取出来的那张。
5. **就地改写排在"产出新文件"的步骤后面**（`标准化 → 改标签`）：改的是 `outputs/`
   里的**产物**，不是 `uploads/` 里的原文件 —— 语义正确，但用户看不见，不说清会以为原文件被改了。
   ⚠ 这条**单独扫一遍**：上面那个循环只到 `len(ops)-1`，链尾那一步永远不会被当成"下一对"看到。

**`_parallel_handoff_gap` 的判据只留"真的丢了东西"的两种**：

```python
if not contract.consumes_upstream(b): return False
return contract.produces(a) == "derived"      # 唯一的断口
```

- **`a` 产出了新文件（`derived`）而并行档不会把它递下去** —— 这一条最要紧：
  `转 FLAC → 标准化` 的类型完全相容（音频 → 要音频），
  所以任何"看类型对不对"的判据都会放它过去。但并行档下标准化读的是**原文件**；
- 反过来**不再报警**的：只读分析（`produce=none`：探测/校验/响度总览图）——
  它什么都没留，下一步读原文件天经地义；旁路产物（`sidecar`）而下一步并不需要那种文件
  —— `导出波形 PNG → 响度分析报告`：报告要音频，源音频好好的在那儿；
  `打包 ZIP`（`consumes=False`）压根不吃上一环。

> 这条**放宽过一次**（用户实测报的）：第二版把 `produce ∈ (sidecar, none)` 一律当断口，
> 于是"导出波形 PNG → 响度分析报告"也弹一条"会回到原文件"，而那条链每一步都能正常跑完。
> **一条链上连排两张只读卡就刷一排警告，而每条都在说一件不成问题的事 ——
> 这种提示会训练用户忽略所有提示。**

### 5.14 前端镜像的那一份判据

**为什么前端要再算一遍**：后端确实拦得住（400 + `stepIdx`），但那是**提交之后**才知道。
用户能在串行档里把「转 MP3 320 → 转 WAV 24bit」两张卡都加进链、看不出任何异常，
点执行才收到一个错。**边界在后端，体验在前端**：能在点之前置灰的就别让用户点了才报错。

前端侧：

- `availabilityOf(op)` 用 `/api/ops` 发过来的**同一份字段**（`needs`/`gives`/`produce`）复算，
  所以"这里置灰的东西后端一定也拒"；
- `syncCardAvailability()` 把结果写到 `.fcard--blocked` + `el.title` + `el.dataset.blocked='1'`；
- 卡片区点击处理器遇到 `dataset.blocked === '1'` **不加入链但说明原因**
  （"只置灰不解释，用户会以为功能坏了"）；
- `formatFlowBlocked(card)` 是 `check_format_flow` 的镜像（同样的两条规则、
  同样的"只判 i>0"与"第 0 步也判"的分工）；
- `addToChain()` 里**再挡一次** —— 点击那条路已经由 `dataset.blocked` 拦住了，
  但**程序化路径**（快照拖拽、预设还原）不走点击。

`currentTheme()`（`data-theme` / `data-mode`）随链一起交给后端 `{theme, themeMode}`：
响度 SVG 是**服务端**渲染的产物，导出后没有任何 CSS 变量可用，
颜色必须在服务端按当前主题算成实色写进文件。

**三个踩过的坑**（都写在 `app.js` 的注释里）：

1. **判据反过来写**：先判 `anyAccepts`、再拿 `noHandoff` 去救 —— `gives='none'` 谁都不收
   （只有汇总类 `zip` 显式收 `none`），于是"响度分析报告"之后**除打包以外全部被置灰**，
   正常用法被前端堵死。**判据是"下一步有没有音频可用"，不是"这一步产出了什么"。**
2. **`give === 'archive'` 曾经是硬禁**（"终态，链到头了"）。但 `zip` 是 `mode=read` +
   `consumes=False`：它什么都没改动，后面的步骤照旧作用于当前文件；
   而且一条链本来就可以有多个打包步骤。改动要与 `执行链打包与串行交接方案.md` §4.4 同步，
   否则 `browser_chain_probe.py` 的 op×op 对账会红。
3. **前后端各算一套判据时必须对账**。`availabilityOf`（前端）与 `availability()`（后端）
   分叉过一次：后端说"可选"、前端把卡全置灰了，而当时的断言只覆盖后端 + 只覆盖
   "zip 之后全灰"，十几组不一致却全绿。现在 `browser_chain_probe.py` 里是
   **op×op 逐组对账**。

---

## 6. 卡片系统

### 6.1 卡片 / op / 任务 / 步骤的关系

```
op（15 个，后端操作）  ──  specs.OPS ──→  参数规格（key/type/min/max/desc/preview）
                   └──  contract.CONTRACT ──→  接触面（§5.3）
card（71 张内置 + 自定义） = op + 一套 params 快照 + 名字 + 图标 + 分类
step（链上一步）  = { cardId?, name?, op, params }
task（执行单元）  = create_task(type_, file_id, params, …)，type_ 由 spec["task"] 给
```

**`op` 名必须与路由名一致**（`extract-cover` 有连字符），而**任务类型是下划线命名**
（`cover_extract`）—— 卡片里的 `op` 与路由不一致就会 404。

**三个响度 op 共用一个 `loudness` 任务类型**（`specs.OPS` 里的 `"task": "loudness"`），
靠 `params["_op"]` 分流。

**op 与卡片不是一一对应**：`builtin.NO_CARD_OPS = ["loudness"]` ——
"响度总览图"这张卡被删了（老板决定），`op` 保留（前端画 Canvas 用，也是
`loudness-image` / `loudness-report` 的数据源）。所以
"`OPS` 里没有对应卡片的 op" 恰好就是 `["loudness"]`，测试里钉着这个等式。

### 6.2 `backend/cards/` 的六个模块

| 模块 | 职责 |
|---|---|
| `contract.py` | 接触面表 + 取值域 + `validate_contract()`（§5.3） |
| `specs.py` | `OPS`（参数规格）、`PARAM_TYPES`、`PICTURE_TYPES`、`_merge_contract()`、`task_params()`、`THEME_OPS` |
| `builtin.py` | 71 张内置卡片、`CARD_CATS`（7 类）、`CARD_ICONS`（10 个）、`SNAPS_DEFAULT`、`NO_CARD_OPS` |
| `validate.py` | `validate_card` / `validate_step` / `validate_steps` / `render_preview` + 封面的参数渲染 |
| `store.py` | `cards.json` 的读写、快照、预设、`CardsFileError` |
| `boundary.py` | 规则 → 矩阵 → 可用性 + CLI（§5.4/§5.5） |

依赖单向：`specs → builtin → validate → store`，`contract`/`boundary` 旁挂。
`cards/__init__.py` 把对外 API 重导出，**与拆分前的 `backend/cards.py` 完全一致**。

`_merge_contract()` 把 `contract` 里的字段（含 `gives_formats`）合并进 `OPS`，
好让 `/api/ops` 一次把前端需要的字段全发出去。

### 6.3 参数规格与校验

每个参数必须有 `label` + `desc`；枚举参数必须有 `options`；数值参数必须有 `min`/`max`
（测试里钉这三条 —— 少一个，编辑器就会出现一个没法填的空白控件）。

几条宽容规则（都是踩出来的）：

- **空字符串按"未设"处理**：`validate_card` 对 int/float/enum 类型把 `""` 视为未填，
  否则用户清空一个数字框就会收到"必须是数字"；
- `paramApplies(spec, params)` 判 `onlyIf`（如 `compressionLevel` 只在 flac 时有效），
  但渲染参数表单时**先铺默认值再判 `onlyIf`** —— 否则新建卡片时 format 还没值，
  FLAC 压缩等级会被误隐藏；
- `syncParamForm()` 只在 `onlyIf` 判定与 DOM 现状**不一致**时才重渲染，
  避免每敲一个字就重建表单/丢焦点；
- `render_preview` 与 `tasks` **共用同一份封面参数渲染** `_cover_args_for_preview` /
  `tasks._apply_cover_args`，否则预览的命令与实际执行的命令会漂移。

### 6.4 `cards.json` 的持久化

`CARDS_JSON` = 项目根 `cards.json`（gitignored，因为它是用户数据）。

`cards/store.py` 顶部记录了一段事故：**三条会静默清空 `cards.json` 的路径**
（老板问"卡片怎么一直消失"）：

1. **读失败时伪造空配置**。所有写路径都是"读 → 改一项 → 整份写回"，
   所以任何一次读失败都会让下一次写把 `cards: []` **永久落盘**。
   现在的做法：读失败抛 `CardsFileError`，由 `app.py` 的全局处理器变成 500 + 人话，
   **绝不静默当成"你没有卡片"**；
2. **固定临时文件名**被两个写入者共用；
3. **没有上一代备份** —— 现在有 `CARDS_BAK`（`cards.json.bak`）。

其余约定：`next_preset_name()` 取 `预设_NN` 的 **max+1，不复用空洞**
（在**后端**算 —— 前端算就要把那套规则再写一遍，两处漂移会存出重名）；
**卡片名全局唯一**（撞内置/自定义都拒，改卡时撞自己不算重名）。

### 6.5 快照与预设

| | 存什么 | 点它 |
|---|---|---|
| **快照**（抽屉顶部那排） | 一张卡的**名字** | 往链上追加**一步** |
| **预设**（预设卡片） | 一整条链的**参数** | 把**整条链**加载进来 + 切档位 |

**存参数而不是名字**的理由：卡片可改名、可删除；存名字的话用户改一张卡会让所有
引用它的预设**静默变样**。代价是预设不跟着卡片更新 —— 这是正确的取舍：
预设的语义是"我当时存的那条链"。所以 `chainSnapshotSteps()` 对 `params` **深拷贝**。

预设的步骤是**自包含快照**：`op` + `params` + `ico` + 身份（`cardId` + 名字只用来显示）。
`isUnknownStep(s)` = `custom === true` **且** `cardId` 不在 `CARDS` 里 ——
内置卡永不触发。链上给未知卡加「（快照）」后缀 + `title` 说明，
`loadPreset` 用 **`showNotice` 而不是 `toast`**（"我有两张卡没有"是需要看清楚的事，
4.2 秒的 toast 等于把它藏起来），带「查看详情」按钮。

预设上限 `PRESET_MAX`，图标规则 `ICON_RULES = {max:5, head:3, tail:2}`
（**别散落魔数**：相邻重复合并 → ≤5 全显示 → >5 留头 3 尾 2 + `…` 算一格）。

### 6.6 服务端注入的 `_` 前缀键

**客户端指定的 `_` 开头键一律无效**，它们在 `specs.task_params()` 里被 `pop` 掉再重新写入：

| 键 | 谁写 | 为什么 |
|---|---|---|
| `_op` | `task_params()` | 响度三兄弟共用一个任务类型，靠它分流。**否则谁都能让 `loudness` 这张卡去写报告文件** |
| `_theme` / `_themeMode` | `chain.build_chain()`（仅 `THEME_OPS`） | 链级主题落到会用到它的步骤上 |
| `_step_name` | `chain.build_chain()` | 打包的 ZIP 文件名按"对应功能卡片名称"拼，而任务行里只有类型没有名字 |

`build_chain` **必须走 `task_params()`**，不能直接拷贝 `step.params` ——
少了 `_op` 时 `loudness-report` / `loudness-image` 会退化成"只算一遍并写缓存"的
`loudness`：任务仍然 `success`、进度 100，**却什么都不产出**（用户实测报的"报告不生成"）。
预设校验会剔除 `_` 开头的键，所以客户端也覆盖不了。

---

## 7. 音频与测量子系统

### 7.1 `audio.py`

| 函数 | 关键点 |
|---|---|
| `probe(path)` | `ffprobe -show_entries …-of json`，超时 30 s。`format` 取 `format_name` 的**第一段**；`size` 缺失回落 `st_size`；`sample_rate`/`channels`/`bits` 走 `_int()`，失败给 0。⚠ `as_dict()` 把它 **`.upper()`**，所以对外的 `info.format` 是 `"FLAC"` / `"MOV"` 这种**大写**；而 mp4 系容器（`.mp4`/`.m4a`/`.m4s`）的第一段一律是 `"mov"`（`mov,mp4,m4a,3gp,3g2,mj2`）—— 这两条一起意味着**任何"拿 `info.format` 去比小写"的代码都是错的**（前端 `PLAYER_MIME` 取 key 时自己 `toLowerCase()` 兜住了） |
| `hasCover` 的判法 | `any(video 流 with attached_pic==1) or any(video 流)` —— 第二个析取项**包含**第一个，所以实际判据等于"**存在任一 video 流**" |
| `read_tags()` | FLAC 优先 `metaflac --export-tags-to=-` → 否则 mutagen(easy) → 最后 `ffprobe format_tags`。键名经 `CANON_KEYS` 归一 |
| `chapters(path)` | `ffprobe -show_chapters`；无名回落 `chart_layout.MARKER_DEFAULT_NAME`；无章节返回 `[]` |
| `peaks()` | `ffmpeg -ac 1 -ar 8000 -f u8 -` 解码，分桶取**绝对峰值**（u8 静音中心是 128）。必须用 `run_bytes`（文本解码会破坏采样值） |
| `loudness_timeline()` | §7.3 |
| `render_loudness_markdown()` | 7 个小节：标题 / 指标说明 / 汇总（15 列表格）/ 自洽性校验 / 响度分区 / 逐曲明细 / 逐帧明细 |

Markdown 报告的设计取舍：**只输出结论不输出过程**。
逐帧 10Hz 一首 3 分钟的歌约 1800 行，直接贴进 md 会把结论淹掉，
所以默认只给汇总 + 分区 + 极值点，要看全量需显式开 `frame_table`。
`_extreme_points()` **按 1 秒分桶取极值再排序** —— 直接排序会返回同一秒里的 5 个点，
对"定位哪里爆了"没用。`_md_cell()` 转义竖线、压掉换行（否则表格断行）。

### 7.2 缓存

| 数据 | 路径 | 命中条件 |
|---|---|---|
| 波形峰值 | `.cache/peaks-<file_key>.json` | 存在 + `force=False` + `version == CACHE_VERSION` |
| 响度时间线 | `.cache/loudness-<file_key>.json` | 同上，命中时 `cached=True` |
| 封面提取中间件 | `.cache/loud-cover-<task.id>.jpg` | 用完 `finally unlink` |
| ebur / peak 元数据 | `.cache/ebur-meta-<task.id>.txt`、`peak-meta-…` | 读完即删 |

`file_key(path) = sha256(f"{path.resolve()}|{st_size}|{int(st_mtime)}")[:32]` ——
**只看路径 + 大小 + mtime**，内容没变就命中。

`CACHE_VERSION = 5` 的作用写在代码里：`file_key()` 只看路径+大小+mtime，
**代码改了它不知道**，所以"改动响度数据的含义/单位时必须 +1"。
（v2 的历史：修 truePeak/samplePeak 单位、LRA 伪值 20、新增 dra 与 samplePeakMax、
`peak=true → sample+true`。）

**响度缓存与主题无关**：`loudness-image` 每次都重写 SVG
（"换主题再跑一次链必须出新的颜色"），缓存的只是时间线。

### 7.3 响度三兄弟

三个 op 共用一个 handler `h_loudness`，靠服务端注入的 `_op` 分流：

| op | 卡 | `produce` / `gives` | 产物 |
|---|---|---|---|
| `loudness` | **无卡**（`NO_CARD_OPS`） | `none` / `none` | 无侧车；只写 `.cache/loudness-*.json`，`summary` 随任务结果回前端画 Canvas |
| `loudness-image` | 导出响度分析图 | `sidecar` / `image` | **SVG** 整页 → `outputs/**/loudness/<stem>.loudness.svg` |
| `loudness-report` | 响度分析报告 | `sidecar` / `none` | **Markdown** → `outputs/**/loudness/<stem>.loudness.md` |

`h_loudness` 三段共用：① `audio.loudness_timeline(p, force, tag=task.id)`；
② 分流**之前**把测量值落库 `_store_measurements(file_id, {loudness, truePeak,
loudnessRange, samplePeak, dra, drp})`（"三个 op 共用这一段，写一次就够"）；
③ `out = {key, duration, frames, hz, cached, summary}` ——**不回 M/S/I 数组**。

`loudness_timeline()` 的 ffmpeg 配方（单趟拿两路元数据）：

```
ffmpeg -hide_banner -loglevel error -nostats
  -i <绝对路径>
  -map 0:a:0
  -af "ebur128=peak=sample+true:framelog=verbose:metadata=true,
       ametadata=mode=print:file=ebur-meta-<tag>.txt,
       astats=metadata=1:reset=1:measure_perchannel=none:measure_overall=Peak_level,
       ametadata=mode=print:file=peak-meta-<tag>.txt"
  -f null -                                        # cwd = config.CACHE
```

**五个都不显然的地方**：

1. **`cwd = config.CACHE` 且 `file=` 只给文件名**：filter 里不能出现 Windows 绝对路径
   —— 反斜杠会被 ffmpeg filter 解析器当转义符吃掉（`C:\Users\...` → `UsersRedmi...`），
   报 "No option name near ..."；
2. **`-i` 必须给绝对路径**：因为 cwd 已经变成 `.cache`，相对路径会被解析到 `.cache/` 下；
3. **必须 `-map 0:a:0`**：本机不少 flac 自带 mjpeg 封面流，不显式选音频会多解一路图、甚至失败；
4. **`peak=sample+true` 不能写成 `peak=true`**：实测 `peak=true` **根本不输出**
   `lavfi.r128.sample_peak`（只有 `true_peak`），`peak=sample` 则反过来。
   这也是"不用另跑一趟 astats"（做汇总指标）的前提；
5. **单趟里两个 `ametadata`**：第一个挂在 `ebur128` 后（逐帧 10Hz 响度 + 累计峰值），
   第二个挂在 `astats` 后（**逐帧** `Overall.Peak_level`）。
   原因：ebur128 的 `true_peak`/`sample_peak` 是**到当前为止的最大值**（实测单调不减），
   拿它定位"哪一刻爆音"会把第一次越线之后的整首标红。
   `measure_perchannel=none` 把输出从 27MB 压到 0.8MB。

**中间文件名必须带 `tag`（调用方传 `task.id`）**：队列是 2 并发，两个响度任务共用
一个固定名会一个 `unlink` 而另一个正开着它，Windows 抛
`PermissionError: [WinError 32]`，症状是"批量分析时随机有一个文件失败"，看着像磁盘问题。

**解析（`parse_ebur_metadata`）**：

- 逐行状态机；同帧缺 `M` 或 `S` 就**整帧丢弃**（"宁可少一点，也不要错位"）；
- `true_peak` / `sample_peak` 是**线性幅度**，不是 dB。实测（997Hz 正弦）：
  −20dB → 0.009、−10dB → 0.028、0dB → 0.088，严格成 `10^(-dB/20)`。
  所以用 `20*log10()` 自己转，`_LINEAR_FLOOR = 1e-6`，0/负值钳到 −120；
- **`S` 保留 2 位小数是必须的**：DRP 要拿它做中心差分（除 0.2s），
  1 位小数会让 0.1 LU 台阶变成 0.5 LU/s 一格，`|dS/dt|` 只有 13 个不同取值；
- `summary` 8 项：`integrated`（末值）/ `lra`（`LRAhigh[-1] − LRAlow[-1]`，
  **不从 LRA 序列取值** —— 常量正弦的 LRA 序列里夹着成对的伪值 `20.000`）/
  `dra`（短时序列 `S > -120` 的 `P95 − P10`，**不带门限**，与 LRA 口径不同、不可互换）/
  `plr` / `momentaryMax` / `shortTermMax` / `truePeakMax` / `samplePeakMax`；
  峰值 max **必须在线性序列上做**（先转 dB 再 max 会在已四舍五入的值上排名）；
- `SILENCE_FLOOR = -120.0` 的静音底要剔除：EBU R128 规定首帧门限还没积累样本时
  M/S 就是 −120.691，是**产物**而不是真的这么安静。⚠ **不能只剔 M**：
  M 是 400ms 窗（一般只有第 0 帧在底上），S 是 **3 秒窗**（10Hz 下前 ~30 帧都在底上）。
  做法是取 `cut = max(ready_at(M), ready_at(S))`，对 `t` 与所有序列**统一切前 cut 帧**
  （早先"各剔各的"会让 M 比 S 长、与 t 错位，图上整条曲线平移）；
- `parse_peak_metadata()` 的坑：`pts_time` **不在行首**（那行是
  `frame:0    pts:0       pts_time:0`），必须用 `in` 判断；
  它的时间基与 ebur128 的 `t` **不同**，别互相索引（实测 `t` 1567 帧、`peakT/peak` 1597 帧）。

DRP 的结果也追加进 `summary`（§7.7），加上前面的 8 项与 `clipSeconds`/`clipCount`/`clips`，
最终 `summary` 一共 18 个键。

**削波（爆音）时段** `_merge_runs(peakT, peak, lambda v: v >= 0.0)`：
用**真实时间戳**（时间线不从 0 开始），命中区间合并，间隔 ≤ `CLIP_MERGE_GAP = 0.1`
的相邻段再并一次。取 0.1 的实测依据：逐帧峰值 10fps（一帧 0.1s）；0 / 0.05 / 0.1
在两个素材上**逐段完全相同**，而 0.25 起最长段爆到 32s、0.5 把 99 段糊成 9 段、最长 86.2s。

### 7.4 响度图纵轴（`chart_axis.py`）

常量：`LUFS_TOP = +0.3`、`LUFS_BOTTOM = -50.0`、`KNEE = -30.0`、`KNEE_SHARE = 0.70`、
`LOG_SHIFT = -LUFS_BOTTOM + 1.0 = 51.0`、`LABELLED = (0,-3,-5,-7,-10,-14,-16,-23,-30)`（9 个全出文字）、
`UNLABELLED = ()`、`RED_ABOVE = -3.0`、`PLOT_H_REF = 482.13`。

映射（`frac` = 从顶边向下的高度比例）：

```
v ≥ KNEE:  frac(v) = (0.3 - v) / 30.3 * 0.70
v <  KNEE: frac(v) = 0.70 + 0.30 * (log10(21) - log10(v + 51)) / log10(21)
```

即**上段线性、下段对数**的拐点轴（"F 轴"，`tests/dsh-wheel/axis_options.py`
里 `SELECTED_KEY = "knee"` 是六个候选方案里的定稿）。

**为什么是这个形状**：旧的**纯 log 轴**把 77% 的绘图区花在 `-30..-50`，
而音乐真正所在的 `0..-16` 被压进 **9.5%** —— 响的那头成了一条缝，
且**没有任何两个标签能坐得下**（最小已标间距 12.59 pt，而标签 14 pt；
要坐满 9 个需要 1300 pt 的绘图区）。F 轴用"上段线性"换来空间，
还有第二个好处：拐点以上**等 LUFS 步长 = 等像素**，响的一头读起来是均匀的。

由公式推出的关键数值（`check()` 用断言钉住其中几个）：

| 量 | 值 |
|---|---|
| `frac(0)` | 0.006931 → `0` 线距顶边 **3.34 pt** |
| 红区高度（`0..-3`） | `frac(-3) × 482.13` = **36.76 pt**（占 7.6%，"一顶薄帽"） |
| `0..-16`（音乐所在） | **36.96 %** |
| `-30..-50` | **30.00 %** |
| 最小已标间距 | **22.28 pt** |
| 9 个标签所需绘图区高度 | ≈ **303 pt**（页面给 482.13） |
| `0` 标签的溢出 | **3.66 pt**（`top_clearance()`） |

`0` 标签溢出**不构成裁切风险**：画布内容框上方有 60 pt 空白
（`chart_layout.CONTENT_TOP = 60.0`，`check` 断言 `CONTENT_TOP >= need`）；
唯一的约束是**导出时不能硬裁到内容框**。

### 7.5 响度图版面（`chart_layout.py`）

**每一个坐标都是从工作区根目录的 `大致布局.ai`（手绘草图）实测的**，
可用 `tests/dsh-wheel/layout_spec.py --from-ai` 重新推导并对账。
几何是实测的；"每个框是什么角色"是**推断**（标 `inferred_role=True`）。

```
画布      1331.81 × 728.504 pt   （左下原点、y 向下）
绘图区    900 × 482.13  @ (100, 60)
纵轴标尺  80 × 482.13   @ (20, 60)
时间带    900 × 140     @ (100, 560)
右栏三卡  260 宽：元数据 260×304.5 @ (1040,60)
                   响度   260×177.63 @ (1040,364.5)
                   动态   260×140   @ (1040,560)
内容框    1280 × 640（左 20 / 右 1300 / 上 60 / 下 700）
```

横轴加宽是两次需求的叠加：`PLOT_EXTRA_W = 240`（只拉横轴）+ `CARD_EXTRA_W = 60`
（只拉右栏三卡），**竖向一个数都没动**。

`check()` 里的不变式：标尺与绘图区同顶同底、右边缘紧贴；右栏距绘图区右缘 40；
右栏三卡同宽；**两张上卡精确铺满绘图区高度**（304.5 + 177.63 = 482.13）；
时间带与绘图区等宽左对齐、与动态卡同行同高；绘图区→时间带间距 **17.87 pt**；
网格 6×6 且均分。

时间带是**动态框**：`time_band_rows = TIME_BASE_ROWS + marker_rows + pattern_rows`，
`time_band_height = max(140, rows × 23.996)`；`canvas_height(grow="canvas")`（默认）
让画布变高、绘图区锁 482.13。基础刻度**五个全在同一行**
（0 / 1/4 / 1/2 / 3/4 / 1，"这里没有每 N 秒一格的步长"）。

字号令牌：`FONT_BODY = 14.0`、`FONT_SMALL = 10.0`（唯一的小字 `TrackNumber`）、
`LINE_STEP = 23.996`（中文字段实测行距）、`LINE_STEP_ASSUMED = 24.0`（**借用**，
草图没给两张指标卡的内部行距）。**颜色不在这个模块** —— 全部来自 `theme.css`。

### 7.6 SVG 渲染（`loudness_svg.py`）

输出：一整页 SVG 文本，根元素 `viewBox="0 0 1331.81 H"`（`H` 随标记/模式行数长高），
`width`/`height` 由参数给显示尺寸 —— **`viewBox` 永远是设计单位 ⇒ 无损缩放，
给不给都不重排版**。

图内**图层按 id 分组，顺序即绘制顺序**：

```
<style>            全部样式集中一处；颜色全是实色字面量，**没有一处 var()**，也没有 :root
<rect>             整张画布底色
<g id="filename">  页面左上角文件名
<g id="axis-rail"> 标尺列 + 9 个刻度文字（0 与 −3 加红加粗）+ 底部 "LUFS"
<g id="plot">      绘图区
<g id="time-band"> 爆音红条 / 基础刻度 / 标记行 / PT 行 / 刻度文字
<g id="cards">     元数据卡 + 响度卡 + 动态卡
```

`<g id="plot">` 内部顺序**是刻意的**：① 底色 → ② 红区带 → ③ 蓝体包络 + 红冠 →
④ **网格线** → ⑤ 总响度虚线 → ⑥ −23 参考线 → ⑦ 边线。
两条踩坑注释：底色与边线**必须分成两个元素**（早先合成一个矩形放在收尾，
结果把包络整个盖住，图上只剩坐标轴）；网格线必须画在**填充之后**
（否则包络盖住刻度线），但又要在总响度虚线/参考线之前。

**双色包络**：

1. `_columns()` 按列取**段内最大值**（平均会把瞬时峰值削平），
   丢弃 `<= LUFS_BOTTOM - 69`（= −119）的静音底，`cols = int(pl.w)` = **900 列**（1 pt 1 列）；
2. **蓝体**：逐列点，`v > RED_ABOVE` 的列**钳到 −3 线**，路径收尾到绘图区底边两侧闭合；
3. **红冠**：把连续 `v > RED_ABOVE` 的列切成 run，每个 run 在**曲线与 −3 线之间**闭合
   （不是从 −3 一路填到底）；
4. `AREA_TINT = 0.55`：主题里只有**一支**警戒色（`--error-solid`），
   旧硬编码调色板那"软红画面 / 强红画标记"的层级改由透明度承担。
   红区带与红冠**都**乘 0.55，只有爆音段与红刻度线用不透明的 red；
5. 总响度虚线**必须先判 `LUFS_BOTTOM <= loud <= LUFS_TOP` 才画** ——
   因为 `frac()` 会把越界值夹到边界上，一个 −70 LUFS 的文件会把线画在底边上（假的）；
   越界就不画（卡片里仍有数字）。

**主题色怎么"烘成实色"**：

- `ROLE_TOKENS` 声明"图的哪个角色用主题的哪个令牌"，配对契约照 `theme.css` 顶部三行；
- `chart_palette(theme, mode)`：`T.normalize()` → 不在 `T.available()` 就用
  `FALLBACK_PALETTE`（**全仓库唯一一份抄下来的色值，只在解析失败时用**）→
  否则逐角色 `T.solid(...)` 把带 alpha 的令牌按底色压平成 `#RRGGBB`
  （一部分 SVG 查看器会把 `rgba()` 当无效色丢掉）；
- `redText` 是**派生**的：`--error-solid` 的契约是"白字压在上面可用"（注定偏暗），
  当它反过来当落在画布上的**文字色**时深色模式只有 ~2.2 对比度，
  所以按 WCAG AA（`theme.AA_RATIO = 4.5`）用 `ensure_contrast()` **只调亮度不动色相**
  推到可读；
- `_style(pal)` 把实色写死进 `<style>`。**为什么不能再用 `:root{--text:…}`**：
  内联进页面时 `:root` 指向 HTML 的 `<html>`，图的样式会反过来污染页面变量。

**字体栈**先中文（`Microsoft YaHei` → `PingFang SC` → `Noto Sans CJK SC` → …）。
**服务端没有字体引擎**，所以"标记字会不会重叠 → 要不要再起一行"只能用**估计宽度**：
`_text_w()` 全角 1.0 em、半角 0.55 em（**故意高估**，宁可多起一行）；
值那一列再乘 `VALUE_W_SLACK = 1.13`。

**爆音段只消费 `audio.py` 算好的 `summary["clips"]`，绝不自己拿 `truePeak` 重算**。
用户报的 bug 是"一有就开始标，一标就从头标到尾"，两个根因：
① 数据源用了 `truePeak`（ebur128 的**累计最大值**，单调不减 ⇒ 第一次越线之后永远越线）；
② 就算换成逐帧数据，`_fill_runs` 用的是严格 `>` 而 `Peak_level` 顶到的是**恰好 0.000 dBFS**
⇒ 一段都画不出来（静默失灵更难发现）。

细节：起始==结束的单帧爆音**必须留着**（早先 `if b > a` 把 Stellar 的 99 段里 10 段单帧丢了）；
每段两条边（穿过绘图区的淡引导线 + 从底边扎进时间带的短刻度）；
**引导线在密集时整批退场**（平均间距 < 12 pt）；
爆音**不占文字行**。

**时间带锚法**：标记名 BOLD 同字号、时间小一号更浅；标记框居中后夹进绘图区；
`PT_n` **只写在每一次出现的起点**，标签**左对齐到起点刻度线的右侧**，终点只画线；
所有竖线**从绘图区顶边画起**；**基础刻度的文字画在最末尾**
（否则每条竖线从 `00m00s` 上压过去，像被划掉的字）。

**卡片**：标签左对齐、值右对齐同一列；`_fit_pair()` 五级字号级联，
都放不下才截**值**（标签是字段名，截了就认不出是哪一项）。
元数据卡：封面（`data:` URI）或主题色占位；4 行拉丁字段的值右对齐到**封面左边**、
6 行中文字段在封面**下面**、值可吃到整卡宽度。

### 7.7 动态模式检测（`drp.py`）

**一句话**：在整曲的**短时响度曲线 S** 上，找出"响度水平 / 动态范围 / 归一化曲线形状"
三项都相近、且**至少出现 2 次**的**段落尺度**片段，作为"动态模式"（方案 C）。

三个判据同时满足才算同一个模式（距离取三项**归一化超限度**的最大值）：

```
d(A,B) = max( |Δlevel|/TOL_LEVEL, |Δdr|/TOL_DR, shape(A,B)/TOL_SHAPE ) ≤ 1.0
TOL_LEVEL = 1.0 LU    TOL_DR = 2.0 LU    TOL_SHAPE = 0.60    SHAPE_N = 100
```

- **取最大值而不是加权和**：任何一项超了都不算同一个模式；加权和会让
  "电平差很多但形状很像"（正是 CQ 那对假阳性）靠形状把总分拉回容差内；
- **形状必须先 z 归一化**（`(v - mean) / pstdev`）：归一化之后这个向量只含形状信息，
  电平高低由 `TOL_LEVEL` 单独管。

**"宁可漏不可错"的依据**（模块 docstring）：手标 20 段实测结论是
**光靠响度统计量分不开音乐结构**、**曲线形状也分不开**、**频谱质心也分不开**。
一个决定性的假阳性：

| 对 | 形状 | Δ电平 | ΔDR |
|---|---|---|---|
| 真：`PT_4@103` ↔ `PT_4@228` | 0.237 | **0.47** | 1.22 |
| 假：`PT_4@228` ↔ `PT_1@260` | 0.260 | **17.77** | 10.40 |

假的那对**形状比真的还近** ⇒"只比形状"必然误报，"形状 + **绝对电平**"才能挡掉。
被挡掉的**真重复怎么处理**：就当**没有模式** —— 这是明确选的取舍，
`patterns()` 也写明"一个安静的文件（或重复都处在边缘的文件）合法地返回 `[]`"。

关键常量：`WINDOW_SEC = 16.0`（段落尺度：手标 20 段无一短于 12.4 s）/
`STEP_SEC = 2.0` / `MIN_OCCURRENCES = 2` / `MAX_SEG_SEC = 48.0` /
`COMPOUND_GAP_SEC = 4.0` / `SLOPE_SPAN_SEC = 2.0` / `S_HEAD_SEC = 3.0` /
`MERGE_GAP_SEC = 3.0` / `MIN_DR = 1.0` / `SILENCE_FLOOR = -120.0`。

算法五步，其中**三处是"留档"**（记录"为什么不是别的做法"）：

1. **分段** `_segments_from_windows()`：就是每 `step` 帧一个、长 `win` 帧的滑窗，
   **没有延伸**。丢开头 3s（S 窗还没填满）与局部平坦的窗。
   **延伸为什么取消**（三种判据实测全部有害）：`|Δ窗均值| ≤ 0.75 LU` 永不成立
   （`PT_4` 斜率 0.8 LU/s，16 s 窗每走 2 s 均值漂 ~1.6 LU）；
   `ΔDR + 形状` 在**相邻**窗之间完全不可用（z 归一化形状对相位极敏感，
   只在起点对齐时才有意义，实测 CQ 111 段 **0 段延伸**）；
   只用 `ΔDR` **会**延伸但**把身份弄坏了**（tau 原本正确的一对变成错的，
   CQ 把最紧的一对弄丢）。结论：**单位就是窗本身**。
2. **匹配** `_complete_linkage()`：凝聚式**完全链接**聚类，两簇可合并当且仅当
   **每一对成员**都满足 `d <= 1.0`。不能用单链接（实测会把 16 个窗链成一个横跨 6 LU
   的"模式"）；也不能一趟贪心（会让两个 id 都在描述同一段时间）。
3. **冲突消解** `_resolve_conflicts()`：**唯一保证"每次出现互不重叠"的地方**。
   分段保证**段**不重叠，但相邻位置建出的两个段可能落进不同簇 ——
   实测约 **19%** 的文件长度被重叠覆盖；这一趟之后重叠为 0。
   规则：按出现长度**从长到短**贪心占用帧，低于 `min_idx` 帧的碎片直接丢。
4. **缝合** `_merge_adjacent(gap=3.0)`：同一个模式、间隔 < 3 s **且空隙没被别的模式占用**
   的相邻出现并成一段（滑窗滑出来的一段连续通段被端点或冲突消解切开就变成两段，
   图上像两个模式其实是同一段）。
5. **合并复合模式** `_merge_compound()`：把"**每一次出现都紧挨着**"的两个模式并成一个。
   证据门槛**必须是确定性的，否则宁可不动**：出现次数相等、接缝宽度处处一致
   （与中位接缝之差 ≤ 一个步长）、接缝 ≤ `gap`、合并后段长 ≤ `MAX_SEG_SEC`、
   且不破坏"出现互不重叠"。

对外 API：`patterns(S, t, hz, *, window_sec, step_sec, min_occurrences) -> list[dict]`
（每项 `id` / `occurrences` / `level` / `slope` / `dr` / `dur` / `spread` / `compound?`）
与 `extremes(pats) -> (PMAX, PMIN)`。

**接线方式：通过 `summary`，没有独立的 op / 端点。**
`loudness_timeline()` 末尾调 `drp.patterns(...)`，结果写进 `summary` 的 7 个键
（`drp` / `pmax` / `pmin` / `drpCount` / `drpOccurrenceCount` / `drpOccurrences` / `drpError`），
并**进缓存**所以只算一次。要跑 DRP 只能跑响度分析（三个 op 中任意一个）。

**失败不静默**：`summary["drpError"]` 留原因。第一版是 `except Exception: pats = []`，
而 `out["hz"]` 当时不存在，KeyError 被吞掉 —— 表现为"这首歌没有动态模式"，
看着像算法结论，其实是接线错误。

### 7.8 波形 PNG（`h_waveform`）

`showwavespic=s=WxH:colors=0xRRGGBB[:split_channels=1][:scale=…]`
+ `_WAVE_SNAP`（`lutrgb` 把 >250 的通道拉回 255，修 ffmpeg YUV 往返把纯白变成 254 的问题）。

参数与钳制：`width` 默认 1920、钳 `[200, 8000]`；`height` 默认 400、钳 `[60, 4096]`
（上限 4096 是为了让 4K 与手机壁纸不被静默夹到 2000）；
`scale ∈ {lin, log, sqrt, cbrt}`（白名单外直接 `failed`，不静默忽略）；
`background ∈ {transparent, black, white}`；`color` 走 `_hex_color`
（正则 `[0-9a-f]{6}`，非法回落 `0xFFFFFF`）；`splitChannels`。

两套配方：透明底 ⇒ 单输入 + `-frames:v 1 -pix_fmt rgba`；
实底 ⇒ 再 `-f lavfi -i color=c=…:s=WxH` + `overlay=format=auto:shortest=1`
（波形压在纯色画布**之上**）、`-pix_fmt rgb24`。
产物 `outputs/**/waveforms/<stem>.png`，失败时 `out.unlink(missing_ok=True)`。

---

## 8. HTTP API

**共 57 个端点**，分布在 `backend/routers/` 的 5 个模块里：

| 模块 | 端点数 | 域 |
|---|---|---|
| `system.py` | 6 | 健康检查、配置、日志、SSE |
| `catalog.py` | 16 | op 目录、卡片 CRUD、快照、预设 |
| `files.py` | 13 | 上传、文件列表、标签、封面、峰值、下载、批量删除 |
| `task_queue.py` | 5 | 任务队列查询、重试、取消 |
| `ops.py` | 17 | 15 个 op 的提交入口 + `/api/ops/chain` + `/api/chains/{id}` |

唯一的 API 文档在 `docs_url="/api/docs"`（FastAPI 自带的 OpenAPI UI，
挂在 `/api/` 下所以不受静态挂载的 catch-all 影响）。

### 8.1 `system.py`

| 方法 | 路径 | 作用 |
|---|---|---|
| GET | `/api/health` | 工具链探针 + 服务器信息 + 队列统计 + `browser_active`（前端心跳 5 s 内） |
| GET | `/api/heartbeat` | 刷新"浏览器最后可见时间"（前端每 2 s 打一次） |
| GET | `/api/config` | 服务器配置（host/port/concurrency/longTaskSlots…） |
| GET | `/api/logs?since=&limit=` | **增量**拉取日志；`limit` 1–2000，默认 500 |
| DELETE | `/api/logs` | 清空日志环形缓冲 |
| GET | `/api/events?interval=` | **SSE**：周期推送队列 + 文件状态快照；`interval` 0.2–5.0 s；**无变化不重复推**（推 `: keepalive`） |

SSE 端点自带 `Cache-Control: no-cache` + `X-Accel-Buffering: no`。

### 8.2 `catalog.py`

| 方法 | 路径 | 作用 |
|---|---|---|
| GET | `/api/ops` | 操作目录：参数、取值、默认值、说明、等价命令模板 + `paramTypes`/`icons`/`categories`；**含 `needs`/`gives`/`produce`/`gives_formats`** |
| GET | `/api/cards` | 内置 + 自定义卡片 + 分类表 + 快照 + `builtinCount` |
| GET / PUT / DELETE | `/api/snapshots` | 读 / 保存（并写日志）/ 恢复默认 |
| GET | `/api/presets` | 所有预设 + **`nextName`**（后端算）+ `max` |
| POST | `/api/presets` | 新建；**非法步骤在这里就拒掉（400 + 逐条原因）** |
| PUT / DELETE | `/api/presets/{pid}` | 改（白名单 patch）/ 删 |
| DELETE | `/api/presets` | 清空（测试用，**不动自定义卡片**） |
| GET | `/api/cards/ops/{op}/preview?params=` | 按参数渲染等价命令（编辑器实时预览） |
| POST / PUT / DELETE | `/api/cards[/{cid}]` | 新建 / 改（**内置卡不允许原地改**，用"另存为"）/ 删 |
| GET | `/api/cards/export` | 导出全部自定义卡片（`{version: 1, cards: […]}`） |
| POST | `/api/cards/import` | 导入；同名/同 id **直接跳过不覆盖**，返回 `added`/`skipped` |

### 8.3 `files.py`

| 方法 | 路径 | 作用 |
|---|---|---|
| POST | `/api/upload` | 多文件上传（`files` + `paths` JSON 还原目录结构） |
| GET | `/api/files` / `/api/files/{fid}` | 列表（含 `info` 与任务摘要）/ 详情 |
| POST | `/api/files/{fid}/probe` | 提交探测任务 |
| GET | `/api/files/{fid}/peaks?buckets=` | 峰值数据（默认 1000 点） |
| GET / PUT | `/api/files/{fid}/tags` | 读（+ `fields` 供前端渲染表单）/ 写（**不重新编码**） |
| DELETE | `/api/files/{fid}?withDisk=` | 删单个（默认软删除） |
| POST | `/api/files/delete` | 批量删除，返回 `count`/`freedBytes`/`failed` |
| GET | `/api/files/{fid}/download` | 下载/试听源（`<audio id="player">` 直接指它） |
| GET | `/api/outputs/{rel:path}?inline=` | 读产物（`rel` 相对 `outputs/`） |
| POST | `/api/files/{fid}/reveal?open=` | 在文件管理器里**选中**工作副本（`open=false` 只解析路径） |
| GET | `/api/files/{fid}/cover` | 内嵌封面图 |

> `withDisk` 与 `purge` 是两件事：软删除的行仍占着 `rel_path` 的 `UNIQUE`，
> 所以"删了再导入同名文件"要 `purge=true`，否则撞 `UNIQUE constraint failed: files.rel_path`。

### 8.4 `task_queue.py`

| 方法 | 路径 | 作用 |
|---|---|---|
| GET | `/api/tasks` / `/api/tasks/queue` / `/api/tasks/{tid}` | 列表 / 队列快照（`running`+`pending`+`recent`+`counts`）/ 详情 |
| POST | `/api/tasks/{tid}/cancel` | 取消 |
| POST | `/api/tasks/{tid}/retry` | 重试 |

`queue.retry_task()` 允许 `failed` / `cancelled` / **`skipped`** 三种 ——
`skipped` 虽然"没被尝试过"，但对用户来说和失败一样需要一次重来的机会，
而且**只有它能重试时整条链才救得回来**（上游重试成功后，下游那些 skipped 才有意义）。

### 8.5 `ops.py`

| 方法 | 路径 |
|---|---|
| POST | `/api/ops/probe` / `peaks` / `convert` / `tags` / `cover` / `normalize` / `rename` |
| POST | `/api/ops/waveform` / `verify` / `zip` / `extract-cover` / `remove-cover` |
| POST | `/api/ops/loudness` / `loudness-image` / `loudness-report` |
| POST | `/api/ops/chain` |
| GET | `/api/chains/{chain_id}` |

两条 `ops.py` 里的注释值得记住：

- `op` 字段必须与**路由名**一致（如 `extract-cover` 有连字符），而任务类型是**下划线**命名
  （`cover_extract`）—— 卡片里的 `op` 与之一致，否则卡片执行会 404；
- `convert` / `normalize` **明确不自己再列一遍参数名单**：
  "列一遍就等于多出一处需要在加参数时同步修改的地方，而漏改是**静默**的"
  （`bitDepth` 那次就是）。路由层只做白名单校验（如 `format`），
  其余参数原样透传给 handler，range 校验在 handler 里。

### 8.6 跨端约定

1. **`/api/ops` 是前端判据的来源**。它把 `needs` / `gives` / `produce` /
   `gives_formats` / `consumes` 一起发出去，前端**不抄第二份规则表**。
   测试里有一条"**前端不再有兜底卡表**"，防的就是前后端两份表各自漂移（13 vs 15）；
2. **`/api/ops/chain` 一次提交整条链**，返回 `notes`（链上提示）与 `taskIds`。
   错误是 400 + `{detail: {message, stepIdx}}`；
3. **`_op` / `_theme` / `_step_name` 这类 `_` 前缀键客户端覆盖不了**（§6.6）。

---

## 9. 前端

### 9.1 加载顺序（`index.html`，444 行）

`<head>` 里 **10 个样式表，顺序是语义**：

| # | 文件 | 负责 |
|---|---|---|
| 1 | `ui/theme.css` | **令牌层**（只有 `:root` 块，没有任何选择器） |
| 2 | `ui/base.css` | 盒模型、`html/body`、`:focus-visible`、`[hidden]`、滚动条、**视图过渡动画** |
| 3 | `ui/common.css` | `.btn` / `.input` / `.badge` / `.dot` |
| 4 | `ui/header.css` | 顶栏、`chainbar`、主题切换组、音量 |
| 5 | `ui/layout.css` | 应用骨架（`body` / `sidebar` / `workspace` / `selbar` / `bulkbar` / 文件列表容器） |
| 6 | `ui/filecard.css` | 文件卡片、波形、运输控件、元数据框、封面、状态列 |
| 7 | `ui/drawer.css` | 抽屉、快照条、分组折叠、预设区、链编辑器 |
| 8 | `ui/cardlib.css` | 功能卡片 + 预设卡片 + **光标跟随/点击反馈/AM 网点** + 抽屉搜索框 + 链 |
| 9 | `ui/overlays.css` | 拖拽遮罩、上传条、右键菜单、通知、模态 |
| 10 | `ui/editor.css` | 卡片编辑器 + **响应式断点**（`@media (max-width:1400px)`） |

`</body>` 前两个脚本，**顺序同样重要**：

```html
<script src="app.js"></script>   <!-- 先：定义 $ / $$ / toast / renderFiles / FILES / init … -->
<script src="api.js"></script>   <!-- 后：定义 API / DND / CTX / App，注册 DOMContentLoaded → App.boot() -->
```

- 两个文件都以 `'use strict';` 开头；
- `app.js` 末尾 `DOMContentLoaded → init()`，`api.js` 末尾 `DOMContentLoaded → App.boot()`，
  按注册顺序触发 ⇒ **`init()` 先跑、`App.boot()` 后跑**。
  这是有意的：`init()` 先把 DOM 骨架（`renderChain()` / `bind()`）铺好，`boot()` 才有东西可填；
  而 `boot()` 里调的 `window.applyServer*` 全是 `app.js` **模块顶层**就赋好的，不是 `init()` 里才建的；
- 唯一的跨文件全局就是那 6 个 `window.apply*`（§2.3）。

页面骨架（`index.html` 里带注释的硬约束）：

- `.dock` **必须在 `.body` 之内**，且 `.body` 的 `overflow` 保持 `visible` ——
  否则 body 是 flex 列容器，未定位的绝对定位元素会以 body 的静态位置为参照而不是视口，面板推不动；
- `.selbar` **必须常驻**：`bulkbar` 只在"已选中"时出现，把全选控件藏在里面会导致
  "全选永远点不到"；
- `#player` 全页共用一个 `<audio>`，天然保证同一时刻只有一个文件在放；
- 4 个模态：`#metaModal` / `#presetModal` / `#chainEditModal` / `#cardModal`。

### 9.2 `app.js` 分区地图（4614 行，148 个函数）

| 区间 | 内容 |
|---|---|
| 工具与图标 | `mulberry32`（确定性伪随机，让波形每次刷新一致）、`hashStr`（FNV-1a）、`cssVar`、`fmtDur`、`tok(name, alpha)`（**从 `theme.css` 令牌取色**，保证 Canvas 波形跟随主题）、`svg(k, sw)`（未命中回落 `wave` 且**只警告一次**，不静默也不刷屏）、`escHtml` |
| 顶层数据 | `FILES` / `TASKS` / `LOGS` / `CARDS` / `SNAPS` 全是 `const []` —— **绝不再伪造** |
| 队列与日志 | `renderQueue` / `renderLogs` / `addLog` / `fmtClock` / `coverHTML` / `renderFiles` |
| 卡片编辑器 | `loadOpsCatalog` / `paramApplies` / `collectParams` / `renderParamForm` / `schedulePreview`（防抖 180 ms）/ `openCardEditor` / `readCardForm` / `saveCard` / `reloadCards`（顺手把链上已删的卡过滤掉）/ `syncParamForm` / `resolveCardParams`（执行前补齐：`tags` 空值就地 `prompt`、`cover` 就地选图） |
| 右键菜单/封面/批量 | `openCardMenu` / `pickFile`（**必须能感知取消**，否则批量按钮永久卡在 disabled）/ `submitOp`（统一入口，**自动带上 `currentTheme()`**）/ `importCover` / `waitForCover` / `runBulk`（7 个动作全部真落到后端） |
| 播放器/波形 | `canPlay` / `playHint` / `syncPlayhead` + rAF 平滑（`timeupdate` 只有 ~4Hz）/ `togglePlay` / `seekFromEvent`（**`wrap` 必须由调用方传入** —— 事件委托下 `e.currentTarget` 是 `#fileList`，拿它量宽度会让跳转位置整体偏掉）/ `bindVolume` / `drawWave` / `redrawAllWaves` |
| 抽屉三档 | `stops` / `applyStop` / `boxForTop` / `syncDrawerLeft` / `cycleDrawer` / `syncSearchFlag` / `setSearch` / `renderSnaps` / `saveSnapshots` |
| 卡片库渲染 | `renderCardSections`（**分类表外的卡片单独成段并 `addLog` 警告**，否则会凭空消失）/ `bindSectionToggles` / `cardHTML` |
| 执行链 | `renderChain` / `renderChainMode` / `renderChainNotes` / `availabilityOf` / `formatFlowBlocked` / `syncCardAvailability`（§5.14） |
| 预设链路 | `presetIcons` / `setDrawerTab` / `renderPresets` / `loadPreset` / `chainSnapshotSteps` / `openPresetSave` / `renderIconPicker` / `commitPreset` / `openChainEditor` / `registerStepAsCard` |
| 档位与执行 | `setChainMode` / `currentTheme` / `runChain` / `resolveChainSteps` / `addToChain` / `addSteps` |
| 选择/通知/模态 | `selectedIds` / `syncBulkbar` / `toast`（4.2 s 自动消失）/ `showNotice`（**不自动消失**的超集）/ `settleModal` / `openModal` / `closeModal` / `openMeta` / `saveMeta` |
| `bind()` | 约 750 行，一次把全站监听器绑上（主题、抽屉拖拽、卡片区委托、队列重试、链点击、模态 `[data-close]`、文件列表、分隔条、`ResizeObserver`、Esc…） |
| 后端数据接入 | 6 个 `window.applyServer*` + `renderEmptyState` / `loadAllPeaks` / `announceOutputs` / `mapState` |
| 跟随引擎 | `bindFollow` / `layoutRectOf` / `initCardFollow` / `initPresetFollow` / `initSnapFollow` / `initCardTap`（§9.4.3） |
| `init()` | 读 localStorage → 首屏 `render*` × 7 → `bind()` → 三个 `init*Follow` → **4 个模态全部登记 `bindFollow`** → `syncDrawerLeft()` → `applyStop('closed')` → rAF 重画波形 → 异步 `loadOpsCatalog()` → `loadPresets()` |

### 9.3 `api.js`（636 行）

**`j(method, path, body)`** —— 唯一的请求封装：

- `BASE = ''`（同源：后端同时托管前端）；
- 响应先 `await r.text()` 再 `JSON.parse`，解析失败退化成 `{_raw: text}`，
  让后面的 `!r.ok` 分支给出的仍是 HTTP 错误而不是语法错误；
- 错误消息优先级 `data.detail → data.message → data.error → HTTP {status}`。
  后端有几处把 `detail` 做成**对象**（`{message, stepIdx}`、`{message, kind}`），
  此时 `JSON.stringify` 兜底成字符串 —— 保证 `new Error()` 的 message 永远是字符串；
- 非 2xx **一律 throw**，不做"返回错误对象"那套。

`API` 暴露 **40 个方法**（系统 4 / 文件 13 / 任务 4 / 操作与链 4 / 卡片 8 / 快照与预设 8）。
`outputUrl(rel, inline)` 做路径归一：剥掉可能多出来的 `outputs/` 前缀，
并对每段 `encodeURIComponent`（中文/空格文件名）。

另外三个 IIFE：

- **`DND`**（拖拽导入 + 上传）：`depth` 计数稳 dragenter/dragleave 乱序；
  目录递归 `webkitGetAsEntry`（**`readEntries` 一次最多返回 100 条，必须循环取空**）；
  上传用 **XHR**（`fetch` 不支持上传进度），`cancel()` 走 `abort()`；
- **`CTX`**（单文件右键菜单）：`open()` **先显示再量尺寸**（否则读到 0）再夹到视口内；
  `close()` **必须清 `el.onclick`**（卡片右键菜单会给同一个元素挂 onclick，
  不清掉下次打开文件菜单时那个旧处理器还活着）；
- **`App`**（桥接层）：`boot()` = `CTX.bind()` + `DND.bind()` → `health()` →
  `cards()` → `reloadFiles()` → `refreshQueue()` → `pollLogs()` → `startEvents()` →
  几组 `setInterval`。`startEvents()` 用 `EventSource('/api/events?interval=1')`，
  **SSE 的 files 只有状态，合并进 cache 而不是替换**（避免丢掉 `info`）。

### 9.4 四个最难的机制

#### 9.4.1 抽屉三档

| 档 | top | box |
|---|---|---|
| `closed` | `vh − bar` | `bar`（只露快照条） |
| `mid` | `vh − EDGE − (bar + 56vh)` | `bar + 56vh` |
| `full` | `0` | `vh` |

- 盒子 `left`/`right` 固定，**面板用 flex 占满盒内剩余高度** —— 这样搜索栏永远贴着盒底、
  不会被挤出视口（"曾经踩过"）；
- `EDGE = 14` **不等于**工作区内容的留白（`.workspace` 是 `padding: 18px 20px 0`），
  所以抽屉比文件列表左右各宽 6px，是**有意保留**的观感；
- **拖拽 1:1 跟手**，`SNAP_MIN = 4` px 以内视为点击；**只在整个区间两端夹住** ——
  之前把行程限死在"本档 ↔ 相邻档"（一次手势只走一档），从 closed 拖到顶端也只到 mid、
  松手还回弹；现在一次手势可以从 closed 直接拖到 full；
- 拖拽途中就把 `data-stop` 指向"松手会落到的档"，圆角与背板才不会等到松手才变。
  **档距不等**（closed→mid 433px、mid→full 243px），只能比距离，不能用固定像素阈值；
- **把手不绑 `click`**：点击/拖拽统一由 `pointer*` 处理，否则 `click` 会与 `pointerup`
  各切换一次，表现为"点了没反应"。用 `setPointerCapture` + `pointercancel`；
- `data-dragging="true"` 时关过渡（跟手），松手删掉（吸附过程有动画）；
- `syncDrawerLeft()` 用侧栏**实际渲染宽度**（`--sidebar-w` 声明 234px，实际渲染 263px），
  且用 **`ResizeObserver` 盯侧栏本身** —— 侧栏宽度会在没有 window resize 的情况下变
  （字体加载完成、出现/消失滚动条、队列列表变长）。只在 init 里量一次会留下固定偏差
  （实测 init 时侧栏右缘 252，稳定后 282，抽屉按 266 落下 → 压住侧栏 16px）。

**标题切换（功能卡片 ⇄ 预设链路）**：标题是 `<button id="drawerTab">` 而不是
`<h2 onclick>`（键盘可达、读屏可读）；⚠ `aria-pressed` 与**标签文字**
（`#drawerTabLabel`）都要换（"只改 title 的话屏幕阅读器读到的还是『功能卡片』"）；
跟随切换的还有三个控件（`#cardsecToggleAll` 在预设视图隐藏、搜索框改搜预设、
`#drawerScope` 不变）。`#cardSections` 与 `#presetSections`
**共用同一个滚动容器**（`#cardViewport`）。

**搜索框双用途的最细一处坑**：失焦收回走 `keepQuery` 路 ——
用 `pointerdown(capture)` 而不是 `blur`（pointerdown 排在 mousedown 之前，
收回引起的布局变化先落定，按下/抬起命中同一个元素），
再配合 `keepQuery`（**不重渲染卡片区**）：收回发生在 click 之前，
在这里重建 `#cardSections` 会把 mousedown 的目标从 DOM 里摘掉，
"点卡片加入执行链"就整个失效了。

#### 9.4.2 预设的图标规则

`ICON_RULES = {max: 5, head: 3, tail: 2}`（**别散落魔数**，文档与代码都用这一份）：

1. **相邻重复合并**（判据用**图标名**，不是 op —— 两张不同 op 的卡用同一个图标时，
   连在一起显示两个一模一样的方块没有信息量）；
2. ≤5 全显示；
3. \>5 留头 3 尾 2，中间一个 `…`；
4. `…` **算一格**（与图标同宽同高），否则 6 格和 5 格的排布会跳。

⚠ **图标取的是 `CARD_ICONS` 里的短名（`flac`/`tag`/`zip`…），不是 op 名**。
第一版映射的是 `s.op`，于是预设卡片上显示的是 `CONVERT`/`TAGS` 这种"给人看 op 名"的东西，
而功能卡片显示的是 `FLAC`/`TAG` —— **两处视觉语言必须一致**。

⚠ 预设的图标选择器与卡片编辑器的 `renderIconPick()` **是同一个组件**
（`.iconpick__btn` + `role="radio"`/`aria-checked`）。这里曾经是另一套
（`.iconpick__item` + 写短名文字、没有 3D 手感），结果是同一个应用里两处"选图标"长得不一样。
唯一的区别是**多选 + 「自动」态**：`presetIconsDraft === null`（自动）时一个都不选中。

#### 9.4.3 `bindFollow`：跟随引擎与 `stableHit` 的抖动分析

这是全项目注释密度最高的一段（约 30 行），因为它同时是性能问题和交互正确性问题。

**① 矩形缓存**。原来每帧 `el.getBoundingClientRect()`，而上一帧刚写过自定义属性、
样式树是脏的 → **强制同步布局**（trace 里每帧一条 `Layout (totalObjects 2266)`）。
现在稳态悬停时**一次都不读**，只在几何真可能变时 `invalidateFollowRects()`：
window resize、任何滚动（滚动不冒泡，用捕获阶段）、抽屉 top/height 过渡结束、
模态旋入结束、抽屉拖拽、两个列表重渲染。

> ⚠ **别在这里挂全局 `transitionend` 兜底**：整个界面到处都有 hover 过渡，
> "指针一动就有过渡在结束 → 缓存被不停作废 → 又变成每帧一次强制同步布局"。
> 实测：全局 transitionend 让模态 hover 的 Layout 从 +14 涨到 +94（30 次移动）。
> 所以只认真正改变几何的那两个过渡
> （`e.target === drawerEl && (propertyName === 'top' || propertyName === 'height')`）。

**② pointermove 合并到每帧一次**。鼠标 1000Hz 时一帧内会来好几个 `pointermove`，
而 `--mx/--my` 驱动的是 `mask-image`/`background-image`（**绘制属性**）——
等于一帧内让同一张卡重绘好几次。用 `pending` + `requestAnimationFrame` 合并，
再加 `MIN_MOVE = 3` px 阈值（网点是软边径向遮罩，3px 以内的位移看不出来）。

**③ `stableHit`（模态专用）—— 抖动的根因与修法**：

> 盒子的倾斜是「指针位置 → 变量 → transform」，而**浏览器命中测试**打在变换**之后**
> 的几何上。于是"指针还在不在盒子上"也变成了倾斜的函数：
> 指针停在盒子边缘 → 静止：命中盒子 → 写变量 → 倾斜 → 那一侧轮廓缩进约 5px →
> 不再命中 → `pointerout` 清掉变量 → 回正 → 又命中 → ……（**按帧率跑的极限环**）
>
> 实测（宽盒 918×425、tilt 1.4°、`.modal` perspective 1000px、视口 966×703）：
> 边缘内侧 1–5px 处命中目标在"静止↔倾斜"之间翻转，共 **5 条带**；
> 轮廓单侧缩进 rotateY 5.07/5.23px、rotateX 2.08px（`transform-origin: 50% 10%`，
> 转轴贴近上沿，所以上沿几乎不动）；视口 966px 时盒宽 918px
> （`min(940px, 100vw - 48px)`）→ 盒子边框离**窗口边**只有 24px，
> 所以用户看到的现象是"鼠标到窗口边缘时模态在抖"。
>
> ⚠ 试过、**不行**的修法：给 `.modal__box` 加 `::after { inset: -8px }` 内扩命中区。
> 内侧那 5 条带确实没了，但伪元素是盒子的子节点、**跟着一起被变换**，
> 内边距自己也会缩进 —— 内外一起扫会看到多出 6 条带，落在边缘**外** 2–7px。
> **只是把不稳定搬了个地方。**

→ 所以这类目标**不再用浏览器的命中测试**：改用 `layoutRectOf()` 的布局矩形
（与 transform 无关）自己判"指针在不在盒子上"，判定边界固定在屏幕空间里，环就不存在了。
`HIT_MARGIN = 6`（必须 ≥ 倾斜造成的轮廓缩进量 ≈ 0.006 × 元素宽；
模态盒宽上限 940px → 约 5.4px。**调 `--nx/--ny` 的 1.4deg 或调小 `perspective` 时要重算**）。

**④ `layoutRectOf()` 的前提与禁令**：沿 offsetParent 链累加 `offsetLeft`/`offsetTop`。
⚠ **前提：从 el 到 container 必须是"嵌套定位链"**（每一步的 offsetParent 正好是下一步）。
「若中间几层的 offsetParent 是同一个祖先（卡片就是这样），逐级累加会**重复计数**，
算出来的矩形是错的」。所以不满足前提时**直接退回 AABB**（=旧的、会抖的那条路）：
"**宁可抖，也不能让命中区错位。要把 `stableHit` 用到卡片上，得先按『扣除每个可滚动
祖先的 scrollTop/scrollLeft』重写这个函数 —— 别直接打开开关。**"

**⑤ 关掉 3D 时"不写"而不是"每帧写 0"**：CSS 侧兜底写的是 `var(--nx, 0)`，
变量不存在等价于 0（`theme.css` 里 `--nx/--ny` 根本没有默认值定义）；
残留旧值由开关一次性归零负责。"而『每帧写』会让模态框每帧白脏一次样式
（它 settle 后有 `backdrop-filter`，一次脏 → 整页背景重绘）"。
另外 `if (fxOff() && !needsGlow) return;` —— 整条链路没事要写，连 rAF 都不排。

**⑥ 释放动画 `.is-returning` + 三条纪律**：`clearFollow()` 在摘掉 `--nx/--ny`
**之前**挂类，CSS 把这次"回正"走成过渡。纪律：① 指针又进来时立刻 `cancelReturning`；
② 定时器**按元素存**（`WeakMap` 不是按容器一个）——「指针扫过卡片网格时同时有好几张卡
在回正，共用一个 id 会漏摘（那些卡会永久停在慢缓动）」；
③ 只在"确实带着倾斜"时挂。

**⑦ 登记遗漏的表现很隐蔽**：漏了 `bindFollow` 登记时，模态**能开能用**，
只是看起来"硬"一点（没有倾斜、毛玻璃从第一帧就有），**不报任何错**，
所以只有人眼能发现（用户就是这么发现的）。同理
`initPresetFollow()` 漏了的 `.pcard` 也"看着很正常、只是不动"。

#### 9.4.4 拖拽：`effectAllowed` / `dropEffect` 与合成事件的教训

**最重要的一条**：`effectAllowed` 与 `dropEffect` 必须兼容。
浏览器在 `dragover` 阶段就会比对，**不允许的组合会直接不派发 `drop`**
（表现为"拖上去松手没反应"，且**没有任何报错**）。
之前来源写 `'copy'`、落点写 `'move'`，两者不容 → 卡片永远替换不了。
同一轮里"拖到 ＋ 追加"却是通过的 —— 因为 ＋ 用的是 `dropEffect='copy'`，
正好落在 `effectAllowed='copy'` 允许的集合里。**这个对比本身就是最好的证据**：
不是事件没绑定，是许可判定把 drop 掐掉了。

**⚠ 合成 `DragEvent` 会绕过浏览器许可判定**：

> 页面里 `dispatchEvent(new DragEvent('drop'))` 会**直接派发** drop，
> 绕过浏览器的 `effectAllowed` / `dropEffect` 兼容性判定。
> 真机上不兼容的组合**根本不派发 drop** —— 这个 bug 曾经在
> **合成事件探针全绿的情况下存在于线上**。

**现在的验证方式**（`tests/snapshot_drag_real.py`）走 CDP 真实管线：
① `Input.setInterceptDrags(true)` + 真实 `mousePressed`/`mouseMoved` →
浏览器真的发起拖拽，从 `Input.dragIntercepted` 拿到 `dragstart` 真正写进去的载荷；
② 用那份载荷 `Input.dispatchDragEvent` 到落点，**让浏览器自己判定允不允许**。

其余细节：自定义 MIME `application/x-ae-snap` **同时写 `text/plain` 副本**
（`"自定义 MIME 读不到时退到 text/plain —— 部分路径会把它剥掉，静默失败最难查，多一条退路"`）；
"这张卡已经在别的格子里 → **两格互换**，而不是复制一份"；
乐观更新（先本地改，失败重新拉 `/api/cards` 回滚）；
`dragend` 在 `#cardSnaps` 和 `document` **各挂一次**（`dragend` 是在**拖动源**上触发的，
从卡片区拖过来时它不会经过 `#cardSnaps`，只挂上面那一处的话落点高亮会一直留着）；
`dragenter` 也要 `preventDefault`（有些浏览器只在 enter 时判定是否接受）。

### 9.5 主题与令牌契约

**`ui/theme.css` 是唯一色值来源**，6 套 = 3 类型（t1 蓝橙 / t2 绿橙 / t3 紫珊瑚）
× 浅/深，各块**恰好 21 个令牌**（`tests/theme_check.py` 校验不少也不多）。
深色用 `:root[data-theme="tN"][data-mode="dark"]`。

**令牌三组配对契约**（最易在深色模式下搞错）：

| 底色族 | 配的文字色 |
|---|---|
| `--bg-app` / `--bg-surface` / `--bg-surface-alt` | **`--ink`** |
| `--tint-primary` / `--tint-accent` / `--error-tint` | **`--ink`** |
| `--fill-primary` / `--fill-accent` | **`--on-fill`** |

深色模式下 `--fill-*` **反转成浅色底**，`--on-fill` 因此是近黑色（`#10171C`）。
此时若在 `--fill-*` 上用 `--ink`，就是浅字压浅底，**实测只有 1.9:1**。
代码里多处"必须显式声明 `color`"的注释都是同一个原因：省略 `color` 会从父级继承，
而父级可能正是另一种底。

其余值得记的令牌：`--ink-error`（**别写成 `--err-ink`**）、`--error-solid`、
`--border`/`--border-strong`、`--shadow-sm`/`--shadow-md`、`--text-muted`
（全站实测最低 2.55，**只用在 ≥11px 的非关键信息上**）、
`--ink-accent`（**目前只有后端用**：`loudness_svg.py` 的 −23 参考线）、
`--text-disabled`（配色契约的保留位，当前无引用）。

**`backend/theme.py` 是解析器，不复制任何色值**：正则解析 `theme.css` 的
`:root[data-theme="tN"]` 块，一进程只解析一次（模块级 `_CACHE`）。
理由（docstring 原话）："色值是设计师在 `theme.css` 里调的。抄一份出来**一定会脱节**
—— t2 就漏跟过一次，`tests/browser_palette_probe.js` 那 200 行就是为那次写的。
这里读同一份文件，脱节不可能发生。"

半透明令牌（`rgb(36 68 94 / 0.62)`）会被**压平成实色**（`solid()`）；
`parse_color` 认不出就**抛**，不静默返回黑色。

**切换怎么发生**（`bind()` 里的 `switchTheme(dir, mutate)`）：

1. 先给 `<html>` 加 `data-theme-switch` —— 它命中 `ui/base.css` 的
   `html[data-theme-switch] * { transition: none !important }`，
   **掐掉各组件自己的颜色/阴影过渡**（实测数百个 Animation 覆盖
   `background-color`/`border-color`/`box-shadow`/`scrollbar-color`，
   每个都在重绘 → 连续掉帧）；
2. **只动 `transition`，绝不动 `animation`**：平扫 `vt-sweep` 与径向
   `vt-expand`/`vt-contract` 都是 animation（在 `::view-transition-*` 伪元素上，
   `*` 后代选择器命中不到）。**两条禁令**：别把 CSS 那条规则扩写成 `animation: none`；
   别把平扫/径向从 animation 改写成 transition(clip-path)；
3. `document.startViewTransition(mutate)` 存在且用户没开 `prefers-reduced-motion`
   时走真正的视图过渡，并设 `--vt-r`（`Math.hypot(innerWidth, innerHeight)`）
   与 `--vt-d`（`innerHeight * tan15°`）；
4. 无 `startViewTransition` 时走 `try/finally` 直接 `mutate()`：
   "这个标记是全局生效的，万一 `mutate()` 抛了（`localStorage` 在隐私模式/配额满时会抛），
   也必须把它摘掉，否则整个页面的过渡会**永久**失效"；
5. 摘标记用**两个 rAF**（第一个 rAF 里新主题的样式才被采用，第二个才确保这一帧已经画完），
   随后 `requestAnimationFrame(redrawAllWaves)`。

**3D / 网点开关**（`#fxToggle`）：状态在 `<html data-fx="off">` + `localStorage['ae-fx3d']`，
**默认开**。关闭时**立刻归零**所有 `.fcard`/`.snap`/`.modal__box` 的 `--nx`/`--ny` ——
"鼠标正停在卡片/模态上不动的话，光靠下一次 pointermove 才生效会有明显的滞后感"。
径向光晕与模态的旋入动画照旧（只关倾斜）。

**AM 网点（`ui/cardlib.css`）**：「近处点大、远处点小」一层做不到
（`mask` 作用于整个元素、**不能按 background 分层**），所以叠 **4 层**：
每层一个点径 + 自己的径向遮罩，四层共用同一格网原点，由外到内点径递增、reach 递减。

- 承载层 `.fcard__dots` / `.pcard__dots` **必须是卡片的直接子元素**（选择器写的是 `>`），
  它的两个伪元素贡献另外两层，父元素 `::after` 是第四层；
- **画序必须由小到大**（伪元素绘制顺序是树序：元素本身 → `::before` → `::after` →
  父 `::after`），"放反了的话，小点会盖在大点上面"；
- 调参面全在 `theme.css` 的 `:root`（主题无关常量）：
  `--spot-cell` / `--spot-dot-1..4` / `--spot-reach-1..4` / `--spot-a1..a3` /
  `--spot-alt-a` / `--spot-mix-base` / `--spot-mix-alt`；
- 不支持 `mask-image` 时用 `@supports` 外的**保底实心光晕**，"绝不出现『整卡铺满网点』的破图"；
- 两族卡片（`.fcard` / `.pcard`）必须**一起写进每条选择器**：
  "少改一处就静默坏掉（少改的那一族卡片会表现得像『没有效果』，不报任何错）"。

### 9.6 模态与通知

`openModal(el)` / `closeModal(el)` / `settleModal(el, delay=320)`：

- 打开：`clearTimeout` → 摘 `is-open`/`is-settled` → `hidden = false` →
  `void el.offsetWidth`（**强制回流，让 transition 有起点**）→
  `requestAnimationFrame(() => add('is-open'))` → `settleModal`；
- `settleModal` 在 320ms 后加 `.is-settled`，**`.modal__box` 的毛玻璃从那一刻才开**：
  "盒子在动的时候它的模糊采样区一直在动，每帧都要重新模糊"。同时
  `invalidateFollowRects()`（"旋入动画到此结束……跟随用的矩形缓存必须作废"）；
- 计时器存在 `WeakMap`（`_modalTimers`）而不是一个变量 —— 4 个模态各有自己的定时器。
  `#metaModal` 自己复制了 `openModal` 的逻辑（用 `metaCloseTimer` 而不是 `_modalTimers`）；
- 关闭：先摘类触发淡出，`clearTimeout` 防抖（"防跳：关-开-关 连点时不会误隐藏"），
  200ms 后才 `hidden = true`（与 CSS 过渡时长一致）。

⚠ **`data-close` 不是全局委托，每个模态必须各有一条监听**
（`app.js` 原话："没有这条监听『取消』『点遮罩』就都不响应（**用户实测报的**
『编辑执行链动作无法取消』）。别的模态都有，**新加模态时最容易漏的就是这一行**。"）。
另有一条 document 级 `keydown` 专门给两个对话框类模态处理 Esc。

**通知**：`toast(title, msg, kind)` 4.2 秒自动消失（重复加类名是 no-op，不重播动画）；
`showNotice(...)` 是超集，两个差别：**不自动消失**（"4.2 秒不足以让用户读完
『我有两张卡没有』并决定要不要看详情；自动消失等于把这件事藏起来"）；
关闭时 `act.onclick = null`（避免关掉之后又点到已经过期的动作）。

### 9.7 后端数据接入的几个口径

- `applyServerFiles(files)` 做**数据指纹**去抖：指纹相同一个 DOM 节点都不动；
  但**刷新不能吃掉勾选**（按 id 继承 `checked`）与**播放中高亮**
  （`playingId === f.id` 补 `is-playing`）。这两个都是 `demo` 自检抓出来的真 bug：
  勾选活不过 2 秒、播放高亮每 2 秒消失一次；
- `loadAllPeaks()` **非音频文件直接跳过**（以前照拉不误，每刷新一次列表就失败重试一遍）；
- `announceOutputs(rows)` 只报**新**成功的产物，上限 6 条，带「打开 / 下载」动作。
  这是"响度分析报告未看到产出"的正解 —— 产物本来就在 `outputs/` 且
  `/api/outputs/` 也能取到（CJK + 空格文件名正确 percent-encode），
  真正缺的是**可发现性**；
- `window.applyServerQueue(q)` 里 `skipped` **必须显式映射成 failed**（§4.4）。

### 9.8 文件列表拖拽

`#fileList` 自身**没有** `draggable` —— 文件卡片不可拖。文件拖拽只发生在
"从系统拖入页面"这一层（`api.js` 的 `DND`）。外面还有一层兜底：
`App.boot()` 里 `syncAndProbe` 每 2s 轮询，对 `info` 为空对象 `{}` 的新文件自动补 probe
（"外部（脚本 / 右键菜单）上传的文件会自动出现"）。

---

## 10. UI 组件库（`ui/` 与 `ui-kit/`）

### 10.1 `ui/` 是源，`ui-kit/` 是产物

`tools/build_ui_kit.py`（138 行）把 `ui/` 下 10 个样式表**按加载顺序纯拼接**成
`ui-kit/audioedition-ui.css`（2921 行 / 118279 B），前面加 `BANNER`，
每段前加分隔注释 `/* ==================== ui/{name} ==================== */`。

**为什么单独出一份**（docstring 原话）：

> `ui/*.css` 是**应用在用的源**（`index.html` 按顺序引 10 个 `<link>`），改完刷新就生效，
> 没有构建步骤。但想把这套样式**拿走给别处用**时，10 个文件 + "顺序不能改"这条口头约定
> 很容易传丢，于是另出一份单文件：拿一个文件即可，顺序已经烘死在里面。

⚠ **它是生成物，手改会被下次运行覆盖**。**应用不读它**（应用仍走 `ui/` 的 10 个文件），
所以"改了源但忘了重新打包"不影响应用本身，但会让 `ui-kit/` 悄悄过期 ——
因此 `tests/ui_check.py` 会**逐字节**校验它和源是否一致
（这是 `demo/app.js` 拷贝不同步那个教训的翻版，必须有闸）。

**顺序的唯一事实来源**是 `build_ui_kit.ORDER`，`ui_check.py` 直接 `import` 它，
所以不存在第三份清单。用法：`python tools/build_ui_kit.py` / `--check`。
`chunks()` 把每个源文件统一成"恰好一个结尾换行"——
"源文件末尾若没有换行，直接拼会把下一个文件的第一行接到注释/规则尾巴上
—— **那会写出一个语法坏掉的包**"。

**刻意不做压缩**："这套样式的价值有一大半在注释里（每条规则为什么这么写、
哪个坑踩过），压缩会把它们全部删掉，还要为此引入工具链"。

### 10.2 `ui-kit/README.md` 与 `gallery.html`

- `ui-kit/README.md`（618 行）是**权威的样式与组件契约**：加载顺序硬约束、
  令牌契约、组件清单、状态钩子；
- `ui-kit/gallery.html`（1289 行）是组件画廊，`<html data-theme="t1">` +
  **只引 `audioedition-ui.css` 一个文件** —— 所以它同时是"这份产物能不能用"的现场验证。
  10 个 `<h2>` 章节正好对 10 个源文件。

### 10.3 顺序不能改的四个具体陷阱（`ui-kit/README.md` §2）

层叠只看声明先后，打乱 = 静默走样、不报错。

1. **`editor.css` 是最后一个，它会覆盖前面的同特异性规则**：
   `.btn--danger` 在 `common.css` 有一份、`editor.css` 末尾**又写了一份**（想改得改后者）；
   `.modal__box--wide` 改的是 `overlays.css` 的 `.modal__box`；
   `@media (max-width:1400px)` 改的是 **`filecard.css` 与 `drawer.css` 的组件**
   （`.metacard`/`.cover`/`.wave`/`.snaps`）——"找响应式规则时别只翻自己那个文件"；
2. **`.iconpick` 这个类名只有一个主人**：`editor.css` 里那条必须写成
   `#cardIcons.iconpick`。若写裸 `.iconpick { display:flex }`，同特异性下后加载的赢，
   会把 `drawer.css` 给预设写的 `display:grid` 悄悄压掉 ——
   表现是"格子算出来了但还是 flex 换行"；
3. **`drawer.css` 在 `editor.css` 之前，所以「自动」态必须靠特异性取胜**：
   `.iconpick--preset[data-auto="1"] .iconpick__btn[aria-checked="true"]`
   多带一个 `.iconpick--preset` 前缀把特异性抬到 (0,2,0)；
4. **`theme.css` 必须第一**，否则令牌在用到它们的规则之后才定义。

### 10.4 验证闸（`tests/ui_check.py`）

`ORDER = kit.ORDER`（import 打包脚本），核对：

1. `ui/` 恰好是 `ORDER` 这 10 个样式表（多一个就红，文档/画廊应放 `ui-kit/`）；
2. `APP_PAGES = ["index.html", "_snapdrag.html"]` 两个页面各引 10 个样式表
   且**顺序与 `ORDER` 逐项一致**，并拒绝写回 `css/` 之类的老路径；
3. 产物里分隔注释递增顺序一致、产物与源**逐字节一致**；
4. `var(--x)` 用到而全库没定义的，必须登记在 `RUNTIME_VARS` 里并写明**谁在运行时写它**；
   定义了却没人用的，必须登记在 `RESERVED_VARS` 里并写明**谁在 CSS 之外消费它**
   （目前只有两个：`--ink-accent` 给后端 SVG 渲染器、`--text-disabled` 是配色契约保留位）。
   **这两张表是声明来源，不是消音开关**；
5. `ui/*.css` 与产物的行尾必须是 **LF**（`.gitattributes` 是 `eol=lf`）。

---

## 11. 测试与自检

### 11.1 分层原则

执行链的自检**刻意分成多层**，因为每一层能证伪的东西完全不同 ——
合并成一个脚本会让"哪一层坏了"变得看不出来：

| 层 | 需要什么 | 例 |
|---|---|---|
| 纯函数 | 无（不连库不连服务） | `boundary_check.py`（50 项） |
| 库结构 | 自己建临时库 | `chain_store_check.py`（75 项）、`preset_store_check.py`（54 项） |
| 真队列（假 handler） | 假 handler 走真队列 | `chain_queue_check.py`（52 项） |
| 真队列 + 真 ffmpeg + 临时工作区 | 无服务 | `chain_build_check.py`（103）、`chain_zip_check.py`（31）、`loudness_chain_e2e_check.py` |
| 真服务（HTTP） | 服务在 8765 | `smoke_api.py`（208）、`chain_e2e_check.py`（36） |
| 真服务 + 无头浏览器（CDP） | 服务 + Edge | `browser_chain_probe.py`（63）、`browser_chain_preset_probe.py`（73）、`browser_drawer_tab_probe.py`（47）、`halftone_check.py`（27） |
| 只读诊断/取证工具 | 各自 | `tests/dsh-wheel/` 的 24 个脚本 |

### 11.2 常用命令

```powershell
# 纯函数，最快，先跑它（下面括号里是 2026-10-07 实测的项数）
python tests/boundary_check.py            # 50 passed
python tests/preset_store_check.py        # 57 passed（tests/README.md 记的是 54，已过期）
python tests/chain_store_check.py         # 75 passed
python tests/chain_queue_check.py         # 52 passed
python tests/chain_build_check.py         # 109 passed
python tests/chain_handoff_check.py       # 17 项（枚举 52+26 组）
python tests/chain_zip_check.py           # 31 项（真 ffmpeg）
python tests/loudness_chain_e2e_check.py  # 真队列 + 真 ffmpeg
python tests/loudness_display_check.py    # 15 项（要 node）
python tests/waveform_check.py            # 36 项（逐像素）
python tests/reveal_cmd_check.py          # 30 passed
python tests/theme_check.py               # 93 项：6 套主题 × 21 令牌 + 对比度
python tests/ui_check.py                  # 26 项：组件库结构与打包一致性

# 需要服务在 8765
run.bat                                   # 另开一个窗口
python tests/smoke_api.py                 # 234 passed（`tests/README.md` 记的是 208，已过期）
python tests/chain_e2e_check.py           # 36 项
python tests/axis_a_check.py              # 19 项（⚠ 会清空 outputs/）
python tests/snapshot_drag_real.py        # 真实拖拽（CDP）
python tests/browser_chain_probe.py       # 前端接线 + 前后端对账
python tests/browser_chain_preset_probe.py
python tests/browser_drawer_tab_probe.py
python tests/halftone_check.py            # AM 网点（CDP + 截图解码）
```

**跑之前先起服务**：`AE_FRESH` 默认清空工作区（§1.3）。

> 上面没有标"passed"的项数（`chain_handoff_check` / `chain_zip_check` /
> `loudness_*` / `waveform_check` / `smoke_api` / `chain_e2e_check` / `axis_a_check` /
> 各浏览器探针）是 **`tests/README.md` 记的**，本次**没有逐个复跑**
> （要真 ffmpeg / 真服务 / 无头浏览器）。已复跑的那一批见 §11.6。

### 11.3 测试系统里的几条"规矩"（`tests/README.md` + 脚本注释）

1. **端到端脚本必须用带随机后缀的文件名。** 用固定名字时，上一轮跑完清理掉的文件行
   会被级联删除，新链却引用了"行还在、磁盘没了"的僵尸文件，
   表现为一堆与本次改动无关的失败；
2. **中间文件的文件名必须带任务 id，不能用固定名**（§7.3 的 WinError 32）。
   **这类竞态没有专项断言**（要复现得并发跑两次响度分析），所以只能靠规矩拦：
   写中间文件前先问"两个任务同时跑会撞吗"；
3. **后端拦得住 ≠ 用户不会撞上**。规矩：**凡是后端会 400 的约束，前端置灰那一路
   也要算一遍**；判据从后端字段来，**别在前端抄第二份规则表**（§5.14）；
4. **前后端各算一套判据时，一定要有对账断言**（§5.14 第 3 条）；
5. **交错断言是"没退化成全局屏障"的唯一证据**。断言"小文件的第 2 步在大文件的
   第 1 步结束之前开始"—— 产物断言全绿也可能是个全局屏障的慢实现。
   这条**真的抓到过一次**：修另一个 bug 时把"按文件等"改成了"按链等"，
   正确性看着更稳妥，实际就是把文件级流水线换成了步骤级屏障；
6. **浏览器缓存会把"逻辑没坏"伪装成"逻辑坏了"**。两个对策：入口 `index.html`
   用 `no-store`；探针**先自证跑的是新代码**（`String(fn).includes(新标识)`），
   不信文件时间、也不信"我改过了"；
7. **合成事件会绕过浏览器判定**（§9.4.4）—— 拖拽必须用 `snapshot_drag_real.py`；
8. **无头浏览器读过渡属性会拿到过渡前的值**。探针一开始就注入
   `*{transition:none!important}`，否则量出来的几何是旧值。
   配色探针尤其必须先冻住过渡（`--virtual-time-budget` 不推进 CSS 过渡）——
   这个坑踩过**三次**；
9. **无头浏览器遇到 `prompt()` 会把渲染进程主线程整个卡死**（CDP 从此不再回话）。
   探针必须先接管 `window.prompt/confirm/alert`；
10. **探针必须自己捕获异常并把结果写进 DOM**。探针是一个 async IIFE，
    里面抛 `TypeError` 没有任何人接 promise，**整个探针静默中止** ——
    页面主线程好好的，看起来却像"卡住了"。现在每做完一项就 flush 一次 `#AE_RESULT`；
11. **探针自己会假通过**：目标选错（`FILES[0]` 是 PNG，而"嵌入封面"对 PNG 是
    **正确地拒绝**，于是那条路径从没被走到）+ 字段读错（前端文件对象是
    `title` + `format`，**没有 `name`**）+ 统计到历史任务（`recent` 里混着上一次运行）。
    三件事要一起做对；
12. **两个预设探针会改写工作区的 `cards.json`**（要预置"含未知卡片"的夹具），
    收尾时会写回一份干净的。别在有真实预设时跑它们。

### 11.4 几个具体的陷阱值

- `explorer` **成功也返回退出码 1**，而参数拼错时不报错、只是默默打开一个无关窗口
  —— 退出码、HTTP 200、`revealed: true` **三处全绿，功能却完全没做**。
  所以 `reveal_cmd_check.py` 把 `subprocess.Popen` 与 `sys.platform` 换掉，
  **只看它收到什么**：必须精确等于 `explorer /select,"<path>"`、引号恰好 2 个、
  **不含转义引号 `\"`**、没有 `shell=True`。改回
  `Popen(["explorer", f'/select,"{p}"'])` 立刻 **12 项变红**；
- 波形 PNG **别用"出现次数最多的颜色"**判断实底图的波形颜色（黑底图上众数色当然是黑），
  要按目标色去数（`count_near`）；
- `rename` 用例要**一案一清**（它改文件路径，"改回去"只能靠 `{filename}`，
  而它取的是当前 stem），且删除必须带 `purge=true`（软删除的行仍占 `rel_path` 的 UNIQUE）；
- `axis_a_check.py` 收尾那段 `ROOT.glob("outputs/**/*")` 是**无条件删除**，
  在真有产物的工作区里跑它会连着别人的产物一起删掉。

### 11.5 `tests/dsh-wheel/`：参考图/规格解码工具链

这是一个**只读的取证与交叉校验工具链**，不是产品代码。缘起（README 开头）：
`target/CQ.svg` 是 Youlean Loudness Meter 的导出，**没有 `<font>`/unicode 元数据**
（文字是 63 组贝塞尔轮廓）、混用了两套坐标空间，本机也**栅格化不了**
（ffmpeg 没有 SVG 解码器；headless 浏览器被沙箱的命名管道限制挡住）。
所以这一整套脚本是为了"无论如何也要把精确数字抠出来"。

与本文相关的几个：

| 脚本 | 作用 |
|---|---|
| `axis_spec.py` | **thin shim**：把 `backend/chart_axis.py` 整个重导出并打印 20 条不变量。保证"F/knee 映射只有一处定义" |
| `layout_spec.py` | `chart_layout` 的 shim **+ `.ai` 专属那一半**（CID 解码器 + `check_against_ai()`）；`--from-ai` 重新从 `大致布局.ai` 推一遍并对账 |
| `axis_options.py` | 定义 **A~F 六个候选纵轴**并排序比较（F 是定稿）；`SELECTED_KEY = "knee"`；基线 A **字面等于** `axis_spec.frac`（防基线悄悄漂移） |
| `axis_orientation.py` | 证明旧纯 log 轴**任何朝向**都摊不开响的那头（映射在文件内重新实现，所以 `axis_spec` 改了这份证据也不变） |
| `check_axis.py` | 参考图的**决定性检查**（不渲染）：用**同空间元素之间的比值**而不是绝对像素（该文件对坐标空间自相矛盾，比值对任何均匀缩放免疫） |
| `check_loudness_svg.py` | **SVG 渲染器回归**（74 处 `check(`，循环 ×6 套主题）：网格线逐条落在 `axis_spec.frac`、红区高度、轴范围外不画虚线、无 `var(`、主题对比度 ≥ AA、确定性与退化输入 |
| `check_loudness_metrics.py` | **测量修复的回归**（真跑 ffmpeg，与 stderr 对账）：truePeak 单位、新 DRA、`peak=sample+true` |
| `verify_units.py` | 逐个 ebur128 数字审单位 —— truePeak 那个 bug 就是它找出来的 |
| `verify_measure_doc.py` | 把测量文档的每条断言拿到**本机**用真 ffmpeg 复验（"Nothing is taken on faith from the doc"） |
| `check_handover_doc.py` | **机械核对交接文档的每条具体断言**与代码是否一致 |
| `drp_truth.py` | **DRP 的唯一基准**：把手标素材的标记钉死，并把三处可分性实验结论与那个假阳性陷阱钉住 |
| `check_drp.py` | `backend.drp` 的不变量回归（合成信号 + **出现互不重叠 = 0 s** 这条最容易回归的）；真文件那节只钉结构不变量、**不假定一定有模式** |
| `render_svg_preview.py` | 把渲染器整页画出来给人眼核对（`--theme/--mode/--png`）；"六套主题都看一眼"是这一步的意义 |

`target/` 是 **gitignored**，所以 `drp_truth.py` 找不到素材时 **SKIP 而不是 FAIL**。

### 11.6 本次核对的即时状态（2026-10-07）

跑一遍不需要服务的那些脚本，**除两项之外全绿**：

| 脚本 | 结果 |
|---|---|
| `boundary_check.py` | 50 passed / 0 failed |
| `preset_store_check.py` | 57 passed / 0 failed |
| `chain_store_check.py` | 75 passed / 0 failed |
| `chain_queue_check.py` | 52 passed / 0 failed |
| `chain_build_check.py` | 109 passed / 0 failed |
| `reveal_cmd_check.py` | 30 passed / 0 failed |
| `ui_check.py` | **25 passed / 1 failed** |
| `theme_check.py` | **92 passed / 1 failed** |

两个失败都是**极小的差值**，而且都指向"另一条工作流正在改样式、还没收尾"
（`git status` 当时显示 `M ui/base.css`）：

- `ui_check`：`ui-kit/audioedition-ui.css` 与 `ui/` 下的源**差了 3 个字符**
  （98091 vs 98094）—— 改了源没重跑 `python tools/build_ui_kit.py`。
  这正是 §10.1 那个"产物会悄悄过期"的闸在起作用，**按提示重跑打包即可**；
- `theme_check`：`t1/light bg-app/tint-accent` 的亮度比 **1.113 < 1.12**
  （阈值只差 0.007）—— 调色时的边界值。

⚠ 这不是本文的结论，是**核对当时的工作区状态**。两条都属于 `ui/` 下的改动，
与 `docs/` 无关；本文**没有改**这两个文件（本任务只新建文档）。

---

## 12. 开发约定与坑清单

### 12.1 加一个 op 要改哪些地方

1. `backend/cards/contract.py` —— **必须**登记接触面（`validate_contract()` 会报"没有登记"）；
2. `backend/cards/specs.py` —— `OPS` 里的参数规格（key/label/desc/type/min/max/preview）；
3. `backend/tasks.py` —— 写 handler，并在 `register_all()` 里注册 `任务类型`；
4. `backend/routers/ops.py` —— **路由名必须与 `op` 完全一致**；
5. `backend/cards/builtin.py` —— 至少加一张内置卡（否则这个 op 在界面上没有入口）。
   `CARD_CATS` 里要有对应分类，`CARD_ICONS` 里要有图标；
6. `tests/boundary_check.py` 的 `ANCHORS` / 抽样断言可能要更新；
7. 前端**不用改**（`loadOpsCatalog()` 从 `/api/ops` 现算；
   `browser_card_exec_probe.js` 的 op 清单也是从 `CARDS` 里现算的）。

**不要**：在 `ops.py` 里再列一遍参数名单（漏改是**静默**的）；
在前端抄第二份规则表；用 `mode` 兼职表达"会不会产出新文件"。

### 12.2 加一张数据库列

`SCHEMA` 里加（给全新库）**且** `MIGRATIONS` 里加（给老库），
注意 `init_db()` 里迁移必须在 `executescript(SCHEMA)` **之前**跑（§4.3）。
加了带索引的列，还要加进 `MIGRATION_INDEXES`。

### 12.3 改样式

1. 只改 `ui/*.css`（**不要改 `ui-kit/audioedition-ui.css`**，那是生成物）；
2. 改完跑 `python tests/ui_check.py`（结构 + 逐字节一致性），
   若报了"产物过期"就跑 `python tools/build_ui_kit.py` 再跑一次；
3. 动令牌要跑 `python tests/theme_check.py`；
4. 新加一个选择器时先想清楚**它在 10 个文件里的位置**（§10.3 的四个陷阱）。

### 12.4 一句一条的坑

- **`_gate` 里一行数据库状态都不许改**（defer 的任务必须留在 `pending`）。
- **`_requeue` 必须还租约**，否则任务永远推迟（像队列卡死）。
- **长任务计数只在"过了闸、真的要跑"这一刻加**，且与 `start_task` 同一个锁。
- **`_publish_derived` 是"全部注册、只交接 derived"**，别把 sidecar 交给下游当音频输入。
- **`build_chain` 必须走 `task_params()`**，否则 `_op` 丢失、响度报告静默不产出。
- **`gives='none'` ≠ 链到头了**，它是"没动那个音频文件"。
- **`convert`/`normalize` 的 `mode` 是 `read`**。
- **`skipped` 要显示成 failed**（前后端各一处）。
- **响应里的 `output` 相对项目根、`relPath` 相对 `outputs/`**，别混。
- **中间文件名带 `task.id`**（2 并发 + 固定名 = WinError 32）。
- **`peak=sample+true`**，不是 `peak=true`。
- **filter 里不能出现 Windows 绝对路径**（`cwd=CACHE` + 只给文件名）。
- **`ebur128` 的 `true_peak` 是累计最大值**，定位爆音要用 astats 的逐帧值。
- **`frac()` 会把越界值夹到边界**，所以画线前必须先判在不在轴范围内。
- **`--dump-matrix` 的对角线不等于 `compile_rules()` 的对角线**（§13）。
- **改 `app.js` 别忘 `ui-kit` 不用管**（应用不读产物），但**改 `ui/*.css` 要重新打包**。
- **`data-close` 不是全局委托**，新模态必须各挂一条 + 登记 `bindFollow`（漏了不报错）。
- **`stableHit` 不能直接开到卡片上**（`layoutRectOf` 的前提不满足）。
- **合成 `DragEvent` 会绕过许可判定**，拖拽必须用真 CDP。

---

## 13. 已知的文档漂移与待修（本次核实）

以下每一条都是 **2026-10-07 实测核对**出来的"代码与注释/文档不一致"。
**本文正文按代码写**；这一节把它们集中列出，便于逐个修。
（未在此列的"未核实"事项见 §11.3 与各节的 ⚠。）

### 13.1 判据层面的不一致（值得先看）

**（1）`--dump-matrix` 的对角线 ≠ `compile_rules()` 的对角线。**

- `boundary.compile_rules()`（**权威矩阵**，`availability` 与测试都用它）
  在 `a == b` 时**强制 `∥`**，并有注释解释"同一种操作连着做两次之间不存在顺序约束"；
- `boundary._dump_matrix()`（CLI）用的是**逐格 `relation(a, b)`**，所以它的对角线是
  `relation(a, a)` 的真实值：

  ```
  relation('probe','probe') = ⇥
  relation('tags','tags')   = ⇉
  relation('cover','cover') = ⤫
  ```

- 而 `boundary.py` 的 docstring 写着"文档 §2.3 那张表必须由它 dump
  （`python -m backend.cards.boundary --dump-matrix`），**不许手抄**"，
  `tests/boundary_check.py:78` 又断言"对角线是 `∥`"。

→ **`--dump-matrix` 的输出不能原样粘进文档**（对角线会被误读成"不能选"）。
两者对同一件事有两套口径，需要择一统一（要么 `_dump_matrix` 也用 `compile_rules()`，
要么在 docstring 里写明对角线以 `compile_rules()` 为准）。

**（2）`--dump-matrix` 需要 `PYTHONIOENCODING=utf-8`。**

四个符号（`∥ ⇉ ⤫ ⇥`）在 GBK 控制台上抛
`UnicodeEncodeError: 'gbk' codec can't encode character '\u21e5'`。
所有会打印这些符号的脚本都得先设 `$env:PYTHONIOENCODING='utf-8'`（`chcp 65001` 不够）。

### 13.2 注释里的陈旧说法

| 位置 | 注释说的 | 实际是 |
|---|---|---|
| `backend/app.py:12` | "ops 12 个 op 的提交入口" | 15 个 op + 2 条链路由 = 17 个端点 |
| `backend/routers/ops.py:1` | "12 个 op 的提交入口" | 同上 |
| `backend/routers/ops.py:173` | "导出响度分析图（PNG）。**图的像素级复刻尚未实现**" | 现在出 **SVG**；`loudness_svg.py` 已实现整页渲染 |
| `backend/cards/specs.py:240-257` | `loudness` 的 preview 写 `ebur128=peak=true` | 实际是 `peak=sample+true`（`specs.py:282` 的 `loudness-image` preview 是对的） |
| `backend/chain.py:473` | "判据 1 用的是 `contract.produces(...) not in HANDOFF`" | `chain` 模块**没有** `HANDOFF` 常量（真名是 `boundary.NO_HANDOFF`），而且当前实现**根本不用它** —— `_parallel_handoff_gap` 只判 `produces(a) == "derived"` |
| `backend/audio.py:998` | "返回的 `summary` 里现在有 9 项" | 下面只列了 8 项（DRP 另行追加 7 个键） |
| `backend/chart_layout.py:48` | "canvas 是 1031.81 x 728.504" | `CANVAS_W` 已派生为 **1331.81**（横轴加宽 240 + 右栏加宽 60 之后） |
| `backend/chart_layout.py:168` | `CONTENT_RIGHT = META_CARD.right  # 1240.0（加宽后）` | 实际值是 **1300.0**（`META_X 1040 + CARD_W 260`）；1240 是右栏还是 200 宽时的旧值。所以内容框是 1280 × 640，不是 1220 |
| `backend/loudness_svg.py:396` | "`viewBox` 永远是设计单位（1031.81 宽）" | 同上，实际 1331.81 |
| `index.html` / `tools/build_ui_kit.py` | 组件库用法见 `ui/README.md`、可视化样例见 `ui/gallery.html` | 这两个文件在 **`ui-kit/`** 下 |
| `README.md:41` | "样式集中在 `ui/`……见 `ui/README.md`" | **死链**，应为 `ui-kit/README.md` |
| `backend/queue.py:324-331` | 描述了"`zip` 可能在建链还没走到它自己那一步之前就被 worker 取走"的竞态 | `chain.build_chain` 是**先全部 INSERT、再统一 submit**，该窗口不存在。（屏障本身仍必要，理由见 `queue.py` 里第一段 ⚠） |
| `backend/queue.py:339` | `anchor = store.chain_started_at(task.chain_id)` | **未被使用的局部变量**（函数后面没有引用它） |
| `backend/store.py:31` / `:1166` | 前者提到 `store.task_settled`；后者说回填条件用 `src_output IS NULL` | 前者**没有这个函数**；后者下一行的 SQL（`:1178`）用的是 `state IN ('pending','running')`（注释里的理由本身是对的，只是与 SQL 不同口径） |
| `backend/routers/task_queue.py:46` | 错误文案"只有失败或已取消的任务可以重试" | `queue.retry_task` 也允许 **`skipped`**（它的 docstring 解释了为什么必须允许） |
| `backend/drp.py:40` | 正文提到"局部连续性用 `WALK_*`" | `WALK_TOL_DR` / `WALK_TOL_SHAPE` **已被删除**（取消延伸时删的） |
| `backend/audio.py:991` | "`I` **不剔除**：它的左端天然偏低是标准行为" | 代码里 `del series[name][:cut]`（`:1063-1064`）对**所有**序列生效（`I` 也在 `series` 里）；之所以不影响指标，是因为 `integrated` 取的是 `I[-1]`（末值） |
| `backend/chain.py` / `SourceAudio` | —— | `SourceAudio.known` 字段**只被赋值、从未被读**（与 `formats.is_known`、`formats.CLASS_LABEL`、`chain.format_token` 一样，属"只有定义、无调用点"） |
| `backend/chart_layout.py:705` | `check()` 覆盖了卡片字段 / `format_time` / RMS 口径 | 那批断言在 `return ok`（`:705`）**之后**、整段被**注释掉**（`:707` 起），**不执行** |
| `tests/dsh-wheel/probe_drp.py:34` | 引用 `drp.WALK_TOL_DR` / `drp.WALK_TOL_SHAPE` | 常量已删 ⇒ 脚本现在 `AttributeError` **跑不通** |
| `tests/dsh-wheel/README.md` | "DRP — current state" 一节写 5 s 窗 / 1 s 步 + 延伸 + `≥3–5 s`；`CACHE_VERSION` 说"bump it when DRP ships"；"F is chosen but not wired" | 实际是 **16 s 窗 / 2 s 步 / 不延伸 / 段落尺度 / `MIN_OCCURRENCES=2`**；`CACHE_VERSION` 已是 **5**；F 轴**已接线**（`chart_axis.py` 就是 F）。README 末尾自己写着 "axis is `backend/chart_axis.py` (F/knee)"，**正文与结尾自相矛盾** |
| `tests/README.md` | 表里列了 `loudness_png_check.py`（36 项）、"链上的「导出响度分析图」真的落 `.png`"、"28 项"；还写了 `demo/` 与 `python demo/_build/verify_demo.py` | `loudness_png_check.py` **文件不存在**（PNG 渲染器已退役）；`loudness_chain_e2e_check.py` 现在真落 **SVG**；`demo/` 目录**不在这个工作区**（`.gitignore` 里有 `demo/`） |

### 13.3 未在本工作区确认的事

- `app.js` 里 `window.__snapReplaceHint`（快照右键"打开卡片库挑一张替换"）**只被赋值、
  未见被读取** —— 可能是遗留的半成品通道；
- `app.js` 的 `init()` 里有一行重复代码（`ae-mode` 的判断连写两遍）——
  无害的重复，可作为"无 lint/无构建"带来的实际例证；
- `backend/toolchain.py` 在 Python 3.14 上的 `PermissionError [WinError 5]`（§3.6）——
  **有 workaround 在测试里，根因未修**；
- `drp.py` 里 `_resolve_conflicts` 的秒换算用的是**硬编码 `* 0.1`**（隐含 10 Hz），
  当前 `EBUR_HZ = 10` 所以成立，换帧率会错 —— 代码里**没有**注释说明这一点；
- `axis_options.py` 那张候选对比表是**在 `top = +1` 下**评估的历史结果，
  文件里明确要求**不要**把它改写成现在的 +0.3（那会把"当初怎么选的"改写成另一次比较）。
  但 `F` 那行的"music band"在不同文件里写作 7.0% / 7.4%，引用时注意口径。

### 13.4 测试自己的卫生问题（实测，不是猜测）

跑一遍 `tests/smoke_api.py` 会在库里留下**一批软删除的垃圾行 + 一批"指向不存在文件"
的派生产物行**，因为它的收尾是：

- `DELETE /api/files/{id}?withDisk=true` —— **不带 `purge`**，所以只软删除
  （`state='deleted'`，行还在，仍占着 `rel_path` 的 `UNIQUE`）；
- 末尾那段 `outputs/` 清理是**无条件删文件**，而**不删对应的 `files` 行** ——
  于是 `origin='derived'` 的行指着已删的产物（§17 / §19 / §20 都这样）。

实测一次运行留下：**25 条软删除的导入行 + 4 条悬空派生行**。
（本次改动顺手把新增的 §21 写成了"显式 `purge=true` + unlink"，所以它自己不制造垃圾；
上面那 4 条来自既有的 §17/§19/§20。）

**为什么平时看不出来**：默认 `AE_FRESH=1` 启动会 `reset_db()` 把整库移进 `.trash/`，
所以这些垃圾下一轮就没了。只有在 `AE_FRESH=0` 调样式/调链的时候才会累积 ——
那时它们会表现为"改名重导入撞 `UNIQUE constraint failed: files.rel_path`"
（`axis_a_check.py` 的 `purge=true` 那个坑就是这个原因）。

**顺带一条**：`prune_trash()` **只在 `wipe_workspace()` 里被调用**（`config.py:369`），
也就是**只有 `AE_FRESH=1` 启动时才会清理回收站**。长期用 `AE_FRESH=0` 调试的话
`.trash/` 会一直涨（本工作区实测 **818 个文件 / 675 MB**，含 Oct 4–5 的多份
数据库快照与 439 个 `.cache` 条目）。这不是 bug，但值得知道：
想回收空间就得**用默认参数起一次服务**，或者手工删 `.trash/`。

---

## 14. 现有文档索引

根目录的 `*.md` **被 `.gitignore` 忽略**（`*.md` + `!README.md`，那个 `!` 不带斜杠，
所以 `README.md` 在**任意层级**都被放行 —— 目前跟踪 4 个：
`README.md` / `tests/README.md` / `tests/dsh-wheel/README.md` / `ui-kit/README.md`）。
它们仍然是有价值的**设计留档**，但**不在版本控制里**：

| 文档 | 行数 | 讲什么 |
|---|---|---|
| `README.md` | 618 | **面向使用者**的总览：功能、界面、运行环境 |
| `执行链并发方案.md` | 2694 | 执行链的**方案**（§2.1 接触面表、§2.2/§2.3 规则与矩阵、§3.x 调度、§9.x 建链与预设、§10.6.5 网点实测）。**本文 §5 的可执行形态就是它** |
| `执行链打包与串行交接方案.md` | 483 | 打包窗口与串行交接（§3 交接规则、§4 打包语义、§4.4 "zip 之后仍可选"） |
| `布局规格.md` | 1400 | 布局与交互规格（含 §13 AM 半调、§14.5 资源管理器取证的实测） |
| `渲染开销优化方案.md` | 1448 | 性能（`bindFollow` 的矩形缓存与抖动分析等） |
| `响度图-SVG-说明.md` | 383 | SVG 路线的说明 |
| `响度图重构-交接.md` | 672 | 响度图重构的交接（有 `check_handover_doc.py` 机械核对） |
| `响度总览图（LoudnessAnalysis）实现构想.md` | 346 | 最初的实现构想（§2.1 有 ebur128 单趟的实测耗时） |
| `音频测量指标设计文档（基于 FFmpeg）.md` | 450 | 测量指标定义（有 `verify_measure_doc.py` 逐条复验） |
| `测量图指标设计.md` | 54 | 图上要显示的指标 |
| `配色方案.md` / `配色方案-落地规格.md` | 20 / 604 | 配色决策与落地 |
| `卡片扩充构想.md` | 670 | 卡片扩容（"从 17 张铺到 72 张"） |
| `网页Trace分析.md` / `3D选项关闭状态下GPU开销问题.md` | 251 / 344 | 性能分析留档 |
| `本地音频工具箱 WebUI 需求总结.md` / `…可选项总结.md` | 202 / 113 | **需求原文**（代码注释里的"需求 §N"指它） |
| `ui-kit/README.md` | 618 | **组件与令牌契约**（组件库的权威说明） |
| `tests/README.md` | 525 | 测试用法与探针的坑 |
| `tests/dsh-wheel/README.md` | 345 | 参考图取证工具链（⚠ DRP 那节已过期，见 §13.2） |

`tests/dsh-wheel/` 另有 24 个脚本（§11.5），共约 4.9k 行。

---

## 15. 规模统计（2026-10-07 核对）

| 部分 | 行数 |
|---|---|
| `backend/**/*.py` | **12041** |
| ├ `store.py` | 1386 |
| ├ `audio.py` | 1454 |
| ├ `tasks.py` | 1227 |
| ├ `chart_layout.py` | 776 |
| ├ `queue.py` | 615 |
| ├ `drp.py` | 571 |
| ├ `chain.py` | 563 |
| ├ `loudness_svg.py` | 850 |
| ├ `config.py` | 420 |
| ├ `cards/`（7 文件，含 `__init__`） | 1973 |
| └ `routers/`（5 文件） | 996 |
| 前端 `app.js` + `api.js` | 5259（4620 + 639） |
| `index.html` + `_snapdrag.html` | 890 |
| `ui/*.css`（10 文件） | 2888 |
| `ui-kit/audioedition-ui.css`（生成物） | 2921 |
| `ui-kit/gallery.html` | 1289 |
| `tests/**` | **17509** |
| ├ `tests/*.py` + `*.js` + `README.md`（45 文件） | 12569 |
| └ `tests/dsh-wheel/`（24 脚本 + README） | 4940 |
| `tools/` | 390 |
| 根目录方案文档（17 个 `*.md`） | 10752 |

其它计数：**op 15 个 / 内置卡片 71 张 / 卡片分类 7 个 / 图标 10 个 /
HTTP 端点 57 个 / 矩阵 225 格（∥59 ⇥110 ⇉46 ⤫10）/ 环境变量 5 个 /
`ui/theme.css` 令牌：主题无关常量 + 6 套 × 21 个**。

# AudioEdition · 本地音频工具箱

一个**跑在本机的音频批处理工作台**：把文件或整个文件夹拖进来，用卡片编排要做的处理，
交给后端队列逐个执行，全程不联网、不上传云端、不改动你的原文件。

面向的是"手上有一堆音频要统一处理"的场景 —— 转格式、改标签、嵌封面、对齐响度、
重命名、校验完整性、导出波形图 —— 这些事用命令行都能做，但记参数、写循环、
逐个确认很烦；这个工具把它们变成点几下的事，并且**每一步都能看见实际执行的命令**。

**MIT 许可 · 零构建 · 零前端依赖 · 全程本地**

---

## 快速开始

```powershell
winget install Gyan.FFmpeg Xiph.FLAC     # 外部工具链（装完重开终端）
git clone https://github.com/coldmentallighter/audio-edition.git
cd audio-edition
.\run.bat                                # Windows：依赖、端口预检、健康检查、开浏览器全自动
```

```bash
./run.sh                                 # Linux / macOS（后端与媒体功能一致，见「八、已知限制与设计取舍」）
```

打开 `http://127.0.0.1:8765`，把文件或整个文件夹拖进去就能用。
想先看看长什么样、又不想导入真实文件：`python tests/seed_demo.py` 会造几个演示文件。

> **注意**：默认每次启动都会**清空工作区**（`uploads/`、`outputs/` 与数据库），
> 页面首帧一定是空的。要保留上一轮就设 `AE_FRESH=0`。

---

## 一、项目特色

### 1. 前端零构建、零依赖

`index.html` + `css/*.css` + `app.js` + `api.js` + `theme.css`，原生 HTML/CSS/JS，
**没有 npm、没有打包器、没有框架**。改完直接刷新页面就生效。
选这条路的理由：本地工具的寿命通常比前端工具链长，不需要为了一个滑块引入 200MB 的 `node_modules`。

代价是所有交互（抽屉三档吸附、拖拽排序、右键菜单、波形绘制）都手写 —— 见"技术栈"一节。

### 2. 执行链：把操作排成流水线

顶栏常驻一条**执行链**。点功能卡片把操作追加进去，链上每一节都可以单独点开只跑这一步，
也可以整条按顺序执行。链上显示的是"歌手 - 标题 → 转 FLAC → 标准化 -16"，一眼看清这次要做什么。

### 3. 功能卡片可自己配，参数由后端给说明书

内置 **69 张卡片**，分 6 个分段（格式转换 23 / 元数据 16 / 峰值 15 / 响度 8 / 封面 5 / 校验打包 2）。
它们全部由 **12 个操作 + 24 个参数**组合而成 —— 扩容靠的是参数预设，不是堆处理器
（见"卡片为什么能铺到 69 张"）。
不满意可以**右键卡片 → 另存为新卡片**，或从「自定义」分段新建：

| 编辑器给你什么 | 说明 |
|---|---|
| 参数表单 | 12 个操作共 24 个参数，每个都有**类型、取值范围、默认值、一句话说明** |
| 条件显隐 | 选 FLAC 时只出现压缩等级，选 MP3 时只出现码率 |
| 等价命令预览 | 实时显示"这张卡等价于哪条 ffmpeg/metaflac 命令" |
| 参数校验 | 越界/非法值在保存时就拦下，报"「FLAC 压缩等级」不能大于 8，收到 99" |

参数规格由 `GET /api/ops` 提供，**和白名单写在同一边**（`backend/cards.py`），
不会出现"前端表单允许的值后端不认"。

### 4. 每个数字都是量出来的，不是估的

配色不是"看着差不多"，而是按 WCAG 相对亮度公式算出来的：3 套色板 × 浅/深 = 6 套主题，
**21 个令牌 × 6 = 126 个值全部有据可查**，对比度全量复算，全站最低 5.00（AA 门槛 4.5）。
规格文档里记录了每一次"为什么不用另一种做法"，包括踩过的坑。

### 5. 自检脚本覆盖到"能证伪"的粒度

不靠肉眼看页面。六套脚本 + 15 个浏览器探针：

- **173 项**后端接口（含路径穿越防护、中文文件名、真实转码产出）
- **36 项**波形 PNG（解出 RGBA 原始像素，逐点统计 alpha 与颜色）
- **93 项**配色（读落盘的 `theme.css` 复算 6 套主题的全部对比度）
- **19 项**卡片扩容（位深的实际 codec / 采样格式 / bits_per_raw_sample、重命名的精确文件名、4K 波形尺寸）
- **12 项**真实拖拽（走 CDP，见下方"为什么拖拽必须用 CDP 验"）

写在文档里的教训包括：`Number(null) === 0` 导致首次打开默认静音、
`metaflac --remove` 留等长 padding 导致缓存不失效、`dragend` 只在拖动源触发、
以及**合成 `DragEvent` 会绕过浏览器许可判定从而放过真 bug**。

### 6. 失败要看得见，不许静默

- 任务失败保留完整 stderr，队列里直接给"重试"
- 后端未连接时页面清空所有数据并提示，**不显示任何假数据**
- 拖拽读不到载荷时明确报错，而不是"松手没反应"
- 浏览器不支持某种格式试听时，说明原因而不是无声失败

### 7. 安全边界靠机制，不靠自觉

外部命令**永远用参数列表**（`subprocess` 不拼 shell 字符串）；
用户永远不能指定输出路径，输出强制落在 `outputs/`；
所有落盘路径过 `safe_join` 收敛，`../`、`..\`、`/abs`、`C:/Windows` 全部被挡住
（有 6 项穿越测试专门验这个）；参数走白名单，越界直接拒绝。

### 8. 卡片为什么能铺到 69 张

因为**一张卡片的成本不在创意，在登记**。这套系统里加一张卡最多要改 4 个地方：
`cards.OPS` 的参数规格、`store.TASK_TYPES` 的任务白名单、`tasks.py` 的处理器 + `app.py` 的路由、
以及前端。只要新卡落在**已有操作**上，后面三层一个字都不用动。

于是扩容走了"预设轴"：**同 op、不同参数组合**。结果是从 17 张铺到 69 张 ——
**操作数仍然是 12，参数类型仍然是 6 种（enum / int / float / bool / text / tags）**。
单 `convert` 一个操作就出了 23 张有明确意图的卡（CD 规格 44.1k/16bit、母带 96k/24bit、
WAV 32bit 浮点、5.1 环绕、8k 提示音素材、FLAC 极限压缩……），
`normalize` 出的是行业标准（EBU R128 广播 -23 / 有声书 -18 / 流媒体 -14 / 俱乐部 -9）。

代价只有 3 处**参数扩展**（不是新能力，是把已有参数接出来）：

| 扩展 | 原来缺什么 |
|---|---|
| `convert.bitDepth` | WAV 的编码器写死 24bit、AIFF 写死 16bit，做不出"CD 规格"和"32bit 浮点" |
| `peaks.force` | 规格里有这个参数，但处理器从不读它 —— 开关是装饰品 |
| `rename` 零填充 | `{tracknumber:02}` 不会被替换，会原样留在文件名里 |

边界也写清楚：`tags` 是把**同一份标签套给所有文件**的"齐一化"操作，
所以"音轨号重排""文件名→标签"这类每文件不同值的想法**做不成预设卡**，
必须开新操作 —— 那是另一条轴（还有 40~50 张的空间），本项目**没有做**。
同样没做的：`waveform` 的"实心面积/叠加曲名"（要 ffmpeg 编进 freetype + 字体路径）、
"只打包 FLAC"（那是前端筛选，不是参数组合）。

---

## 二、功能一览

| 分组 | 能力 |
|---|---|
| **导入** | 拖拽文件**或整个文件夹**（保留目录结构）、多选上传、进度与取消、中文文件名、**资源管理器右键直接导入**（`tools/send_to_ae.py`） |
| **格式转换** | FLAC / WAV / MP3 / AAC / M4A / OGG / Opus / AIFF / WMA，可选采样率、声道数、码率、FLAC 压缩等级、**位深（16/24/32/32f，仅无损容器）**，可保留标签与封面 |
| **元数据** | ffprobe 探测、批量/单个改标签（不重新编码）、按模板重命名（支持 `{tracknumber:02}` 零填充） |
| **封面** | 嵌入（Front/Back/Artist 等 9 种类型）、提取为 jpg、删除；卡片上直接显示封面 |
| **响度** | loudnorm 两遍法标准化，目标 LUFS / 真峰值 / LRA 可调 |
| **波形** | 全曲峰值图（按文件缓存）、**导出 PNG（默认白色波形 + 透明底）**、左右声道分道、log/sqrt 刻度 |
| **试听** | 内置播放器：播放/暂停、**点击波形跳转**、播放指针跟随、音量滑块 |
| **队列** | 2 并发执行、实时进度、失败重试、运行日志（SSE + 轮询双通道） |
| **批量** | 批量转换 / 改标签 / 嵌封面 / 标准化 / 重命名 / 打包 ZIP / **删除** |
| **文件右键** | 转换 / 标准化 / 嵌封面 / 重建峰值 / 按标签重命名 / 完整性校验 / **显示所在目录（在资源管理器里选中工作副本）** / 复制文件路径 / 删除 |
| **其他** | FLAC 完整性校验、打包 ZIP、产物可下载、6 套主题、深浅色切换 |

> **「显示所在目录」打开的是 `uploads/` 里的工作副本，不是导入前的原文件** ——
> 接口只收文件 id，路径由后端从库里算并校验（永远落在 `uploads/` 内），
> 所以前端无法让它去打开任意路径。而 `uploads/` 默认每次启动都会被清空，
> 也就是说这一项的含义是"去取出这份副本"。详见 `布局规格.md` §14。

---

## 三、技术栈

### 后端

| 组件 | 选型 | 说明 |
|---|---|---|
| Web 框架 | **FastAPI** 0.141.1 | 38 条路径 / 45 个端点 |
| ASGI 服务器 | **Uvicorn** 0.54.0 | 仅监听 `127.0.0.1` |
| 数据库 | **SQLite**（Python 内置 `sqlite3`） | 单文件 `audioedition.db`，无 ORM |
| 并发 | 自研有界线程池队列 | 2 worker，`threading` + `queue`，不用 Celery/Redis |
| 进度推送 | **SSE** + 前端轮询兜底 | 见下方"为什么两套" |
| 标签读写 | **mutagen** 1.48.1 + metaflac | mutagen 覆盖通用格式，FLAC 优先走 metaflac |
| 表单解析 | **python-multipart** 0.0.32 | 多文件/文件夹上传 |
| 媒体处理 | **ffmpeg / ffprobe 9.0.2**、**flac / metaflac 1.5.0** | 外部进程，参数列表调用 |

### 前端

| 方面 | 做法 |
|---|---|
| 语言 | 原生 ES2020+，**无框架、无构建、无依赖** |
| 布局 | Flexbox + CSS Grid + 自定义属性（设计令牌） |
| 主题 | `data-theme` × `data-mode` 两个属性切 6 套，21 个令牌 |
| 波形 | 原生 `<canvas>` 绘制，数据来自后端预计算峰值 |
| 播放 | 单个共享 `<audio>` + Web Audio 无关的原生 API |
| 拖拽 | HTML5 Drag & Drop（含 `effectAllowed`/`dropEffect` 协商） |
| 状态 | 无状态库；`FILES` / `TASKS` / `LOGS` 三张表由后端填充 |

### 为什么进度用"SSE + 轮询"两套

SSE 负责"有变化立刻推"，但它对断线、代理、休眠恢复都不够稳；
所以队列 2s、日志 1.5s 各有一个轮询兜底。本地工具这点开销可以忽略，
换来的是**页面永远不会卡在过期状态**。

---

## 四、运行环境构建

### 前置要求

| 项 | 要求 | 检查命令 |
|---|---|---|
| 操作系统 | Windows 10/11（`run.bat`）；Linux / macOS（`run.sh`） | — |
| Python | **3.10+**（实测 3.14.7） | `python --version` |
| ffmpeg / ffprobe | 任意近期版本（实测 9.0.2） | `ffmpeg -version` |
| flac / metaflac | 任意近期版本（实测 1.5.0） | `flac --version` |

Python 依赖（`run.bat` / `run.sh` 会**自动安装**，也可手动装）：

```bash
pip install -r requirements.txt
# 等价于：pip install fastapi "uvicorn[standard]" mutagen python-multipart
```

> `requirements.txt` 里写的是**已验证版本作为下限**（fastapi 0.141.1 /
> uvicorn 0.54.0 / mutagen 1.48.1 / python-multipart 0.0.32），不是硬锁定。
> `uvicorn[standard]` 会带上 `websockets`，自检脚本 `tests/snapshot_drag_real.py` 用它连 CDP。

### 安装媒体工具链

```powershell
# Windows（推荐 winget）
winget install Gyan.FFmpeg Xiph.FLAC
```

```bash
# Debian / Ubuntu
sudo apt install ffmpeg flac
# macOS
brew install ffmpeg flac
```

装完**重开一个终端**让 PATH 生效，然后确认四个命令都能跑：

```powershell
ffmpeg -version; ffprobe -version; flac --version; metaflac --version
```

> 工具链缺失时服务仍能启动，启动横幅会标注哪个不可用，相关功能报错但其余照常。

### 不需要装的东西

不需要 Node.js、npm、数据库服务、Redis、Docker。前端没有构建步骤，SQLite 是 Python 自带的。

---

## 五、运行方法

### 一键启动

**Windows** —— 双击 `run.bat`。它会依次：

1. 检查 Python 与四个媒体工具
2. 缺失依赖时自动 `pip install`
3. **检查端口是否被占用**（被占用会指名占用进程并给出两种解决办法，不会硬撞）
4. 启动服务，并**轮询 `/api/health` 直到返回 200 才打开浏览器**
   （服务没起来就不开，避免把浏览器指向别人的服务）

**Linux / macOS**：

```bash
chmod +x run.sh
./run.sh
```

两种方式都会在服务就绪后打印：

```
[toolchain] [OK  ] ffmpeg    ffmpeg version 9.0.2
[toolchain] [OK  ] flac      flac 1.5.0
[startup] 就绪 → http://127.0.0.1:8765
```

然后浏览器自动打开 `http://127.0.0.1:8765`；按 `Ctrl+C` 停止。

### 手动启动（调试用）

```bash
python -m backend.app
```

### 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `AE_HOST` | `127.0.0.1` | 监听地址。**默认只监听本机**，改成 `0.0.0.0` 会暴露到局域网，请自行确认风险 |
| `AE_PORT` | `8765` | 端口。被占用时换一个：`set AE_PORT=9000`（Windows）/ `AE_PORT=9000 ./run.sh` |
| `AE_FRESH` | `1` | **启动时是否清空工作区**（见下方警告）。想保留上一轮的文件调试，设为 `0` |
| `AE_NO_BROWSER` | 未设置 | 设为 `1` 时启动脚本**不自动打开浏览器**（右键导入用它接管开浏览器的时机） |

> ### ⚠️ 每次启动都会清空工作区
>
> 这是**刻意设计**（需求是"每次打开都是空页面"）：服务启动时会删除
> `uploads/`、`outputs/`、`.cache/` 里的内容并重建数据库。
>
> **你磁盘上的原文件不受影响** —— 导入是**复制**进 `uploads/` 的，删掉的只是工作副本。
> 但如果你在 `uploads/` 里有想留的东西，请先设 `AE_FRESH=0` 再启动。
>
> 想手动清空而不重启：全选文件 → 删除。

### 从资源管理器右键导入（Windows，可选）

在资源管理器里右键一个音频文件、或一个装音频的文件夹，直接送进 AudioEdition，
不用先开网页再拖一遍。桥接脚本是 **`tools/send_to_ae.py`**。

它会：先把选中的路径解析成待导入清单，服务没在跑就拉起来，然后走和网页拖拽
**同一个** `POST /api/upload` 接口把文件投进去。

| 脚本做的事 | 细节 |
|---|---|
| **拉起服务** | 通过项目根目录的 `run.bat` 启动，并给它**自己的控制台窗口** —— 能看到启动横幅、能 `Ctrl+C` 停、出错会 `pause` 住不被吞 |
| **不清空工作区** | 拉起时带 `AE_FRESH=0` —— 上一节那条「每次启动都会清空工作区」的警告，右键导入就是靠这个变量避开的 |
| **谁来开浏览器** | 带 `AE_NO_BROWSER=1` 让 `run.bat` 别开，改由脚本在导入**完成之后**开，避免打开一个还是空的页面 |
| **展开文件夹** | 递归收集，并以**顶层文件夹名**为根重建目录结构（和网页拖文件夹一致） |
| **按扩展名预过滤** | 10 种音频 + 6 种图片（`flac wav mp3 m4a aac ogg opus aiff aif wma` / `jpg jpeg png webp bmp gif`），后端也会再挡一次 |
| **分批上传** | 每批 ≤ **500 个文件**或 ≤ **256MB**，避免撞后端的 `MAX_BATCH_FILES` 和爆内存；单文件上限 4GB |
| **触发探测** | 导入后对 `info` 为空（还没解析过）的文件逐个 `POST /api/files/{id}/probe`，把元数据与响度算上 |
| **失败不静默** | 打印「导入 N，跳过 N，失败 N」，跳过/失败逐条给原因 |

#### 一、装 ContextMenuManager

从 [ContextMenuManager Releases](https://github.com/BluePointLilac/ContextMenuManager/releases)
下载 zip 解压即用（无需安装），然后 **右键 → 以管理员身份运行** ——
改右键菜单是注册表操作，普通权限改不了系统级菜单。

#### 二、新建三个菜单项

左侧选位置，右侧点「新建一个菜单项目」：

| 右键位置 | 左侧选 | 菜单文本 | 菜单命令 | 命令参数 |
|---|---|---|---|---|
| 文件上 | **文件** | `用 AudioEdition 打开` | `"...\pythonw.exe"` | `"...\audio-edition\tools\send_to_ae.py" %1` |
| 文件夹上 | **目录** | 同上 | 同上 | `"...\audio-edition\tools\send_to_ae.py" %1` |
| 文件夹空白处 | **目录背景** | 同上 | 同上 | `"...\audio-edition\tools\send_to_ae.py" "%V"` |

- `%1` = **选中对象**的完整路径（长文件名，带引号），文件和文件夹都用它
- `%V` = **当前文件夹**路径（非选中项），只有"目录背景"场景才用它
- 三处的 `pythonw.exe` 与脚本路径都要写**绝对路径**并加引号（路径里有空格就会断）

> **用 `pythonw.exe` 而不是 `python.exe`**：前者不弹黑框。**代价是脚本自己的输出全丢了** ——
> `pythonw` 没有控制台，`send_to_ae.py` 打印的「导入 N，跳过 N，失败 N」和逐条跳过原因
> 都看不到（`run.bat` 那个窗口里只有**服务**的横幅，不是脚本的）。
> 想看见这些就用 `python.exe` 临时替换 —— 会多一个黑框，但排查时值得。

#### 三、图标（可选）

菜单项上右键 →「更改图标」，指向任意 `.ico` 即可（**仓库没有附带图标文件**）。

#### 四、验证

1. 配好后关掉 ContextMenuManager（菜单实时生效，必要时刷新一下资源管理器）
2. 右键一个 `.flac` → 应看到「用 AudioEdition 打开」
3. 右键一个文件夹、以及文件夹空白处 → 同样应看到
4. 点它：**服务没在跑**时会弹出一个 `run.bat` 控制台窗口（内容是**服务**的启动横幅），
   服务就绪后浏览器自动打开，文件已经在列表里；**服务已经在跑**时不会弹窗，直接导入并开浏览器
5. 判断导入结果看**页面列表**就行；`pythonw` 下脚本不打印任何东西（见上面那条）

#### 五、多选：图形界面做不了，要动注册表

`%1` 只传**第一个**选中项。批量多选要注册表原生的 `%*`，
而 ContextMenuManager 的图形界面**不支持 `%*`**。做法是先用它配好单文件菜单，
再去注册表把该菜单项的 `%1` 改成 `%*`，并给同一项加上 `MultiSelectModel=Player`。

**两边怎么选：**

| 方面 | ContextMenuManager | 直接改 `.reg` |
|---|---|---|
| 多选批量导入 | ❌ 图形界面不支持 `%*` | ✅ 支持 |
| 可视化管理 | ✅ 随时启用/禁用/改图标 | ❌ 得手动编辑注册表 |
| 误操作风险 | 低（有开关，可随时恢复） | 中（改错会影响别的菜单项） |
| 迁移/备份 | ✅ 支持导出导入配置 | 手动备份 `.reg` |

主要用单个文件或单个文件夹 → ContextMenuManager 够用且更好维护；
经常多选批量导入 → 用它配好之后再去注册表补 `%*` 与 `MultiSelectModel=Player`，两边的好处都拿到。

#### 六、常见问题

**双击菜单项没反应？** 先确认 `pythonw.exe` 的路径写对了（用 `python.exe` 临时替换可见错误）。

**Win11 里菜单藏在「显示更多选项」里？** ContextMenuManager 加的项默认落在经典菜单，
要点「显示更多选项」或按 `Shift+F10` 才看得到 —— 这是 Win11 的分层机制，不是配置错了。
想进一级菜单得把它打包成 MSIX / Sparse Package 并实现 `IExplorerCommand`，
对一个本地工具来说不值得，本项目不做。

---

## 六、目录结构

```
audio-edition/
├── run.bat / run.sh          启动脚本（含依赖安装、端口预检、健康检查）
├── requirements.txt          Python 依赖（已验证版本作为下限）
├── LICENSE                   MIT
├── index.html                页面骨架（无构建，直接引用下面几个文件）
├── app.js                    视图与交互（抽屉、波形、播放器、卡片编辑器…）
├── api.js                    API 封装、拖拽导入、右键菜单、轮询
├── theme.css                 6 套主题 × 21 个设计令牌
├── css/                      样式表，按原分节横幅拆分（**顺序不能改**）
│   ├── base.css              契约 + 重置 + 滚动条
│   ├── common.css            通用件：按钮 / 输入 / 徽章 / 进度
│   ├── header.css            顶栏 + 执行链 + 音量
│   ├── layout.css            侧栏 + 工作区
│   ├── filecard.css          文件卡片：波形 / 传输 / 元数据 / 状态
│   ├── drawer.css            抽屉 + 快照条
│   ├── cardlib.css           卡片库：基础 / 光标跟随 / 点击反馈 / 对勾
│   ├── overlays.css          拖拽导入 / 上传进度 / 右键菜单 / 通知 / 模态
│   └── editor.css            卡片编辑器 + 响应式
│
├── backend/
│   ├── app.py                应用骨架：生命周期 / CORS / 路由装配 / 静态挂载 / 入口
│   ├── routers/              接口按域拆分（38 条路径 / 45 个端点）
│   │   ├── system.py         健康检查 / 配置 / 日志 / SSE 事件流
│   │   ├── catalog.py        操作目录、卡片 CRUD、快照
│   │   ├── files.py          上传、文件列表、标签、封面、峰值、下载、批量删除
│   │   ├── task_queue.py     任务队列查询与重试/取消（不叫 tasks，避免与下面撞名）
│   │   └── ops.py            12 个 op 的提交入口 POST /api/ops/<route>
│   ├── cards/                卡片目录按职责拆分
│   │   ├── specs.py          参数规格（前端表单的唯一来源）
│   │   ├── builtin.py        69 张内置卡片
│   │   ├── validate.py       卡片校验 + 命令预览
│   │   └── store.py          自定义卡片与快照的持久化（cards.json）
│   ├── config.py             路径约束、白名单、safe_join、启动清空工作区
│   ├── store.py              SQLite 表结构与查询
│   ├── queue.py              有界并发任务队列
│   ├── tasks.py              12 个任务处理器 + 参数白名单
│   ├── runner.py             subprocess 封装（文本 / 二进制两条路）
│   ├── audio.py              ffmpeg/metaflac/mutagen 音频操作
│   ├── logs.py               运行日志环形缓冲
│   └── toolchain.py          工具链探测与版本
│
├── tools/
│   └── send_to_ae.py         资源管理器右键导入的桥接脚本（见"从资源管理器右键导入"）
│
├── tests/                    自检脚本（见下）
├── uploads/                  导入的工作副本（启动清空）
├── outputs/                  产物：转换结果 / 波形 PNG / ZIP（启动清空）
├── .cache/                   峰值与封面缓存（启动清空）
├── cards.json                自定义卡片与快照（**不清空**，用户配置）
└── audioedition.db           SQLite（启动重建）
```

> **`backend/cards/` 对外 API 与拆分前的 `backend/cards.py` 完全一致** ——
> `__init__.py` 把 21 个公开名字全部重导出，所以 `app.py` 和测试里的 `cards.xxx` 调用一行都没改。
> 依赖是单向的：`specs → builtin → validate → store`，没有循环。

---

## 七、自检

```powershell
# 先起服务（另开一个窗口），再跑：
$env:PYTHONIOENCODING='utf-8'; chcp 65001 | Out-Null

python tests/smoke_api.py           # 173 项：接口、上传、转码、封面、批量删除、卡片 CRUD
python tests/waveform_check.py      #  36 项：波形 PNG 逐像素验证
python tests/theme_check.py         #  93 项：6 套主题对比度与可见度
python tests/axis_a_check.py        #  19 项：位深 / 重命名零填充 / 4K 波形 / 12 op 参数透传
python tests/snapshot_drag_real.py  #  12 项：真实拖拽（CDP）
python tests/halftone_check.py      #  27 项：卡片 hover 的 AM 网点（CDP 截图 + 周期/点径）
python tests/reveal_cmd_check.py    #  30 项：「显示所在目录」拼出的命令行形式（不弹窗）
python tests/palette_regen.py       #  改色板时用来推导新令牌
```

> `halftone_check.py` 是唯一**必须看渲染结果**的脚本：computed style 只能证明 CSS 写对了，
> 证明不了"看起来是网点"，所以它截图后解像素、对亮度做自相关（见 `tests/README.md` §1d）。

浏览器探针（14 个回归 + 1 个级联诊断）覆盖抽屉几何、手势、播放器、音量、封面、
卡片编辑器、配色落地、69 张卡片渲染与 12 个 op 的实际执行等，用法见 `tests/README.md`。

### 为什么拖拽必须用 CDP 验

页面里 `dispatchEvent(new DragEvent('drop'))` 会**直接派发** drop，
绕过浏览器的 `effectAllowed` / `dropEffect` 兼容性判定。
真机上不兼容的组合**根本不派发 drop**（无异常、无日志、松手没反应）——
这个 bug 曾经在合成事件探针全绿的情况下存在于线上。

`tests/snapshot_drag_real.py` 走 CDP 真实管线：
先用 `Input.setInterceptDrags` + 真实鼠标事件让浏览器**真的发起拖拽**，
拿到 `dragstart` 真正写入的载荷，再用它 `Input.dispatchDragEvent` 到落点，
让浏览器自己判定允不允许。

---

## 八、已知限制与设计取舍

| 项 | 现状 | 原因 |
|---|---|---|
| 无用户系统 | 单机单用户 | 定位是本地工具，不是服务 |
| 并发固定 2 | 可改 `config.MAX_CONCURRENCY` | 默认值在"跑满 CPU"和"机器还能用"之间取的折中 |
| 峰值用 8kHz 单声道 | 够画 1000 点概览 | 逐样本扫描对上万文件太慢；要精确请用专业工具 |
| 深色模式"反转填充" | `--fill-*` 在深色下是浅块 + 近黑字 | 实测深底上"实心块可见"与"块上文字可读"无法同时满足 |
| Linux/macOS 只保后端 | 后端与媒体功能一致；`run.sh` 已通过语法检查与端口预检单测，但**未在真实 Linux 上跑过** | 开发机是 Windows；浏览器探针也写死了 Edge 路径 |
| 工作区不持久 | 每次启动清空 | 见第五节警告，需要就设 `AE_FRESH=0` |
| 端口默认 8765 | 占用时脚本会提示换端口 | 不做自动跳端口，避免用户找不到服务 |

---

## 九、文档索引

| 文件 | 内容 |
|---|---|
| `本地音频工具箱 WebUI 需求总结.md` | 原始需求 |
| `本地音频工具箱 WebUI 可选项总结.md` | 可选功能清单与优先级 |
| `布局规格.md` | 布局与交互规格（含抽屉三档几何、拖拽阈值、**A 轴铺卡实测**、踩过的坑） |
| `配色方案.md` | 三套源色板（只读输入） |
| `配色方案-落地规格.md` | 令牌映射推导、对比度验证、变更记录 |
| `卡片扩充构想.md` | 卡片扩容规划：四条轴、69 张的出处、B/C/D 轴未做部分 |
| `tests/README.md` | 自检脚本用法与无头浏览器测量的四个坑 |

---

## 十、许可证

[MIT](LICENSE) © 2026 coldmentallighter

第三方依赖各自的许可（下面这张表是**读本机 `.dist-info` 里落盘的 LICENSE 文件**得到的，
不是照抄文档）：

| 依赖 | 许可 |
|---|---|
| FastAPI、Pydantic | MIT |
| Uvicorn、Starlette、websockets | BSD-3-Clause |
| python-multipart | Apache-2.0 |
| **mutagen** | **GPL-2.0-or-later** |
| ffmpeg / ffprobe、flac / metaflac | 外部程序，由使用者自行安装，本项目只调用其命令行 |

> **关于 mutagen 的 GPL。** 本项目**不打包也不分发** mutagen ——
> 它只是 `pip install -r requirements.txt` 时装进来的运行时依赖，本项目通过公开 API 调用它，
> 属于"聚合"而非衍生作品，所以本项目可以保持 MIT。
> 但如果你要把本项目**再分发成捆绑依赖的二进制包**（PyInstaller 之类），
> 那一步需要你自己确认 GPL 的传染范围。


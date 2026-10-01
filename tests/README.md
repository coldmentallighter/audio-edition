# tests —— 自检脚本

跑一遍就能知道「后端 + 前端」是不是真的在工作，而不是靠肉眼看页面。

> **启动即清空**：`run.bat` / `run.sh` 默认会清掉上一轮的 `uploads/`、`outputs/`、
> `.cache/` 和数据库（需求：「每次打开都是空页面」）。所以自检请**先起服务、再跑测试**，
> 否则你会看到自己的文件被清掉。想保留就设 `AE_FRESH=0`。

## 1. 后端接口（173 项）

```powershell
# 先起服务（另开一个窗口）
run.bat

# 再跑测试
$env:PYTHONIOENCODING='utf-8'; chcp 65001 | Out-Null
python tests/smoke_api.py
```

覆盖：健康检查、卡片目录（每张卡必须带 `op` 和 `ico`）、真实 WAV 上传
（中文文件名/中文子目录）、文件列表、`/api/ops/*` 真跑任务、队列快照、
日志增量拉取、峰值计算（真解码 + 缓存命中）、重试接口、路径穿越防护、
扩展名白名单、软删除后重新导入必须复活、**封面嵌入→显示→缓存→移除**、
**批量删除**（列表与磁盘都要消失）、**功能卡片 CRUD 与参数校验**、
**自定义卡片真跑一次**（参数必须原样传到执行层并产出文件）。

扩容（A 轴）之后新增的一批，都是"证伪型"断言：

| 断言 | 防的是什么 |
|---|---|
| **路由名 = op 名**一致性，且 12 条 `/api/ops/<route>` 都可达 | 卡片执行 404（`op` 写成任务类型名，如 `cover_embed`） |
| **24 个参数逐个透传** | 路由把参数丢掉（`op_convert` 曾静默丢 `bitDepth`） |
| 卡片校验：分类必须在册、`id`/slug 唯一 | 分类写错时静默降级到「自定义」 |
| 前端**不再有兜底卡表** | 前后端两份卡片表各自漂移（13 vs 15） |

测试自己造音频和图片、自己清理（连磁盘一起删，自定义卡片也会删干净），
不会留垃圾。

> 卡片相关断言里有几条是「结构性」的，别删：
> 每个参数都必须有 `label` + `desc`；枚举参数必须有 `options`；
> 数值参数必须有 `min`/`max`。这几条正是"方便用户自己配卡片"的底线 ——
> 少一个，编辑器就会出现一个没法填的空白控件。

## 2. 波形 PNG（36 项）

`tests/waveform_check.py` 单独一份，因为它要**逐像素**验证 PNG：

```powershell
python tests/waveform_check.py
```

验证：默认白色波形 + 透明底（解出 RGBA 原始像素后统计 alpha 分布与颜色）、
纯白是 `#FFFFFF` 而不是 `#FEFEFE`、自定义颜色/背景、左右分道、
参数越界处理、产物取回的路径穿越防护、快照接口。

> **别用"出现次数最多的颜色"判断实底图的波形颜色。** 黑底图上众数色当然是黑。
> 要按目标色去数（脚本里的 `count_near`）。

## 1b. 真实拖拽（12 项，CDP）

```powershell
python tests/snapshot_drag_real.py     # 需要服务在 8765 且已铺数据
```

**拖拽必须用这个验，不能用页面里的合成 DragEvent。**

页面里 `dispatchEvent(new DragEvent('drop'))` 会**直接派发** drop，
绕过浏览器的 `effectAllowed` / `dropEffect` 兼容性判定 ——
而真机上不兼容的组合**根本不派发 drop**（无异常、无日志、松手没反应）。
`browser_snapshot_probe.js` 就因此在功能全坏的时候照样全绿。

本脚本走 CDP 的真实管线：

1. `Input.setInterceptDrags(true)` + 真实 `mousePressed` / `mouseMoved`
   → 浏览器真的发起拖拽，从 `Input.dragIntercepted` 取到 `dragstart` 真正写入的载荷；
2. 用那份载荷 `Input.dispatchDragEvent` 到落点，让浏览器自己判定允不允许。

它覆盖：卡片→快照替换、卡片→＋追加、快照互拖换位，以及
`dragstart/dragover/drop` 三阶段各自的 `effectAllowed` / `dropEffect` 轨迹。

> 依赖 `websockets`（`uvicorn[standard]` 会带上）。会自己拉起一个
> `--remote-debugging-port=9333` 的无头 Edge，跑完自动关掉。

## 1c. 卡片扩容（19 项）

```powershell
python tests/axis_a_check.py     # 需要服务在 8765
```

验的是"卡片库从 17 张铺到 69 张"这件事**真的落地了**，而不是只多了一堆字典条目。
四组：

| 组 | 项数 | 怎么验的 |
|---|---|---|
| `convert.bitDepth` | 9 | 不看命令串，**读产出文件的真实属性**：codec 名、`sample_fmt`、`bits_per_raw_sample`。覆盖 WAV 16/24/32/32f、AIFF 24（必须是 `pcm_s24be`，大端）、FLAC 16/24 |
| 拒绝路径 | 2 | FLAC + 32f、MP3 + bitDepth 必须 `failed` 且报错可读，**不能静默忽略参数** |
| `rename` 零填充 | 5 | 断言 5 个模板渲染出的**精确文件名**（含缺标签时的分隔符残渣清理） |
| waveform 尺寸 | 4 | 3840×2160 / 1170×2532 的输出尺寸 + **PNG 实际像素**，确认没被 `WAVE_MAX_*` 静默夹紧 |
| 参数透传 | 1 | 12 个 op 的代表性参数都能被后端接受（覆盖 24 个参数） |

> **用例要一案一清。** `rename` 会改文件路径，而"改回去"只能靠 `{filename}`
> （它取当前 stem，改不回原名），所以 5 个模板若共用一份文件会互相干扰；
> 更麻烦的是用例 2/3 渲染出**同名目标**（album 缺失要被收掉），
> 第二个只能拿到 `-1` 后缀，断言就不再是"精确名字"。
> 现在每个用例上传全新副本，断言完立刻
> `DELETE /api/files/{id}?purge=true&withDisk=true`。
> **`purge=true` 不能省** —— 软删除的行仍占着 `rel_path`，
> 不真删下一轮改名会撞 `UNIQUE constraint failed: files.rel_path`。

## 1d. 卡片 hover 的 AM 半调（27 项，CDP + 截图解码）

```powershell
python tests/halftone_check.py      # 需要服务在 8765；卡片库是内置的，不用铺数据
```

验证 `#cardSections .fcard` 的**彩色 AM 网点**（近处点大、远处点小，见 `布局规格.md` §13）。

**为什么不能只读 computed style**：那只能证明"CSS 写对了"，证明不了它**看起来**是网点，
更证明不了"近大远小"。所以这个脚本走 CDP：真实鼠标移到卡片上 → `Page.captureScreenshot`
→ ffmpeg 解成 RGBA → 沿点行做**自相关**（周期）与**游程统计**（点径）。

| 组 | 断言 | 实测 |
|---|---|---|
| 结构 | 4 层网点、每层一层 `closest-side`、共用 `10px` 格网且原点都是 `0 0`、点径递增 `18/28/40/54%`、reach 递减 `92/74/56/38%`、未 hover 时全 `opacity: 0` | 全过 |
| 链路 | 真实 `pointermove` 后 `--mx` 被 `bindFollow` 写上 | `60px` |
| 周期 | 自相关：整格 `+0.944` / 两倍格 `+0.834` / 非格距处最大 `+0.036` | ✔ |
| **点径** | 游程长度：光标附近中位 **10** 设备像素（⌀5.4px）、远处 **4**（⌀1.8px） | 比值 **2.5×** |
| 负控 | 鼠标移开（**先断言前提**：`opacity=0` 且不匹配 `:hover`） | `0.000`，那条带峰谷差 `0.0` |
| 颜色 | 点芯色 vs 本主题 ink token | t1 **0.2**、t3 **0.0**；两主题相距 **32.7** |

**为什么量"游程长度"而不是覆盖率**：只调透明度的做法也会让"平均压暗"随距离下降，
覆盖率分不出"点变小了"和"点变淡了"。**点的直径**才是 AM 与纯 alpha 的分水岭。

坑（都写在脚本注释里，别再踩）：

1. **探针自己把远处那几层藏了。** 隔离内容用的 `#cardSections .fcard > *` 也命中了
   承载 3 层网点的 `.fcard__dots`，于是只剩 `.fcard::after` 那一层最大的点，
   量出来像"远处没有点"。要写 `> *:not(.fcard__dots)`。**是探针的错，不是页面的错。**
2. **`content` 不能写在共用规则里。** 它会命中真实元素 `.fcard__dots`，
   给真实元素设 `content` 会按替换元素渲染、背景整个不画。
3. **采样带必须压在点行上。** 落在两行点之间就只扫到点的尾巴，颜色被底色冲淡。
   现在竖直方向搜一条**方差最大**的窄带，不猜相位。
4. **DPR 1 量不出 ink 颜色。** 点太小、几乎没有完全覆盖的像素。必须
   `--force-device-scale-factor=2`。
5. **自相关要先挡掉"几乎是常数"的输入。** 平坦带上理论分母为 0，但 `sum/n` 的浮点残差
   会让它变成"极小数除极小数"，比值任意 —— 实测在 `min==max==215.4` 的平坦带上报出
   **+0.949**。**时灵时不灵**，所以新探针务必**复跑几次**再下结论。
6. **`min` 不能当剖面指标。** 诊断"哪层画出来了"时按窗口取最小亮度，每层都得到点芯亮度、
   看起来一模一样 —— 那个指标对均匀网点是瞎的。

> `Page.captureScreenshot` 的 `clip` 用**页面坐标**，`getBoundingClientRect()` 给的是
> **视口坐标** —— 页面一滚动两者就不相等，得加上 `scrollX/scrollY`。

## 1e. 「显示所在目录」的命令行形式（30 项）

```powershell
python tests/reveal_cmd_check.py    # 不需要服务，也不会弹出资源管理器
```

`explorer` **成功也返回退出码 1**，而参数拼错时它不报错、只是默默打开一个
无关窗口 —— 退出码、HTTP 200、`revealed: true` **三处全绿，功能却完全没做**。
所以这个脚本把 `subprocess.Popen` 与 `sys.platform` 换掉，**只看它收到什么**：

| 组 | 断言 |
|---|---|
| Windows（3 种路径形态 × 7 项） | 只拉起一个进程；收到的是**字符串**而不是列表；精确等于 `explorer /select,"<path>"`；引号恰好 2 个、只包路径；**不含转义引号 `\"`**；没有 `shell=True`；返回 `True` |
| 负对照 | `list2cmdline` 确实会转义出 `\"`、且两种形式确实不同 —— 证明上面的断言**有区分力**，不是恒真的废话 |
| 其它平台 | macOS `open -R <abs>`；Linux 退化为 `xdg-open <父目录>` 且返回 `False` |
| 前提 | `sanitize_name` / `sanitize_relpath` 会清掉 `"`，所以不二次转义是安全的 |

> 3 种路径形态 = ASCII 无空格 / ASCII 带空格 / **中文 + 空格**。
> 把实现改回 `Popen(["explorer", f'/select,"{p}"'])`，这条脚本立刻
> **12 项变红**（实测 18 passed / 12 failed）—— 这就是它存在的理由。

真要验"到底有没有定位并选中"，得用 COM 读**资源管理器自己的**状态
（`LocationURL` + `Document.FocusedItem` / `SelectedItems()`），而且必须带**负对照**：
同目录用**不带** `/select` 的 `explorer <目录>` 打开一次，它的 `FocusedItem`
就是"列表第一项"、`SelectedItems` 恒空 —— 这是"没选中"的指纹，
所以目标文件还要**故意不是第一项**，否则两者分不开。
那套取证会真的弹窗并留下窗口，故不进套件；做法与实测写在 `布局规格.md` §14.5。

## 1f. UI 演示版自检（75 项，CDP）

```powershell
python demo/_build/verify_demo.py    # 自己起静态服务，不需要 8765，不碰真实文件
```

`demo/` 是给"只想看界面"用的演示版（详见 `demo/README.md`）。它跑的是**同一份前端**
（`app.js` / `api.js` / `css` 逐字节拷贝，`_build/provenance.json` 记了 sha），只把后端换成
`mock.js`，所以这个脚本等价于"把真实界面上的交互挨个点一遍"。

覆盖 75 项：首屏结构（9 个文件卡 / 69 张卡片 / 快照 / 队列含失败态 / 日志）、6 套主题、
勾选与批量（含**扛过 2 秒自动刷新**）、批量转换跑完 9 个任务、右键菜单 12 项、
元数据模态 10 字段、抽屉三档（点击 + 拖拽把手）、分组折叠、搜索过滤、卡片编辑器
（参数表单 / 命令预览 / 另存为新卡片）、执行链、试听（含 `currentSrc` 被改写到本机资产）、
拖拽导入、失败重试、清空日志、音量与静音。

它还断言**没有任何 `/api` 请求漏到静态服务器** —— 也就是 mock 的覆盖是完整的。
（这条比"页面上看着对"强：封面 `<img>` 那类最容易漏，见 `demo/README.md` 的坑 2。）

### 这个自检抓出来的两个真 bug（都在 `app.js`，已修）

两个都是同一类：**周期性重绘把"瞬时 UI 状态"吃掉了**。
`api.js` 的 `syncAndProbe` 每 2s 调一次 `reloadFiles()` → `applyServerFiles()` → `renderFiles()`。

| bug | 现象 | 修法 |
|---|---|---|
| `applyServerFiles` 硬写 `checked: false` | 勾选**活不过 2 秒**：点一下 → 批量栏出现 → 2 秒后批量栏自己消失。批量操作实际上只能在 2 秒内完成 | 按 id 继承 `checked`（勾选是"用户的选择"，不是服务端数据） |
| `renderFiles` 不还原 `is-playing` | 播放中每 2 秒高亮消失一次（`box-shadow` 竖条与按钮底色），但音频还在放 | 模板里按 `playingId === f.id` 补上 `is-playing` |

> 修 `is-playing` 前先确认语义：`resetCard()` 才摘掉它，`pauseFile()` **故意留着**
> —— 它表示"当前文件（播放或暂停）"，不是"正在出声"。
> 探针一开始按"暂停后应当没有 is-playing"断言，那是**错的**；正确判据是播放器 `paused`。

### 探针自己的两个坑

1. **无头浏览器遇到 `prompt()` 会把渲染进程主线程整个卡死**（CDP 从此不再回话，
   `Page.javascriptDialogOpening` 是现场证据）。批量转换就用了 `prompt`。
   探针必须先接管 `window.prompt/confirm/alert` 自动应答，否则看起来像"页面卡死"，
   实际上是探针没管对话框。`confirm()` 项目里早就替换过，`prompt()` 是这次才踩到。
2. **探针必须自己捕获异常并**把结果写进 DOM**。探针是一个 async IIFE，
   里面抛一个 `TypeError` 没有任何人接 promise，**整个探针静默中止** ——
   页面主线程好好的，看起来却像"卡住了"。现在每做完一项就 flush 一次
   `#AE_RESULT`，卡住也能读到"卡在哪一项之前"。

> 还踩了一个**探针缓存**的坑：`demo/app.js` 是**拷贝**。改了根目录的 `app.js`
> 之后忘了跑 `make_demo.py`，自检会对着旧副本**一路绿灯**（实测被骗了一次）。
> 现在 `verify_demo.py` 开头就比 sha，不一致直接报错退出。

## 2b. 配色（93 项 + 推导）

```powershell
python tests/theme_check.py     # 读 theme.css 落盘值复算，改了色板必跑
python tests/palette_regen.py   # 从源色板推导 + 打印完整推导过程（排查用）
```

`theme_check.py` 覆盖 **6 套主题 × 21 个令牌齐全性 / 8 组文字对比度 /
白字对错误实心 / 层级可见度 / 错误色与第一色不撞**，并打印全站最低对比度。
阈值分浅色深色两套：文档 §3.4 的层级目标只针对浅色，深色是另一条台阶，
套错阈值会得到假报警（这次就踩了）。

`palette_regen.py` 是"从源色板推导令牌"的过程脚本，改源色时用它算新值。
里面有两条**反直觉但正确**的规则，别顺手"修正"：

1. `--error-solid` 要**先把彩度压到 45% 以内**再降亮度。不然 S=100% 的粉彩
   压暗会得到 `#C400C8` 这种荧光块。
2. `--fill-accent` **不**为"第一色与第二色亮度接近"做硬压。色相差够就能分辨
   （§2 接受 t1 的 1.08），硬压会把 t3 的珊瑚压到近黑。

### `browser_palette_probe.js` 为什么重写过

它原来把期望值写成**十六进制字面量**：

```js
t2AccentIsOldError: t2l.tokens["--fill-accent"] === "#FFE5B4",   // 旧写法
```

于是 `theme.css` 一改色探针就红 —— 但红出来的含义是"你改了颜色"，
不是"有 bug"，等于把主题定值抄了两遍。现在改成**关系断言**，`allOk` 是明确的通过位：

| 断言 | 防的是什么 |
|---|---|
| `*RolesDistinct` | 第一色 / 第二色 / 错误色三者撞车（"第二色没生效"那类 bug） |
| `progressUsesAccent` | 进度条填充必须等于 `--fill-accent` —— 第二色真的落到组件上 |
| `dot{t1,t2,t3}TwoTone` | 切换器色点（`app.css` 里**硬编码**）与 `theme.css` 脱节 |
| `*ErrorIsWarmRed` | 错误色退回奶油 / 黄绿 / 橄榄绿这类"不像出错"的颜色 |
| `t2PrimaryDesaturated` | 绿色降饱和（算 HSL 饱和度，要求 <40，实测 34.7） |
| `bodyBgFollowsTheme` | 主题没作用到 `body` |
| `contrastMeetsAA` | 浏览器算出的对比度低于 4.5 |

**两个曾经的假结果**，都在这一版修掉了：

- 探针**没有冻住过渡**，`bodyBg` 六套主题全返回 `#F4F6F9`（t1 浅色的值）。
  冻住之后六套各自正确 —— 这是项目里第 3 次踩同一个坑。
- 色点读的是 `background-color`，但 `.dot--t*` 用的是 `linear-gradient`，
  所以六套主题全得到 `#000000`。要读 `background-image`。

## 3. 前端（浏览器内）

探针清单见下表，结果以 JSON 写进页面末尾的 `<pre id="AE_RESULT">`。

| 探针 | 验证什么 | 需要先铺数据 |
|---|---|---|
| `browser_drawer_probe.js` | 抽屉三档几何 + 点击/拖拽手势 + 搜索唤出 | 不需要 |
| `browser_e2e_probe.js` | 全选/反选、加链、执行链打真后端、队列与日志 | 不需要 |
| `browser_empty_probe.js` | **首帧必须是空页面**、不能残留假计数 | 不需要 |
| `browser_cover_bulk_probe.js` | 封面显示 / 导入接线 / 取消不卡按钮 / 批量删除 | `python tests/seed_demo.py` |
| `browser_player_probe.js` | 播放/暂停、**点击波形跳转**、指针位置、切歌复位 | `python tests/seed_demo.py` |
| `browser_volume_probe.js` | 音量滑块位置/拖动/静音/自动解除静音/持久化 | 不需要 |
| `browser_volume_theme_probe.js` | 滑块在 6 套主题下的可辨识度（对比度表） | 不需要 |
| `browser_card_geometry_probe.js` | 卡片 160px 没被撑坏、波形区不被压扁 | 任意有文件的库 |
| `browser_card_editor_probe.js` | 右键编辑 / 另存为 / 新建 / 参数说明 / 服务端校验 | 不需要 |
| `browser_card_exec_probe.js` | **69 张内置卡全部渲染 + 12 个 op 各挑一张真执行 + 封面嵌入/提取/删除三步全成功**（任一 404 即红） | 任意有文件的库（含至少一个 flac/mp3/m4a/wma） |
| `browser_snapshot_probe.js` | 快照拖拽**逻辑分支**（替换/互换/追加/恢复默认） | 不需要 |
| `browser_align_probe.js` | 抽屉与内容框左右对齐（各差 6px，对称） | 任意有文件的库 |
| `browser_palette_probe.js` | 6 套主题的令牌是否**真的落到组件上**（关系断言，不是抄十六进制） | 不需要 |
| `browser_accent_visibility_probe.js` | 第二颜色在**默认状态**下是否看得见 | 任意有文件的库 |
| `browser_cascade_probe.js` | 诊断用：某个底色到底从哪条规则继承来的（不判定通过/失败） | 任意有文件的库 |

> **`browser_card_exec_probe.js` 每个 op 提交完会等任务跑完**（轮询 `/api/tasks/{id}` 到终态）
> 再提交下一个。不等的话 `cover`（嵌入）与 `extract-cover` 会作为两个**并发**任务
> （队列 2 worker）同时动同一个文件：embed 正在重写封面时 extract 去读，会读到
> "该文件没有内嵌封面"而失败。那是探针自己造出来的竞态，不是页面的问题 ——
> 实测确实这样翻过一次车（`allOk=false`，而页面完全正常）。
| `browser_cascade_probe.js` | 诊断用：某个底色到底从哪条规则继承来的（不判定通过/失败） | 任意有文件的库 |

播放器探针要加 `--autoplay-policy=no-user-gesture-required`：

```powershell
& $edge --headless=new --disable-gpu --no-sandbox --window-size=1406,927 `
  --autoplay-policy=no-user-gesture-required --virtual-time-budget=35000 `
  --user-data-dir="$PWD\_edgep" --dump-dom "http://127.0.0.1:8765/_t.html" > _t.out.html
```

> **无头没有音频设备**：`paused=false` 但音频时钟不推进，`clockAdvances` 通常是
> `false`。所以播放器探针断言的是**确定性**部分（跳转位置 = 指针百分比），
> 「指针随播放实时移动」只能在有声卡的机器上肉眼确认。

```powershell
# 造封面和待删文件（封面探针用）
python tests/seed_demo.py

# 生成临时页（把 SSE 断掉，否则无头浏览器永远不会 idle，拿不到结果）
$html = Get-Content index.html -Raw -Encoding UTF8
$stub = "<script>window.EventSource = class { constructor(){} addEventListener(){} removeEventListener(){} close(){} };</script>"
$html = $html -replace '(?s)<script src="app\.js">', ($stub + "`r`n<script src=`"app.js`">")
$html = $html -replace '(?s)</body>', ('<script src="tests/browser_empty_probe.js"></script>' + "`r`n</body>")
Set-Content _t.html -Value $html -Encoding UTF8

& "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" --headless=new `
  --disable-gpu --no-sandbox --window-size=1406,927 --virtual-time-budget=25000 `
  --user-data-dir="$PWD\_edgep" --dump-dom "http://127.0.0.1:8765/_t.html" > _t.out.html

Select-String -Path _t.out.html -Pattern 'AE_RESULT' -Context 0,200
```

### 四个必踩的坑

1. **抽屉把手由 `pointer*` 驱动，不是 `click`。**
   用 `element.click()` 合成点击不会触发任何逻辑，会误判成「点了没反应」。
   探针里用 `dispatchEvent(new PointerEvent(...))` 发完整的
   `pointerdown → pointermove → pointerup`。

2. **无头浏览器读过渡属性会拿到过渡前的值。**
   探针一开始就注入 `*{transition:none!important}`，否则量出来的几何是旧值。
   **配色探针尤其必须先冻住过渡**：`--virtual-time-budget` 不推进 CSS 过渡，
   切主题后带 `transition: background` 的元素会**一直**返回旧颜色，
   看起来像"样式没生效 / 层叠被覆盖"，实际只是没走完过渡。
   （这个坑已经踩了三次，`browser_accent_visibility_probe.js` 就是为它写的。）

3. **`confirm()` 在无头里默认返回 false**，批量删除会走不到删除分支。
   封面/批量探针里把 `window.confirm` 换成 `() => true`，只替换确认框，
   其余全是页面上真实的代码路径。

4. **探针自己会假通过：目标选错 + 字段读错 + 统计到历史任务。**
   `browser_card_exec_probe.js` 曾经固定拿 `FILES[0]` 当执行目标，
   而种子数据第一个是 PNG —— 「嵌入封面」对 PNG 是**正确地拒绝**，
   于是"嵌封面真能成功"这条路径从没被走到，探针却 `allOk: true`。
   三件事要一起做对：

   - **目标要挑对**：封面类操作优先 flac/mp3/m4a/wma，其余选音频而非图片。
     图片判定别只看扩展名 —— ffprobe 把 PNG 报成 **`png_pipe`**，得连前缀一起匹配。
   - **字段要读对**：前端文件对象是 **`title` + `format`，没有 `name`**
     （`name` 只出现在上传响应里）。读错字段时过滤条件全部落空，
     挑出来的目标会静默变成 `undefined`。
   - **只统计本次新增的任务**：`/api/tasks/queue` 的 `recent` 里混着上一次运行和
     种子脚本留下的任务，直接统计会把旧失败算到这一次头上。
     先记下已有 task id，结束时只看新增的那几条。

### 已验证的档位几何（视口 1372×749）

| 档位 | drawer top | drawer bottom | 高度 | 圆角 | 搜索栏在视口内 |
|---|---|---|---|---|---|
| `closed` | 676 | 749 | 73 | 20/20/0/0 | 否（面板整体在边缘之下） |
| `mid` | 243 | 735 | 492 | 20 | 是 |
| `full` | 0 | 749 | 749 | 0 | 是 |

水平方向恒为 `296 → 1358`，即侧栏右缘 282 + 14px 留白。

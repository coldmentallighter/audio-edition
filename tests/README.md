# tests —— 自检脚本

跑一遍就能知道「后端 + 前端」是不是真的在工作，而不是靠肉眼看页面。

> **启动即清空**：`run.bat` / `run.sh` 默认会清掉上一轮的 `uploads/`、`outputs/`、
> `.cache/` 和数据库（需求：「每次打开都是空页面」）。所以自检请**先起服务、再跑测试**，
> 否则你会看到自己的文件被清掉。想保留就设 `AE_FRESH=0`。

## 1. 后端接口（163 项）

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

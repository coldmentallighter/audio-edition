# AudioEdition UI Kit · 组件库

AudioEdition 的全部样式，按**组件**组织、带用法契约。
纯 CSS：**零构建、零依赖、零 npm**。

这个目录是**打包产物**，可以整个拿走直接用；样式表的**源**在上一级的 `ui/`。

```
ui-kit/
├── audioedition-ui.css   单文件（10 个样式表按加载顺序拼好）—— 一般引这一个就够
├── README.md             本文件：契约与踩坑
└── gallery.html          组件画廊：每个组件 × 6 套主题
```

```html
<html data-theme="t1">                  <!-- 浅色 -->
<html data-theme="t1" data-mode="dark"> <!-- 深色 -->
```

> 本文件讲**契约与坑**，[`gallery.html`](gallery.html) 讲**长什么样**。两边一起看。

---

## 一、快速开始

### 用打包好的单文件（推荐）

```html
<link rel="stylesheet" href="audioedition-ui.css">
```

一个请求、没有顺序问题 —— 加载顺序已经烘死在文件里。
`gallery.html` 就是这么引的，所以它同时是这份产物能不能用的现场验证。

### 或者引 10 个源文件

需要按文件覆写、或想顺着源调试时用这种；**顺序不能改**（原因见 §2）：

```html
<link rel="stylesheet" href="ui/theme.css">   <!-- 1. 令牌层，必须第一 -->
<link rel="stylesheet" href="ui/base.css">    <!-- 2. 重置与全局 -->
<link rel="stylesheet" href="ui/common.css">  <!-- 3. 基础件 -->
<link rel="stylesheet" href="ui/header.css">
<link rel="stylesheet" href="ui/layout.css">
<link rel="stylesheet" href="ui/filecard.css">
<link rel="stylesheet" href="ui/drawer.css">
<link rel="stylesheet" href="ui/cardlib.css">
<link rel="stylesheet" href="ui/overlays.css">
<link rel="stylesheet" href="ui/editor.css"> <!-- 10. 最后一个，会覆盖前面 -->
```

应用本身（`index.html` / `_snapdrag.html`）走的就是这一种 —— 它**不读**打包产物，
所以"改了样式忘了重新打包"不会影响应用，只会让 `ui-kit/` 悄悄过期。
为此有两道闸：

```bash
python tools/build_ui_kit.py    # 重新打包
python tests/ui_check.py        # 逐字节校验产物与源一致（忘了打包会红）
```

### 主题

6 套 = 3 主题 × 2 模式，由 `<html>` 上两个属性决定：

| 属性 | 取值 | 说明 |
|---|---|---|
| `data-theme` | `t1` / `t2` / `t3` | t1 蓝橙 · t2 绿橙 · t3 紫珊瑚 |
| `data-mode` | 省略（浅色）/ `dark` | 省略即浅色 |

切换主题时 `app.js` 会临时给 `<html>` 挂 `data-theme-switch`，
并在 `data-vt-dir` 上写 `sweep` / `expand` / `contract` 选过渡方向。见 §6.7。

### 不是一个 npm 包

这是刻意的：本项目的寿命目标是长过前端工具链（见根 `README.md` §1）。
所以没有 `package.json`、没有 npm 打包器、没有 `@import` 汇总入口
（`@import` 会引入串行请求，而且顺序会分散到两处）。

"汇总入口"这件事由 `ui-kit/audioedition-ui.css` 这个**纯拼接产物**承担：
零依赖、一个 `tools/build_ui_kit.py` 就能重生成。
**顺序的唯一事实来源**是那个脚本的 `ORDER` ——
它生成产物时要用，`tests/ui_check.py` 直接 import 它，所以不存在第三份清单。

---

## 二、加载顺序（硬约束）

### 每个文件的职责

| # | 文件 | 负责 |
|---|---|---|
| 1 | `theme.css` | **令牌层**：主题无关常量 + 6 套主题 × 21 个令牌。不含任何选择器 |
| 2 | `base.css` | `box-sizing`、`html/body`、`:focus-visible`、滚动条、视图过渡动画、主题切换静音 |
| 3 | `common.css` | 基础件：`.btn` `.input` `.badge` `.dot` |
| 4 | `header.css` | `.header` `.brand` `.chainbar` `.switchgroup`/`.seg` `.engine` `.empty` `.vol__*` `#fxToggle` |
| 5 | `layout.css` | 应用骨架：`.body` `.sidebar` `.pane` `.splitter` `.task` `.progress` `.log` `.workspace` `.selbar` `.bulkbar` `.check` `.filelist` |
| 6 | `filecard.css` | 文件卡片：`.card` `.wave` `.transport` `.kv` `.metacard` `.cover` `.metabox` `.statuscol` |
| 7 | `drawer.css` | 抽屉：`.dock` `.drawer` `.snaps`/`.snap` `.cardsec` `.preset__*` `.chainedit`/`.cedit` `.iconpick--preset` |
| 8 | `cardlib.css` | 卡片库：`.fcard` `.pcard` + 光标跟随/点击反馈/对勾；**以及** `.drawer__search`/`.searchbtn`/`.searchfield` 与 `.chain` |
| 9 | `overlays.css` | 浮层：`.dropzone` `.uploadbar` `.ctxmenu` `.notice` `.modal`/`.field` `.fcard--custom` |
| 10 | `editor.css` | 卡片编辑器：`.cardedit` `.pspec` `.iconpick__btn` `.cmdpreview` + **响应式断点** |

> 单文件产物 `audioedition-ui.css` 里，这 10 个部分各有一条分隔注释
> （`/* ==================== ui/cardlib.css ==================== */`），
> 所以下面提到文件名时，在产物里按那条注释找同一段即可。

### 为什么顺序不能改

层叠（cascade）只看**声明出现的先后**，而这份样式里有几处是
「后来者按同特异性或更高特异性覆盖前面」的。打乱顺序 = 静默走样，不报错。

具体陷阱（都是踩过的，改之前先读这几条）：

1. **`editor.css` 是最后一个，它会覆盖前面的同特异性规则。**
   - `.btn--danger` 在 `common.css` 有一份，`editor.css` 末尾**又写了一份**
     （`color: var(--ink-error)` + `:hover{background: var(--error-tint)}`）。
     所以最终外观由 `editor.css` 决定 —— 想改 `.btn--danger` 得改那一份。
   - `.modal__box--wide`（`editor.css`）改的是 `overlays.css` 的 `.modal__box`。
   - `@media (max-width:1400px)`（`editor.css` 末尾）改的是 **`filecard.css` 与
     `drawer.css` 的组件**：`.metacard` `.cover` `.wave` `.snaps`。
     找响应式规则时别只翻自己那个文件。

2. **`.iconpick` 这个类名只有一个主人。**
   `editor.css` 里那条必须写成 `#cardIcons.iconpick`（只给卡片编辑器那个容器）。
   预设模态的容器也带 `.iconpick`（`#presetIcons.iconpick.iconpick--preset`），
   若 `editor.css` 写裸 `.iconpick { display:flex }`，同特异性下**后加载的赢**，
   会把 `drawer.css` 给预设写的 `display:grid` 悄悄压掉 ——
   表现是「格子算出来了但还是 flex 换行」。

3. **`drawer.css` 在 `editor.css` 之前，所以「自动」态必须靠特异性取胜。**
   `.iconpick--preset[data-auto="1"] .iconpick__btn[aria-checked="true"]` 要压过
   `editor.css` 的 `.iconpick__btn[aria-checked="true"]`。同分看顺序、而顺序对它不利，
   所以它多带了一个 `.iconpick--preset` 前缀把特异性抬到 (0,2,0)。

4. **`theme.css` 必须第一**，否则令牌在用到它们的规则之后才定义
   （自定义属性不参与层叠顺序，但可读性与工具脚本都依赖它排在最前）。

> 想确认自己没搞坏：`python tests/ui_check.py`（顺序 + 未定义令牌）。

---

## 三、令牌层（`theme.css`）

### 3.1 三组配对契约 —— 最容易在深色模式下搞错的地方

| 底色族 | 配的文字色 | 用途 |
|---|---|---|
| `--bg-app` `--bg-surface` `--bg-surface-alt` | **`--ink`** | 页面底 / 面 / 内嵌槽 |
| `--tint-primary` `--tint-accent` `--error-tint` | **`--ink`** | 软状态色（选中、运行中、出错底） |
| `--fill-primary` `--fill-accent` | **`--on-fill`** | 强色块（主按钮、批量栏、链上标签） |

**为什么不能混**：深色模式下 `--fill-*` 会**反转成浅色底**，
`--on-fill` 因此是**近黑色**（`#10171C`）。此时若在 `--fill-*` 上用 `--ink`
（深色模式下是浅色），就是浅字压浅底 —— 实测只有 1.9:1，基本读不出来。
反过来，在 `--bg-*` / `--tint-*` 上用 `--on-fill` 同理会得到深字压深底。

所以规则只有一条：**先看底是 `--fill-*` 还是 `--bg-*`/`--tint-*`，再决定用 `--on-fill` 还是 `--ink`。**
代码里多处写了「必须显式声明 `color`」的注释，都是同一个原因：
省略 `color` 会让它从父级继承，而父级可能正是另一种底。

### 3.2 其余令牌

| 令牌 | 契约 |
|---|---|
| `--ink-error` | **文字色**。配 `--error-tint` 底用。别写成 `--err-ink`（不存在） |
| `--error-solid` | 实心错误块，**保证白字可用**（`#fff` 压它 ≥ 4.5:1） |
| `--border` / `--border-strong` | 描边。空气感标尺下**层级主要靠描边与投影表达**（背景三层对比度只有 1.07–1.09） |
| `--shadow-sm` / `--shadow-md` | 投影两档 |
| `--scroll-track` / `--scroll-thumb` | 滚动条 |
| `--text-muted` | 次要文字。全站实测最低 2.55，**只用在 ≥11px 的非关键信息上** |
| `--ink-accent` | 目前**只有后端**用（`backend/loudness_svg.py` 的 −23 参考线） |
| `--text-disabled` | 配色契约的保留位，当前无引用 |

### 3.3 与主题无关的常量（`:root`，不在主题块里）

字号、圆角、间距、层级，以及**彩色半调（AM 网点）**的全部参数：

```
--font-sans  --fs-body
--radius-workspace(32)  --radius-card(20)  --radius-cover(15)  --radius-inner(10)
--gap-card(23)  --gap-col(29)
--header-h(104)  --sidebar-w(234)  --drawer-handle(12)  --z-notice(60)
--spot-cell  --spot-dot-1..4  --spot-reach-1..4  --spot-a1..a3  --spot-alt-a
--spot-mix-base  --spot-mix-alt
```

`--spot-*` 是卡片 hover 那套「近处点大、远处点小」的网点的调参面，
原理见 `cardlib.css` 顶部注释与 `渲染开销优化方案.md`。

### 3.4 约束：每个主题块必须**恰好** 21 个令牌

`tests/theme_check.py` 会校验每个 `:root[data-theme="tN"]` 块
**既不少也不多**（`REQUIRED` 列表 + `extra` 检查）。

- 要**新增**主题令牌，得同时改 `tests/theme_check.py` 的 `REQUIRED`。
- 想加**与主题无关**的东西，加进 `:root`（解析器不覆盖那个块，安全）。

---

## 四、组件清单

`类` 一列只列主要入口，元素与修饰符见 §5 的用法。

### 基础件（`common.css`）

| 组件 | 类 | 说明 |
|---|---|---|
| 按钮 | `.btn` | 修饰符 `--primary` `--danger` `--ghost` `--sm` `--xs` `--icon`；另有 `.btn--run`（`header.css`） |
| 输入框 | `.input` | 修饰符 `--search`（带 focus 环） |
| 徽章 | `.badge` | 修饰符 `--run` `--done` `--failed` `--idle` |
| 主题色点 | `.dot` | `--t1` `--t2` `--t3`。**颜色是硬编码的**，见 §6.2 |

### 布局骨架（`layout.css`）

| 组件 | 类 | 说明 |
|---|---|---|
| 侧栏 | `.sidebar` `.pane` | `.pane--queue` / `.pane--log`；`.pane__body` 是滚动容器 |
| 分隔条 | `.splitter` | 队列/日志之间的拖拽条 |
| 任务项 | `.task` | `--running` `--failed`；含 `.task__out`（产物入口） |
| 进度条 | `.progress` | 内层 `<i>` 的宽度由 JS 写 |
| 日志行 | `.log` | `--ok` `--err` |
| 选择条 | `.selbar` | 全选 / 反选 / 计数 |
| 批量栏 | `.bulkbar` | 强色块，内含 `.btn--sm` 的**作用域覆盖** |
| 复选框 | `.check` | 结构固定：`input` + `span`（勾用 `::after` 画） |
| 文件列表 | `.filelist` | 滚动容器 |

### 顶栏（`header.css`）

| 组件 | 类 | 说明 |
|---|---|---|
| 顶栏 | `.header` `.brand` | `.brand__mark` `__title` `__sub` |
| 执行链栏 | `.chainbar` | `.chainbar__label` `__acts` `__vol`；`.chain__notes` 是「接不上」提示 |
| 分段开关 | `.switchgroup` > `.seg` | 选中态走 `aria-pressed="true"` |
| 引擎信息 | `.engine` | `.engine__k` `__v` |
| 空状态 | `.empty` | `--sm` 给侧栏窄版 |
| 音量 | `.chainbar__vol` `.vol__btn` `.vol__range` `.vol__val` | 已填充部分靠 `--vol-pct` 画在背景上；`is-muted` 走错误色 |
| 3D 跟随开关 | `#fxToggle` | 关闭态靠 `aria-pressed="false"` + 一条斜杠伪元素 |

### 文件卡片（`filecard.css`）

| 组件 | 类 | 说明 |
|---|---|---|
| 卡片 | `.card` | 状态：`is-focused` `is-checked` `is-playing` |
| 波形 | `.wave` | `.wave__canvasWrap` `__canvas` `__legend` `__play` |
| 试听传输 | `.transport` | `.transport__btn` `__time` |
| 键值对 | `.kv` | `.kv__k` `__v` |
| 元数据卡 | `.metacard` `.metabox` | 固定 288px 宽（三列宽度必须锁定） |
| 封面 | `.cover` | `--has` 有图；`.cover__img` `__ph` `__ovl` `__act`（`--danger`） |
| 状态列 | `.statuscol` | `.statuscol__prog` `__info` `__act` |

### 抽屉与卡片库（`drawer.css` + `cardlib.css`）

| 组件 | 类 | 定义在 | 说明 |
|---|---|---|---|
| 抽屉 | `.dock` `.drawer` `.drawer-backdrop` | drawer.css | 档位由 `data-stop`（`closed`/`mid`/`full`）+ `data-open` 决定 |
| 快照条 | `.snaps` `.snap` | drawer.css | `is-added` `--add` `--drop`；`draggable` 时换抓取光标 |
| 分类段 | `.cardsec` | drawer.css | `data-collapsed`；标题可折叠，靠 `grid-template-rows` 过渡 |
| 功能卡片 | `.fcard` | cardlib.css | `--new` `--blocked` `--custom`（后者在 `overlays.css`）；`is-added` |
| 预设卡片 | `.pcard` | cardlib.css | `is-unknown`；`.pcard__warn` `__icons` `__ico` |
| 光标跟随 | `.fcard__dots` `.pcard__dots` | cardlib.css | **必须是卡片的直接子元素**（选择器写的是 `>`） |
| 搜索 | `.drawer__search` `.searchbtn` `.searchfield` | **cardlib.css** | 见 §6.3 |
| 执行链标签 | `.chain` `.chain__item` | **cardlib.css** | 容器已移到顶栏，这里只留外观 |
| 预设网格 | `.preset__grid` `.preset__empty` `__meta` `__list` | drawer.css | |
| 链编辑器 | `.chainedit` `.cedit` | drawer.css | `--unknown` |
| 图标选择器 | `.iconpick__btn` `.iconpick__acts` | editor.css + drawer.css | 见 §2 的 2/3 条 |

### 浮层（`overlays.css`）

| 组件 | 类 | 说明 |
|---|---|---|
| 拖拽导入 | `.dropzone` | `data-over="true"` 是悬停态 |
| 上传进度 | `.uploadbar` | 毛玻璃，固定在右下 |
| 右键菜单 | `.ctxmenu` | `__head` `__tag` `__item`（`--danger`） `__sep` |
| 通知 | `.notice` | `is-open`；`data-kind="error"` |
| 模态 | `.modal` | `is-open` + `is-settled`（落定后才开毛玻璃）；`.modal__scrim` `__box` `__title` `__sub` `__form` `__acts` |
| 表单字段 | `.field` | `--wide` 跨两列 |

### 编辑器（`editor.css`）

| 组件 | 类 | 说明 |
|---|---|---|
| 编辑器外壳 | `.cardedit` | `__col` `__base` `__h` `__hint` `__opsdesc` `__params` `__note` `__err` `__spacer` |
| 参数行 | `.pspec` | `--off`；`__label` `__ctl` `__desc` `__na` `__check` |
| 命令预览 | `.cmdpreview` | 等宽、可换行 |

---

## 五、基础件用法

```html
<!-- 按钮 -->
<button class="btn">默认</button>
<button class="btn btn--primary">主操作</button>
<button class="btn btn--danger">删除</button>
<button class="btn btn--ghost">无底色</button>
<button class="btn btn--sm">小</button>
<button class="btn btn--xs">更小</button>
<button class="btn btn--icon" aria-label="设置"><svg>…</svg></button>
<a class="btn btn--xs" href="/api/outputs/x.flac" download>下载</a>

<!-- 输入 -->
<input class="input" placeholder="标签值">
<input class="input input--search" type="search" placeholder="搜索卡片">

<!-- 徽章 -->
<span class="badge">待处理</span>
<span class="badge badge--run">运行中</span>
<span class="badge badge--failed">失败</span>

<!-- 主题色点（切换器里用） -->
<span class="dot dot--t1"></span>

<!-- 复选框：结构固定，勾是 ::after 画的，不要塞自己的图标 -->
<label class="check">
  <input type="checkbox"><span></span>
</label>

<!-- 进度条：宽度写在 <i> 的行内样式上 -->
<div class="progress"><i style="width:42%"></i></div>

<!-- 分段开关：选中态用 aria-pressed，不要自己加 .is-active -->
<div class="switchgroup">
  <button class="seg" aria-pressed="true">串行</button>
  <button class="seg" aria-pressed="false">并行</button>
</div>

<!-- 表单字段 -->
<div class="modal__form">
  <label class="field"><span>名称</span><input class="input"></label>
  <label class="field field--wide"><span>描述</span><input class="input"></label>
</div>

<!-- 空状态 -->
<div class="empty">
  <svg>…</svg><strong>还没有文件</strong><span>把音频拖到这里</span>
</div>

<!-- 键值对 -->
<div class="kv"><span class="kv__k">时长</span><span class="kv__v">3:42</span></div>

<!-- 图标选择器（role=radio + aria-checked，样式认的是 aria） -->
<div class="iconpick" role="radiogroup">
  <button class="iconpick__btn" role="radio" aria-checked="true"><svg>…</svg></button>
</div>
```

**认 `aria-*` 而不是自造类名**的组件（可访问性与样式同一个事实来源）：

| 状态 | 写在哪 |
|---|---|
| 分段开关选中 | `.seg[aria-pressed="true"]` |
| 图标选择器选中 | `.iconpick__btn[aria-checked="true"]` |
| 3D 跟随开关 | `#fxToggle[aria-pressed="false"]` |
| 静音 | `.vol__btn.is-muted`（这个反而是类） |

---

## 六、改动须知

### 6.1 JS 契约：这些状态不由样式自己决定

下面的类 / 属性由 `app.js` 加摘，**删样式前先看有没有 JS 在写它**：

| 钩子 | 谁写 | 作用 |
|---|---|---|
| `html[data-fx="off"]` | 3D 开关 | 关掉跟随：不给图标叠毛玻璃、不给跟随动画 |
| `html[data-theme-switch]` | `switchTheme()` | 主题切换期间静音**全部 transition**（见 §6.7） |
| `html[data-vt-dir="sweep"\|"expand"\|"contract"]` | `switchTheme()` | 选视图过渡方向 |
| `--nx` `--ny` | `bindFollow()` | 归一化指针位置（−1..1），继承给整个子树 |
| `--mx` `--my` | `bindFollow()` | 指针像素位置，给网点与光晕做径向遮罩原点 |
| `--vt-d` `--vt-r` | `switchTheme()` | 平扫的斜线偏移量 / 径向半径 |
| `--drawer-top` `--drawer-box` | 抽屉档位逻辑 | 盒顶与盒高 |
| `--drawer-left` | `app.js:1402` | 左边以**左侧栏实际渲染宽度**为准（不是 `--sidebar-w`） |
| `--vol-pct` | 音量逻辑 | range 的已填充比例 |
| `is-open` / `is-settled` | `openModal` / `closeModal` | 模态动效两阶段 |
| `is-returning` | `clearFollow()` | **释放动画**：指针离开时给这一次"回正"开过渡。只在 380ms 窗口内有效，指针再进来立刻摘掉 —— 见 §6.9 |
| `is-focused` `is-checked` `is-playing` | 卡片交互 | 文件卡片三态 |
| `is-added` | 链逻辑 | 卡片/快照「已加入执行链」的对勾 |
| `data-stop` `data-open` `data-dragging` | 抽屉手势 | 三档 + 拖拽中关过渡 |
| `data-collapsed` | 分类段折叠 | `true`/`false` |
| `data-search` `data-filtered` | 搜索 | `open` / 有过滤词 |
| `data-running` | 链运行中 | `.chainbar[data-running="true"] .btn--run` |
| `data-over` | 拖拽导入 | 悬停高亮 |
| `data-auto` | 预设图标 | `1` = 图标是按链推导的（虚线框） |

`--nx/--ny/--mx/--my` 是**行内自定义属性**，优先级高于任何 CSS 规则。
想「关掉跟随」不要去 CSS 里 `transform: none`（会连入场动画一起砍掉，
而且 `!important` 也很难看）—— 交给 `bindFollow()` 把变量写 0。

### 6.2 不许硬编码颜色

组件里一律用 `var(--token)`。唯一的例外是 `.dot--t1/t2/t3`（`common.css`）：
它是「切到那套主题会长什么样」的**预览**，所以必须是各家色板自己的值，
哪怕当前生效的是另一套主题。

代价：`theme.css` 改色后这里要手工跟。**t2 的第二色就漏跟过一次**
（预览里还是对调前的 `#FFE5B4`）。`tests/browser_palette_probe.js` 现在会拿
色点的渐变去比对当前令牌，防止再次脱节。

### 6.3 跨文件覆盖是既定事实，别「顺手整理」

几个组件的样式**不在**同名文件里，这是历史原因且被注释钉住的：

| 看到这个 | 定义在 | 为什么 |
|---|---|---|
| `.drawer__search` `.searchbtn` `.searchfield` `.input--search` | `cardlib.css` | 搜索属于卡片库那一屏 |
| `.chain` `.chain__item` `.chain__arrow` | `cardlib.css` | 链标签的外观，容器在顶栏 |
| `.fcard--custom` | `overlays.css` | 跟随「自定义」角标一起加的 |
| `.btn--danger` 的最终值 | `editor.css`（覆盖 `common.css`） | 见 §2 第 1 条 |
| 响应式 `@media` | `editor.css`（改 filecard/drawer 的组件） | 见 §2 第 1 条 |

**想搬家先把注释里的原因一起搬**，否则下一个人会当成放错了地方。

### 6.4 新增滚动容器必须登记

`scrollbar-width` **不是继承属性**，所以它必须逐个列在真正会滚的容器上。
目前只有 4 个，都在 `base.css`：

```
.pane__body   .filelist   .cardedit   .drawer__cards
```

`scrollbar-color` 是继承的，写在 `html` 上即可（**不要写回 `*`**：
那等于给全树每个元素加一条声明，主题切换时这一项会被放大）。

> `.chainbar .chain` 是**故意隐藏**滚动条的（`header.css` 里 `scrollbar-width: none`），
> 别把它加进上面那个列表。

### 6.5 `[hidden]` 与浮层显隐

`base.css` 有一条 `[hidden] { display: none !important; }`，
它会压过组件自己的 `display`（否则 `.bulkbar{display:flex}` 之类会把隐藏元素显示出来）。

但**要过渡的浮层不能靠 `display` 切换**，所以它们显式把它顶回去：

```css
.modal[hidden]  { display: grid; }              /* overlays.css */
.notice[hidden] { display: flex !important; }   /* overlays.css */
```

之后显隐完全交给 `visibility` + `.is-open`（打开时 `visibility 0s` 立即生效，
关闭时延迟到淡出播完再真正 `hidden`）。新增浮层请照这个套路写。

### 6.6 性能两条硬约束

1. **`will-change` 只在 hover / `is-open` 期间给**，不许常驻。
   常驻意味着 69 张卡片 + 69 个图标各占一个合成层（实测 GPU 显存峰值 ~675MB），
   而抽屉收起时它们根本不可见。见 `渲染开销优化方案.md`。
2. **`backdrop-filter` 跟着 `data-fx` 走**：关掉 3D 时不给 hover 图标叠毛玻璃
   （那层模糊本来是为视差服务的，关掉之后只剩「每帧重新模糊」的代价）。

### 6.7 主题切换静音（`base.css` 末尾）的两条禁令

```css
html[data-theme-switch] *, ... { transition: none !important; }
```

主题切换 = 全树令牌变化，凡声明了 `transition` 的元素都会起一段动画（实测数百个），
所以切换期间要静音。但：

- **别把它扩写成 `animation: none`** —— 平扫、径向过渡都是 `animation`，会被一起停掉。
- **别把平扫/径向从 `animation` 改写成 `transition(clip-path)`** ——
  一改就会被这条规则掐掉。它们**故意**用 `animation`。

### 6.8 每个动画都要有 reduced-motion 兜底

`@media (prefers-reduced-motion: reduce)` 在 `base.css`、`header.css`、
`drawer.css`、`cardlib.css`、`overlays.css`、`editor.css` 里都有。
新增动效时照抄一处：位移/旋转归零，但**保留颜色与玻璃质感**。

### 6.9 「元素自己会动」时，别在 CSS 里修命中测试

跟随效果（`.modal__box` 的倾斜、`.fcard` 的位移+倾斜）会改变元素在
**浏览器命中测试**里的轮廓。指针停在它边缘时会出现这样一个环：

```
静止 → 命中 → 施加跟随 → 那一侧轮廓缩进 → 不再命中 → pointerout 清变量
     → 回正 → 又命中 → ……                    （按帧率跑，看起来就是"抖"）
```

**给元素加伪元素内扩命中区是修不好的**（试过）：伪元素是它的子节点、
跟着一起被变换，内边距自己也会缩进 —— 只是把不稳定带从边缘**内侧**挪到**外侧**
（实测：内侧 5 条带消失，外侧多出 6 条带，2–7px）。

正解在 `app.js`：这类目标的命中判定改用**布局矩形**
（`offsetLeft/offsetTop/offsetWidth/offsetHeight`，与 `transform` 无关），
即 `bindFollow(container, sel, { stableHit: true })`。判定边界固定在屏幕空间里，
不随倾斜移动，环就不存在了。`ui/overlays.css`（产物里的 `ui/overlays.css` 那一段）
里也留了这条结论。

回归钉子：`python tests/browser_modal_follow_probe.py`（**23 项**，要服务 + Edge）——
它会**同时扫边缘内侧与外侧**，所以"把不稳定挪个地方"式的假修复过不了；
另外还钉住两条性能前提（跟随态 `transform` 不在过渡列表里、`.is-returning` 不留脏类）。

### 6.10 释放动画：`transform` 不在过渡列表时，退出会是瞬变

`.modal__box` 落定后 `transform` 被**故意**移出过渡列表（§6.6 第 2 条的反面：
跟随期间 `--nx/--ny` 每帧都改，过渡每帧重启 → 每帧一次 Layout，实测 +15 → +88）。
代价是指针一离开、变量被摘掉，倾斜**一帧跳回平整**。

```css
/* 落定后：跟随期间不过渡 transform */
.modal.is-settled .modal__box            { transition-property: opacity, translate, scale; }
/* 释放那一次：把 transform 加回列表，让"回正"走成过渡。单次变化，不带回那个开销 */
.modal.is-settled .modal__box.is-returning { transition-property: opacity, translate, scale, transform; }
```

`.is-returning` 由 `app.js` 的 `clearFollow()` 在摘变量**之前**挂上 —— 同一帧内完成，
按 CSS Transitions 规范"起不起过渡"看的是**变化后**样式里的 `transition-property`，
所以这样挂是有效的。380ms 后自动摘掉；指针又进来时 `cancelReturning()` 立刻摘掉。

时长/缓动不用在 `.is-returning` 里写：基础规则已经给了
`transform .30s cubic-bezier(.22, 1, .36, 1)`，属性列表与时长列表按位置对应。

`.fcard` / `.pcard` 本来就带 `.18s` 的 transform 过渡（**不是**瞬变），
它们的 `.is-returning` 只是把"离开那一次"放慢到 `.30s` + 更强的缓出，
让三种跟随效果的退出一致（实测三者都是 19 帧到稳定）。

⚠ 改这些的时候别把 `transform` **常驻**加回 `.modal.is-settled` 那条 ——
那正是当初花了一次实测才去掉的开销。

---

## 七、校验与回归

| 手段 | 管什么 |
|---|---|
| `python tests/ui_check.py` | 加载顺序护栏（10 个文件、集合与顺序）+ **打包产物与源逐字节一致** + 未定义令牌审计（26 项） |
| `python tests/theme_check.py` | 6 套主题 × 对比度/可见度（读落盘值复算） |
| `tests/browser_palette_probe.js` | 令牌是否**真的落到组件上**（关系断言，不是抄十六进制） |
| `tests/browser_cascade_probe.js` | 诊断用：某个属性到底从哪条规则来的 |
| [`gallery.html`](gallery.html) | 人工目视：全组件 × 6 套主题（引的就是打包产物，所以也是它的现场验证） |
| `python tools/build_ui_kit.py --check` | 只校验产物是否同步，不写文件 |
| `python tests/smoke_api.py` | 接口层（含静态资源可达） |

改了样式之后至少跑前两个 + 打开画廊扫一遍 6 套主题。

---

## 八、已知问题

1. **`theme_check.py` 有 1 项失败**（本次整理**之前就存在**，与搬迁无关）：
   `t1/light bg-app/tint-accent = 1.113 < 1.12` —— t1 浅色的第二色软底
   与页底的可见度差一点点没达标。要修就得动 `theme.css` 的 `--tint-accent`
   并重解该主题的亮度阶梯（见 `配色方案-落地规格.md` §3）。

2. **`--ink-accent` / `--text-disabled` 在 CSS 里没有引用**：
   前者被后端 `backend/loudness_svg.py` 消费（−23 参考线），
   后者是配色契约的保留位。两者都在 `theme_check.py` 的 `REQUIRED` 里，
   **不要因为「没人用」就删掉**。

3. **文档滞后**：`布局规格.md`、`执行链并发方案.md`、`渲染开销优化方案.md` 等
   设计文档里仍写作 `css/*.css` 与旧行号。它们是**当时的过程记录**，不是当前状态的
   说明；现行结构以本文件与根 `README.md` 为准。

4. **`.fcard` / `.pcard` 有同一类"命中测试随变换移动"的隐患，尚未处理。**
   卡片自己也带跟随 transform（`translate3d(±6px, ±6px)` + `rotateX/Y(±5deg)`），
   所以卡片**后沿**附近同样可能出现"静止命中 → 跟随移动 → 脱离 → 回正"的环，
   估算带宽约 7px（平移 6px + 旋转透视缩进 ~1.3px）。
   目前**没修**，原因有两条，都不是"忘了"：

   - `.fcard` 的两个伪元素**都被占用了**（`::before` 是「已加入执行链」的对勾，
     `::after` 是网点四层里的一层），没法照 `.iconpick__btn` 那样加命中垫；
   - `app.js` 的 `layoutRectOf()` 目前只支持**嵌套定位链**，而卡片的几层父容器
     共享同一个 `offsetParent`（`.drawer`），逐级累加会重复计数 ——
     要用到卡片上得先重写成"扣除每个可滚动祖先的 scrollTop/scrollLeft"的版本。
     `layoutRectOf()` 里对不满足前提的情况**直接退回 AABB**，所以误开 `stableHit`
     不会算错命中区（只会仍然抖）。

   要动它，先读 §6.9 与 `tests/browser_modal_follow_probe.py`。

---

## 九、本次整理做了什么

从根目录的 `theme.css` + `css/` 搬到 `ui/`，**选择器与声明逐字未动**、
**加载顺序未变**（git 记录的改名是 0 字节改动），只做了下面这些：

| 文件 | 改动 |
|---|---|
| `drawer.css` | 修 `var(--surface-alt)` → `var(--bg-surface-alt)`（未定义，原落到硬编码退路） |
| `drawer.css` | 修 `var(--text)` → `var(--ink)`（未定义且**无退路**，该 `color` 声明整条失效） |
| `drawer.css` | 删死令牌 `--drawer-edge`（JS 用自己的常量） |
| `theme.css` | 删死令牌 `--fs-micro` `--drawer-h` `--z-drawer` |
| `cardlib.css` | 删死令牌 `--krx`（已被硬编码成 1，注释里自己写明了） |
| `editor.css` | 同步上面那条注释（不再引用已删的 `--krx`） |
| `index.html` `_snapdrag.html` | 改 `<link>` 路径；删无处消费的行内 `--drawer-h:0px` |
| `backend/theme.py` | `CSS_PATH` → `ui/theme.css`（后端解析令牌的唯一入口） |
| `tests/*` | 同步路径（`theme_check.py`、`check_handover_doc.py`） |
| 新增 | `README.md`（本文件）、`gallery.html`、`tests/ui_check.py` |

搬迁的等价性由 git 自身记录：10 个文件是纯改名（0 insertions / 0 deletions），
内容改动只有上面那 4 个文件、合计 +4 / −9 行。

## 十、打包成这个目录（`ui-kit/`）

上面那几步之后，样式在 `ui/` 里是**应用在用的源**（10 个文件 + 一条"顺序不能改"的
口头约定）。这个目录是另外出的**交付形态**：

| 文件 | 来历 |
|---|---|
| `audioedition-ui.css` | **生成物**：`tools/build_ui_kit.py` 按 `ui/` 的加载顺序拼接，每个源文件前留一条 `/* ==== ui/x.css ==== */` 分隔注释 |
| `README.md` | 本文件（从 `ui/` 搬来） |
| `gallery.html` | 画廊（从 `ui/` 搬来），改引单文件 —— 它同时是这份产物的现场验证 |

三条与之相关的约定：

1. **应用不读产物。** `index.html` / `_snapdrag.html` 仍按顺序引 `ui/` 下的 10 个文件。
   好处是"改了样式忘了打包"**不会**把应用带偏；代价是产物会悄悄过期 ——
   所以第 2 条。
2. **产物必须与源逐字节一致**，由 `tests/ui_check.py` 守着（源改了忘打包就红）。
   这就是 `demo/app.js` 那个教训的翻版：拷贝不同步，自检会一路绿灯。
3. **不压缩。** 这套样式的价值有一大半在注释里（每条规则为什么这么写、哪个坑踩过），
   压缩会把它们全部删掉，还要为此引入工具链 —— 与"零构建"的取向冲突。
   要为体积取舍的话，先想清楚丢掉的注释值不值。

顺序的**唯一事实来源**是 `tools/build_ui_kit.py` 的 `ORDER`：打包脚本与
`tests/ui_check.py` 都用它，`index.html` 由测试核对，不存在第三份清单。

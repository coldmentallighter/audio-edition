# AudioEdition · UI 演示版

**只用来看界面和交互，不执行任何真实功能** —— 不转码、不改标签、不打开资源管理器、
不下载产物。适合拿去给人看 UI，或者做视觉验收。

## 怎么跑

```powershell
双击 start_demo.bat                      # Windows
./start_demo.sh                          # macOS / Linux
python serve.py                          # 或直接起服务，然后开 http://127.0.0.1:8800
python serve.py --port 8900 --no-browser  # 换端口 / 不自动开浏览器
```

只要 Python 3.10+，**不需要** ffmpeg、FastAPI、任何 pip 依赖。
（直接双击 `index.html` 也能跑 —— 所有 `/api` 都是浏览器内拦掉的 —— 但走 http 更接近真实环境。）

## 演示版和正式版的差别

跑的是**同一份前端**：`index.html` / `app.js` / `api.js` / `css/*` / `theme.css`
都是从仓库根目录**逐字节拷贝**来的（`_build/provenance.json` 记了每个源文件的 sha256），
只外加三样东西：

| 外加的 | 作用 |
|---|---|
| `data/demo-data.js` | 内置样例数据（采集自真实后端） |
| `mock.js` | 把后端整个换掉 |
| `demo.css` + 一个角标 | 声明"这是演示版" |

所以**演示版里看到的界面就是真界面**，不会出现"演示版会、正式版不会"的偏差。

### 真的会动的部分

| 交互 | 演示版里的表现 |
|---|---|
| 主题 3 套 × 浅/深（6 套） | 真的换 |
| 勾选 / 全选 / 反选 / 取消选择 / 批量栏 | 真的改状态，而且扛得住 2 秒一次的自动刷新 |
| 提交任何卡片、右键操作、批量操作 | 真的进队列：2 并发、进度会走、成功/失败、写日志 |
| 失败 / 重试 / 取消 | 真的能点（开局预置了一条失败任务，否则这套界面看不到） |
| 卡片抽屉（点击三档 / 拖拽把手 / 分组折叠 / 全部收起 / 搜索） | 真的算、真的动 |
| 卡片编辑器（新建 / 另存为 / 参数表单 / 图标 / 等价命令预览） | 真的存（存在内存里） |
| 执行链（快照加链 / 执行 / 保存预设 / 导出脚本） | 真的跑 |
| 试听 | **真的播** —— 样例音频就是本机文件 |
| 波形 | 后端 ffmpeg 解码算出来的**真实峰值**，不是画的假波形 |
| 内嵌封面 | 真的是从样例音频里抽出来的图 |
| 右键菜单（12 项）/ 编辑元数据模态 / toast / 日志面板 | 真的开、真的写 |
| 拖文件进来 | 真的出现在列表里（元数据与波形是**合成**的） |
| 产物链接 | 点了提示"演示版不会真的下载"，并在日志里记一行 |

### 不会真的做的部分

转码 / 改标签 / 嵌封面 / 重命名 / 打包 / 删磁盘文件 / 打开资源管理器 / 下载产物。

这些会走完整的前端流程，然后：进队列并在模拟执行里跑到 100%、该弹 toast 的弹 toast、
并在「日志输出」里写一行 `ⓘ 演示版：…` 说明这一步没有真的执行。

**数据只在内存里，刷新页面即复原。**

## 内置样例

9 个文件（8 音频 + 1 图片），元数据全部是**真实后端探测出来的**：

- FLAC ×3 / WAV / MP3 / M4A / OGG / OPUS，其中 3 首带真实内嵌封面
- 标题/艺术家/专辑/轨号是真的写进文件又读回来的
- 卡片库 69 张内置卡片 + 7 个分类 + 5 个快照，来自真实 `/api/cards`
- 采集时间记在 `data/demo-data.js` 顶部

## 目录

```
demo/
  index.html            生成物：源 index.html + 注入下面几个
  app.js api.js css/ theme.css    前端的逐字节拷贝（别直接改这里）
  mock.js               假后端：fetch / XHR / EventSource / 媒体 src / 产物链接
  demo.css              只有角标（不影响真样式）
  data/demo-data.js     内置样例数据
  assets/media/         样例音频 —— 试听用的是它们，能真播
  assets/covers/        真实内嵌封面
  assets/outputs/       产物样例图
  serve.py              静态服务器（只用标准库）；有 /api 漏过来会显眼报警
  start_demo.bat/.sh    双击即看
  _build/               生成与自检脚本（发给别人时可以删掉）
```

## 重新生成 / 自检

```powershell
# 0) 改过前端（app.js / css / index.html）之后必须先跑这个，否则 demo 里是旧副本
python demo/_build/make_demo.py

# 1) 自检：无头浏览器把 75 项交互真的点一遍（自己起服务，跑完自动清理）
python demo/_build/verify_demo.py

# 2) 重建样例数据（需要真实后端在 8765 上跑着 + ffmpeg/ffprobe/flac/metaflac）
python demo/_build/collect_data.py
```

`verify_demo.py` 会先检查 `demo/` 里的拷贝是否与源文件一致 —— 不一致直接报错退出
（**不查这一条的话，自检会对着旧副本一路绿灯**，这事真踩过）。然后跑 75 项交互，
最后再断言**没有任何 `/api` 请求漏到静态服务器**（也就是 mock 覆盖完整）。

## 踩过的两个坑（都写在代码注释里了）

1. **无头浏览器遇到 `prompt()` 会把渲染进程主线程整个卡死** —— 页面从此不再响应 CDP，
   看起来像"探针自己卡死了"，其实是 `Page.javascriptDialogOpening`。
   批量转换就用了 `prompt`。探针必须先接管 `window.prompt/confirm/alert` 自动应答。
2. **封面的 `<img>` 不能只补 `HTMLImageElement.prototype.src`。**
   app.js 是拼 HTML 字符串再 `innerHTML`，解析器写 `src` **不走 JS 的 setter**，
   prototype 补丁根本不会被调用。必须在**字符串层面**改写（`mock.js` 的 `rewriteHtml`）。
   `serve.py` 那句"未拦截的 /api 请求"就是为抓这类漏网加的。

"""参数规格：每个 op 认哪些参数、取值、默认值、说明与命令模板。

前端卡片编辑器完全靠这份规格渲染表单，所以它和后端白名单**写在同一处**，
不会出现"表单让填的值后端不认"。规格里的 onlyIf 只影响编辑器显隐，
真正的校验在 validate.py 里另做一遍。
"""
from __future__ import annotations

from collections import Counter
from typing import Any

from backend.tasks import (BITRATES, CHANNELS, FLAC_LEVELS, FORMAT_ARGS,
                           SAMPLE_RATES, WAVE_MAX_H, WAVE_MAX_W, WAVE_MIN_H,
                           WAVE_MIN_W)

# ---------------------------------------------------------------- 参数规格

_FORMAT_LABEL = {
    "flac": "FLAC（无损）", "wav": "WAV（未压缩 PCM 24bit）", "mp3": "MP3（有损）",
    "aac": "AAC（有损）", "m4a": "M4A（有损）", "ogg": "OGG Vorbis（有损）",
    "opus": "Opus（有损）", "aiff": "AIFF（未压缩）", "wma": "WMA（有损）",
}
_LOSSY = ["mp3", "aac", "m4a", "ogg", "opus", "wma"]

PICTURE_TYPES = ["Front Cover", "Back Cover", "Artist", "Band", "Composer",
                 "Conductor", "Lyricist", "Media", "Other"]

# 每个操作：label / desc / category / icon / params / preview
OPS: dict[str, dict[str, Any]] = {
    "probe": {
        "task": "probe",
        "label": "重新探测",
        "desc": "用 ffprobe 重新读取时长、采样率、位深、声道、标签与封面有无。只更新数据库记录，不改动音频本身。",
        "category": "元数据", "icon": "tag", "params": [],
        "preview": "ffprobe -v quiet -print_format json -show_format -show_streams <文件>",
    },
    "convert": {
        "task": "convert",
        "label": "格式转换",
        "desc": "用 ffmpeg 转成目标格式。输出落在 outputs/，不会覆盖源文件。",
        "category": "格式转换", "icon": "flac",
        "params": [
            {"key": "format", "label": "目标格式", "type": "enum", "required": True,
             "default": "flac", "desc": "容器/编码格式。不指定位深时，WAV 默认 24bit、AIFF 默认 16bit。",
             "options": [{"value": k, "label": _FORMAT_LABEL.get(k, k.upper())}
                         for k in FORMAT_ARGS]},
            {"key": "bitDepth", "label": "位深", "type": "enum", "default": "",
             "desc": "留空用该容器的默认位深。只对无损容器（FLAC / WAV / AIFF）有效；"
                     "FLAC 不支持浮点（32f）。",
             "options": [{"value": "", "label": "容器默认"},
                         {"value": "16", "label": "16 bit（CD 规格）"},
                         {"value": "24", "label": "24 bit（母带常见）"},
                         {"value": "32", "label": "32 bit 整型"},
                         {"value": "32f", "label": "32 bit 浮点（除外 FLAC）"}],
             "onlyIf": {"key": "format", "in": ["flac", "wav", "aiff"]}},
            {"key": "compressionLevel", "label": "FLAC 压缩等级", "type": "int",
             "default": 5, "min": min(FLAC_LEVELS), "max": max(FLAC_LEVELS),
             "desc": "0 最快、文件最大；8 最慢、文件最小。只对 FLAC 生效。",
             "onlyIf": {"key": "format", "in": ["flac"]}},
            {"key": "bitrate", "label": "码率", "type": "enum", "default": "192k",
             "desc": "有损格式的音频码率，越高越接近原声。只对有损格式生效。",
             "options": [{"value": b, "label": b} for b in sorted(
                 BITRATES, key=lambda x: int(x.rstrip("k")))],
             "onlyIf": {"key": "format", "in": _LOSSY}},
            {"key": "sampleRate", "label": "采样率", "type": "enum", "default": "",
             "desc": "留空表示保持原采样率。降采样会丢失高频，升采样不会增加信息量。",
             "options": [{"value": "", "label": "保持原样"}] +
                        [{"value": str(s), "label": f"{s} Hz"} for s in sorted(SAMPLE_RATES)]},
            {"key": "channels", "label": "声道数", "type": "enum", "default": "",
             "desc": "留空表示保持原声道布局。6/8 声道是 5.1/7.1 环绕声。",
             "options": [{"value": "", "label": "保持原样"}, {"value": "1", "label": "1（单声道）"},
                         {"value": "2", "label": "2（立体声）"}, {"value": "6", "label": "6（5.1）"},
                         {"value": "8", "label": "8（7.1）"}]},
            {"key": "keepTags", "label": "保留标签", "type": "bool", "default": True,
             "desc": "把 Title/Artist/Album 等元数据带到输出文件。"},
            {"key": "keepCover", "label": "保留封面", "type": "bool", "default": True,
             "desc": "把内嵌封面图一并带到输出文件（目标容器需支持）。"},
        ],
        "preview": "ffmpeg -i <文件> {codec} {extra} <输出>",
    },
    "tags": {
        "task": "tag_edit",
        "label": "批量改标签",
        "desc": "写入或清除音频标签。FLAC 走 metaflac，其余走 mutagen，都不重新编码。",
        "category": "元数据", "icon": "tag",
        "params": [
            {"key": "tags", "label": "标签", "type": "tags", "default": {},
             "desc": "每行一条 key=value。常用键：title / artist / album / "
                     "albumartist / tracknumber / date / genre / comment。留空值表示清空该项。"},
            {"key": "clearMissing", "label": "清除未列出的标签", "type": "bool", "default": False,
             "desc": "开启后，上面没写到的标签会被删掉；关闭则只覆盖写到的项。"},
        ],
        "preview": "metaflac --set-tag=TITLE=... --remove-tag=... <文件>",
    },
    "cover": {
        "task": "cover_embed",
        "label": "嵌入封面",
        "desc": "把一张图片写进音频。FLAC 用 metaflac，其余用 ffmpeg 附加图像流，都不重编码音频。",
        "category": "封面", "icon": "cover",
        "params": [
            {"key": "imagePath", "label": "封面图路径", "type": "text",
             "default": "", "desc": "uploads/ 内的相对路径（例如 covers/art.png）。"
                                    "**留空则执行时弹文件选择器**（卡片不该写死某张图，"
                                    "所以它不是必填）；直接在文件卡片的封面上点「导入」也可以。",
             "placeholder": "留空 = 执行时选图"},
            {"key": "pictureType", "label": "封面类型", "type": "enum", "default": "Front Cover",
             "desc": "ID3/Vorbis 的图片类型标记，播放器按它决定显示哪张图。",
             "options": [{"value": p, "label": p} for p in PICTURE_TYPES]},
        ],
        "preview": "metaflac --import-picture-from=<图片> <文件>",
    },
    "extract-cover": {
        "task": "cover_extract",
        "label": "提取封面",
        "desc": "把内嵌封面导出成 jpg，放在 outputs/covers/（链上则在本轮执行的目录里）。",
        "category": "封面", "icon": "cover", "params": [],
        "preview": "ffmpeg -i <文件> -map 0:v:0 -frames:v 1 -c:v mjpeg <输出.jpg>",
    },
    "remove-cover": {
        "task": "cover_remove",
        "label": "删除封面",
        "desc": "移除内嵌封面（目前仅 FLAC 支持）。",
        "category": "封面", "icon": "cover", "params": [],
        "preview": "metaflac --remove --block-type=PICTURE <文件>",
    },
    "normalize": {
        "task": "normalize",
        "label": "响度标准化",
        "desc": "用 ffmpeg loudnorm 两遍法把整体响度对齐到目标值：先测一遍实际响度，再按实测值做线性归一。",
        "category": "响度", "icon": "gain",
        "params": [
            {"key": "targetLufs", "label": "目标响度", "type": "float",
             "default": -16, "min": -40, "max": 0,
             "desc": "单位 LUFS，越小越轻。播客常用 -16，流媒体常用 -14，EBU R128 是 -23。"},
            {"key": "truePeak", "label": "真峰值上限", "type": "float",
             "default": -1.5, "min": -9, "max": 0,
             "desc": "单位 dBTP。留出余量避免转成有损格式后削波，-1.5 比较稳妥。"},
            {"key": "lra", "label": "响度范围", "type": "float",
             "default": 11, "min": 1, "max": 50,
             "desc": "单位 LU，允许的动态范围。越大保留越多起伏，越小压得越平。"},
        ],
        "preview": "ffmpeg -i <文件> -af loudnorm=I={targetLufs}:TP={truePeak}:LRA={lra}:linear=true <输出>",
    },
    "rename": {
        "task": "rename",
        "label": "按标签重命名",
        "desc": "按模板用标签重命名文件。只在 uploads/ 内改名，不移动出受管目录。",
        "category": "元数据", "icon": "tag",
        "params": [
            {"key": "pattern", "label": "命名模板", "type": "text",
             "default": "{artist} - {title}",
             "desc": "可用占位符：{title} {artist} {album} {albumartist} "
                     "{tracknumber} {date} {genre} {filename}。取不到值的占位符会留空。"
                     "支持零填充：{tracknumber:02} 会把 1 写成 01。",
             "placeholder": "{tracknumber:02} - {title}"},
        ],
        "preview": "把 <文件> 改名为「{pattern}」.扩展名",
    },
    "peaks": {
        "task": "peaks",
        "label": "生成峰值图",
        "desc": "解码整首歌算出归一化峰值数组并按文件缓存，页面上那条波形就是它画的。"
                "本身不改动音频；想要一张图请用「导出波形 PNG」。",
        "category": "峰值", "icon": "wave",
        "params": [
            {"key": "buckets", "label": "峰值点数", "type": "int", "default": 1000,
             "min": 64, "max": 20000,
             "desc": "采样成多少个点。点数越多波形越细，代价是计算更久；"
                     "页面上按卡片宽度显示，1000 点足够。"},
            {"key": "force", "label": "强制重算", "type": "bool", "default": False,
             "desc": "默认命中缓存就直接返回。源文件被外部改动过、但大小与时间戳没变时，"
                     "开启它才会真的重新解码。"},
        ],
        "preview": "ffmpeg -i <文件> -f s8 -ac 1 -ar 8000 -  # 解码后按桶取最大值",
    },
    "verify": {
        "task": "verify",
        "label": "完整性校验",
        "desc": "用 flac -t 解码整条流校验完整性，能发现损坏的帧。仅支持 FLAC。",
        "category": "校验/打包", "icon": "check", "params": [],
        "preview": "flac -t <文件>",
    },
    "waveform": {
        "task": "waveform",
        "label": "导出波形 PNG",
        "desc": "把整首歌的波形渲染成一张 PNG。默认白色波形 + 透明底，可以直接叠到任何封面上；"
                "输出在 outputs/waveforms/（链上则在本轮执行的目录里）。",
        "category": "峰值", "icon": "wave",
        "params": [
            {"key": "width", "label": "宽度", "type": "int", "default": 1920,
             "min": WAVE_MIN_W, "max": WAVE_MAX_W,
             "desc": "输出图片的像素宽度。做视频背景常用 1920，做封面常用 1000~1500。"},
            {"key": "height", "label": "高度", "type": "int", "default": 400,
             "min": WAVE_MIN_H, "max": WAVE_MAX_H,
             "desc": "输出图片的像素高度。太高会显得空，一般取宽度的 1/5 左右。"},
            {"key": "color", "label": "波形颜色", "type": "text", "default": "#FFFFFF",
             "desc": "十六进制颜色，例如 #FFFFFF 白色、#22AAFF 蓝色、#FF3B30 红色。",
             "placeholder": "#FFFFFF"},
            {"key": "background", "label": "背景", "type": "enum", "default": "transparent",
             "desc": "透明底方便叠到别的图上；白波形配白底会看不见，需要实底时选黑。",
             "options": [{"value": "transparent", "label": "透明（推荐）"},
                         {"value": "black", "label": "纯黑"},
                         {"value": "white", "label": "纯白"}]},
            {"key": "scale", "label": "刻度", "type": "enum", "default": "lin",
             "desc": "线性按真实振幅画；log/sqrt/cbrt 会放大弱音，安静段落也看得见。",
             "options": [{"value": "lin", "label": "线性（真实动态）"},
                         {"value": "sqrt", "label": "平方根"},
                         {"value": "cbrt", "label": "立方根"},
                         {"value": "log", "label": "对数（弱音最明显）"}]},
            {"key": "splitChannels", "label": "左右声道分开画", "type": "bool", "default": False,
             "desc": "开启后左右声道各占一条横向轨道，能看出声道差异；关闭则合并成一条。"},
        ],
        "preview": "ffmpeg -i <文件> -filter_complex showwavespic=s={width}x{height}:colors={color} "
                   "-frames:v 1 -pix_fmt rgba <输出.png>",
    },
    "zip": {
        "task": "zip",
        "label": "打包 ZIP",
        "desc": "把**打包窗口内的全部终产物**打成一个 ZIP：串行取每个文件走到打包前一步的"
                "产物、并行取窗口内全部非 zip 产物（所以一条链可以有多个打包步骤）。"
                "链上落 `outputs/<本次执行的目录>/`，名字是 `upload-<日期>-<来源卡片名>.zip`；"
                "单独一张时打包源文件，落 `outputs/zips/`。",
        "category": "校验/打包", "icon": "zip", "params": [],
        "preview": "zip <输出.zip> <文件...>",
    },
    # ---------------------------------------------------------------- 响度分析
    # 数据来自单趟 `ebur128=peak=true:framelog=verbose`（见
    # 响度总览图（LoudnessAnalysis）实现构想.md §2.1：3:07 的歌实测 0.7s）。
    # 两个 op 都是**只读**：`loudness` 只往缓存里写 JSON 时间线，`loudness-image`
    # 另外导出一张 PNG。它们都不改 `uploads/` 里的文件（contract.py 里 mode=read）。
    "loudness": {
        "task": "loudness",
        "label": "响度总览图",
        "desc": "跑一遍 ebur128 拿到逐帧 M/S/I 时间线与 8 项响度指标（Integrated、LRA、"
                "PLR、瞬时/短时最大值、真峰值），按文件缓存。本身不改动音频。",
        "category": "响度", "icon": "gain",
        "params": [
            {"key": "force", "label": "强制重算", "type": "bool", "default": False,
             "desc": "默认命中缓存就直接返回。源文件被外部工具改过、但大小与时间戳"
                     "都没变时，开启它才会真的重新解码。"},
        ],
        "preview": "ffmpeg -i <文件> -map 0:a:0 -af "
                   "ebur128=peak=true:framelog=verbose:metadata=true,"
                   "ametadata=mode=print:file=ebur.txt -f null -   # 再解析 ebur.txt",
    },
    "loudness-image": {
        "task": "loudness",
        "label": "导出响度分析图",
        "desc": "把响度总览图渲染成一张 **SVG** 整页（纵轴标尺 + 双色响度包络 + "
                "总响度虚线 + 时间带 + 元数据卡 / 响度卡 / 动态卡），放在 "
                "outputs/loudness/（链上则在本轮执行的目录里）。矢量、可任意缩放，"
                "中文用看的人的字体（不会变成方块）。版式与纵轴规格见 "
                "backend/chart_layout.py 与 backend/chart_axis.py。",
        "category": "响度", "icon": "gain",
        "params": [
            {"key": "width", "label": "显示宽度", "type": "int", "default": 1200,
             "min": 600, "max": 8000,
             "desc": "SVG 的显示宽度（px）。`viewBox` 固定是设计单位，所以这**只是"
                     "显示尺寸、不重排版** —— 矢量图任意缩放都清晰；嵌进窄容器时 "
                     "CSS 的 max-width 还会再兜一层。"},
            {"key": "refLufs", "label": "参考线响度", "type": "float", "default": -23,
             "min": -54, "max": -5,
             "desc": "那条橙色参考线画在哪个响度上。EBU R128 是 -23，"
                     "流媒体常用 -14，有声书 -18。"},
            {"key": "force", "label": "强制重算", "type": "bool", "default": False,
             "desc": "同上：跳过缓存重新分析。"},
        ],
        "preview": "ffmpeg -i <文件> -map 0:a:0 -af "
                   "ebur128=peak=sample+true:framelog=verbose:metadata=true -f null -"
                   "   # 取时间线；再由后端按 chart_layout / chart_axis 渲染成 SVG",
    },
    "loudness-report": {
        "task": "loudness",
        "label": "响度分析报告",
        "desc": "把响度分析写成一份 **Markdown 报告**（汇总表 + 逐曲 8 项指标 + "
                "响度分区统计 + 最响/最轻的时间点），放在 outputs/loudness/（链上则在本轮执行的目录里）。"
                "适合归档、交付说明，或贴进 issue 复现问题。",
        "category": "响度", "icon": "tag",
        "params": [
            {"key": "detail", "label": "详细程度", "type": "enum", "default": "summary",
             "desc": "summary 只给汇总与分区；full 另附逐曲的指标明细与时间点。",
             "options": [{"value": "summary", "label": "汇总（推荐）"},
                         {"value": "full", "label": "完整（逐曲明细）"}]},
            {"key": "timePoints", "label": "最响/最轻时间点个数", "type": "int",
             "default": 5, "min": 0, "max": 30,
             "desc": "每首歌各列几个最响与最轻的时间点（0 = 不列）。"
                     "用来快速定位哪里爆了、哪里太安静。"},
            {"key": "frameTable", "label": "附逐帧明细表", "type": "bool", "default": False,
             "desc": "把 10Hz 的逐帧 M/S 也写成表格。默认关：3 分钟的歌约 1800 行，"
                     "会把结论淹掉。"},
            {"key": "refLufs", "label": "参考目标响度", "type": "float", "default": -23,
             "min": -54, "max": -5,
             "desc": "报告里用来判偏响/偏轻的参考线。EBU R128 是 -23，"
                     "流媒体 -14，有声书 -18。"},
            {"key": "force", "label": "强制重算", "type": "bool", "default": False,
             "desc": "同上：跳过缓存重新分析。"},
        ],
        "preview": "（读响度缓存 → 渲染成 Markdown 落 outputs/loudness/）",
    },
}

# 参数类型 → 给前端表单用的控件
PARAM_TYPES = ("enum", "int", "float", "bool", "text", "tags")


# ---------------------------------------------------------------- 合并"接触面"
#
# `contract.py` 里那份接触面（touch/mode/obs/produce/needs/gives/consumes）
# **必须出现在 `/api/ops` 里**，否则前端算不出"下一步能选什么卡"（方案 §3.2.4），
# 只好自己再写一份判据 —— 两份判据迟早分叉，而分叉的表现是"卡片灰得莫名其妙"。
#
# 合并放在这里（而不是让 contract.py 反过来 import specs）的原因：
# 依赖方向必须是 specs → contract 单向，contract 内部再 import specs 就成环了。
# 测试 tests/boundary_check.py 会核对"每个 op 都登记了接触面"，漏登记直接红。
def _merge_contract() -> None:
    from backend.cards.contract import CONTRACT

    for op, spec in OPS.items():
        c = CONTRACT.get(op)
        if not c:
            continue
        spec["touch"] = list(c["touch"])
        spec["mode"] = c["mode"]
        spec["obs"] = c["obs"]
        spec["produce"] = c["produce"]
        spec["needs"] = list(c["needs"]) if not isinstance(c["needs"], str) else [c["needs"]]
        spec["gives"] = c["gives"]
        spec["consumes"] = bool(c.get("consumes", True))
        # `gives_formats` 也要送出去：前端要自己算串行档的"有损→无损"规则
        # （见 app.js 的 `formatFlowBlocked`）。不送的话前端只能再抄一份规则表，
        # 而"两份规则表各自漂移"是这个项目反复踩过的坑。
        spec["gives_formats"] = c.get("gives_formats") or "none"


_merge_contract()


# ---------------------------------------------------------------- 任务分发键 `_op`
#
# **一个任务类型被多个 op 共用时，handler 只有拿到 `params["_op"]` 才知道自己
# 该做哪一件。** 目前只有响度三兄弟是这样：`loudness` / `loudness-image` /
# `loudness-report` 共用任务类型 `loudness`（任务类型与卡片 op 是两套命名，
# 靠上面每个 spec 的 `task` 字段桥接），分流在 `tasks.h_loudness` 里读这个键。
#
# 所以判据**从上面那张表现算**，不写死名单 —— 写死的话，将来再加一个共用
# 任务类型的 op，同一个坑会原样重演。
#
# ⚠ 这个键**必须由服务端在建任务时注入**，而且**两条建任务的路径都要走这里**：
#   ① 路由路径 `routers/ops.py` 的 `submit_batch`（单步提交、右键菜单）
#   ② 建链路径 `chain.build_chain`（执行链，前端主路径）
# 曾经只有 ① 注入了，于是链上的「响度分析报告」退化成"只算一遍响度写缓存"，
# 任务状态还是 `success` —— 用户看到的是"成功但什么都没产出"，
# 界面不报错、库里没产物，极难查（钉子见 tests/chain_build_check.py §6c 与
# tests/loudness_chain_e2e_check.py）。
def _task_of(op: str) -> str:
    """op → 它实际使用的任务类型（未知 op 时原样返回，不猜）。"""
    spec = OPS.get(op) or {}
    return str(spec.get("task") or op)


def _shared_task_ops() -> frozenset[str]:
    """与别的 op 共用同一个任务类型的 op —— 这些**必须**带上 `_op`。"""
    counts = Counter(_task_of(op) for op in OPS)
    return frozenset(op for op in OPS if counts[_task_of(op)] > 1)


SHARED_TASK_OPS = _shared_task_ops()

#: 会**用到主题**的 op —— 也就是产出带颜色的产物、并且颜色要在服务端算出来的那些。
#:
#: 目前只有 `loudness-image`：它渲染 SVG，而 SVG 有两种活法（内联进页面 / 导出成独立
#: 文件），后者**没有任何 CSS 变量可用**。所以颜色必须在服务端按主题算成实色写进文件
#: （老板 2026-10："svg 生成的主题颜色改成用户执行链时主题的，并在导出时将颜色硬编码
#: 入文件"）。`chain.build_chain` 只给这些 op 注入链级的 `theme`/`themeMode`。
#:
#: ⚠ 别的产物不在这里：`waveform` 是 ffmpeg 画的 PNG（颜色走它自己的参数），
#: `loudness-report` 是 Markdown（没有颜色）。前端画布是**另一个故事** ——
#: 它在浏览器里，直接读 `theme.css` 的变量，天然跟着主题走。
THEME_OPS = frozenset({"loudness-image"})


def task_params(op: str, params: dict | None = None) -> dict:
    """把某个 op 的 params 规范成**任务**的 params（建任务前必须过这一道）。

    规则只有两条：

      · `_op` 是**服务端状态**，客户端传什么都要丢掉（否则谁都能让
        `loudness` 这个卡片去写报告文件）；
      · 共用任务类型的 op 家族，把 op 自己写进 `_op` —— handler 靠它分流。
    """
    p = dict(params or {})
    p.pop("_op", None)                      # 不许客户端伪造服务端状态
    if op in SHARED_TASK_OPS:
        p["_op"] = op
    return p


def op_of_task(task_type: str, params: dict | None = None) -> str:
    """这条任务**实际执行的是哪个 op** —— 与 `task_params` 互逆。

    ⚠ 两个来源，**顺序不能反**：先看服务端注入的 `_op`，再按任务类型反查。
    按类型反查只能拿到"第一个登记的那个 op"（响度三兄弟共用任务类型 `loudness`，
    反查永远得到 `loudness` 这个 `produce=none` 的），于是"这一步交出的是不是
    能递给下一步的**音频产物**"会判错 —— 而那个判据决定
    `queue._publish_derived` 要不要交接（旁路产物被交接就是那个串行报错的根因）。
    """
    op = str((params or {}).get("_op") or "").strip()
    if op in OPS:
        return op
    for cand in OPS:
        if _task_of(cand) == task_type:
            return cand
    return ""


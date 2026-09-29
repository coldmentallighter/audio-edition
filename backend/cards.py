"""功能卡片目录：内置卡片 + 自定义卡片 + 参数规格。

为什么要在这里维护参数规格：
  前端要能"自己配卡片"，就必须知道每个操作**到底认哪些参数**、
  取值范围是什么、默认值是什么。把这些写在前端等于让白名单漂移，
  所以规格跟 tasks.py 里的白名单放在同一侧，由 /api/ops 吐给前端。

**OPS 的 key 必须是【路由名】，不是任务类型名。**
  前端执行卡片时是 `POST /api/ops/<op>`（见 api.js 的 API.op），拼错就是 404。
  路由名与任务类型名有几处并不相同：

      OPS key（路由）   任务类型（tasks.py 注册名）
      tags             tag_edit
      cover            cover_embed
      extract-cover    cover_extract
      remove-cover     cover_remove

  曾经这里误用了任务类型名，于是「提取封面」「删除封面」两张内置卡片必然 404；
  而前端离线兜底表、右键菜单、smoke_api 用的全是路由名，三处都对 ——
  这个 bug 就靠"两边命名不一致"躲过了当时所有测试。
  现在每条规格都带 `task` 字段，并且有测试逐条 POST /api/ops/<op> 确认不是 404。

自定义卡片存在 cards.json（项目根目录）。
注意它**不在** uploads/ outputs/ .cache/ 里 —— 启动时的 wipe_workspace()
只清工作区，用户配置必须跨会话保留。
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
from pathlib import Path
from typing import Any

from backend import config
from backend.tasks import (BITRATES, CHANNELS, FLAC_LEVELS, FORMAT_ARGS, SAMPLE_RATES,
                           WAVE_MAX_H, WAVE_MAX_W, WAVE_MIN_H, WAVE_MIN_W)

CARDS_JSON = config.ROOT / "cards.json"

_lock = threading.Lock()

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
        "desc": "把内嵌封面导出成 jpg，放在 outputs/covers/。",
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
                "输出在 outputs/waveforms/。",
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
        "desc": "把这一批文件打成一个 ZIP，放在 outputs/zips/。",
        "category": "校验/打包", "icon": "zip", "params": [],
        "preview": "zip <输出.zip> <文件...>",
    },
}

# 参数类型 → 给前端表单用的控件
PARAM_TYPES = ("enum", "int", "float", "bool", "text", "tags")

# ---------------------------------------------------------------- 内置卡片

BUILTIN_CARDS: list[dict[str, Any]] = [
    {"id": "b_convert_flac", "cat": "格式转换", "name": "转 FLAC",
     "desc": "无损，压缩等级 5，保留标签与封面", "tier": "一键", "ico": "flac",
     "op": "convert", "params": {"format": "flac", "compressionLevel": 5}},
    {"id": "b_convert_mp3", "cat": "格式转换", "name": "转 MP3 320",
     "desc": "320 kbps CBR，兼容性优先", "tier": "一键", "ico": "mp3",
     "op": "convert", "params": {"format": "mp3", "bitrate": "320k"}},
    {"id": "b_convert_wav", "cat": "格式转换", "name": "转 WAV 24bit",
     "desc": "未压缩 PCM，24bit", "tier": "一键", "ico": "wav",
     "op": "convert", "params": {"format": "wav"}},
    {"id": "b_resample_48k", "cat": "格式转换", "name": "重采样 48k",
     "desc": "保持位深与声道，仅改采样率", "tier": "配置", "ico": "wave",
     "op": "convert", "params": {"format": "flac", "sampleRate": 48000}},
    {"id": "b_probe", "cat": "元数据", "name": "重新探测",
     "desc": "用 ffprobe 刷新时长/采样率/标签", "tier": "一键", "ico": "tag",
     "op": "probe", "params": {}},
    {"id": "b_rename", "cat": "元数据", "name": "按标签重命名",
     "desc": "「歌手 - 标题」格式重命名文件", "tier": "配置", "ico": "tag",
     "op": "rename", "params": {"pattern": "{artist} - {title}"}},
    # tags / cover 这两张是后补的：它们本来就有完整参数规格，却没有任何内置卡，
    # 于是「批量改标签」「嵌入封面」（需求里的 P0）在卡片抽屉里根本找不到入口。
    # 现在 12 个操作每个都至少有一张内置卡。
    {"id": "b_tags", "cat": "元数据", "name": "批量改标签",
     "desc": "写入标签；在编辑器里填 key=value", "tier": "配置", "ico": "tag",
     "op": "tags", "params": {"tags": {}, "clearMissing": False}},
    {"id": "b_cover_embed", "cat": "封面", "name": "嵌入封面",
     "desc": "执行时选一张图，写进所有选中文件", "tier": "配置", "ico": "cover",
     "op": "cover", "params": {"imagePath": "", "pictureType": "Front Cover"}},
    {"id": "b_cover_extract", "cat": "封面", "name": "提取封面",
     "desc": "导出内嵌封面为 jpg", "tier": "一键", "ico": "cover",
     "op": "extract-cover", "params": {}},
    {"id": "b_cover_remove", "cat": "封面", "name": "删除封面",
     "desc": "移除内嵌封面（FLAC）", "tier": "一键", "ico": "cover",
     "op": "remove-cover", "params": {}},
    {"id": "b_norm16", "cat": "响度", "name": "标准化 -16 LUFS",
     "desc": "loudnorm 两遍法，播客标准", "tier": "一键", "ico": "gain",
     "op": "normalize", "params": {"targetLufs": -16}},
    {"id": "b_norm14", "cat": "响度", "name": "标准化 -14 LUFS",
     "desc": "loudnorm 两遍法，流媒体标准", "tier": "配置", "ico": "gain",
     "op": "normalize", "params": {"targetLufs": -14}},
    {"id": "b_peaks", "cat": "峰值", "name": "生成峰值图",
     "desc": "按 1000 点计算并缓存", "tier": "一键", "ico": "wave",
     "op": "peaks", "params": {"buckets": 1000}},
    {"id": "b_waveform", "cat": "峰值", "name": "导出波形 PNG",
     "desc": "白色波形，透明底，1920×400", "tier": "一键", "ico": "wave",
     "op": "waveform", "params": {"width": 1920, "height": 400, "color": "#FFFFFF",
                                  "background": "transparent", "scale": "lin",
                                  "splitChannels": False}},
    {"id": "b_waveform_split", "cat": "峰值", "name": "波形 PNG（左右分开）",
     "desc": "白色波形，透明底，左右声道各一条", "tier": "配置", "ico": "wave",
     "op": "waveform", "params": {"width": 1920, "height": 500, "color": "#FFFFFF",
                                  "background": "transparent", "scale": "lin",
                                  "splitChannels": True}},
    {"id": "b_verify", "cat": "校验/打包", "name": "FLAC 完整性校验",
     "desc": "flac -t 逐文件校验", "tier": "一键", "ico": "check",
     "op": "verify", "params": {}},
    {"id": "b_zip", "cat": "校验/打包", "name": "打包 ZIP",
     "desc": "批量打包为一个压缩包", "tier": "一键", "ico": "zip",
     "op": "zip", "params": {}},

    # ===== A 轴：纯预设卡（同一个 op，不同参数组合）=====
    # 全部零后端改动 —— 只走 OPS 里已有的参数空间。
    {"id": "b_convert_01", "cat": "格式转换", "name": "转 Opus 96k", "desc": "语音/播客存档，同听感体积约为 MP3 的 60%", "tier": "一键", "ico": "flac", "op": "convert", "params": {"format": "opus", "bitrate": "96k"}},
    {"id": "b_convert_02", "cat": "格式转换", "name": "转 Opus 128k", "desc": "音乐有损首选，低码率明显优于 MP3", "tier": "一键", "ico": "flac", "op": "convert", "params": {"format": "opus", "bitrate": "128k"}},
    {"id": "b_convert_03", "cat": "格式转换", "name": "转 M4A 256k", "desc": "苹果生态、车载 U 盘", "tier": "一键", "ico": "mp3", "op": "convert", "params": {"format": "m4a", "bitrate": "256k"}},
    {"id": "b_convert_04", "cat": "格式转换", "name": "转 OGG 192k", "desc": "游戏引擎 / 开源素材", "tier": "配置", "ico": "mp3", "op": "convert", "params": {"format": "ogg", "bitrate": "192k"}},
    {"id": "b_convert_05", "cat": "格式转换", "name": "转 MP3 128k", "desc": "「哪都能放」的最小体积", "tier": "一键", "ico": "mp3", "op": "convert", "params": {"format": "mp3", "bitrate": "128k"}},
    {"id": "b_convert_06", "cat": "格式转换", "name": "转 MP3 192k 单声道", "desc": "有声书/人声，体积直接砍半", "tier": "配置", "ico": "mp3", "op": "convert", "params": {"format": "mp3", "bitrate": "192k", "channels": "1"}},
    {"id": "b_convert_07", "cat": "格式转换", "name": "转 AIFF", "desc": "老式剪辑软件素材", "tier": "配置", "ico": "wav", "op": "convert", "params": {"format": "aiff"}},
    {"id": "b_convert_08", "cat": "格式转换", "name": "转 WMA", "desc": "极端老车机兜底", "tier": "高级", "ico": "mp3", "op": "convert", "params": {"format": "wma", "bitrate": "192k"}},
    {"id": "b_convert_09", "cat": "格式转换", "name": "CD 规格 44.1k", "desc": "44.1kHz / 16bit，归档基准线", "tier": "配置", "ico": "wave", "op": "convert", "params": {"format": "flac", "sampleRate": 44100, "bitDepth": "16"}},
    {"id": "b_convert_10", "cat": "格式转换", "name": "母带规格 96k", "desc": "96kHz / 24bit，后期交付避免二次重采样", "tier": "配置", "ico": "wave", "op": "convert", "params": {"format": "flac", "sampleRate": 96000, "bitDepth": "24"}},
    {"id": "b_convert_11", "cat": "格式转换", "name": "单声道 FLAC", "desc": "双轨录音合并，无损省一半", "tier": "配置", "ico": "flac", "op": "convert", "params": {"format": "flac", "channels": "1"}},
    {"id": "b_convert_12", "cat": "格式转换", "name": "5.1 环绕 FLAC", "desc": "环绕声归档", "tier": "高级", "ico": "flac", "op": "convert", "params": {"format": "flac", "channels": "6"}},
    {"id": "b_convert_13", "cat": "格式转换", "name": "5.1 下混立体声", "desc": "环绕转交付格式", "tier": "高级", "ico": "flac", "op": "convert", "params": {"format": "flac", "channels": "2"}},
    {"id": "b_convert_14", "cat": "格式转换", "name": "8k 提示音素材", "desc": "8kHz 单声道小文件，铃声/提示音", "tier": "高级", "ico": "mp3", "op": "convert", "params": {"format": "mp3", "sampleRate": 8000, "bitrate": "96k", "channels": "1"}},
    {"id": "b_convert_15", "cat": "格式转换", "name": "FLAC 极限压缩", "desc": "等级 8：冷存归档，慢但最小", "tier": "配置", "ico": "flac", "op": "convert", "params": {"format": "flac", "compressionLevel": 8}},
    {"id": "b_convert_16", "cat": "格式转换", "name": "FLAC 极速", "desc": "等级 0：赶时间，体积换速度", "tier": "配置", "ico": "flac", "op": "convert", "params": {"format": "flac", "compressionLevel": 0}},
    {"id": "b_convert_17", "cat": "格式转换", "name": "WAV 16bit", "desc": "CD 规格未压缩", "tier": "配置", "ico": "wav", "op": "convert", "params": {"format": "wav", "bitDepth": "16"}},
    {"id": "b_convert_18", "cat": "格式转换", "name": "WAV 32bit 浮点", "desc": "给混音/母带软件吃的浮点 WAV", "tier": "高级", "ico": "wav", "op": "convert", "params": {"format": "wav", "bitDepth": "32f"}},
    {"id": "b_convert_19", "cat": "格式转换", "name": "空间腾挪", "desc": "FLAC→Opus 96k，一批下来能腾出约 80% 空间", "tier": "一键", "ico": "flac", "op": "convert", "params": {"format": "opus", "bitrate": "96k"}},
    {"id": "b_normalize_01", "cat": "响度", "name": "广播 EBU R128", "desc": "-23 LUFS / -2 dBTP / LRA 15，欧洲广播硬指标", "tier": "配置", "ico": "gain", "op": "normalize", "params": {"targetLufs": -23, "truePeak": -2, "lra": 15}},
    {"id": "b_normalize_02", "cat": "响度", "name": "有声书交付", "desc": "-18 LUFS / -3 dBTP / LRA 7，留白最保守", "tier": "配置", "ico": "gain", "op": "normalize", "params": {"targetLufs": -18, "truePeak": -3, "lra": 7}},
    {"id": "b_normalize_03", "cat": "响度", "name": "短视频 -14 紧", "desc": "-14 LUFS / -1 dBTP / LRA 8，竖屏压得更平", "tier": "配置", "ico": "gain", "op": "normalize", "params": {"targetLufs": -14, "truePeak": -1, "lra": 8}},
    {"id": "b_normalize_04", "cat": "响度", "name": "播客交付", "desc": "-16 LUFS / -1.5 dBTP / LRA 9，交付级", "tier": "配置", "ico": "gain", "op": "normalize", "params": {"targetLufs": -16, "truePeak": -1.5, "lra": 9}},
    {"id": "b_normalize_05", "cat": "响度", "name": "俱乐部 -9", "desc": "-9 LUFS / -1 dBTP / LRA 6，几乎全压", "tier": "高级", "ico": "gain", "op": "normalize", "params": {"targetLufs": -9, "truePeak": -1, "lra": 6}},
    {"id": "b_normalize_06", "cat": "响度", "name": "无损留白", "desc": "-16 LUFS 但真峰值留到 -3，之后要转有损也不削波", "tier": "高级", "ico": "gain", "op": "normalize", "params": {"targetLufs": -16, "truePeak": -3, "lra": 11}},
    {"id": "b_waveform_01", "cat": "峰值", "name": "视频背景 4K", "desc": "3840×2160 白色透明底，剪映/PR 底图", "tier": "配置", "ico": "wave", "op": "waveform", "params": {"width": 3840, "height": 2160, "color": "#FFFFFF", "background": "transparent", "scale": "lin", "splitChannels": False}},
    {"id": "b_waveform_02", "cat": "峰值", "name": "视频角标条", "desc": "3840×120 白色透明底，顶部装饰条", "tier": "配置", "ico": "wave", "op": "waveform", "params": {"width": 3840, "height": 120, "color": "#FFFFFF", "background": "transparent", "scale": "lin", "splitChannels": False}},
    {"id": "b_waveform_03", "cat": "峰值", "name": "封面方形", "desc": "1500×1500 白色 + 黑底，播客/专辑封面", "tier": "配置", "ico": "wave", "op": "waveform", "params": {"width": 1500, "height": 1500, "color": "#FFFFFF", "background": "black", "scale": "lin", "splitChannels": False}},
    {"id": "b_waveform_04", "cat": "峰值", "name": "方形黑金", "desc": "1400×1400 金色 + 黑底，视觉风格化封面", "tier": "高级", "ico": "wave", "op": "waveform", "params": {"width": 1400, "height": 1400, "color": "#E8B44A", "background": "black", "scale": "sqrt", "splitChannels": False}},
    {"id": "b_waveform_05", "cat": "峰值", "name": "印刷用", "desc": "3000×800 黑色 + 白底，纸面/说明文档", "tier": "配置", "ico": "wave", "op": "waveform", "params": {"width": 3000, "height": 800, "color": "#000000", "background": "white", "scale": "lin", "splitChannels": False}},
    {"id": "b_waveform_06", "cat": "峰值", "name": "暗色主题", "desc": "1920×400 青色透明底，配深色主题", "tier": "配置", "ico": "wave", "op": "waveform", "params": {"width": 1920, "height": 400, "color": "#22D3EE", "background": "transparent", "scale": "lin", "splitChannels": False}},
    {"id": "b_waveform_07", "cat": "峰值", "name": "手机壁纸", "desc": "1170×2532 白色 + 黑底", "tier": "高级", "ico": "wave", "op": "waveform", "params": {"width": 1170, "height": 2532, "color": "#FFFFFF", "background": "black", "scale": "lin", "splitChannels": False}},
    {"id": "b_waveform_08", "cat": "峰值", "name": "诊断视图", "desc": "1920×600 对数刻度 + 左右分道，看弱音与声道差异", "tier": "高级", "ico": "wave", "op": "waveform", "params": {"width": 1920, "height": 600, "color": "#FFFFFF", "background": "transparent", "scale": "log", "splitChannels": True}},
    {"id": "b_waveform_09", "cat": "峰值", "name": "极简细线", "desc": "2000×160 白色透明底，页眉装饰", "tier": "配置", "ico": "wave", "op": "waveform", "params": {"width": 2000, "height": 160, "color": "#FFFFFF", "background": "transparent", "scale": "lin", "splitChannels": False}},
    {"id": "b_tags_01", "cat": "元数据", "name": "清空全部标签", "desc": "交付前洗白：删除所有标签（会先确认一次）", "tier": "高级", "ico": "tag", "op": "tags", "params": {"tags": {}, "clearMissing": True}},
    {"id": "b_tags_02", "cat": "元数据", "name": "统一专辑名", "desc": "把这一批的 album 写成同一个值（点击后填）", "tier": "配置", "ico": "tag", "op": "tags", "params": {"tags": {"album": ""}, "clearMissing": False}},
    {"id": "b_tags_03", "cat": "元数据", "name": "统一专辑艺人", "desc": "写 albumartist，让播放器正确分组合辑", "tier": "配置", "ico": "tag", "op": "tags", "params": {"tags": {"albumartist": ""}, "clearMissing": False}},
    {"id": "b_tags_04", "cat": "元数据", "name": "批量设置流派", "desc": "写 genre", "tier": "配置", "ico": "tag", "op": "tags", "params": {"tags": {"genre": ""}, "clearMissing": False}},
    {"id": "b_tags_05", "cat": "元数据", "name": "写入年份", "desc": "写 date", "tier": "配置", "ico": "tag", "op": "tags", "params": {"tags": {"date": ""}, "clearMissing": False}},
    {"id": "b_tags_06", "cat": "元数据", "name": "写入版权/来源", "desc": "写 comment，归档留痕", "tier": "高级", "ico": "tag", "op": "tags", "params": {"tags": {"comment": ""}, "clearMissing": False}},
    {"id": "b_tags_07", "cat": "元数据", "name": "清除备注", "desc": "把 comment 清空", "tier": "高级", "ico": "tag", "op": "tags", "params": {"tags": {"comment": ""}, "clearMissing": False}},
    {"id": "b_rename_01", "cat": "元数据", "name": "音轨号两位 - 标题", "desc": "01 - 标题.flac，补零后排序不乱", "tier": "一键", "ico": "tag", "op": "rename", "params": {"pattern": "{tracknumber:02} - {title}"}},
    {"id": "b_rename_02", "cat": "元数据", "name": "歌手 - 专辑 - 音轨号 - 标题", "desc": "完整归档命名", "tier": "配置", "ico": "tag", "op": "rename", "params": {"pattern": "{artist} - {album} - {tracknumber:02} - {title}"}},
    {"id": "b_rename_03", "cat": "元数据", "name": "日期_标题", "desc": "按日期归档", "tier": "配置", "ico": "tag", "op": "rename", "params": {"pattern": "{date}_{title}"}},
    {"id": "b_rename_04", "cat": "元数据", "name": "流派前缀", "desc": "[流派] 歌手 - 标题", "tier": "高级", "ico": "tag", "op": "rename", "params": {"pattern": "[{genre}] {artist} - {title}"}},
    {"id": "b_rename_05", "cat": "元数据", "name": "仅标题", "desc": "只留标题，其余丢掉", "tier": "高级", "ico": "tag", "op": "rename", "params": {"pattern": "{title}"}},
    {"id": "b_rename_06", "cat": "元数据", "name": "文件名清理", "desc": "借 sanitize_name 洗掉非法字符，不改语义", "tier": "高级", "ico": "tag", "op": "rename", "params": {"pattern": "{filename}"}},
    {"id": "b_cover_01", "cat": "封面", "name": "嵌封底", "desc": "写入 Back Cover", "tier": "配置", "ico": "cover", "op": "cover", "params": {"imagePath": "", "pictureType": "Back Cover"}},
    {"id": "b_cover_02", "cat": "封面", "name": "嵌艺人照", "desc": "写入 Artist 类型图片", "tier": "配置", "ico": "cover", "op": "cover", "params": {"imagePath": "", "pictureType": "Artist"}},
    {"id": "b_peaks_01", "cat": "峰值", "name": "精细峰值 4000 点", "desc": "放大查看用，点更密", "tier": "配置", "ico": "wave", "op": "peaks", "params": {"buckets": 4000, "force": False}},
    {"id": "b_peaks_02", "cat": "峰值", "name": "轻量峰值 200 点", "desc": "长音频/整批快速概览", "tier": "配置", "ico": "wave", "op": "peaks", "params": {"buckets": 200, "force": False}},
    {"id": "b_peaks_03", "cat": "峰值", "name": "强制重算峰值", "desc": "忽略缓存重新解码（源文件被外部改过时用）", "tier": "高级", "ico": "wave", "op": "peaks", "params": {"buckets": 1000, "force": True}},
]

CARD_CATS = ["格式转换", "元数据", "封面", "响度", "峰值", "校验/打包", "自定义"]

SNAPS_DEFAULT = ["转 FLAC", "标准化 -16 LUFS", "按标签重命名", "生成峰值图", "打包 ZIP"]

CARD_ICONS = ["flac", "mp3", "wav", "wave", "tag", "cover", "gain", "check", "zip", "folder"]


# ---------------------------------------------------------------- 校验

def _applies(spec: dict, params: dict) -> bool:
    """参数的 onlyIf 条件是否成立（例如 compressionLevel 只在 format=flac 时有意义）。"""
    cond = spec.get("onlyIf")
    if not cond:
        return True
    cur = params.get(cond["key"])
    return ("" if cur is None else str(cur)) in [str(v) for v in cond["in"]]


def _check_value(spec: dict, value: Any) -> Any:
    """按规格把值收敛/校验；不合法直接抛 ValueError（带人能看懂的原因）。"""
    key, label, typ = spec["key"], spec["label"], spec["type"]

    if typ == "bool":
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on")
        return bool(value)

    if typ == "int":
        try:
            n = int(value)
        except (TypeError, ValueError):
            raise ValueError(f"「{label}」必须是整数，收到 {value!r}") from None
        if "min" in spec and n < spec["min"]:
            raise ValueError(f"「{label}」不能小于 {spec['min']}，收到 {n}")
        if "max" in spec and n > spec["max"]:
            raise ValueError(f"「{label}」不能大于 {spec['max']}，收到 {n}")
        return n

    if typ == "float":
        try:
            x = float(value)
        except (TypeError, ValueError):
            raise ValueError(f"「{label}」必须是数字，收到 {value!r}") from None
        if "min" in spec and x < spec["min"]:
            raise ValueError(f"「{label}」不能小于 {spec['min']}，收到 {x}")
        if "max" in spec and x > spec["max"]:
            raise ValueError(f"「{label}」不能大于 {spec['max']}，收到 {x}")
        return x

    if typ == "enum":
        allowed = [o["value"] for o in spec.get("options", [])]
        s = "" if value is None else str(value)
        if s not in allowed:
            raise ValueError(f"「{label}」只能是 {allowed} 之一，收到 {value!r}")
        return s

    if typ == "tags":
        if value in (None, ""):
            return {}
        if isinstance(value, str):
            out: dict[str, str] = {}
            for line in value.splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" not in line:
                    raise ValueError(f"标签行必须是 key=value：{line!r}")
                k, _, v = line.partition("=")
                k = k.strip()
                if not k:
                    raise ValueError(f"标签名为空：{line!r}")
                out[k] = v.strip()
            return out
        if isinstance(value, dict):
            return {str(k): str(v) for k, v in value.items()}
        raise ValueError("标签必须是对象或 key=value 文本")

    # text
    s = "" if value is None else str(value)
    if spec.get("required") and not s.strip():
        raise ValueError(f"「{label}」不能为空")
    if len(s) > 500:
        raise ValueError(f"「{label}」太长了（上限 500 字符）")
    return s


def validate_card(card: dict, *, existing_ids: set[str] | None = None) -> dict:
    """校验并规范化一张卡片，返回干净的可存储对象。"""
    if not isinstance(card, dict):
        raise ValueError("卡片必须是对象")

    name = str(card.get("name") or "").strip()
    if not name:
        raise ValueError("卡片名称不能为空")
    if len(name) > 40:
        raise ValueError("卡片名称最长 40 字")

    op = str(card.get("op") or "").strip()
    if op not in OPS:
        raise ValueError(f"未知的操作：{op!r}；可用：{', '.join(OPS)}")

    cat = str(card.get("cat") or OPS[op]["category"]).strip()
    if cat not in CARD_CATS:
        # 原来是静默回落成"自定义"：用户导入卡片时写错分类名不报错，
        # 卡片会莫名其妙跑到「自定义」段里，很难查。
        raise ValueError(f"未知分类：{cat!r}；可用：{', '.join(CARD_CATS)}")

    ico = str(card.get("ico") or OPS[op]["icon"]).strip()
    if ico not in CARD_ICONS:
        ico = OPS[op]["icon"]

    tier = str(card.get("tier") or "自定义").strip()[:8]
    desc = str(card.get("desc") or OPS[op]["desc"]).strip()[:120]

    raw_params = card.get("params") or {}
    if not isinstance(raw_params, dict):
        raise ValueError("params 必须是对象")

    specs = {p["key"]: p for p in OPS[op]["params"]}
    unknown = [k for k in raw_params if k not in specs]
    if unknown:
        raise ValueError(f"「{OPS[op]['label']}」不认识这些参数：{', '.join(unknown)}")

    params: dict[str, Any] = {}
    for key, spec in specs.items():
        if not _applies(spec, raw_params):
            continue
        if key in raw_params:
            params[key] = _check_value(spec, raw_params[key])
        elif "default" in spec:
            params[key] = spec["default"]
    # 必填但没给值的，让这里统一报错
    for key, spec in specs.items():
        if not _applies(spec, params):
            continue
        if spec.get("required") and not str(params.get(key) or "").strip():
            raise ValueError(f"「{spec['label']}」是必填项")

    cid = str(card.get("id") or "").strip()
    if not cid:
        slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:24] or "card"
        # 用 md5 而不是内置 hash()：hash 对 str 每进程随机化，
        # 同一张卡两次生成的 id 会不一样
        digest = hashlib.md5(f"{op}|{name}".encode("utf-8")).hexdigest()[:6]
        cid = f"c_{slug}_{digest}"
    if existing_ids and cid in existing_ids:
        raise ValueError(f"卡片 id 已存在：{cid}")
    if not re.fullmatch(r"[A-Za-z0-9_\-]{1,64}", cid):
        raise ValueError(f"卡片 id 只能含字母数字下划线连字符：{cid!r}")

    out = {"id": cid, "cat": cat, "name": name, "desc": desc, "tier": tier,
           "ico": ico, "op": op, "params": params, "custom": True}
    note = str(card.get("note") or "").strip()[:300]
    if note:
        out["note"] = note
    return out


def render_preview(op: str, params: dict) -> str:
    """把参数代进模板，给用户看"这张卡等价于什么命令"。"""
    spec = OPS.get(op)
    if not spec:
        return ""
    tpl = spec.get("preview", "")

    # convert 的 {codec}/{extra} 不是参数名，必须在通用替换之前先展开，
    # 否则会被当成"未知占位符"替换成空串。
    if op == "convert":
        fmt = str(params.get("format") or "flac")
        codec = " ".join(FORMAT_ARGS.get(fmt, []))
        extra: list[str] = []
        if fmt == "flac" and params.get("compressionLevel") not in (None, ""):
            extra += ["-compression_level", str(params["compressionLevel"])]
        if params.get("bitrate"):
            extra += ["-b:a", str(params["bitrate"])]
        if params.get("sampleRate"):
            extra += ["-ar", str(params["sampleRate"])]
        if params.get("channels"):
            extra += ["-ac", str(params["channels"])]
        if params.get("keepTags", True):
            extra += ["-map_metadata", "0"]
        extra += ["-map", "0" if params.get("keepCover", True) else "0:a"]
        tpl = tpl.replace("{codec}", codec).replace("{extra}", " ".join(extra))

    def sub(m):
        k = m.group(1)
        v = params.get(k)
        return "" if v in (None, "") else str(v)

    out = re.sub(r"\{([a-zA-Z_]+)\}", sub, tpl)

    if op == "tag_edit":
        tags = params.get("tags") or {}
        if isinstance(tags, str):
            tags = {}
        bits = " ".join(f"--set-tag={k.upper()}={v}" for k, v in tags.items())
        out = out.replace("--set-tag=TITLE=... --remove-tag=...", bits or "--set-tag=KEY=VALUE")
    if op == "cover_embed":
        out = out.replace("<图片>", str(params.get("imagePath") or "<图片>"))

    return re.sub(r"\s{2,}", " ", out).strip()


# ---------------------------------------------------------------- 存储

def _read_custom() -> list[dict]:
    if not CARDS_JSON.exists():
        return []
    try:
        data = json.loads(CARDS_JSON.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    items = data.get("cards") if isinstance(data, dict) else data
    return [c for c in (items or []) if isinstance(c, dict)]


def _read_file() -> dict:
    """读整份配置（cards + snapshots）。"""
    if not CARDS_JSON.exists():
        return {"version": 1, "cards": [], "snapshots": []}
    try:
        data = json.loads(CARDS_JSON.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "cards": [], "snapshots": []}
    if isinstance(data, list):                       # 兼容早期只有数组的格式
        return {"version": 1, "cards": data, "snapshots": []}
    data.setdefault("cards", [])
    data.setdefault("snapshots", [])
    data.setdefault("version", 1)
    return data


def _write_file(payload: dict) -> None:
    tmp = CARDS_JSON.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(CARDS_JSON)          # 原子替换，别写一半把用户配置弄丢


def _write_custom(items: list[dict]) -> None:
    data = _read_file()
    data["cards"] = items
    _write_file(data)


# ---------------------------------------------------------------- 快照

SNAP_COUNT = 5


def snapshots() -> list[str]:
    """抽屉顶部那排快照。用户拖拽改过就用他的，否则用默认。

    要过滤掉已经不存在的卡片名：卡片是能被删的，快照里留个找不到的洞
    会让前端渲染出一个空白格子。
    """
    raw = [n for n in (_read_file().get("snapshots") or []) if isinstance(n, str)]
    if not raw:
        return list(SNAPS_DEFAULT)
    valid = {c["name"] for c in all_cards()}
    kept = [n for n in raw if n in valid]
    return kept or list(SNAPS_DEFAULT)


def set_snapshots(names: list) -> list[str]:
    """存快照。只接受"存在的卡片名"，其余丢弃 —— 卡片被删了快照不该留个洞。"""
    valid = {c["name"] for c in all_cards()}
    clean: list[str] = []
    for n in names or []:
        n = str(n).strip()
        if not n or n in clean:
            continue
        if n not in valid:
            raise ValueError(f"没有这张卡片：{n}")
        clean.append(n)
    if not clean:
        raise ValueError("快照不能为空")
    if len(clean) > 12:
        raise ValueError("快照最多 12 个")
    with _lock:
        data = _read_file()
        data["snapshots"] = clean
        _write_file(data)
    return clean


def builtin() -> list[dict]:
    return [dict(c, custom=False) for c in BUILTIN_CARDS]


def custom() -> list[dict]:
    return [dict(c, custom=True) for c in _read_custom()]


def all_cards() -> list[dict]:
    return builtin() + custom()


def custom_ids() -> set[str]:
    return {c.get("id") for c in _read_custom()}


def add(card: dict) -> dict:
    ids = custom_ids() | {c["id"] for c in BUILTIN_CARDS}
    clean = validate_card(card, existing_ids=ids)
    with _lock:
        items = _read_custom()
        items.append(clean)
        _write_custom(items)
    return clean


def update(cid: str, card: dict) -> dict:
    if cid in {c["id"] for c in BUILTIN_CARDS}:
        raise ValueError("内置卡片不能直接改，请用「另存为新卡片」")
    with _lock:
        items = _read_custom()
        idx = next((i for i, c in enumerate(items) if c.get("id") == cid), None)
        if idx is None:
            raise KeyError(cid)
        # id 不允许改：任务历史/执行链里可能出现它
        clean = validate_card({**card, "id": cid})
        items[idx] = clean
        _write_custom(items)
    return clean


def remove(cid: str) -> bool:
    if cid in {c["id"] for c in BUILTIN_CARDS}:
        raise ValueError("内置卡片不能删除")
    with _lock:
        items = _read_custom()
        left = [c for c in items if c.get("id") != cid]
        if len(left) == len(items):
            return False
        _write_custom(left)
    return True


def reset() -> int:
    """清空自定义卡片（测试用）。"""
    with _lock:
        n = len(_read_custom())
        data = _read_file()
        data["cards"] = []
        _write_file(data)
    return n


def reset_snapshots() -> list[str]:
    """恢复默认快照。"""
    with _lock:
        data = _read_file()
        data["snapshots"] = []
        _write_file(data)
    return list(SNAPS_DEFAULT)

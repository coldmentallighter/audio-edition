"""69 张内置卡片。

全部只靠"同 op、不同参数组合"堆出来 —— 新增卡片时如果落在已有 op 上，
这个文件是唯一要改的地方（见 布局规格.md 第 11 节）。
"""
from __future__ import annotations

from typing import Any

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



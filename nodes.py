"""音频增强加载 / 格式转换 / 平台发布 —— 全中文界面自定义节点。

本文件包含两个节点：

1. 加载音频(增强)  LoadAudioAdvanced
   - 覆盖官方 Load Audio 的全部使用方式（input 目录下拉选择）
   - 支持填写本地任意绝对路径加载，且不会复制任何副本
   - 支持直接读取主流视频文件（mp4 / mkv / mov / avi / webm / flv / ts ...）并提取音频
   - 支持长音频截断：从头截取指定时长 / 起点到终点 / 起点到结尾

2. 保存音频(平台发布)  SaveAudioPlatformExport
   - 一次产出 WAV / MP3 / FLAC / OPUS 多格式（多格式共用一次 ffmpeg 调用）
   - 参数支持 auto：自动适配源音频参数（采样率 / 声道 / 位深 / 码率）
   - 与「加载音频(增强)」直连做格式转换时，同格式且参数全为 auto → 直接复制，0 秒完成
   - 平台合规校验条件可自定义，内置国内主流平台预设
"""

import os
import re
import shutil
import struct
import subprocess
import tempfile
from shutil import which

import numpy as np
import torch

import folder_paths

try:
    import av
except Exception:  # pragma: no cover - 运行环境缺少 PyAV 时给出友好提示
    av = None

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

_WAV_CODECS = {"16": "pcm_s16le", "24": "pcm_s24le", "32": "pcm_s32le"}

# Opus 编码器仅支持这些采样率，其余需就近吸附
_OPUS_RATES = (8000, 12000, 16000, 24000, 48000)

# 浏览器 <audio> 兼容性优先级，用于挑选试听文件（MP3 最通用）
_PLAYBACK_ORDER = ("MP3", "WAV", "FLAC", "OPUS")

_AUDIO_EXTS = ("wav", "mp3", "opus", "flac")

# MP3 V0 为 VBR，平均码率约 245kbps，用于平台码率校验
_MP3_V0_KBPS = 245

# auto 模式下的候选码率
_MP3_KBPS_CHOICES = (128, 192, 256, 320)
_OPUS_KBPS_CHOICES = (64, 96, 128, 192, 320)

# PCM 编解码器 → 位深
_PCM_BITS = {
    "pcm_u8": 8, "pcm_s8": 8,
    "pcm_s16le": 16, "pcm_s16be": 16, "pcm_u16le": 16, "pcm_u16be": 16,
    "pcm_s24le": 24, "pcm_s24be": 24, "pcm_u24le": 24, "pcm_u24be": 24,
    "pcm_s32le": 32, "pcm_s32be": 32, "pcm_u32le": 32, "pcm_u32be": 32,
    "pcm_f32le": 32, "pcm_f32be": 32, "pcm_f64le": 64, "pcm_f64be": 64,
}

# 无损编码器
_LOSSLESS_CODECS = {"flac", "alac", "wavpack", "ape", "tta", "truehd"}

# 支持的媒体扩展名（音频 + 视频）
MEDIA_EXTENSIONS = (
    # 音频
    "wav", "wave", "mp3", "flac", "opus", "ogg", "oga", "m4a", "aac", "wma",
    "aiff", "aif", "ape", "wv", "amr", "ac3", "dts", "mka",
    # 视频
    "mp4", "m4v", "mkv", "mov", "avi", "webm", "flv", "f4v", "ts", "mts", "m2ts",
    "wmv", "mpg", "mpeg", "vob", "3gp", "rm", "rmvb", "ogv",
)

# 国内主流平台预设：常见审核要求参考值。平台规则可能调整，必要时改用「Custom」。
_PLATFORM_PRESETS = {
    "Soda Music": {"formats": "wav,mp3", "min_rate": 44100, "min_bits": 16, "min_kbps": 320, "channels": "stereo", "max_mb": 200},
    "QQ Music": {"formats": "wav,mp3", "min_rate": 44100, "min_bits": 16, "min_kbps": 320, "channels": "stereo", "max_mb": 200},
    "NetEase Cloud Music": {"formats": "wav,mp3", "min_rate": 44100, "min_bits": 16, "min_kbps": 320, "channels": "stereo", "max_mb": 200},
    "Kugou Music": {"formats": "wav,mp3", "min_rate": 44100, "min_bits": 16, "min_kbps": 320, "channels": "stereo", "max_mb": 200},
    "Douyin": {"formats": "wav,mp3", "min_rate": 44100, "min_bits": 16, "min_kbps": 320, "channels": "stereo", "max_mb": 200},
    "Kuaishou": {"formats": "wav,mp3", "min_rate": 44100, "min_bits": 16, "min_kbps": 192, "channels": "stereo", "max_mb": 200},
    "Bilibili": {"formats": "wav,flac,mp3", "min_rate": 44100, "min_bits": 16, "min_kbps": 320, "channels": "stereo", "max_mb": 200},
}

# 旧版中文平台名 → 现用英文名。用于读取旧工作流中的平台预设值。
# 汽水音乐与 QQ 音乐是两个不同平台，在旧版本里也是分开的两项，
# 因此这里必须一一对应，不能合并。
_PLATFORM_ALIASES = {
    "汽水音乐": "Soda Music",
    "QQ音乐": "QQ Music",
    "网易云音乐": "NetEase Cloud Music",
    "酷狗音乐": "Kugou Music",
    "抖音": "Douyin",
    "快手": "Kuaishou",
    "哔哩哔哩": "Bilibili",
    "自定义": "Custom",
    "不校验": "No validation",
}

CUSTOM_PRESET = "Custom"
NO_CHECK_PRESET = "No validation"

# 预设下拉项：具体平台 + Custom + No validation
_PRESET_CHOICES = list(_PLATFORM_PRESETS.keys()) + [CUSTOM_PRESET, NO_CHECK_PRESET]

# AUDIO 数据里携带源文件信息的键名（节点直连时用于判断能否「同格式直出」）
_SOURCE_KEY = "_audio_source"


# ---------------------------------------------------------------------------
# 报告语言
#
# 报告文本本身不受 ComfyUI 的 locale 机制管理（它是一个普通的字符串输出），
# 所以语言由节点上的开关决定。默认跟随界面语言：ComfyUI 界面为中文时出中文
# 报告，为英文时出英文报告；也可以在节点上手动锁定某一种语言。
# ---------------------------------------------------------------------------

LANG_FOLLOW_UI = "Follow UI language"
LANG_EN = "English"
LANG_ZH = "中文"
LANG_CHOICES = [LANG_FOLLOW_UI, LANG_EN, LANG_ZH]

_LANG_ALIASES = {
    "auto": LANG_FOLLOW_UI,
    "follow": LANG_FOLLOW_UI,
    "跟随界面": LANG_FOLLOW_UI,
    "跟随界面语言": LANG_FOLLOW_UI,
    "en": LANG_EN,
    "english": LANG_EN,
    "英文": LANG_EN,
    "zh": LANG_ZH,
    "cn": LANG_ZH,
    "chinese": LANG_ZH,
    "中文": LANG_ZH,
}

# 前端上报的界面语言。用可变容器而不是普通变量，方便测试里替换。
_UI_LANGUAGE = {"value": ""}

# 报告文案。key 为英文，值为 (中文, English)；语言在 _tr() 里选择。
_TR = {
    # 加载节点
    "loaded":            ("已加载：{name}", "Loaded: {name}"),
    "source_dir":        ("来源：ComfyUI input 目录", "Source: ComfyUI input directory"),
    "path":              ("路径：{path}", "Path: {path}"),
    "sample_rate_ch":    ("采样率：{rate} Hz    声道：{ch}", "Sample rate: {rate} Hz    Channels: {ch}"),
    "original_duration": ("原始时长：{dur}", "Original duration: {dur}"),
    "bitrate":           ("码率：约 {kbps} kbps（{kind}）", "Bitrate: approx {kbps} kbps ({kind})"),
    "lossless":          ("无损", "lossless"),
    "lossy":             ("有损", "lossy"),
    "from_video":        ("视频文件：是（已从中提取音频）", "Video file: yes (audio extracted from it)"),
    "multi_audio":       ("音频流数：共 {n} 条（使用第 {used} 条）", "Audio streams: {n} (using track {used})"),
    "truncated":         ("已截断：{start}s - {end}s（{dur}，模式：{mode}）",
                          "Truncated: {start}s - {end}s ({dur}, mode: {mode})"),
    "output":            ("输出：{dur} / {rate} Hz / {ch}", "Output: {dur} / {rate} Hz / {ch}"),
    "res_fps":           ("分辨率：{w}x{h}    帧率：{fps}", "Resolution: {w}x{h}    Frame rate: {fps}"),
    "v_codec":           ("视频编码：{v}", "Video codec: {v}"),
    "audio_yes":         ("音轨：有（{codec} / {rate} Hz / {ch}）",
                          "Audio track: yes ({codec} / {rate} Hz / {ch})"),
    "v_streams":         ("视频流数：第 {used} 条，共 {n} 条",
                          "Video streams: #{used} of {n}"),
    "trunc_to_end":      ("已截断：{start}s 到结尾（模式：{mode}）",
                          "Truncated: {start}s to the end (mode: {mode})"),
    "text_end":          ("结尾", "end"),
    "v_output":          ("输出：{dur} / {w}x{h} / {fps}",
                          "Output: {dur} / {w}x{h} / {fps}"),

    # 平台合规报告
    "check_disabled":    ("平台合规检查：已关闭（预设 = 不校验）",
                          "Platform compliance check: disabled (preset = No validation)"),
    "check_title":       ("平台合规检查（平台：{plat} - {cond}）",
                          "Platform compliance check (platform: {plat} - {cond})"),
    "cond_preset":       ("预设条件", "preset conditions"),
    "cond_disabled":     ("已关闭", "disabled"),
    "cond_custom":       ("自定义条件", "custom conditions"),
    "requirements":      ("要求：", "Requirements: "),
    "req_formats":       ("格式={v}", "formats={v}"),
    "req_rate":          ("采样率≥{v}kHz", "sample rate>={v}kHz"),
    "req_bits":          ("位深≥{v}bit（仅 WAV）", "bit depth>={v}bit (WAV only)"),
    "req_bitrate":       ("码率≥{v}kbps（仅 MP3/OPUS）", "bitrate>={v}kbps (MP3/OPUS only)"),
    "req_channels":      ("声道={v}", "channels={v}"),
    "req_size":          ("单文件≤{v}MB", "file size<={v}MB"),
    "not_set":           ("未设置", "not set"),
    "source_file":       ("源文件：{name}（{fmt}）", "Source file: {name}  ({fmt})"),
    "source_outside":    ("源文件不在 ComfyUI 目录内，改由内存音频重新编码", "the recorded source file is outside the ComfyUI directories, re-encoded from memory"),
    "adapted":           ("适配参数：{v}", "Adapted parameters: {v}"),
    "lossless_master":   ("无损母带", "lossless master"),
    "approx":            ("约 {v}kbps", "approx {v}kbps"),
    "tag_same_format":   ("（同格式直转）", " (same-format copy)"),
    "tag_extra":         ("（额外格式）", " (extra)"),
    "note_rate_low":     ("采样率<{v}kHz", "sample rate<{v}kHz"),
    "note_bits_low":     ("位深<{v}bit", "bit depth<{v}bit"),
    "note_bitrate_low":  ("码率<{v}kbps", "bitrate<{v}kbps"),
    "note_not_stereo":   ("非双声道", "not stereo"),
    "note_not_mono":     ("非单声道", "not mono"),
    "note_size_over":    ("文件超过 {v}MB", "file size over {v}MB"),
    "copied_direct":     ("有 {n} 个格式与源文件一致，已原样复制（瞬时，未重新编码）",
                          "{n} format(s) matched the source and were copied as-is "
                          "(instant, no re-encoding)"),
    "output_folder":     ("输出目录：{folder}", "Output folder: {folder}"),
    "preview_file":      ("试听文件：{name}", "Preview file: {name}"),
    "result":            ("结果：{v}", "Result: {v}"),
    "v_disabled":        ("平台校验已关闭，文件已写出", "Platform validation disabled; files were written"),
    "v_no_formats":      ("未设置合格格式，未做平台校验", "No accepted formats set; no platform validation was performed"),
    "v_no_match":        ("未产出平台要求的格式（要求：{v}）",
                          "No format required by the platform was produced (required: {v})"),
    "v_ok":              ("“{plat}”的全部要求均已满足", "All requirements for \"{plat}\" are satisfied"),
    "v_bad":             ("部分要求未满足，请调整参数", "Some requirements are not met; adjust the parameters"),

    # 视频报告
    "video_report":      ("视频报告", "Video report"),
    "source_label":      ("源文件", "Source"),
    "exported":          ("输出文件：{n} 个", "Exported files: {n}"),
    "exported_none":     ("输出文件：无记录", "Exported files: none recorded"),
    "container":         ("封装格式", "Container"),
    "resolution":        ("分辨率", "Resolution"),
    "fps":               ("帧率", "Frame rate"),
    "video_codec":       ("视频编码", "Video codec"),
    "pixel_format":      ("像素格式", "Pixel format"),
    "duration":          ("时长", "Duration"),
    "file_size":         ("文件大小", "File size"),
    "audio_track":       ("音轨", "Audio"),
    "no_audio_track":    ("无", "none"),
    "video_streams":     ("视频流数：{n}", "Video streams: {n}"),
    "reencoded":         ("重新编码", "transcoded"),
    "direct_copy":       ("同格式直转（未重新编码）", "same-format copy (no re-encoding)"),
    "export_label":      ("输出 {n}（{kind}，{how}）", "Export {n} ({kind}, {how})"),
    "probe_failed":      ("（无法读取该文件的参数：{error}）",
                          "(could not read this file's parameters: {error})"),
    "file_missing":      ("（文件不存在：{path}）", "(file does not exist: {path})"),
    "not_file_input":    ("源文件：不是文件型输入（无磁盘上的源文件可检查）",
                          "Source: not a file-backed input (no source file on disk to inspect)"),
}


def _tr(key, lang, **kw):
    """按语言取出报告文案并格式化。"""
    entry = _TR.get(key)
    if entry is None:
        return key
    text = entry[0] if lang == "zh" else entry[1]
    return text.format(**kw) if kw else text


def _channel_label(value, lang):
    """声道数的报告用名称。"""
    if lang != "zh":
        return value
    return {STEREO: "双声道", MONO: "单声道", UNLIMITED: "不限"}.get(value, value)


def _norm_lang(value):
    """把语言开关归一为标准值，兼容旧工作流与大小写差异。"""
    text = str("" if value is None else value).strip()
    if text in LANG_CHOICES:
        return text
    return _LANG_ALIASES.get(text.lower(), text)


def _ui_language():
    """返回前端上报的界面语言；未上报时按英文处理。

    ComfyUI 的界面语言只存在于浏览器端，后端无法自行读取，所以由
    web/media_toolbox.js 在启动与语言切换时通过
    POST /audio_platform_export/ui_language 上报到这里。
    """
    return _UI_LANGUAGE["value"]


def set_ui_language(locale):
    """记录前端上报的界面语言。"""
    text = str(locale or "").strip()
    _UI_LANGUAGE["value"] = text
    return text


def _resolve_report_lang(value):
    """把开关值解析为最终报告语言，返回 "zh" 或 "en"。"""
    lang = _norm_lang(value)
    if lang == LANG_ZH:
        return "zh"
    if lang == LANG_EN:
        return "en"
    return "zh" if _ui_language().lower().startswith("zh") else "en"


# ---------------------------------------------------------------------------
# 下拉选项
#
# 节点界面上所有下拉项一律使用英文；内部计算也直接比较这些英文值。
# 早期版本的下拉项是中文，且已被写入用户保存的工作流，因此
# _CHOICE_ALIASES / _TRUNCATE_ALIASES 保留全部旧中文值，
# 读取时统一走 _norm_choice() / _norm_truncate() 归一化，
# 保证老工作流打开后不会报错、也不会退化成 Unknown 选项。
# 中文界面请通过 locales/zh/ 由 ComfyUI 的语言机制提供。
# ---------------------------------------------------------------------------

AUTO = "Auto (follow source)"
STEREO = "Stereo"
MONO = "Mono"
UNLIMITED = "Any"
V0_BEST = "V0 (highest quality)"

# 旧版中文值 → 现用英文值。读取工作流时用它把历史值映射到当前选项。
_CHOICE_ALIASES = {
    "auto": AUTO,
    "stereo": STEREO,
    "mono": MONO,
    "unlimited": UNLIMITED,
    "v0": V0_BEST,
    "自动（跟随源）": AUTO,
    "双声道": STEREO,
    "单声道": MONO,
    "不限": UNLIMITED,
    "V0（最高质量）": V0_BEST,
}

# 截断方式：显示值即语义，内部直接比较显示值
TRUNCATE_NONE = "No truncation (use the whole file)"
TRUNCATE_HEAD = "Head segment only (0 s -> duration)"
TRUNCATE_RANGE = "Middle range only (start -> end)"
TRUNCATE_TAIL = "From start to the end (start -> end of file)"

TRUNCATE_CHOICES = [TRUNCATE_NONE, TRUNCATE_HEAD, TRUNCATE_RANGE, TRUNCATE_TAIL]

_TRUNCATE_ALIASES = {
    TRUNCATE_NONE: TRUNCATE_NONE,
    TRUNCATE_HEAD: TRUNCATE_HEAD,
    TRUNCATE_RANGE: TRUNCATE_RANGE,
    TRUNCATE_TAIL: TRUNCATE_TAIL,
    "不截断": TRUNCATE_NONE,
    "不截断（使用完整音频）": TRUNCATE_NONE,
    "从头截取时长": TRUNCATE_HEAD,
    "只取开头一段（0 秒 → 截取时长）": TRUNCATE_HEAD,
    "起点到终点": TRUNCATE_RANGE,
    "只取中间一段（起点 → 终点）": TRUNCATE_RANGE,
    "起点到结尾": TRUNCATE_TAIL,
    "从起点一直到结尾（起点 → 末尾）": TRUNCATE_TAIL,
}


def _norm_choice(value):
    """把下拉值归一为英文标准值，兼容旧工作流中的中文与早期英文选项。"""
    text = str("" if value is None else value).strip()
    if text in _CHOICE_ALIASES.values():
        return text
    if text in _CHOICE_ALIASES:
        return _CHOICE_ALIASES[text]
    return _CHOICE_ALIASES.get(text.lower(), text)


def _norm_truncate(value):
    """把截断方式归一为英文标准值，兼容旧工作流中的中文选项。"""
    text = str("" if value is None else value).strip()
    return _TRUNCATE_ALIASES.get(text, text)


def _choice_int(value, default):
    """从下拉值里取出整数（兼容 "24-bit" / 旧版中文 "24 位" / 纯数字 "24"）。"""
    digits = re.sub(r"\D", "", str(value))
    return int(digits) if digits else default


def _sample_rate_hz(value, default):
    """把采样率下拉值解析成 Hz，兼容 "44.1 kHz" / "48000 Hz" / 旧版中文写法。"""
    head = str(value).strip().lower()
    match = re.search(r"\d+(?:\.\d+)?", head)
    if not match:
        return default
    number = float(match.group())
    return int(round(number * 1000)) if "khz" in head else int(number)


# ---------------------------------------------------------------------------
# 通用工具
# ---------------------------------------------------------------------------

# ffmpeg 缺失时的提示。ffmpeg 不是 ComfyUI 自带的，需要单独安装。
_FFMPEG_HINT = """ffmpeg was not found, so format conversion is unavailable.

ffmpeg does not ship with ComfyUI and must be installed separately. Pick one of these options:

1. Install the Python-provided build (simplest):
     pip install imageio-ffmpeg

2. Already have ffmpeg: add the directory containing ffmpeg.exe to the system PATH,
   or set the FFMPEG_BINARY environment variable to the ffmpeg executable, then restart ComfyUI.

Confirm it is ready with:
  python -c "import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())"
"""


def find_ffmpeg():
    """按优先级定位 ffmpeg：环境变量 → imageio-ffmpeg 自带 → PATH。"""
    for key in ("FFMPEG_BINARY", "IMAGEIO_FFMPEG_EXE"):
        candidate = os.environ.get(key)
        if candidate and os.path.isfile(candidate):
            return candidate

    try:
        import imageio_ffmpeg

        binaries = os.path.join(os.path.dirname(imageio_ffmpeg.__file__), "binaries")
        for candidate in sorted(os.listdir(binaries)):
            if not candidate.startswith("ffmpeg"):
                continue
            full = os.path.join(binaries, candidate)
            if os.path.isfile(full) and not candidate.endswith((".txt", ".json", ".md")):
                return full
    except Exception:
        pass

    return which("ffmpeg")


def _f32_pcm(wav):
    """把整数 PCM 归一化到 float32 [-1, 1]（与官方实现保持一致）。"""
    if wav.dtype.is_floating_point:
        return wav
    if wav.dtype == torch.int16:
        return wav.float() / (2 ** 15)
    if wav.dtype == torch.int32:
        return wav.float() / (2 ** 31)
    if wav.dtype == torch.uint8:
        return (wav.float() - 128.0) / 128.0
    raise ValueError(f"Unsupported sample format: {wav.dtype}")


def _read_wav_header(path):
    """解析 WAV 的 fmt 块，拿到真实采样率/位深/声道（不依赖 ffprobe）。"""
    try:
        with open(path, "rb") as handle:
            head = handle.read(4096)
    except OSError:
        return None

    if len(head) < 12 or head[:4] != b"RIFF" or head[8:12] != b"WAVE":
        return None

    offset = 12
    while offset + 8 <= len(head):
        chunk_id = head[offset:offset + 4]
        chunk_size = struct.unpack("<I", head[offset + 4:offset + 8])[0]
        if chunk_id == b"fmt " and offset + 24 <= len(head):
            _, channels, rate, _, _, bits = struct.unpack("<HHIIHH", head[offset + 8:offset + 24])
            return {"sample_rate": rate, "channels": channels, "bits": bits}
        offset += 8 + chunk_size + (chunk_size & 1)

    return None


def _detect_format(ext, codec, has_video=False):
    """归一化容器/编码格式名，用于判断「同格式」。"""
    ext = (ext or "").lower()
    codec = (codec or "").lower()
    if ext in ("wav", "wave"):
        return "wav"
    if ext == "mp3":
        return "mp3"
    if ext == "flac":
        return "flac"
    if ext == "opus":
        return "opus"
    if ext == "ogg":
        return "opus" if "opus" in codec else "ogg"
    if ext == "m4a":
        return "m4a"
    if ext in ("mp4", "m4v") and not has_video and codec in ("aac", "alac"):
        return "m4a"
    return ext or codec or "unknown"


def _probe_media(path, track_index=0):
    """读取媒体文件的音频参数（不依赖 ffprobe，使用 PyAV）。"""
    info = {
        "path": os.path.abspath(path),
        "size": os.path.getsize(path),
        "ext": os.path.splitext(path)[1].lower().lstrip("."),
    }

    if av is None:
        info.update({
            "format": info["ext"], "codec": None, "sample_rate": None, "channels": None,
            "bits": None, "bitrate_kbps": None, "duration": None,
            "has_video": False, "is_lossless": None, "audio_streams": 1,
        })
        return info

    with av.open(path) as container:
        duration = float(container.duration / av.time_base) if container.duration else None
        info["duration"] = duration
        info["has_video"] = len(container.streams.video) > 0

        streams = list(container.streams.audio)
        if not streams:
            raise ValueError("This file contains no audio stream and cannot be loaded.")
        if track_index >= len(streams):
            track_index = 0
        info["track_index"] = track_index
        info["audio_streams"] = len(streams)

        stream = streams[track_index]
        codec = stream.codec_context.name
        info["codec"] = codec
        info["sample_rate"] = int(stream.codec_context.sample_rate or getattr(stream, "rate", 0) or 44100)
        info["channels"] = int(stream.channels or 1)

        bits = _PCM_BITS.get(codec)
        if bits is None and info["ext"] in ("wav", "wave"):
            header = _read_wav_header(path)
            if header:
                bits = header["bits"]
        info["bits"] = bits

        kbps = None
        bit_rate = getattr(stream, "bit_rate", None)
        if bit_rate:
            kbps = int(round(bit_rate / 1000))
        elif duration:
            kbps = int(round(info["size"] * 8 / duration / 1000))
        info["bitrate_kbps"] = kbps

        info["is_lossless"] = codec in _LOSSLESS_CODECS or codec.startswith("pcm") or info["ext"] in ("wav", "flac")
        info["format"] = _detect_format(info["ext"], codec, info["has_video"])

    return info


def _decode_with_av(path, track_index=0):
    """用 PyAV 解码音频/视频文件中的音频流，返回 (波形, 采样率, 声道数)。"""
    if av is None:
        raise RuntimeError("PyAV (av) is not available in this environment, so media files cannot be decoded.")

    with av.open(path) as container:
        streams = list(container.streams.audio)
        if not streams:
            raise ValueError("该文件不包含音频流，无法加载。")
        if track_index >= len(streams):
            track_index = 0
        stream = streams[track_index]
        sample_rate = int(stream.codec_context.sample_rate or getattr(stream, "rate", 0) or 44100)
        n_channels = int(stream.channels or 1)

        frames = []
        for frame in container.decode(streams=stream.index):
            buf = torch.from_numpy(frame.to_ndarray())
            if buf.shape[0] != n_channels:
                buf = buf.view(-1, n_channels).t()
            frames.append(buf)

        if not frames:
            raise ValueError("No audio frames could be decoded from this file.")

        waveform = _f32_pcm(torch.cat(frames, dim=1))
        return waveform, sample_rate, n_channels


def _decode_with_ffmpeg(path, ffmpeg, track_index=0):
    """PyAV 解码失败时，退回 ffmpeg 先抽取音频再解码。"""
    handle = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    handle.close()
    temp_path = handle.name
    try:
        cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
               "-i", path, "-map", f"0:a:{track_index}", "-f", "wav", temp_path]
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if result.returncode != 0:
            raise RuntimeError("ffmpeg audio extraction failed: " + result.stderr.decode("utf-8", "ignore")[-600:])
        return _decode_with_av(temp_path, 0)
    finally:
        try:
            os.unlink(temp_path)
        except OSError:
            pass


def load_media(path, track_index=0, ffmpeg=None):
    """加载任意音频/视频文件的音频，PyAV 优先，ffmpeg 兜底。"""
    try:
        return _decode_with_av(path, track_index)
    except Exception as first_error:
        if ffmpeg:
            try:
                return _decode_with_ffmpeg(path, ffmpeg, track_index)
            except Exception:
                pass
        raise first_error


def _apply_truncate(waveform, sample_rate, mode, start, end, duration):
    """按时间范围截取波形，返回 (新波形, (起点, 终点) 或 None)。"""
    total = waveform.shape[-1]
    mode = _norm_truncate(mode)
    if mode == TRUNCATE_NONE:
        return waveform, None

    if mode == TRUNCATE_HEAD:
        begin, finish = 0.0, float(duration)
    elif mode == TRUNCATE_RANGE:
        begin, finish = float(start), float(end)
    elif mode == TRUNCATE_TAIL:
        begin, finish = float(start), None
    else:
        raise ValueError(
            "Unknown truncation mode: " + str(mode) + ". Please pick one from the dropdown again."
        )

    if finish is not None and finish <= begin:
        raise ValueError(
            f"End must be greater than Start: current start {begin:.2f}s, end {finish:.2f}s."
        )

    i0 = max(0, int(round(begin * sample_rate)))
    i1 = total if finish is None else min(total, int(round(finish * sample_rate)))

    if i0 >= total:
        raise ValueError(
            f"Start {begin:.2f}s is beyond the total audio duration of {total / sample_rate:.2f}s; reduce Start."
        )
    if i1 <= i0:
        raise ValueError(
            "The truncation result is 0 seconds; check that the truncation mode matches the time parameters below."
        )

    return waveform[..., i0:i1], (i0 / sample_rate, i1 / sample_rate)


def _format_duration(seconds):
    if seconds is None:
        return "unknown"
    seconds = float(seconds)
    minutes, secs = divmod(seconds, 60)
    hours, minutes = divmod(int(minutes), 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{secs:05.2f}"
    return f"{minutes:02d}:{secs:05.2f}"


def _to_waveform(audio):
    """把任意音频输出节点的 AUDIO 统一成 (channels, samples) 的 float32 数组。"""
    waveform = audio["waveform"]
    source_rate = int(audio["sample_rate"])
    if hasattr(waveform, "detach"):
        waveform = waveform.detach().cpu().float().numpy()
    waveform = np.asarray(waveform, dtype=np.float32)
    if waveform.ndim == 3:
        waveform = waveform[0]
    if waveform.ndim == 1:
        waveform = waveform[np.newaxis, :]
    return waveform, source_rate, int(waveform.shape[0])


def _opus_rate(rate):
    """Opus 只接受 8/12/16/24/48kHz，其它一律吸附到 48kHz。"""
    return rate if rate in _OPUS_RATES else 48000


def _pick_playback(produced):
    """按浏览器兼容性从已产出文件中挑一个用于节点内试听。"""
    for kind in _PLAYBACK_ORDER:
        for entry in produced:
            if entry[0] == kind:
                return entry[0], entry[1]
    return produced[0][0], produced[0][1]


def _parse_formats(text):
    """把 "wav,mp3" 这类文本解析成小写格式集合，兼容中英文分隔符。"""
    cleaned = text.replace("，", ",").replace("、", ",").replace(" ", ",")
    return {token.strip().lower() for token in cleaned.split(",") if token.strip()}


def _next_counter(folder, name):
    """扫描目标目录，返回下一个可用的递增序号（避免覆盖已有文件）。"""
    try:
        entries = os.listdir(folder)
    except OSError:
        return 0

    highest = -1
    prefix = name + "_"
    for entry in entries:
        stem, dot, ext = entry.rpartition(".")
        if not dot or ext.lower() not in _AUDIO_EXTS:
            continue
        if not stem.startswith(prefix):
            continue
        tail = stem[len(prefix):]
        if tail.isdigit():
            highest = max(highest, int(tail))
    return highest + 1 if highest >= 0 else 1


def _resolve_destination(filename_prefix, output_dir, default_name="ComfyUI",
                         media_exts=_AUDIO_EXTS):
    """解析文件名前缀，得到最终保存位置。

    前缀一律按相对路径处理，落点始终在 ComfyUI 的 output 目录内
    （沿用官方 get_save_image_path 逻辑）。绝对路径与 ``..`` 一律拒绝：
    插件不允许在 input / output 之外写入任何文件。

    `media_exts` 列出该节点支持的扩展名；前缀带扩展名时会被剥离，
    避免出现「clip.mp4_00001.mp4」这类把扩展名当文件名的情况。

    返回 (目录, 文件名前缀, 序号, subfolder)
    """
    prefix = (filename_prefix or "").strip() or default_name

    if os.path.isabs(prefix) or os.path.splitdrive(prefix)[0]:
        raise ValueError(
            "Filename prefix must be a relative path inside the ComfyUI output "
            "directory; absolute paths are rejected.\n"
            f"Rejected: {prefix}"
        )
    if os.path.pardir in prefix.replace("\\", "/").split("/"):
        raise ValueError(
            "Filename prefix must not contain '..'; it is resolved inside the "
            "ComfyUI output directory.\n"
            f"Rejected: {prefix}"
        )

    if media_exts:
        pattern = r"\.(" + "|".join(re.escape(e) for e in media_exts) + r")$"
        prefix = re.sub(pattern, "", prefix, flags=re.IGNORECASE)

    full_folder, name, counter, subfolder, _ = folder_paths.get_save_image_path(prefix, output_dir)
    os.makedirs(full_folder, exist_ok=True)
    return full_folder, name, counter, subfolder


def _unique_copy(src_path, folder, base, ext):
    """把源文件复制到目标目录（同格式直出，不重新编码）。"""
    candidate = os.path.join(folder, f"{base}.{ext}")
    index = 1
    while os.path.abspath(candidate) == os.path.abspath(src_path) or os.path.exists(candidate):
        candidate = os.path.join(folder, f"{base}_{index}.{ext}")
        index += 1
    shutil.copy2(src_path, candidate)
    return candidate


def _auto_kbps(source_kbps, source_lossless, choices, lossless_default, lossy_default, floor):
    """auto 模式下挑选最合适的码率：无损源用高码率，有损源不超过源码率。"""
    if source_lossless:
        return lossless_default
    if source_kbps:
        picked = max([k for k in choices if k <= source_kbps] or [floor])
        return picked
    return lossy_default


# ---------------------------------------------------------------------------
# 节点 1：加载音频(增强)
# ---------------------------------------------------------------------------

class LoadAudioAdvanced:
    """加载本地音频/视频文件（含绝对路径、视频提取音频、长音频截断）。"""

    CATEGORY = "Audio"
    FUNCTION = "load"
    RETURN_TYPES = ("AUDIO", "STRING")
    RETURN_NAMES = ("audio", "info")
    DESCRIPTION = (
        "Load an audio or video file and output AUDIO.\n"
        "• Audio file: pick from the ComfyUI `input` directory (same as official Load Audio).\n"
        "  To use a local file, drag and drop it into the input directory first.\n"
        "• Video files (mp4 / mkv / mov / avi / webm / flv / ts…) are accepted and the audio is extracted automatically\n"
        "• Truncation: take a segment from the start, any middle range, or from a point to the end"
    )

    @classmethod
    def INPUT_TYPES(cls):
        files = list_input_media()
        if not files:
            files = ["(no audio or video files in the input directory)"]

        return {
            "required": {
                # The upload button is added by this plugin's frontend extension
                # (web/media_toolbox.js) rather than by ComfyUI's own
                # "audio_upload" / "video_upload" markers. Those markers make
                # ComfyUI inject its own button plus a built-in player, which
                # would duplicate the player this plugin already adds.
                # ComfyUI stores the uploaded file in the input directory and
                # hands back a relative name, which then goes through the same
                # input-directory resolution as the listed files.
                "音频文件": (files, {
                    "media_toolbox_upload": "audio",
                    "tooltip": "Pick a file from the ComfyUI `input` directory, or use the upload "
                               "button to bring in a file from anywhere on this machine. "
                               "Video files are listed too, so their audio track can be extracted. "
                               "The audio is previewed as soon as you pick it, without running the node.",
                }),
                "音轨序号": ("INT", {
                    "default": 0, "min": 0, "max": 32, "step": 1,
                    "tooltip": "Which audio track to use for files with multiple tracks (e.g. videos). 0 means the first track.",
                }),
                "截断方式": (TRUNCATE_CHOICES, {
                    "default": TRUNCATE_NONE,
                    "tooltip": "How to cut a long audio file:\n"
                               "① Head segment only: from 0 s, take Duration to take seconds\n"
                               "② Middle range only: from Start to End\n"
                               "③ From start to the end: use everything from Start to the end of the file\n"
                               "For ① fill in Duration to take; for ② fill in Start and End; "
                               "for ③ fill in Start only.",
                }),
                "起点(秒)": ("FLOAT", {
                    "default": 0.0, "min": 0.0, "max": 1000000.0, "step": 0.01,
                    "tooltip": "Where to start (seconds).\n"
                               "Middle range: used together with End.\n"
                               "From start to the end: the starting position; End is ignored.",
                }),
                "终点(秒)": ("FLOAT", {
                    "default": 0.0, "min": 0.0, "max": 1000000.0, "step": 0.01,
                    "tooltip": "Where to stop (seconds). Must be greater than Start.\n"
                               "Only applies to the Middle range mode; ignored in other modes.",
                }),
                "截取时长(秒)": ("FLOAT", {
                    "default": 60.0, "min": 0.0, "max": 1000000.0, "step": 0.01,
                    "tooltip": "How much to take (seconds), measured from the beginning of the file, "
                               "so the start is fixed at 0 s.\n"
                               "Only applies to the Head segment mode; ignored in other modes.",
                }),
                "报告语言": (LANG_CHOICES, {
                    "default": LANG_FOLLOW_UI,
                    "tooltip": "Language of this node's report text. "
                               "\"Follow UI language\" produces a Chinese report while the "
                               "ComfyUI interface is Chinese, and an English report otherwise.",
                }),
            }
        }

    def load(self, **kwargs):
        ffmpeg = find_ffmpeg()

        lang = _resolve_report_lang(kwargs.get("报告语言", LANG_FOLLOW_UI))
        combo = str(kwargs.get("音频文件", "") or "").strip()
        track_index = int(kwargs.get("音轨序号", 0) or 0)
        mode = _norm_truncate(kwargs.get("截断方式", TRUNCATE_NONE))
        start = float(kwargs.get("起点(秒)", 0.0) or 0.0)
        end = float(kwargs.get("终点(秒)", 0.0) or 0.0)
        duration = float(kwargs.get("截取时长(秒)", 0.0) or 0.0)

        if combo and not combo.startswith("("):
            path = folder_paths.get_annotated_filepath(combo)
            path = _resolve_within_roots(path, label="audio file")
        else:
            raise ValueError(
            "Please pick a file from the ComfyUI input directory in 'Audio file'."
        )

        if not os.path.isfile(path):
            raise ValueError(f"File not found: {path}")

        info = _probe_media(path, track_index)
        waveform, sample_rate, channels = load_media(path, track_index, ffmpeg)
        original_samples = waveform.shape[-1]

        waveform, cut_range = _apply_truncate(waveform, sample_rate, mode, start, end, duration)

        info["original_duration"] = original_samples / sample_rate
        info["output_duration"] = waveform.shape[-1] / sample_rate
        info["truncated"] = cut_range is not None

        audio = {
            "waveform": waveform.unsqueeze(0).contiguous(),
            "sample_rate": sample_rate,
            _SOURCE_KEY: info,
        }

        lines = [_tr("loaded", lang, name=os.path.basename(path))]
        lines.append(_tr("source_dir", lang))
        lines.append(_tr("path", lang, path=path))
        fmt_desc = info.get("format") or "unknown"
        if info.get("codec"):
            fmt_desc += f" / {info['codec']}"
        if info.get("bits"):
            fmt_desc += f" / {info['bits']}bit"
        lines.append(f"Format: {fmt_desc}")
        lines.append(_tr("sample_rate_ch", lang, rate=sample_rate, ch=channels))
        if info.get("duration"):
            lines.append(_tr("original_duration", lang, dur=_format_duration(info["duration"])))
        if info.get("bitrate_kbps"):
            kind_text = _tr("lossless" if info.get("is_lossless") else "lossy", lang)
            lines.append(_tr("bitrate", lang, kbps=info["bitrate_kbps"], kind=kind_text))
        if info.get("audio_streams", 1) > 1:
            lines.append(_tr("multi_audio", lang, n=info["audio_streams"],
                                       used=info.get("track_index", 0) + 1))
        if info.get("has_video"):
            lines.append(_tr("from_video", lang))
        if cut_range:
            lines.append(_tr("truncated", lang,
                                       start=f"{cut_range[0]:.2f}",
                                       end=f"{cut_range[1]:.2f}",
                                       dur=f"{cut_range[1] - cut_range[0]:.2f}",
                                       mode=mode))
        lines.append(_tr("output", lang,
                         dur=_format_duration(info["output_duration"]),
                         rate=sample_rate, ch=f"{channels}ch"))
        report = "\n".join(lines)
        print("[Load Audio] " + report.replace("\n", "\n[Load Audio] "))

        # Inert text line instead of an "audio" entry: the frontend turns this
        # into a /view URL. The file is always inside the input directory
        # (enforced by _resolve_within_roots).
        return {
            "ui": {"text": [report, f"__preview__|input||{os.path.basename(path)}"]},
            "result": (audio, report),
        }


def _allowed_roots():
    """返回允许读写的目录列表（已 realpath 规范化）：仅 input 与 output。"""
    roots = []
    for getter in (folder_paths.get_input_directory, folder_paths.get_output_directory):
        try:
            roots.append(os.path.realpath(getter()))
        except Exception:  # pragma: no cover
            continue
    return roots


def _source_roots():
    """上游源文件允许位于的目录：ComfyUI 自己的 input / output / temp。

    这三个目录由 ComfyUI 管理（上传落在 input，保存落在 output，
    预览与中间产物落在 temp），因此位于其中的文件是本机产生的可信媒体，
    可以直接复制；其余路径一律不可信。
    """
    roots = _allowed_roots()
    try:
        roots.append(os.path.realpath(folder_paths.get_temp_directory()))
    except Exception:  # pragma: no cover
        pass
    return roots


def _names_remote_machine(text):
    """字符串是否指向另一台机器（Windows UNC，形如 ``\\\\server\\share``）。

    Windows 上对这类路径做任何 stat（甚至只是 realpath）都会立刻发起 SMB
    会话，把当前登录用户的凭据交给对方机器。因此这种形状必须在任何文件
    操作之前就被拒绝，而不能用「先试一下再判断存在性」。
    """
    normalized = str(text or "").strip().replace("/", "\\")
    return normalized.startswith("\\\\")


def _display_name(raw):
    """报告里显示的来源名：按两种分隔符取最后一段，不作为路径使用。"""
    text = str(raw or "").replace("\\", "/").rstrip("/")
    return text.rsplit("/", 1)[-1] or "(memory)"


def _within_roots(candidate, roots):
    """candidate 是否严格位于某个 root 内部（相等本身不算「内部的文件」）。"""
    for root in roots:
        if candidate == root:
            continue
        try:
            if os.path.commonpath([candidate, root]) == root:
                return True
        except ValueError:
            # Windows 上跨盘符时 commonpath 会抛 ValueError，视为不匹配
            continue
    return False


def _trusted_source_path(raw, roots=None):
    """把上游记录的媒体源路径限制到 ComfyUI 的 input/output/temp 内。

    上游 AUDIO/VIDEO 里的 ``path`` 不是本插件写的就必须当敌意输入看待：
    ComfyUI 会把未连线的输入原样交给节点，所以提交的 prompt 里可以自带
    任意路径。校验顺序不能换：先拒绝指向其他机器的形状（避免任何 stat
    触发 SMB 认证），再 realpath 消解符号链接与 ``..``，最后 commonpath
    确认仍在允许的根内；任一步不合格就返回 None，调用方回退到重新编码。
    """
    text = str(raw or "").strip()
    if not text or _names_remote_machine(text):
        return None

    candidate = os.path.realpath(os.path.expanduser(os.path.expandvars(text)))
    if not _within_roots(candidate, _source_roots() if roots is None else roots):
        return None

    return candidate if os.path.isfile(candidate) else None


def _resolve_within_roots(raw, roots=None, label="path"):
    """把路径解析到允许的根目录内，越界则抛出 ValueError。

    先 realpath 消解符号链接与 ``..``，再用 commonpath 确认结果确实位于
    某个根目录内部。两步都必要：realpath 单独用挡不住指向目录内的符号链接，
    commonpath 单独用挡不住尚未展开的 ``..``。
    """
    roots = _allowed_roots() if roots is None else roots
    text = str(raw or "").strip()
    if not text:
        raise ValueError(f"Empty {label}.")
    if _names_remote_machine(text):
        raise ValueError(
            f"{label} must be a file inside the ComfyUI directories, not a network "
            f"share path. Got: {text}"
        )

    candidate = os.path.realpath(os.path.expanduser(os.path.expandvars(text)))

    if not _within_roots(candidate, roots):
        allowed = ", ".join(roots) if roots else "(none)"
        raise ValueError(
            "This node only reads and writes inside the ComfyUI input/output directories.\n"
            f"Rejected {label}: {text}\n"
            f"Allowed roots: {allowed}\n"
            "Upload the file into the input directory (drag and drop in the ComfyUI window), "
            "then pick it from the file list."
        )

    return candidate


def list_input_media():
    """列出 ComfyUI input 目录下的音频/视频文件（按扩展名判定，覆盖 opus/m4a 等）。

    递归子目录：ComfyUI 的 /view 支持 "subfolder/filename" 形式的相对名，
    所以子文件夹里的媒体文件同样可以直接选中使用。
    """
    input_dir = folder_paths.get_input_directory()
    os.makedirs(input_dir, exist_ok=True)
    result = []
    for base, _dirs, names in os.walk(input_dir):
        for name in names:
            full = os.path.join(base, name)
            if not os.path.isfile(full):
                continue
            ext = os.path.splitext(name)[1].lower().lstrip(".")
            if ext not in MEDIA_EXTENSIONS:
                continue
            rel = os.path.relpath(full, input_dir).replace("\\", "/")
            result.append(rel)
    return sorted(result)


# ---------------------------------------------------------------------------
# 节点 2：保存音频(平台发布)
# ---------------------------------------------------------------------------

class SaveAudioPlatformExport:
    """一次产出 WAV / MP3 / FLAC / OPUS 多格式，并做平台合规校验。"""

    CATEGORY = "Audio"
    FUNCTION = "save"
    RETURN_TYPES = ("STRING", "AUDIO")
    RETURN_NAMES = ("report", "audio")
    OUTPUT_NODE = True
    DESCRIPTION = (
        "Export AUDIO to WAV / MP3 / FLAC / OPUS in one run, with a platform compliance report.\n"
        "• Sample rate / channels / WAV bit depth / MP3 bitrate / OPUS bitrate set to \"Auto\" "
        "follow the source audio parameters\n"
        "• Directly connected to \"Load Audio (Advanced)\" for format conversion: when the target format "
        "matches the source and all parameters are \"Auto\", the source file is copied as-is "
        "(instant, no re-encoding)\n"
        "• Multiple target formats can be produced in a single run"
    )

    def __init__(self):
        self.output_dir = folder_paths.get_output_directory()

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "音频": ("AUDIO", {"tooltip": "The AUDIO input of any audio output node."}),
                "文件名前缀": ("STRING", {
                    "default": "audio/ComfyUI",
                    "tooltip": "A relative path inside the ComfyUI output directory. "
                               "Absolute paths are rejected.",
                }),
                "导出WAV": ("BOOLEAN", {"default": True}),
                "WAV位深": ([AUTO, "16-bit", "24-bit", "32-bit"], {
                    "default": AUTO,
                    "tooltip": "WAV sample bit depth. \"Auto\" follows the source bit depth (16-bit if unknown).",
                }),
                "导出MP3": ("BOOLEAN", {"default": True}),
                "MP3码率": ([AUTO, V0_BEST, "128 kbps", "192 kbps", "256 kbps", "320 kbps"], {
                    "default": AUTO,
                    "tooltip": "MP3 bitrate. \"Auto\" uses 320 kbps for lossless sources and never exceeds "
                               "a lossy source's bitrate; \"V0\" is variable-bitrate highest quality.",
                }),
                "导出OPUS": ("BOOLEAN", {"default": False}),
                "OPUS码率": ([AUTO, "64 kbps", "96 kbps", "128 kbps", "192 kbps", "320 kbps"], {
                    "default": AUTO,
                    "tooltip": "OPUS bitrate. \"Auto\" uses 192 kbps for lossless sources and never exceeds "
                               "a lossy source's bitrate.",
                }),
                "保留FLAC": ("BOOLEAN", {"default": True}),
                "采样率": ([AUTO, "44.1 kHz", "48 kHz", "96 kHz"], {
                    "default": AUTO,
                    "tooltip": "Sample rate. \"Auto\" follows the source sample rate.",
                }),
                "声道": ([AUTO, STEREO, MONO], {
                    "default": AUTO,
                    "tooltip": "Channel count. \"Auto\" follows the source channel count.",
                }),
                "平台预设": (_PRESET_CHOICES, {"default": "Soda Music"}),
                "平台名称": ("STRING", {"default": "Soda Music"}),
                "合格格式": ("STRING", {"default": "wav,mp3"}),
                "最低采样率": ("INT", {"default": 44100, "min": 8000, "max": 192000, "step": 100,
                                  "tooltip": "Minimum sample rate required by the platform (Hz)."}),
                "最低位深": ("INT", {"default": 16, "min": 8, "max": 32,
                                "tooltip": "Minimum bit depth required by the platform (bit); applies to WAV only."}),
                "最低码率kbps": ("INT", {"default": 320, "min": 0, "max": 1000, "step": 1,
                                   "tooltip": "Minimum bitrate required by the platform (kbps); applies to MP3 / OPUS only."}),
                "要求声道": ([STEREO, MONO, UNLIMITED], {
                    "default": STEREO,
                    "tooltip": "Channel count required by the platform.",
                }),
                "文件大小上限MB": ("INT", {"default": 200, "min": 1, "max": 4096,
                                     "tooltip": "Maximum size of a single audio file (MB). Anything larger is treated as non-compliant."}),
                "报告语言": (LANG_CHOICES, {
                    "default": LANG_FOLLOW_UI,
                    "tooltip": "Language of the compliance report. "
                               "\"Follow UI language\" produces a Chinese report while the "
                               "ComfyUI interface is Chinese, and an English report otherwise.",
                }),
            }
        }

    def save(self, 音频, 文件名前缀, 导出WAV, WAV位深, 导出MP3, MP3码率,
             导出OPUS, OPUS码率, 保留FLAC, 采样率, 声道,
             平台预设, 平台名称, 合格格式, 最低采样率, 最低位深, 最低码率kbps,
             要求声道, 文件大小上限MB, 报告语言=LANG_FOLLOW_UI):
        ffmpeg = find_ffmpeg()
        if 音频 is None:
            raise ValueError("The Audio input is empty (the upstream node produced no audio).")
        if not ffmpeg:
            raise RuntimeError(_FFMPEG_HINT)

        source_info = 音频.get(_SOURCE_KEY) if isinstance(音频, dict) else None
        waveform, source_rate, source_channels = _to_waveform(音频)

        # ---- 解析输出参数（「自动」自动适配源音频） ----
        采样率 = _norm_choice(采样率)
        声道 = _norm_choice(声道)
        WAV位深 = _norm_choice(WAV位深)
        MP3码率 = _norm_choice(MP3码率)
        OPUS码率 = _norm_choice(OPUS码率)
        要求声道 = _norm_choice(要求声道)

        out_rate = source_rate if 采样率 == AUTO else _sample_rate_hz(采样率, 44100)
        out_channels = source_channels if 声道 == AUTO else (2 if 声道 == STEREO else 1)

        source_bits = source_info.get("bits") if source_info else None
        if WAV位深 == AUTO:
            out_bits = source_bits if source_bits in (16, 24, 32) else 16
        else:
            out_bits = _choice_int(WAV位深, 16)

        source_kbps = source_info.get("bitrate_kbps") if source_info else None
        source_lossless = source_info.get("is_lossless") if source_info else None
        if MP3码率 == AUTO:
            mp3_kbps = _auto_kbps(source_kbps, source_lossless, _MP3_KBPS_CHOICES, 320, 320, 128)
            mp3_v0 = False
        elif MP3码率 == V0_BEST:
            mp3_kbps, mp3_v0 = _MP3_V0_KBPS, True
        else:
            mp3_kbps, mp3_v0 = _choice_int(MP3码率, 192), False

        if OPUS码率 == AUTO:
            opus_kbps = _auto_kbps(source_kbps, source_lossless, _OPUS_KBPS_CHOICES, 192, 192, 96)
        else:
            opus_kbps = _choice_int(OPUS码率, 128)

        # ---- 平台校验条件 ----
        平台预设 = _PLATFORM_ALIASES.get(str(平台预设 or "").strip(), 平台预设)
        cfg = _PLATFORM_PRESETS.get(平台预设)
        no_check = 平台预设 == NO_CHECK_PRESET
        if cfg:
            plat_name = 平台预设
            cond_key = "cond_preset"
            allowed = _parse_formats(cfg["formats"])
            min_rate, min_bits = cfg["min_rate"], cfg["min_bits"]
            min_kbps, want_ch, max_mb = cfg["min_kbps"], _norm_choice(cfg["channels"]), cfg["max_mb"]
        elif no_check:
            plat_name = NO_CHECK_PRESET
            cond_key = "cond_disabled"
            allowed = set()
            min_rate = min_bits = min_kbps = 0
            want_ch, max_mb = UNLIMITED, 0
        else:
            plat_name = 平台名称.strip() or CUSTOM_PRESET
            cond_key = "cond_custom"
            allowed = _parse_formats(合格格式)
            min_rate, min_bits = 最低采样率, 最低位深
            min_kbps, want_ch, max_mb = 最低码率kbps, 要求声道, 文件大小上限MB

        folder, name, counter, subfolder = _resolve_destination(
            文件名前缀, self.output_dir, default_name="audio/ComfyUI")
        base = f"{name}_{counter:05}"

        # ---- 目标格式清单 ----
        targets = []
        if 导出WAV:
            targets.append(("WAV", "wav"))
        if 导出MP3:
            targets.append(("MP3", "mp3"))
        if 导出OPUS:
            targets.append(("OPUS", "opus"))
        if 保留FLAC:
            targets.append(("FLAC", "flac"))
        if not targets:
            raise ValueError("Select at least one output format (WAV / MP3 / OPUS / FLAC).")

        # ---- 判断哪些目标可以「同格式直出」 ----
        # 源路径来自上游 AUDIO，可能是提交的 prompt 自带的，因此只有在它位于
        # ComfyUI 的 input/output/temp 内时才可信任；否则回退到重新编码
        # （波形数据本身由内存传给 ffmpeg，不经过文件系统，天然安全）。
        raw_source_path = source_info.get("path") if source_info else None
        source_path = _trusted_source_path(raw_source_path)
        source_format = (source_info.get("format") if source_info else None)
        auto_rate_ch = (采样率 == AUTO and 声道 == AUTO)
        auto_param = {"WAV": WAV位深 == AUTO, "MP3": MP3码率 == AUTO,
                      "OPUS": OPUS码率 == AUTO, "FLAC": True}

        passthrough = {}
        for kind, _ext in targets:
            can = (
                bool(source_info)
                and source_path is not None
                and source_format == kind.lower()
                and auto_rate_ch
                and auto_param[kind]
            )
            passthrough[kind] = can

        # ---- 构建 ffmpeg 命令（仅转码目标） ----
        cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
               "-f", "f32le", "-ar", str(source_rate), "-ac", str(source_channels), "-i", "pipe:0"]

        produced = []
        for kind, ext in targets:
            path = os.path.join(folder, f"{base}.{ext}")
            if passthrough[kind]:
                produced.append((kind, path, True))
                continue

            if kind == "WAV":
                cmd += ["-map", "0:a", "-map_metadata", "-1", "-c:a", _WAV_CODECS[str(out_bits)],
                        "-ar", str(out_rate), "-ac", str(out_channels), path]
            elif kind == "MP3":
                quality = ["-q:a", "0"] if mp3_v0 else ["-b:a", f"{mp3_kbps}k"]
                cmd += ["-map", "0:a", "-map_metadata", "-1", "-c:a", "libmp3lame", *quality,
                        "-ar", str(out_rate), "-ac", str(out_channels), path]
            elif kind == "OPUS":
                cmd += ["-map", "0:a", "-map_metadata", "-1", "-c:a", "libopus", "-b:a", f"{opus_kbps}k",
                        "-ar", str(_opus_rate(out_rate)), "-ac", str(out_channels), path]
            else:  # FLAC
                cmd += ["-map", "0:a", "-map_metadata", "-1", "-c:a", "flac",
                        "-ar", str(out_rate), "-ac", str(out_channels), path]
            produced.append((kind, path, False))

        # ---- 执行转码 ----
        if any(not entry[2] for entry in produced):
            pcm = np.ascontiguousarray(waveform.transpose(1, 0)).astype("<f4").tobytes()
            result = subprocess.run(cmd, input=pcm, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if result.returncode != 0:
                raise RuntimeError("ffmpeg conversion failed:\n" + result.stderr.decode("utf-8", "ignore")[-1200:])

        # ---- 同格式直出：直接复制源文件 ----
        final_produced = []
        for kind, path, is_pass in produced:
            if is_pass:
                path = _unique_copy(source_path, folder, base, kind.lower())
            final_produced.append((kind, path, is_pass))
        produced = final_produced

        # ---- 生成报告 ----
        lang = _resolve_report_lang(报告语言)
        allowed_text = "/".join(sorted(allowed)) if allowed else _tr("not_set", lang)

        if no_check:
            lines = [_tr("check_disabled", lang), ""]
        else:
            reqs = [_tr("req_formats", lang, v=allowed_text),
                    _tr("req_rate", lang, v=f"{min_rate / 1000:.1f}"),
                    _tr("req_bits", lang, v=min_bits)]
            if min_kbps:
                reqs.append(_tr("req_bitrate", lang, v=min_kbps))
            reqs += [_tr("req_channels", lang, v=_channel_label(want_ch, lang)),
                     _tr("req_size", lang, v=max_mb)]
            lines = [
                _tr("check_title", lang, plat=plat_name, cond=_tr(cond_key, lang)),
                _tr("requirements", lang) + " | " + " | ".join(reqs),
                "",
            ]

        if source_info:
            adapt_parts = [f"{out_rate}Hz", f"{out_channels}ch"]
            if 导出WAV:
                adapt_parts.append(f"{out_bits}bit")
            if 导出MP3:
                adapt_parts.append(f"MP3 {mp3_kbps}kbps")
            if 导出OPUS:
                adapt_parts.append(f"OPUS {opus_kbps}kbps")

            head = [_tr("source_file", lang,
                        name=os.path.basename(source_path) if source_path
                        else _display_name(raw_source_path),
                        fmt=source_format)]
            if source_path is None and raw_source_path:
                head.append("  " + _tr("source_outside", lang))
            head.append(_tr("adapted", lang, v=" / ".join(adapt_parts)))
            head.append("")
            lines = head + lines

        platform_ok = True
        has_platform = False
        direct_count = 0

        for kind, path, is_pass in produced:
            size_mb = os.path.getsize(path) / (1024 * 1024)
            is_platform = kind.lower() in allowed
            notes = []
            ok = True
            bits = None
            kbps = None

            if is_pass:
                direct_count += 1
                rate = source_rate
                channels = source_channels
                bits = source_bits
                kbps = source_kbps
                if kind == "WAV":
                    desc = f"{rate / 1000:.1f}kHz / {bits or '?'}bit / {channels}ch"
                elif kind in ("MP3", "OPUS"):
                    desc = (f"{rate / 1000:.1f}kHz / "
                            + _tr("approx", lang, v=kbps or "?") + f" / {channels}ch")
                else:
                    desc = f"{rate / 1000:.1f}kHz / {_tr('lossless_master', lang)} / {channels}ch"
            elif kind == "WAV":
                info = _read_wav_header(path)
                rate = info["sample_rate"] if info else out_rate
                bits = info["bits"] if info else out_bits
                channels = info["channels"] if info else out_channels
                desc = f"{rate / 1000:.1f}kHz / {bits}bit / {channels}ch"
            elif kind == "MP3":
                rate = out_rate
                channels = out_channels
                kbps = _MP3_V0_KBPS if mp3_v0 else mp3_kbps
                desc = f"{rate / 1000:.1f}kHz / {'V0' if mp3_v0 else f'{mp3_kbps}kbps'} / {channels}ch"
            elif kind == "OPUS":
                rate = _opus_rate(out_rate)
                channels = out_channels
                kbps = opus_kbps
                desc = f"{rate / 1000:.1f}kHz / {opus_kbps}kbps / {channels}ch"
            else:  # FLAC
                rate = out_rate
                channels = out_channels
                desc = f"{rate / 1000:.1f}kHz / {_tr('lossless_master', lang)} / {channels}ch"

            if is_platform and not no_check:
                has_platform = True
                if rate and rate < min_rate:
                    ok = False
                    notes.append(_tr("note_rate_low", lang, v=f"{min_rate / 1000:.1f}"))
                if bits is not None and bits < min_bits:
                    ok = False
                    notes.append(_tr("note_bits_low", lang, v=min_bits))
                if min_kbps and kbps is not None and kbps < min_kbps:
                    ok = False
                    notes.append(_tr("note_bitrate_low", lang, v=min_kbps))
                if want_ch != UNLIMITED:
                    want = 2 if want_ch == STEREO else 1
                    if channels != want:
                        ok = False
                        notes.append(_tr("note_not_stereo" if want == 2 else "note_not_mono", lang))
                if size_mb > max_mb:
                    ok = False
                    notes.append(_tr("note_size_over", lang, v=max_mb))
                platform_ok = platform_ok and ok

            if is_pass:
                mark, tag = "[copy]", kind + _tr("tag_same_format", lang)
            elif no_check:
                mark, tag = "[--]  ", kind
            else:
                mark = "[OK]  " if ok else "[FAIL]"
                tag = kind if is_platform else kind + _tr("tag_extra", lang)
            suffix = ("  <- " + "; ".join(notes)) if notes else ""
            lines.append(f"{mark} {tag:<12s} {os.path.basename(path)}  {desc}  {size_mb:.2f}MB{suffix}")

        # 试听：文件必定落在 output 目录内，直接引用即可
        playback_kind, playback_path = _pick_playback(produced)
        ui_filename, ui_subfolder, preview_note = os.path.basename(playback_path), subfolder or "", None

        if no_check:
            verdict = _tr("v_disabled", lang)
        elif not allowed:
            verdict = _tr("v_no_formats", lang)
        elif not has_platform:
            verdict = _tr("v_no_match", lang, v=allowed_text)
        elif platform_ok:
            verdict = _tr("v_ok", lang, plat=plat_name)
        else:
            verdict = _tr("v_bad", lang)

        if direct_count:
            lines.append(_tr("copied_direct", lang, n=direct_count))

        lines += ["", _tr("output_folder", lang, folder=folder)]
        if preview_note:
            lines.append(preview_note)
        if ui_filename:
            lines.append(_tr("preview_file", lang, name=ui_filename))
        lines.append(_tr("result", lang, v=verdict))

        report = "\n".join(lines)
        print("[Platform Export] " + report.replace("\n", "\n[Platform Export] "))

        # Reported as an inert text line rather than an "audio" entry, so the
        # frontend decides what to preview and no extra built-in widget appears.
        preview = []
        if ui_filename:
            preview.append(f"__preview__|output|{ui_subfolder or ''}|{ui_filename}")

        return {"ui": {"text": [report] + preview}, "result": (report, 音频)}

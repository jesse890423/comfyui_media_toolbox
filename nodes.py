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

_CHANNEL_TEXT = {"双声道": "双声道", "单声道": "单声道", "不限": "不限", "自动（跟随源）": "跟随源"}

# MP3 V0 为 VBR，平均码率约 245kbps，用于平台码率校验
_MP3_V0_KBPS = 245

# 试听副本目录（保存到 output 目录之外时使用）
_PREVIEW_SUBFOLDER = "_platform_preview"

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

# 国内主流平台预设：常见审核要求参考值。平台规则可能调整，必要时改用「自定义」。
_PLATFORM_PRESETS = {
    "汽水音乐": {"formats": "wav,mp3", "min_rate": 44100, "min_bits": 16, "min_kbps": 320, "channels": "stereo", "max_mb": 200},
    "网易云音乐": {"formats": "wav,mp3", "min_rate": 44100, "min_bits": 16, "min_kbps": 320, "channels": "stereo", "max_mb": 200},
    "QQ音乐": {"formats": "wav,mp3", "min_rate": 44100, "min_bits": 16, "min_kbps": 320, "channels": "stereo", "max_mb": 200},
    "酷狗音乐": {"formats": "wav,mp3", "min_rate": 44100, "min_bits": 16, "min_kbps": 320, "channels": "stereo", "max_mb": 200},
    "抖音": {"formats": "wav,mp3", "min_rate": 44100, "min_bits": 16, "min_kbps": 320, "channels": "stereo", "max_mb": 200},
    "快手": {"formats": "wav,mp3", "min_rate": 44100, "min_bits": 16, "min_kbps": 192, "channels": "stereo", "max_mb": 200},
    "哔哩哔哩": {"formats": "wav,flac,mp3", "min_rate": 44100, "min_bits": 16, "min_kbps": 320, "channels": "stereo", "max_mb": 200},
}

# 预设下拉项：具体平台 + 自定义 + 不校验
_PRESET_CHOICES = list(_PLATFORM_PRESETS.keys()) + ["自定义", "不校验"]

# AUDIO 数据里携带源文件信息的键名（节点直连时用于判断能否「同格式直出」）
_SOURCE_KEY = "_audio_source"


# ---------------------------------------------------------------------------
# 下拉选项汉化
#
# 节点界面上所有下拉项一律使用中文；内部计算仍使用规范化值。
# 旧版工作流里保存过 auto / stereo / mono / V0 等英文选项，
# 因此读取时统一走 _norm_choice() 归一化，保证老工作流打开后不会报错。
# ---------------------------------------------------------------------------

AUTO = "自动（跟随源）"
STEREO = "双声道"
MONO = "单声道"
UNLIMITED = "不限"
V0_BEST = "V0（最高质量）"

_CHOICE_ALIASES = {
    "auto": AUTO,
    "stereo": STEREO,
    "mono": MONO,
    "unlimited": UNLIMITED,
    "v0": V0_BEST,
}

# 截断方式：显示值即语义，内部直接比较显示值
TRUNCATE_NONE = "不截断（使用完整音频）"
TRUNCATE_HEAD = "只取开头一段（0 秒 → 截取时长）"
TRUNCATE_RANGE = "只取中间一段（起点 → 终点）"
TRUNCATE_TAIL = "从起点一直到结尾（起点 → 末尾）"

TRUNCATE_CHOICES = [TRUNCATE_NONE, TRUNCATE_HEAD, TRUNCATE_RANGE, TRUNCATE_TAIL]

# 旧工作流保存的截断方式 → 新显示值
_TRUNCATE_ALIASES = {
    "不截断": TRUNCATE_NONE,
    "从头截取时长": TRUNCATE_HEAD,
    "起点到终点": TRUNCATE_RANGE,
    "起点到结尾": TRUNCATE_TAIL,
}


def _norm_choice(value):
    """把下拉值归一为中文显示值，兼容旧工作流中的英文选项。"""
    text = str("" if value is None else value).strip()
    if text in _CHOICE_ALIASES.values():
        return text
    return _CHOICE_ALIASES.get(text.lower(), _CHOICE_ALIASES.get(text, text))


def _norm_truncate(value):
    """把截断方式归一为新的中文显示值，兼容旧工作流。"""
    text = str("" if value is None else value).strip()
    return _TRUNCATE_ALIASES.get(text, text)


def _choice_int(value, default):
    """从下拉值里取出整数（兼容 "24 位" / 旧版纯数字 "24"）。"""
    head = str(value).split("（")[0]
    digits = re.sub(r"\D", "", head)
    return int(digits) if digits else default


def _sample_rate_hz(value, default):
    """把采样率下拉值解析成 Hz，兼容 "44.1 kHz" / "48000 Hz" / 旧版纯数字。"""
    head = str(value).split("（")[0].strip().lower()
    match = re.search(r"\d+(?:\.\d+)?", head)
    if not match:
        return default
    number = float(match.group())
    return int(round(number * 1000)) if "khz" in head else int(number)


# ---------------------------------------------------------------------------
# 通用工具
# ---------------------------------------------------------------------------

# ffmpeg 缺失时的提示。ffmpeg 不是 ComfyUI 自带的，需要单独安装。
_FFMPEG_HINT = """未找到 ffmpeg，无法进行格式转换。

ffmpeg 不是 ComfyUI 自带的，需要单独安装。请任选一种方式：

1. 安装 Python 自带版本（最简单）：
     pip install imageio-ffmpeg

2. 已有 ffmpeg：把 ffmpeg.exe 所在目录加入系统 PATH，
   或设置环境变量 FFMPEG_BINARY 指向 ffmpeg.exe 后重启 ComfyUI。

可用下面这行命令确认是否已就绪：
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
    raise ValueError(f"不支持的采样格式：{wav.dtype}")


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
            raise ValueError("该文件不包含音频流，无法加载。")
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
        raise RuntimeError("运行环境缺少 PyAV（av），无法解码音频/视频文件。")

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
            raise ValueError("未能从该文件解码出音频帧。")

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
            raise RuntimeError("ffmpeg 提取音频失败：" + result.stderr.decode("utf-8", "ignore")[-600:])
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
            "未知的「截断方式」：" + str(mode) + "。请在节点下拉框中重新选择一项。"
        )

    if finish is not None and finish <= begin:
        raise ValueError(
            f"「终点」必须大于「起点」：当前起点 {begin:.2f}s、终点 {finish:.2f}s。"
        )

    i0 = max(0, int(round(begin * sample_rate)))
    i1 = total if finish is None else min(total, int(round(finish * sample_rate)))

    if i0 >= total:
        raise ValueError(
            f"「起点」{begin:.2f}s 已超出音频总时长 {total / sample_rate:.2f}s，请调小起点。"
        )
    if i1 <= i0:
        raise ValueError(
            "截取结果为 0 秒，请检查「截断方式」与下方时间参数是否匹配。"
        )

    return waveform[..., i0:i1], (i0 / sample_rate, i1 / sample_rate)


def _format_duration(seconds):
    if seconds is None:
        return "未知"
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

    相对路径 → ComfyUI output 目录（沿用官方 get_save_image_path 逻辑）
    绝对路径 → 本地任意目录

    `media_exts` 列出该节点支持的扩展名；用户填绝对路径时若带扩展名会被剥离，
    避免出现「clip.mp4_00001.mp4」这类把扩展名当文件名的情况。

    返回 (目录, 文件名前缀, 序号, subfolder, 是否位于 output 目录内)
    """
    prefix = (filename_prefix or "").strip() or default_name

    if not os.path.isabs(prefix):
        full_folder, name, counter, subfolder, _ = folder_paths.get_save_image_path(prefix, output_dir)
        os.makedirs(full_folder, exist_ok=True)
        return full_folder, name, counter, subfolder, True

    folder = os.path.dirname(prefix) or output_dir
    stem = os.path.basename(prefix)
    if media_exts:
        pattern = r"\.(" + "|".join(re.escape(e) for e in media_exts) + r")$"
        stem = re.sub(pattern, "", stem, flags=re.IGNORECASE)
    name = stem or default_name
    os.makedirs(folder, exist_ok=True)
    counter = _next_counter(folder, name)

    subfolder = None
    inside = False
    try:
        rel = os.path.relpath(folder, output_dir)
    except ValueError:
        rel = None
    if rel is not None and rel != os.pardir and not rel.startswith(os.pardir + os.sep) and not os.path.isabs(rel):
        inside = True
        subfolder = "" if rel == "." else rel.replace(os.sep, "/")

    return folder, name, counter, subfolder, inside


def _ensure_preview(ffmpeg, source_path, kind, output_dir, base):
    """保存到 output 目录之外时，在 output/_platform_preview 生成可试听的副本。"""
    preview_dir = os.path.join(output_dir, _PREVIEW_SUBFOLDER)
    os.makedirs(preview_dir, exist_ok=True)
    dest = os.path.join(preview_dir, base + ".mp3")

    # 覆盖旧副本：硬链接 / 复制都不允许目标已存在
    try:
        if os.path.exists(dest):
            os.unlink(dest)
    except OSError:
        pass

    if kind == "MP3":
        try:
            os.link(source_path, dest)
            return dest
        except OSError:
            shutil.copy2(source_path, dest)
            return dest

    result = subprocess.run(
        [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", source_path,
         "-map", "0:a", "-c:a", "libmp3lame", "-b:a", "128k", dest],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        return None
    return dest


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

    CATEGORY = "音频"
    FUNCTION = "load"
    RETURN_TYPES = ("AUDIO", "STRING")
    RETURN_NAMES = ("音频", "信息")
    DESCRIPTION = (
        "加载音频或视频文件并输出 AUDIO。\n"
        "· 文件路径：填本地任意绝对路径（优先级最高），不会复制任何副本\n"
        "· 音频文件：从 ComfyUI 的 input 目录下拉选择（与官方 Load Audio 一致）\n"
        "· 视频文件（mp4/mkv/mov/avi/webm/flv/ts…）会自动提取其中的音频\n"
        "· 长音频截断：从开头取一段、取中间任意区间、或从某一点一直到结尾"
    )

    @classmethod
    def INPUT_TYPES(cls):
        files = list_input_media()
        if not files:
            files = ["(input 目录暂无音频/视频文件)"]

        return {
            "required": {
                "文件路径": ("STRING", {
                    "default": "",
                    "multiline": False,
                    "tooltip": "本地任意绝对路径，支持音频与视频。填写后优先使用，且不会复制任何副本。",
                }),
                "音频文件": (files, {
                    "tooltip": "从 ComfyUI 的 input 目录选择文件（当「文件路径」为空时生效）。",
                }),
                "音轨序号": ("INT", {
                    "default": 0, "min": 0, "max": 32, "step": 1,
                    "tooltip": "多音轨文件（如视频）中要提取的音轨序号，0 表示第一条。",
                }),
                "截断方式": (TRUNCATE_CHOICES, {
                    "default": TRUNCATE_NONE,
                    "tooltip": "长音频只取一部分时使用：\n"
                               "① 只取开头一段：0 秒开始，取「截取时长」秒\n"
                               "② 只取中间一段：从「起点」到「终点」\n"
                               "③ 从起点一直到结尾：从「起点」一直用到音频结束\n"
                               "选第①种时只填「截取时长」；选第②种时填「起点」和「终点」；"
                               "选第③种时只填「起点」。",
                }),
                "起点(秒)": ("FLOAT", {
                    "default": 0.0, "min": 0.0, "max": 1000000.0, "step": 0.01,
                    "tooltip": "从哪里开始（秒）。\n"
                               "只取中间一段：与「终点」配合使用。\n"
                               "从起点一直到结尾：作为开始位置，忽略「终点」。",
                }),
                "终点(秒)": ("FLOAT", {
                    "default": 0.0, "min": 0.0, "max": 1000000.0, "step": 0.01,
                    "tooltip": "到哪里结束（秒），必须大于「起点」。\n"
                               "只在「只取中间一段」模式下生效，其余模式忽略。",
                }),
                "截取时长(秒)": ("FLOAT", {
                    "default": 60.0, "min": 0.0, "max": 1000000.0, "step": 0.01,
                    "tooltip": "要取多长（秒）。从音频开头算起，所以起点固定是 0 秒。\n"
                               "只在「只取开头一段」模式下生效，其余模式忽略。",
                }),
            }
        }

    def load(self, **kwargs):
        ffmpeg = find_ffmpeg()

        raw_path = str(kwargs.get("文件路径", "") or "").strip().strip('"').strip("'")
        combo = str(kwargs.get("音频文件", "") or "").strip()
        track_index = int(kwargs.get("音轨序号", 0) or 0)
        mode = _norm_truncate(kwargs.get("截断方式", TRUNCATE_NONE))
        start = float(kwargs.get("起点(秒)", 0.0) or 0.0)
        end = float(kwargs.get("终点(秒)", 0.0) or 0.0)
        duration = float(kwargs.get("截取时长(秒)", 0.0) or 0.0)

        from_abs = bool(raw_path)
        if raw_path:
            path = os.path.expandvars(os.path.expanduser(raw_path))
            if not os.path.isabs(path):
                path = os.path.join(folder_paths.get_input_directory(), path)
        elif combo and not combo.startswith("("):
            path = folder_paths.get_annotated_filepath(combo)
        else:
            raise ValueError("请填写「文件路径」（本地绝对路径），或在「音频文件」中选择一个文件。")

        if not os.path.isfile(path):
            raise ValueError(f"找不到文件：{path}")

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

        lines = [f"已加载：{os.path.basename(path)}"]
        lines.append("来源：" + ("本地绝对路径（未复制副本）" if from_abs else "ComfyUI input 目录"))
        lines.append(f"路径：{path}")
        fmt_desc = info.get("format") or "未知"
        if info.get("codec"):
            fmt_desc += f" / {info['codec']}"
        if info.get("bits"):
            fmt_desc += f" / {info['bits']}bit"
        lines.append(f"格式：{fmt_desc}")
        lines.append(f"采样率：{sample_rate} Hz    声道：{channels}")
        if info.get("duration"):
            lines.append(f"原始时长：{_format_duration(info['duration'])}")
        if info.get("bitrate_kbps"):
            kind_text = "无损" if info.get("is_lossless") else "有损"
            lines.append(f"码率：约 {info['bitrate_kbps']} kbps（{kind_text}）")
        if info.get("audio_streams", 1) > 1:
            lines.append(f"音轨：第 {info.get('track_index', 0) + 1} 条 / 共 {info['audio_streams']} 条")
        if info.get("has_video"):
            lines.append("视频文件：是（已提取其中音频）")
        if cut_range:
            lines.append(
                f"截取：{cut_range[0]:.2f}s ~ {cut_range[1]:.2f}s"
                f"（共 {cut_range[1] - cut_range[0]:.2f}s，方式：{mode}）"
            )
        lines.append(
            f"输出：{_format_duration(info['output_duration'])} / {sample_rate} Hz / {channels}ch"
        )
        report = "\n".join(lines)
        print("[加载音频] " + report.replace("\n", "\n[加载音频] "))

        preview_url = None
        if av is not None or ffmpeg:
            preview_url = "/audio_platform_export/view?path=" + _url_quote(path)

        return {
            "ui": {"text": [report], "audio": [{"url": preview_url}] if preview_url else []},
            "result": (audio, report),
        }


def _url_quote(text):
    from urllib.parse import quote

    return quote(os.path.abspath(text), safe="")


def list_input_media():
    """列出 ComfyUI input 目录下的音频/视频文件（按扩展名判定，覆盖 opus/m4a 等）。"""
    input_dir = folder_paths.get_input_directory()
    os.makedirs(input_dir, exist_ok=True)
    result = []
    for name in os.listdir(input_dir):
        if not os.path.isfile(os.path.join(input_dir, name)):
            continue
        ext = os.path.splitext(name)[1].lower().lstrip(".")
        if ext in MEDIA_EXTENSIONS:
            result.append(name)
    return sorted(result)


# ---------------------------------------------------------------------------
# 节点 2：保存音频(平台发布)
# ---------------------------------------------------------------------------

class SaveAudioPlatformExport:
    """一次产出 WAV / MP3 / FLAC / OPUS 多格式，并做平台合规校验。"""

    CATEGORY = "音频"
    FUNCTION = "save"
    RETURN_TYPES = ("STRING", "AUDIO")
    RETURN_NAMES = ("报告", "音频")
    OUTPUT_NODE = True
    DESCRIPTION = (
        "把 AUDIO 一次导出为 WAV / MP3 / FLAC / OPUS 多种格式，并给出平台发布合规校验报告。\n"
        "· 采样率 / 声道 / WAV位深 / MP3码率 / OPUS码率 选择 auto 时，自动适配源音频参数\n"
        "· 与「加载音频(增强)」直连做格式转换：目标格式与源格式相同且参数为 auto 时，"
        "直接复制源文件，0 秒完成、不重新编码\n"
        "· 支持一次勾选多个目标格式"
    )

    def __init__(self):
        self.output_dir = folder_paths.get_output_directory()

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "音频": ("AUDIO", {"tooltip": "任意音频输出节点的 AUDIO 输入。"}),
                "文件名前缀": ("STRING", {
                    "default": "audio/ComfyUI",
                    "tooltip": "相对路径存到 ComfyUI output 目录；也可填绝对路径存到本地任意目录。",
                }),
                "导出WAV": ("BOOLEAN", {"default": True}),
                "WAV位深": ([AUTO, "16 位", "24 位", "32 位"], {
                    "default": AUTO,
                    "tooltip": "WAV 的采样位深。「自动」跟随源音频位深（识别不到时按 16 位）。",
                }),
                "导出MP3": ("BOOLEAN", {"default": True}),
                "MP3码率": ([AUTO, V0_BEST, "128 kbps", "192 kbps", "256 kbps", "320 kbps"], {
                    "default": AUTO,
                    "tooltip": "MP3 码率。「自动」时：无损源用 320 kbps，有损源不超过源码率；"
                               "「V0」为可变码率最高质量。",
                }),
                "导出OPUS": ("BOOLEAN", {"default": False}),
                "OPUS码率": ([AUTO, "64 kbps", "96 kbps", "128 kbps", "192 kbps", "320 kbps"], {
                    "default": AUTO,
                    "tooltip": "OPUS 码率。「自动」时：无损源用 192 kbps，有损源不超过源码率。",
                }),
                "保留FLAC": ("BOOLEAN", {"default": True}),
                "采样率": ([AUTO, "44.1 kHz", "48 kHz", "96 kHz"], {
                    "default": AUTO,
                    "tooltip": "采样率。「自动」跟随源音频采样率。",
                }),
                "声道": ([AUTO, STEREO, MONO], {
                    "default": AUTO,
                    "tooltip": "声道数。「自动」跟随源音频声道数。",
                }),
                "平台预设": (_PRESET_CHOICES, {"default": "汽水音乐"}),
                "平台名称": ("STRING", {"default": "汽水音乐"}),
                "合格格式": ("STRING", {"default": "wav,mp3"}),
                "最低采样率": ("INT", {"default": 44100, "min": 8000, "max": 192000, "step": 100,
                                  "tooltip": "平台要求的最低采样率（Hz）。"}),
                "最低位深": ("INT", {"default": 16, "min": 8, "max": 32,
                                "tooltip": "平台要求的最低位深（bit），仅对 WAV 生效。"}),
                "最低码率kbps": ("INT", {"default": 320, "min": 0, "max": 1000, "step": 1,
                                   "tooltip": "平台要求的最低码率（kbps），仅对 MP3 / OPUS 生效。"}),
                "要求声道": ([STEREO, MONO, UNLIMITED], {
                    "default": STEREO,
                    "tooltip": "平台要求的声道数。",
                }),
                "文件大小上限MB": ("INT", {"default": 200, "min": 1, "max": 4096,
                                     "tooltip": "单个音频文件的体积上限（MB），超过即视为不合规。"}),
            }
        }

    def save(self, 音频, 文件名前缀, 导出WAV, WAV位深, 导出MP3, MP3码率,
             导出OPUS, OPUS码率, 保留FLAC, 采样率, 声道,
             平台预设, 平台名称, 合格格式, 最低采样率, 最低位深, 最低码率kbps,
             要求声道, 文件大小上限MB):
        ffmpeg = find_ffmpeg()
        if 音频 is None:
            raise ValueError("音频 输入为空（上游节点没有音频输出）")
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
        cfg = _PLATFORM_PRESETS.get(平台预设)
        no_check = 平台预设 == "不校验"
        if cfg:
            plat_name = 平台预设
            cond_text = "预设条件"
            allowed = _parse_formats(cfg["formats"])
            min_rate, min_bits = cfg["min_rate"], cfg["min_bits"]
            min_kbps, want_ch, max_mb = cfg["min_kbps"], _norm_choice(cfg["channels"]), cfg["max_mb"]
        elif no_check:
            plat_name = "不校验"
            cond_text = "已关闭"
            allowed = set()
            min_rate = min_bits = min_kbps = 0
            want_ch, max_mb = UNLIMITED, 0
        else:
            plat_name = 平台名称.strip() or "自定义"
            cond_text = "自定义条件"
            allowed = _parse_formats(合格格式)
            min_rate, min_bits = 最低采样率, 最低位深
            min_kbps, want_ch, max_mb = 最低码率kbps, 要求声道, 文件大小上限MB

        folder, name, counter, subfolder, inside_output = _resolve_destination(
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
            raise ValueError("至少要勾选一种输出格式（WAV / MP3 / OPUS / FLAC）")

        # ---- 判断哪些目标可以「同格式直出」 ----
        source_path = source_info.get("path") if source_info else None
        source_format = (source_info.get("format") if source_info else None)
        auto_rate_ch = (采样率 == AUTO and 声道 == AUTO)
        auto_param = {"WAV": WAV位深 == AUTO, "MP3": MP3码率 == AUTO,
                      "OPUS": OPUS码率 == AUTO, "FLAC": True}

        passthrough = {}
        for kind, _ext in targets:
            can = (
                bool(source_info)
                and source_path is not None
                and os.path.isfile(source_path)
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
                raise RuntimeError("ffmpeg 转换失败：\n" + result.stderr.decode("utf-8", "ignore")[-1200:])

        # ---- 同格式直出：直接复制源文件 ----
        final_produced = []
        for kind, path, is_pass in produced:
            if is_pass:
                path = _unique_copy(source_path, folder, base, kind.lower())
            final_produced.append((kind, path, is_pass))
        produced = final_produced

        # ---- 生成报告 ----
        allowed_text = "/".join(sorted(allowed)) if allowed else "未设置"

        if no_check:
            lines = ["平台发布校验：已关闭（预设 = 不校验）", ""]
        else:
            reqs = [f"合格格式={allowed_text}", f"采样率≥{min_rate / 1000:.1f}kHz",
                    f"位深≥{min_bits}bit(仅WAV)"]
            if min_kbps:
                reqs.append(f"码率≥{min_kbps}kbps(仅MP3/OPUS)")
            reqs += [f"声道={_CHANNEL_TEXT.get(want_ch, want_ch)}", f"文件大小≤{max_mb}MB"]
            lines = [f"平台发布校验（平台：{plat_name} · {cond_text}）", "要求：" + " · ".join(reqs), ""]

        if source_info:
            adapt_parts = [f"{out_rate}Hz", f"{out_channels}ch"]
            if 导出WAV:
                adapt_parts.append(f"{out_bits}bit")
            if 导出MP3:
                adapt_parts.append(f"MP3 {mp3_kbps}kbps")
            if 导出OPUS:
                adapt_parts.append(f"OPUS {opus_kbps}kbps")
            lines.insert(0, f"源文件：{os.path.basename(source_path)}  （{source_format}）")
            lines.insert(1, "适配参数：" + " / ".join(adapt_parts))
            lines.insert(2, "")

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
                elif kind == "MP3":
                    desc = f"{rate / 1000:.1f}kHz / 约{kbps or '?'}kbps / {channels}ch"
                elif kind == "OPUS":
                    desc = f"{rate / 1000:.1f}kHz / 约{kbps or '?'}kbps / {channels}ch"
                else:
                    desc = f"{rate / 1000:.1f}kHz / 无损母版 / {channels}ch"
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
                desc = f"{rate / 1000:.1f}kHz / 无损母版 / {channels}ch"

            if is_platform and not no_check:
                has_platform = True
                if rate and rate < min_rate:
                    ok = False
                    notes.append(f"采样率<{min_rate / 1000:.1f}kHz")
                if bits is not None and bits < min_bits:
                    ok = False
                    notes.append(f"位深<{min_bits}bit")
                if min_kbps and kbps is not None and kbps < min_kbps:
                    ok = False
                    notes.append(f"码率<{min_kbps}kbps")
                if want_ch != UNLIMITED:
                    want = 2 if want_ch == STEREO else 1
                    if channels != want:
                        ok = False
                        notes.append("非双声道" if want == 2 else "非单声道")
                if size_mb > max_mb:
                    ok = False
                    notes.append(f"文件大小超过{max_mb}MB")
                platform_ok = platform_ok and ok

            if is_pass:
                mark, tag = "[直出]", f"{kind}(同格式直出)"
            elif no_check:
                mark, tag = "[--]  ", kind
            else:
                mark = "[OK]  " if ok else "[FAIL]"
                tag = kind if is_platform else f"{kind}(附加)"
            suffix = ("  <- " + "；".join(notes)) if notes else ""
            lines.append(f"{mark} {tag:<12s} {os.path.basename(path)}  {desc}  {size_mb:.2f}MB{suffix}")

        # 试听：output 目录内直接引用；目录外则生成 output 下的试听副本
        playback_kind, playback_path = _pick_playback(produced)
        ui_filename, ui_subfolder, preview_note = os.path.basename(playback_path), subfolder or "", None

        if not inside_output:
            preview = _ensure_preview(ffmpeg, playback_path, playback_kind, self.output_dir, base)
            if preview:
                ui_filename = os.path.basename(preview)
                ui_subfolder = _PREVIEW_SUBFOLDER
                preview_note = f"试听副本：{_PREVIEW_SUBFOLDER}/{ui_filename}（源文件在 output 目录之外）"
            else:
                ui_filename, ui_subfolder = None, ""
                preview_note = "源文件在 output 目录之外，节点内试听不可用"

        if no_check:
            verdict = "已关闭平台校验，仅输出文件"
        elif not allowed:
            verdict = "未设置合格格式，未做平台校验"
        elif not has_platform:
            verdict = f"未产出平台要求的格式（要求：{allowed_text}）"
        elif platform_ok:
            verdict = f"全部满足「{plat_name}」要求"
        else:
            verdict = "存在不满足项，请调整参数"

        if direct_count:
            lines.append(f"其中 {direct_count} 个格式与源文件一致，已直接复制（0 秒、未重新编码）")

        lines += ["", f"输出目录：{folder}"]
        if preview_note:
            lines.append(preview_note)
        if ui_filename:
            lines.append(f"试听文件：{ui_filename}")
        lines.append("结果：" + verdict)

        report = "\n".join(lines)
        print("[平台发布] " + report.replace("\n", "\n[平台发布] "))

        audio_ui = []
        if ui_filename:
            audio_ui = [{"filename": ui_filename, "subfolder": ui_subfolder, "type": "output"}]

        return {"ui": {"text": [report], "audio": audio_ui}, "result": (report, 音频)}

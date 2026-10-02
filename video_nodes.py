"""视频加载 / 格式转换 —— 全中文界面自定义节点。

本文件包含两个节点，与「加载音频(增强)」「保存音频(平台发布)」保持一致的用法逻辑：

1. 加载视频(增强)  LoadVideoAdvanced
   - 覆盖官方 Load Video 的 input 目录下拉选择
   - 支持填写本地任意绝对路径，且不会复制任何副本
   - 支持长视频截断：从开头取一段、取中间任意区间、或从某一点一直到结尾

2. 保存视频(格式转换)  SaveVideoConverter
   - 与「加载视频(增强)」直连即可做格式转换
   - 分辨率 / 帧率 / 视频编码 / 音频处理 选「自动」时跟随源视频
   - 目标格式与源格式一致且参数全为「自动」时，直接复制源文件，0 秒完成
   - 支持一次勾选多个目标格式，多个目标共用一次 ffmpeg 调用
   - 不输出报告文本，仅落盘文件
"""

import io
import os
import re
import subprocess

import folder_paths

from .nodes import (
    TRUNCATE_CHOICES,
    TRUNCATE_NONE,
    TRUNCATE_HEAD,
    TRUNCATE_RANGE,
    TRUNCATE_TAIL,
    _norm_truncate,
    _resolve_destination,
    _unique_copy,
    _url_quote,
    find_ffmpeg,
)
from .nodes import _FFMPEG_HINT

try:
    import av
except Exception:  # pragma: no cover - 运行环境缺少 PyAV 时给出友好提示
    av = None


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

AUTO = "自动（跟随源）"
KEEP_SOURCE = "保持原始"

# 视频扩展名（用于「视频文件」下拉）
VIDEO_EXTS = (
    "mp4", "m4v", "mkv", "mov", "avi", "webm", "flv", "f4v", "ts", "mts",
    "m2ts", "wmv", "mpg", "mpeg", "vob", "3gp", "rm", "rmvb", "ogv",
)

# 容器显示名 → (ffmpeg 容器名, 输出扩展名)
_CONTAINERS = {
    "MP4": ("mp4", "mp4"),
    "MKV": ("matroska", "mkv"),
    "WEBM": ("webm", "webm"),
    "AVI": ("avi", "avi"),
    "MOV": ("mov", "mov"),
}

# 编码器显示名 → ffmpeg 编码器
_ENCODERS = {
    "H.264 (libx264)": "libx264",
    "H.265 (libx265)": "libx265",
    "VP9 (libvpx-vp9)": "libvpx-vp9",
    "AV1 (libaom-av1)": "libaom-av1",
    "VP8 (libvpx)": "libvpx",
    "MPEG-4 (mpeg4)": "mpeg4",
}

# 各容器推荐的视频编码器（按兼容性从高到低）
_CONTAINER_ENCODERS = {
    "MP4": ["H.264 (libx264)", "H.265 (libx265)", "AV1 (libaom-av1)"],
    "MKV": ["H.264 (libx264)", "H.265 (libx265)", "VP9 (libvpx-vp9)", "AV1 (libaom-av1)"],
    "WEBM": ["VP9 (libvpx-vp9)", "AV1 (libaom-av1)", "VP8 (libvpx)"],
    "AVI": ["MPEG-4 (mpeg4)", "H.264 (libx264)"],
    "MOV": ["H.264 (libx264)", "H.265 (libx265)", "AV1 (libaom-av1)"],
}

# 各编码器的 (默认 CRF, 默认 preset)
_ENCODER_TUNING = {
    "libx264": (20, "medium"),
    "libx265": (24, "medium"),
    "libvpx-vp9": (32, "medium"),
    "libaom-av1": (32, "good"),
    "libvpx": (10, "realtime"),
    "mpeg4": (4, "medium"),
}

# 支持「-crf」的质量编码器
_CRF_ENCODERS = set(_ENCODER_TUNING)

# 音频处理方式
_AUDIO_KEEP = "保留原音频（不重新编码）"
_AUDIO_DROP = "移除音频（导出无声视频）"
_AUDIO_CHOICES = [
    _AUDIO_KEEP,
    AUTO,
    "AAC (m4a)",
    "MP3",
    "OPUS",
    "FLAC",
    "WAV (pcm_s16le)",
]

_AUDIO_ENCODERS = {
    "AAC (m4a)": "aac",
    "MP3": "libmp3lame",
    "OPUS": "libopus",
    "FLAC": "flac",
    "WAV (pcm_s16le)": "pcm_s16le",
}

# 各容器在「自动」音频模式下使用的编码器
_CONTAINER_AUDIO = {
    "MP4": "aac",
    "MOV": "aac",
    "MKV": "aac",
    "AVI": "libmp3lame",
    "WEBM": "libopus",
}

# 源视频信息挂载在 VIDEO 对象上的属性名
_VIDEO_SOURCE_ATTR = "_video_source"

# OPUS 支持的采样率
_OPUS_RATES = (8000, 12000, 16000, 24000, 48000)

# PyAV 编码名 → ffmpeg 编码器名
_CODEC_TO_ENCODER = {
    "h264": "libx264",
    "hevc": "libx265",
    "vp9": "libvpx-vp9",
    "vp8": "libvpx",
    "av1": "libaom-av1",
    "mpeg4": "mpeg4",
}


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def list_input_video():
    """列出 ComfyUI input 目录下的视频文件。"""
    input_dir = folder_paths.get_input_directory()
    os.makedirs(input_dir, exist_ok=True)
    result = []
    for name in os.listdir(input_dir):
        if not os.path.isfile(os.path.join(input_dir, name)):
            continue
        ext = os.path.splitext(name)[1].lower().lstrip(".")
        if ext in VIDEO_EXTS:
            result.append(name)
    return sorted(result)


def _choice_int(value, default):
    """从 "18（高质量）" / "320 kbps" 这类下拉值里取出整数。"""
    head = str(value).split("（")[0]
    digits = re.sub(r"\D", "", head)
    return int(digits) if digits else default


def _fmt_duration(seconds):
    if seconds is None:
        return "未知"
    seconds = float(seconds)
    minutes, secs = divmod(seconds, 60)
    hours, minutes = divmod(int(minutes), 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{secs:05.2f}"
    return f"{minutes:02d}:{secs:05.2f}"


def _fmt_fps(fps):
    if not fps:
        return "未知"
    text = f"{float(fps):.3f}".rstrip("0").rstrip(".")
    return text + " fps"


def _container_display(ext):
    """扩展名 → 容器显示名。"""
    ext = (ext or "").lower()
    mapping = {"mp4": "MP4", "m4v": "MP4", "mkv": "MKV", "webm": "WEBM",
               "avi": "AVI", "mov": "MOV"}
    return mapping.get(ext, ext.upper() or "未知")


def _encoder_display(encoder):
    """ffmpeg 编码器名 → 显示名。"""
    for display, name in _ENCODERS.items():
        if name == encoder:
            return display
    return encoder or "未知"


def _to_encoder_name(codec):
    """PyAV 编码名 → ffmpeg 编码器名。"""
    return _CODEC_TO_ENCODER.get((codec or "").lower(), codec)


def _probe_video(path, stream_index=0):
    """读取视频流参数（宽高、帧率、编码、时长、是否含音轨）。"""
    if av is None:
        raise RuntimeError("运行环境缺少 PyAV（av），无法处理视频文件。")

    with av.open(path) as container:
        streams = list(container.streams.video)
        if not streams:
            raise ValueError("该文件不包含视频流，无法作为视频加载。")
        if stream_index >= len(streams):
            stream_index = 0

        stream = streams[stream_index]
        info = {
            "path": os.path.abspath(path),
            "size": os.path.getsize(path),
            "ext": os.path.splitext(path)[1].lower().lstrip("."),
            "width": int(stream.width),
            "height": int(stream.height),
            "video_codec": stream.codec_context.name,
            "pix_fmt": str(stream.pix_fmt) if stream.pix_fmt else None,
            "video_streams": len(streams),
            "stream_index": stream_index,
        }

        rate = stream.average_rate or stream.guessed_rate
        info["fps"] = float(rate) if rate else None

        duration = None
        if container.duration:
            duration = float(container.duration / av.time_base)
        elif stream.duration and stream.time_base:
            duration = float(stream.duration * stream.time_base)
        info["duration"] = duration

        audio = list(container.streams.audio)
        info["has_audio"] = bool(audio)
        if audio:
            astream = audio[0]
            info["audio_codec"] = astream.codec_context.name
            info["audio_rate"] = int(astream.codec_context.sample_rate or 44100)
            info["audio_channels"] = int(astream.channels or 2)

    return info


def _has_video_stream(path):
    """判断文件是否含视频流。"""
    if av is None:
        return False
    try:
        with av.open(path) as container:
            return len(container.streams.video) > 0
    except Exception:
        return False


def _video_source_of(video):
    """取出上游节点挂在 VIDEO 上的源信息。"""
    return getattr(video, _VIDEO_SOURCE_ATTR, None) if video is not None else None


def _file_source_of(video):
    """若 VIDEO 来自本地文件，返回文件路径（可零成本处理）；否则返回 None。"""
    getter = getattr(video, "get_stream_source", None)
    if not callable(getter):
        return None
    try:
        source = getter()
    except Exception:
        return None
    if isinstance(source, str) and os.path.isfile(source):
        return source
    return None


def _buffer_of(video):
    """若 VIDEO 由内存缓冲构造，返回可读的 BytesIO。"""
    for attr in ("_comfyui_owned_video_buffer", "__buffer", "_buffer"):
        buffer = getattr(video, attr, None)
        if buffer is not None and hasattr(buffer, "read"):
            try:
                buffer.seek(0)
            except Exception:
                pass
            return buffer
    return None


# ---------------------------------------------------------------------------
# 节点 1：加载视频(增强)
# ---------------------------------------------------------------------------

class LoadVideoAdvanced:
    """加载本地视频文件（绝对路径 / input 下拉 / 长视频截断）。"""

    CATEGORY = "视频"
    FUNCTION = "load"
    RETURN_TYPES = ("VIDEO", "STRING")
    RETURN_NAMES = ("视频", "信息")
    DESCRIPTION = (
        "加载视频文件并输出 VIDEO。\n"
        "· 文件路径：填本地任意绝对路径（优先级最高），不会复制任何副本\n"
        "· 视频文件：从 ComfyUI 的 input 目录下拉选择（与官方 Load Video 一致）\n"
        "· 支持 mp4 / mkv / mov / avi / webm / flv / ts 等主流格式\n"
        "· 长视频截断：从开头取一段、取中间任意区间、或从某一点一直到结尾"
    )

    @classmethod
    def INPUT_TYPES(cls):
        files = list_input_video()
        if not files:
            files = ["(input 目录暂无视频文件)"]

        return {
            "required": {
                "文件路径": ("STRING", {
                    "default": "",
                    "multiline": False,
                    "tooltip": "本地任意绝对路径。填写后优先使用，且不会复制任何副本。",
                }),
                "视频文件": (files, {
                    "tooltip": "从 ComfyUI 的 input 目录选择视频（当「文件路径」为空时生效）。",
                }),
                "视频流序号": ("INT", {
                    "default": 0, "min": 0, "max": 16, "step": 1,
                    "tooltip": "文件含多条视频流时使用哪一条，0 表示第一条。",
                }),
                "截断方式": (TRUNCATE_CHOICES, {
                    "default": TRUNCATE_NONE,
                    "tooltip": "长视频只取一部分时使用：\n"
                               "① 只取开头一段：0 秒开始，取「截取时长」秒\n"
                               "② 只取中间一段：从「起点」到「终点」\n"
                               "③ 从起点一直到结尾：从「起点」一直用到视频结束\n"
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
                    "tooltip": "要取多长（秒）。从视频开头算起，所以起点固定是 0 秒。\n"
                               "只在「只取开头一段」模式下生效，其余模式忽略。",
                }),
            }
        }

    def load(self, **kwargs):
        from comfy_api import input_impl as input_impl

        raw_path = str(kwargs.get("文件路径", "") or "").strip().strip('"').strip("'")
        combo = str(kwargs.get("视频文件", "") or "").strip()
        stream_index = int(kwargs.get("视频流序号", 0) or 0)
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
            raise ValueError("请填写「文件路径」（本地绝对路径），或在「视频文件」中选择一个文件。")

        if not os.path.isfile(path):
            raise ValueError(f"找不到文件：{path}")
        if not _has_video_stream(path):
            raise ValueError("该文件不含视频流。若只想处理音频，请改用「加载音频(增强)」节点。")

        info = _probe_video(path, stream_index)
        total = info.get("duration") or 0.0

        # ---- 计算截取窗口 ----
        begin, finish = 0.0, None
        if mode == TRUNCATE_HEAD:
            finish = float(duration)
        elif mode == TRUNCATE_RANGE:
            begin, finish = float(start), float(end)
        elif mode == TRUNCATE_TAIL:
            begin, finish = float(start), None
        elif mode != TRUNCATE_NONE:
            raise ValueError("未知的「截断方式」：" + str(mode) + "。请在下拉框中重新选择一项。")

        if finish is not None and finish <= begin:
            raise ValueError(
                f"「终点」必须大于「起点」：当前起点 {begin:.2f}s、终点 {finish:.2f}s。"
            )
        if total and begin >= total:
            raise ValueError(
                f"「起点」{begin:.2f}s 已超出视频总时长 {total:.2f}s，请调小起点。"
            )

        clip_duration = (finish - begin) if finish is not None else None
        info["start_time"] = begin
        info["clip_duration"] = clip_duration
        info["truncated"] = mode != TRUNCATE_NONE
        info["original_duration"] = total
        info["output_duration"] = clip_duration if clip_duration is not None else total

        video = input_impl.VideoFromFile(path, start_time=begin,
                                         duration=clip_duration or 0)
        setattr(video, _VIDEO_SOURCE_ATTR, info)

        lines = [f"已加载：{os.path.basename(path)}"]
        lines.append("来源：" + ("本地绝对路径（未复制副本）" if from_abs else "ComfyUI input 目录"))
        lines.append(f"路径：{path}")
        lines.append(f"分辨率：{info['width']}×{info['height']}    帧率：{_fmt_fps(info.get('fps'))}")
        lines.append(f"视频编码：{_encoder_display(_to_encoder_name(info.get('video_codec')))}")
        if info.get("has_audio"):
            lines.append(
                f"音频轨：有（{info.get('audio_codec')} / {info.get('audio_rate')} Hz / "
                f"{info.get('audio_channels')} 声道）"
            )
        else:
            lines.append("音频轨：无")
        if info.get("video_streams", 1) > 1:
            lines.append(f"视频流：第 {info.get('stream_index', 0) + 1} 条 / 共 {info['video_streams']} 条")
        if total:
            lines.append(f"原始时长：{_fmt_duration(total)}")
        if mode != TRUNCATE_NONE:
            end_text = f"{finish:.2f}s" if finish is not None else "结尾"
            lines.append(f"截取：{begin:.2f}s ~ {end_text}（方式：{mode}）")
        lines.append(
            f"输出：{_fmt_duration(info['output_duration'])} / "
            f"{info['width']}×{info['height']} / {_fmt_fps(info.get('fps'))}"
        )
        report = "\n".join(lines)
        print("[加载视频] " + report.replace("\n", "\n[加载视频] "))

        return {
            "ui": {
                "text": [report],
                "images": [{"url": "/audio_platform_export/view?path=" + _url_quote(path),
                            "filename": os.path.basename(path), "type": "input"}],
                "animated": (True,),
            },
            "result": (video, report),
        }


# ---------------------------------------------------------------------------
# 节点 2：保存视频(格式转换)
# ---------------------------------------------------------------------------

class SaveVideoConverter:
    """一次产出 MP4 / MKV / WEBM / AVI / MOV 多种格式，参数可自动适配源视频。"""

    CATEGORY = "视频"
    FUNCTION = "save"
    RETURN_TYPES = ("VIDEO",)
    RETURN_NAMES = ("视频",)
    OUTPUT_NODE = True
    DESCRIPTION = (
        "把 VIDEO 一次导出为 MP4 / MKV / WEBM / AVI / MOV 多种格式。\n"
        "· 分辨率 / 帧率 / 视频编码 / 音频处理 选「自动」时，跟随源视频参数\n"
        "· 与「加载视频(增强)」直连做格式转换：目标格式与源格式一致且参数为「自动」时，"
        "直接复制源文件，0 秒完成、不重新编码\n"
        "· 支持一次勾选多个目标格式；不输出报告，仅落盘文件"
    )

    def __init__(self):
        self.output_dir = folder_paths.get_output_directory()

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "视频": ("VIDEO", {"tooltip": "任意视频输出节点的 VIDEO 输入。"}),
                "文件名前缀": ("STRING", {
                    "default": "video/ComfyUI",
                    "tooltip": "相对路径存到 ComfyUI output 目录；也可填绝对路径存到本地任意目录。",
                }),
                "导出MP4": ("BOOLEAN", {"default": True}),
                "导出MKV": ("BOOLEAN", {"default": False}),
                "导出WEBM": ("BOOLEAN", {"default": False}),
                "导出AVI": ("BOOLEAN", {"default": False}),
                "导出MOV": ("BOOLEAN", {"default": False}),
                "视频编码": ([AUTO] + list(_ENCODERS.keys()), {
                    "default": AUTO,
                    "tooltip": "「自动」时沿用源视频编码；若该编码与目标容器不兼容，"
                               "自动回退到容器推荐编码器。",
                }),
                "画质CRF": ([AUTO, "16（极高）", "20（高）", "23（标准）", "26（较小）"], {
                    "default": AUTO,
                    "tooltip": "恒定质量。数值越小越清晰、体积越大；「自动」按编码器取默认值。",
                }),
                "分辨率": ([KEEP_SOURCE, "1920×1080", "1280×720", "854×480", "640×360"], {
                    "default": KEEP_SOURCE,
                    "tooltip": "「保持原始」= 源分辨率；其余按标注尺寸等比缩放（不会变形、不会裁切）。",
                }),
                "帧率": ([KEEP_SOURCE, "60", "30", "25", "24", "15"], {
                    "default": KEEP_SOURCE,
                    "tooltip": "「保持原始」= 源帧率；其余按数值输出。",
                }),
                "音频处理": (_AUDIO_CHOICES, {
                    "default": _AUDIO_KEEP,
                    "tooltip": "「保留原音频」= 不重新编码；「自动」= 目标容器不兼容时自动转码；"
                               "「移除音频」= 导出无声视频；其余为强制转码为指定格式。",
                }),
                "音频码率": ([AUTO, "128 kbps", "192 kbps", "256 kbps", "320 kbps"], {
                    "default": AUTO,
                    "tooltip": "音频转码码率。「自动」时按音频编码取常用值。",
                }),
            }
        }

    def save(self, 视频, 文件名前缀, 导出MP4, 导出MKV, 导出WEBM, 导出AVI, 导出MOV,
             视频编码, 画质CRF, 分辨率, 帧率, 音频处理, 音频码率):
        ffmpeg = find_ffmpeg()
        if 视频 is None:
            raise ValueError("视频 输入为空（上游节点没有视频输出）")
        if not ffmpeg:
            raise RuntimeError(_FFMPEG_HINT)

        targets = [name for flag, name in ((导出MP4, "MP4"), (导出MKV, "MKV"),
                                           (导出WEBM, "WEBM"), (导出AVI, "AVI"),
                                           (导出MOV, "MOV")) if flag]
        if not targets:
            raise ValueError("至少要勾选一种输出格式（MP4 / MKV / WEBM / AVI / MOV）")

        # ---- 归一化全部下拉参数 ----
        want_encoder = AUTO if 视频编码 == AUTO else 视频编码
        want_crf = None if 画质CRF == AUTO else _choice_int(画质CRF, 23)
        audio_action = AUTO if 音频处理 == AUTO else 音频处理
        audio_kbps = None if 音频码率 == AUTO else _choice_int(音频码率, 192)

        scale_w = scale_h = None
        if 分辨率 != KEEP_SOURCE:
            matched = re.search(r"(\d+)\s*[×xX*]\s*(\d+)", str(分辨率))
            if matched:
                scale_w, scale_h = int(matched.group(1)), int(matched.group(2))

        want_fps = None if 帧率 == KEEP_SOURCE else _choice_int(帧率, 0) or None

        source_info = _video_source_of(视频)
        source_path = source_info.get("path") if source_info else None
        source_container = _container_display(source_info.get("ext")) if source_info else None

        folder, name, counter, subfolder, inside_output = _resolve_destination(
            文件名前缀, self.output_dir,
            default_name="video/ComfyUI",
            media_exts=tuple(_CONTAINERS[k][1] for k in _CONTAINERS))
        base = f"{name}_{counter:05}"

        # ---- 同格式直出判定：源格式在目标里 + 无任何转码参数 + 未截断 ----
        untouched = (source_info is not None
                     and not source_info.get("truncated")
                     and not source_info.get("start_time"))
        can_direct = bool(
            untouched
            and source_path and os.path.isfile(source_path)
            and source_container in targets
            and want_encoder == AUTO
            and want_crf is None
            and scale_w is None
            and want_fps is None
            and audio_action in (AUTO, _AUDIO_KEEP)
        )

        if can_direct:
            produced = []
            for kind in targets:
                ext = _CONTAINERS[kind][1]
                produced.append((kind, _unique_copy(source_path, folder, base, ext), True))
            return self._result(视频, produced, folder)

        # ---- 需要 ffmpeg 转码 ----
        source = _file_source_of(视频)
        stdin_buffer = None
        if source is None:
            stdin_buffer = _buffer_of(视频)
            if stdin_buffer is None:
                try:
                    fallback = io.BytesIO()
                    视频.save_to(fallback, format="mp4", codec="h264")
                    fallback.seek(0)
                    stdin_buffer = fallback
                except Exception as exc:
                    raise RuntimeError(
                        "上游视频不是文件型输入，无法直接转码。"
                        "请把「加载视频(增强)」作为上游节点。\n" + str(exc)
                    ) from exc
            source = "pipe:0"

        # 截取窗口（来自加载节点）
        start_time = float(source_info.get("start_time") or 0.0) if source_info else 0.0
        clip_duration = source_info.get("clip_duration") if source_info else None

        has_audio = bool(source_info.get("has_audio")) if source_info else True
        drop_audio = (audio_action == _AUDIO_DROP) or not has_audio

        # ---- 构建命令：多个目标共用一次 ffmpeg 调用 ----
        cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error"]
        if start_time > 0:
            cmd += ["-ss", f"{start_time:.3f}"]
        if clip_duration:
            cmd += ["-t", f"{float(clip_duration):.3f}"]
        cmd += ["-i", source]

        produced = []
        for kind in targets:
            _container, ext = _CONTAINERS[kind]
            path = os.path.join(folder, f"{base}.{ext}")

            encoder = self._pick_encoder(kind, want_encoder, source_info)
            cmd += ["-map", "0:v:0", "-c:v", encoder]
            if encoder in _CRF_ENCODERS:
                crf = want_crf if want_crf is not None else _ENCODER_TUNING[encoder][0]
                cmd += ["-crf", str(crf), "-preset", _ENCODER_TUNING[encoder][1]]
            else:
                cmd += ["-b:v", "6M"]

            filters = []
            if scale_w and scale_h:
                filters.append(f"scale={scale_w}:{scale_h}:force_original_aspect_ratio=decrease")
            if want_fps:
                filters.append(f"fps={want_fps}")
            if filters:
                cmd += ["-vf", ",".join(filters)]

            if drop_audio:
                cmd += ["-an"]
            else:
                acodec = self._pick_audio_encoder(kind, audio_action)
                if acodec:
                    cmd += ["-c:a", acodec]
                    if acodec in ("aac", "libmp3lame", "libopus"):
                        cmd += ["-b:a", f"{audio_kbps or self._default_audio_kbps(acodec)}k"]
                    if acodec == "libopus" and source_info:
                        rate = int(source_info.get("audio_rate") or 48000)
                        if rate in _OPUS_RATES:
                            cmd += ["-ar", str(rate)]
                cmd += ["-map", "0:a:0?"]

            cmd.append(path)
            produced.append((kind, path, False))

        payload = stdin_buffer.getvalue() if stdin_buffer is not None else None
        result = subprocess.run(cmd, input=payload,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if result.returncode != 0:
            raise RuntimeError(
                "ffmpeg 视频转换失败：\n"
                + result.stderr.decode("utf-8", "ignore")[-1500:]
            )

        return self._result(视频, produced, folder)

    @staticmethod
    def _pick_encoder(kind, want_encoder, source_info):
        """视频编码器：显式选择 > 源编码（容器兼容时）> 容器推荐首选。"""
        if want_encoder != AUTO:
            return _ENCODERS.get(want_encoder, want_encoder)

        if source_info:
            source_encoder = _to_encoder_name(source_info.get("video_codec"))
            for display in _CONTAINER_ENCODERS.get(kind, []):
                if _ENCODERS.get(display) == source_encoder:
                    return source_encoder

        return _ENCODERS[_CONTAINER_ENCODERS[kind][0]]

    @staticmethod
    def _pick_audio_encoder(kind, audio_action):
        """音频编码器：显式选择 > 容器兼容编码（自动模式）。"""
        if audio_action in _AUDIO_ENCODERS:
            return _AUDIO_ENCODERS[audio_action]
        return _CONTAINER_AUDIO.get(kind, "aac")

    @staticmethod
    def _default_audio_kbps(acodec):
        return {"aac": 192, "libmp3lame": 192, "libopus": 128}.get(acodec, 192)

    @staticmethod
    def _result(video, produced, folder):
        """组装返回：落盘摘要 + 节点内预览。"""
        parts = [f"{kind}（{'直出' if direct else '转码'}）" for kind, _path, direct in produced]
        summary = f"已输出 {len(produced)} 个格式：{'、'.join(parts)}\n目录：{folder}"

        return {
            "ui": {
                "text": [summary],
                "images": [{"filename": os.path.basename(path), "type": "output",
                            "subfolder": ""} for _kind, path, _direct in produced],
            },
            "result": (video,),
        }

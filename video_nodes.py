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
    LANG_CHOICES,
    LANG_EN,
    LANG_FOLLOW_UI,
    LANG_ZH,
    TRUNCATE_CHOICES,
    TRUNCATE_NONE,
    TRUNCATE_HEAD,
    TRUNCATE_RANGE,
    TRUNCATE_TAIL,
    _norm_truncate,
    _resolve_destination,
    _resolve_report_lang,
    _resolve_within_roots,
    _tr,
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

AUTO = "Auto (follow source)"
KEEP_SOURCE = "Keep original"

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
_AUDIO_KEEP = "Keep original audio (no re-encoding)"
_AUDIO_DROP = "Remove audio (export silent video)"
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

# 记录保存节点实际写出的文件，供 VideoReportNode 读取
_PRODUCED_ATTR = "_video_produced"

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

def _preview_hint(filename, type_="output", subfolder=""):
    """将预览目标编码为前端可识别的惰性文本行。

    Web 扩展从 ``text`` 列表中读取该行并转换为 ``/view`` URL。使用
    ``text`` 而不是 ``images``，可避免 ComfyUI 在插件播放器旁额外创建
    自己的画布媒体预览。
    """
    return f"__preview__|{type_}|{subfolder}|{filename}"


def list_input_video():
    """列出 ComfyUI input 目录下的视频文件（含子目录，理由同 list_input_media）。"""
    input_dir = folder_paths.get_input_directory()
    os.makedirs(input_dir, exist_ok=True)
    result = []
    for base, _dirs, names in os.walk(input_dir):
        for name in names:
            full = os.path.join(base, name)
            if not os.path.isfile(full):
                continue
            ext = os.path.splitext(name)[1].lower().lstrip(".")
            if ext not in VIDEO_EXTS:
                continue
            rel = os.path.relpath(full, input_dir).replace("\\", "/")
            result.append(rel)
    return sorted(result)


def _choice_int(value, default):
    """从 "18（高质量）" / "320 kbps" 这类下拉值里取出整数。"""
    head = str(value).split("（")[0]
    digits = re.sub(r"\D", "", head)
    return int(digits) if digits else default


def _fmt_duration(seconds):
    if seconds is None:
        return "unknown"
    seconds = float(seconds)
    minutes, secs = divmod(seconds, 60)
    hours, minutes = divmod(int(minutes), 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{secs:05.2f}"
    return f"{minutes:02d}:{secs:05.2f}"


def _fmt_fps(fps):
    if not fps:
        return "unknown"
    text = f"{float(fps):.3f}".rstrip("0").rstrip(".")
    return text + " fps"


def _container_display(ext):
    """扩展名 → 容器显示名。"""
    ext = (ext or "").lower()
    mapping = {"mp4": "MP4", "m4v": "MP4", "mkv": "MKV", "webm": "WEBM",
               "avi": "AVI", "mov": "MOV"}
    return mapping.get(ext, ext.upper() or "unknown")


def _encoder_display(encoder):
    """ffmpeg 编码器名 → 显示名。"""
    for display, name in _ENCODERS.items():
        if name == encoder:
            return display
    return encoder or "unknown"


def _to_encoder_name(codec):
    """PyAV 编码名 → ffmpeg 编码器名。"""
    return _CODEC_TO_ENCODER.get((codec or "").lower(), codec)


def _probe_video(path, stream_index=0):
    """读取视频流参数（宽高、帧率、编码、时长、是否含音轨）。"""
    if av is None:
        raise RuntimeError("PyAV (av) is not available in this environment, so video files cannot be processed.")

    with av.open(path) as container:
        streams = list(container.streams.video)
        if not streams:
            raise ValueError("This file contains no video stream and cannot be loaded as a video.")
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


def _source_path_of(video):
    """报告用的源文件路径：优先取节点自己记录的，回退到 VIDEO 自身的流来源。"""
    info = _video_source_of(video)
    recorded = info.get("path") if isinstance(info, dict) else None
    if recorded and os.path.isfile(recorded):
        return recorded
    return _file_source_of(video)


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

    CATEGORY = "Video"
    FUNCTION = "load"
    RETURN_TYPES = ("VIDEO", "STRING")
    RETURN_NAMES = ("video", "info")
    DESCRIPTION = (
        "Load a video file and output VIDEO.\n"
        "• Video file: pick from the ComfyUI `input` directory (same as official Load Video).\n"
        "  To use a local file, drag and drop it into the input directory first.\n"
        "• Supports mp4 / mkv / mov / avi / webm / flv / ts and other mainstream formats\n"
        "• Truncation: take a segment from the start, any middle range, or from a point to the end"
    )

    @classmethod
    def INPUT_TYPES(cls):
        files = list_input_video()
        if not files:
            files = ["(no video files in the input directory)"]

        return {
            "required": {
                # The upload button is added by this plugin's frontend extension
                # (web/media_toolbox.js). ComfyUI's own "video_upload" marker is
                # deliberately not used: it routes through Comfy.UploadImage,
                # which builds its own upload button and turns on a canvas
                # preview inside the node, which would appear as a second player
                # next to the player this plugin already adds.
                # ComfyUI stores the uploaded file in the input directory and
                # returns a relative name, resolved against the input directory below.
                "视频文件": (files, {
                    "media_toolbox_upload": "video",
                    "tooltip": "Pick a video from the ComfyUI `input` directory, or use the upload "
                               "button to bring in a file from anywhere on this machine. "
                               "The video is previewed as soon as you pick it, without running the node.",
                }),
                "视频流序号": ("INT", {
                    "default": 0, "min": 0, "max": 16, "step": 1,
                    "tooltip": "Which video stream to use for files with multiple streams. 0 means the first one.",
                }),
                "截断方式": (TRUNCATE_CHOICES, {
                    "default": TRUNCATE_NONE,
                    "tooltip": "How to cut a long video:\n"
                               "① Head segment only: from 0 s, take Duration to take seconds\n"
                               "② Middle range only: from Start to End\n"
                               "③ From start to the end: use everything from Start to the end of the video\n"
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
        from comfy_api import input_impl as input_impl

        lang = _resolve_report_lang(kwargs.get("报告语言", LANG_FOLLOW_UI))
        combo = str(kwargs.get("视频文件", "") or "").strip()
        stream_index = int(kwargs.get("视频流序号", 0) or 0)
        mode = _norm_truncate(kwargs.get("截断方式", TRUNCATE_NONE))
        start = float(kwargs.get("起点(秒)", 0.0) or 0.0)
        end = float(kwargs.get("终点(秒)", 0.0) or 0.0)
        duration = float(kwargs.get("截取时长(秒)", 0.0) or 0.0)

        if combo and not combo.startswith("("):
            path = folder_paths.get_annotated_filepath(combo)
            path = _resolve_within_roots(path, label="video file")
        else:
            raise ValueError(
                "Please pick a file from the ComfyUI input directory in 'Video file'."
            )

        if not os.path.isfile(path):
            raise ValueError(f"File not found: {path}")
        if not _has_video_stream(path):
            raise ValueError(
                "This file has no video stream. To process audio only, "
                "use the 'Load Audio (Advanced)' node instead."
            )

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
            raise ValueError("Unknown truncation mode: " + str(mode) + ". Please pick one from the dropdown again.")

        if finish is not None and finish <= begin:
            raise ValueError(
                f"End must be greater than Start: current start {begin:.2f}s, end {finish:.2f}s."
            )
        if total and begin >= total:
            raise ValueError(
                f"Start {begin:.2f}s is beyond the total video duration of {total:.2f}s; reduce Start."
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

        lines = [_tr("loaded", lang, name=os.path.basename(path))]
        lines.append(_tr("source_dir", lang))
        lines.append(_tr("path", lang, path=path))
        lines.append(_tr("res_fps", lang, w=info["width"], h=info["height"],
                         fps=_fmt_fps(info.get("fps"))))
        lines.append(_tr("v_codec", lang,
                         v=_encoder_display(_to_encoder_name(info.get("video_codec")))))
        if info.get("has_audio"):
            lines.append(_tr("audio_yes", lang, codec=info.get("audio_codec"),
                                            rate=info.get("audio_rate"),
                                            ch=info.get("audio_channels")))
        else:
            lines.append(f"{_tr('audio_track', lang)}: {_tr('no_audio_track', lang)}")
        if info.get("video_streams", 1) > 1:
            lines.append(_tr("v_streams", lang, used=info.get("stream_index", 0) + 1,
                                           n=info["video_streams"]))
        if total:
            lines.append(_tr("original_duration", lang, dur=_fmt_duration(total)))
        if mode != TRUNCATE_NONE:
            if finish is not None:
                lines.append(_tr("truncated", lang, start=f"{begin:.2f}",
                                               end=f"{finish:.2f}",
                                               dur=f"{finish - begin:.2f}", mode=mode))
            else:
                lines.append(_tr("trunc_to_end", lang, start=f"{begin:.2f}", mode=mode))
        lines.append(_tr("v_output", lang, dur=_fmt_duration(info["output_duration"]),
                                         w=info["width"], h=info["height"],
                                         fps=_fmt_fps(info.get("fps"))))
        report = "\n".join(lines)
        print("[Load Video] " + report.replace("\n", "\n[Load Video] "))

        # Deliberately no "images" key: returning it makes ComfyUI render its own
        # canvas media preview, which would sit next to the player this plugin
        # adds and show up as a second grey bar. "text" is inert for the frontend
        # and is consumed by our own onExecuted hook instead.
        return {
            "ui": {"text": [report, _preview_hint(os.path.basename(path), "input")]},
            "result": (video, report),
        }


# ---------------------------------------------------------------------------
# 节点 2：保存视频(格式转换)
# ---------------------------------------------------------------------------

class SaveVideoConverter:
    """一次产出 MP4 / MKV / WEBM / AVI / MOV 多种格式，参数可自动适配源视频。"""

    CATEGORY = "Video"
    FUNCTION = "save"
    RETURN_TYPES = ("VIDEO", "STRING")
    RETURN_NAMES = ("video", "summary")
    OUTPUT_NODE = True
    DESCRIPTION = (
        "Export VIDEO to MP4 / MKV / WEBM / AVI / MOV in one run.\n"
        "• Resolution / frame rate / video codec / audio handling set to \"Auto\" follow the source video\n"
        "• Directly connected to \"Load Video (Advanced)\" for format conversion: when the target format "
        "matches the source and all parameters are \"Auto\", the source file is copied as-is "
        "(instant, no re-encoding)\n"
        "• Multiple target formats can be produced in a single run; no report is produced, "
        "files are simply written to disk"
    )

    def __init__(self):
        self.output_dir = folder_paths.get_output_directory()

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "视频": ("VIDEO", {"tooltip": "The VIDEO input of any video output node."}),
                "文件名前缀": ("STRING", {
                    "default": "video/ComfyUI",
                    "tooltip": "A relative path inside the ComfyUI output directory. "
                               "Absolute paths are rejected.",
                }),
                "导出MP4": ("BOOLEAN", {"default": True}),
                "导出MKV": ("BOOLEAN", {"default": False}),
                "导出WEBM": ("BOOLEAN", {"default": False}),
                "导出AVI": ("BOOLEAN", {"default": False}),
                "导出MOV": ("BOOLEAN", {"default": False}),
                "视频编码": ([AUTO] + list(_ENCODERS.keys()), {
                    "default": AUTO,
                    "tooltip": "\"Auto\" reuses the source codec; if that codec is incompatible with the "
                               "target container it falls back to the container's recommended codec.",
                }),
                "画质CRF": ([AUTO, "16 (very high)", "20 (high)", "23 (standard)", "26 (smaller)"], {
                    "default": AUTO,
                    "tooltip": "Constant rate factor. Lower values mean higher quality and larger files; "
                               "\"Auto\" picks a sensible default per codec.",
                }),
                "分辨率": ([KEEP_SOURCE, "1920×1080", "1280×720", "854×480", "640×360"], {
                    "default": KEEP_SOURCE,
                    "tooltip": "\"Keep original\" uses the source resolution; any other option scales to the "
                               "listed size proportionally (no distortion, no cropping).",
                }),
                "帧率": ([KEEP_SOURCE, "60", "30", "25", "24", "15"], {
                    "default": KEEP_SOURCE,
                    "tooltip": "\"Keep original\" uses the source frame rate; any other option outputs "
                               "the given value.",
                }),
                "音频处理": (_AUDIO_CHOICES, {
                    "default": _AUDIO_KEEP,
                    "tooltip": "\"Keep original audio\" does not re-encode; \"Auto\" transcodes when the target "
                               "container cannot carry the source audio; \"Remove audio\" exports a silent "
                               "video; any other option forces transcoding to that format.",
                }),
                "音频码率": ([AUTO, "128 kbps", "192 kbps", "256 kbps", "320 kbps"], {
                    "default": AUTO,
                    "tooltip": "Bitrate used when the audio is re-encoded. \"Auto\" picks a common value for "
                               "the chosen audio codec.",
                }),
                "报告语言": (LANG_CHOICES, {
                    "default": LANG_FOLLOW_UI,
                    "tooltip": "Language of the parameter report. "
                               "\"Follow UI language\" produces a Chinese report while the "
                               "ComfyUI interface is Chinese, and an English report otherwise.",
                }),
            }
        }

    def save(self, 视频, 文件名前缀, 导出MP4, 导出MKV, 导出WEBM, 导出AVI, 导出MOV,
             视频编码, 画质CRF, 分辨率, 帧率, 音频处理, 音频码率, 报告语言=LANG_FOLLOW_UI):
        ffmpeg = find_ffmpeg()
        if 视频 is None:
            raise ValueError("The Video input is empty (the upstream node produced no video).")
        if not ffmpeg:
            raise RuntimeError(_FFMPEG_HINT)

        lang = _resolve_report_lang(报告语言)

        targets = [name for flag, name in ((导出MP4, "MP4"), (导出MKV, "MKV"),
                                           (导出WEBM, "WEBM"), (导出AVI, "AVI"),
                                           (导出MOV, "MOV")) if flag]
        if not targets:
            raise ValueError("Select at least one output format (MP4 / MKV / WEBM / AVI / MOV).")

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

        folder, name, counter, subfolder = _resolve_destination(
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
            return self._result(self._tag_produced(视频, produced), produced, folder, subfolder, lang)

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
                        "The upstream video is not a file-backed input and cannot be converted directly. "
            "Please use 'Load Video (Advanced)' as the upstream node.\n" + str(exc)
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
                "ffmpeg video conversion failed:\n"
                + result.stderr.decode("utf-8", "ignore")[-1500:]
            )

        return self._result(self._tag_produced(视频, produced), produced, folder, subfolder, lang)

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
    def _result(video, produced, folder, subfolder="", lang="en"):
        """组装返回：落盘摘要 + 节点内预览。

        `subfolder` 必须与文件相对output 目录的真实位置一致，否则前端的
        /view?subfolder=... 找不到文件，预览就会失败。
        """
        parts = [f"{kind} ({'copy' if direct else 'transcoded'})" for kind, _path, direct in produced]
        summary = (
            f"Wrote {len(produced)} format(s): {', '.join(parts)}\nFolder: {folder}"
        )

        # The full parameter report is produced here, so no separate report node
        # is needed; it is shown in the panel and returned as a string output.
        report = build_video_report(video, produced, lang)
        print("[Save Video] " + report.replace("\n", "\n[Save Video] "))

        return {
            "ui": {
                "text": [report] + [
                    _preview_hint(os.path.basename(path), "output", subfolder or "")
                    for _kind, path, _direct in produced
                ],
            },
            "result": (video, report),
        }

    @staticmethod
    def _tag_produced(video, produced):
        """Record what was written on the VIDEO object, for VideoReportNode."""
        try:
            setattr(video, _PRODUCED_ATTR, list(produced))
        except Exception:  # pragma: no cover - exotic VIDEO objects
            pass
        return video


# ---------------------------------------------------------------------------
# 节点 3：视频报告（源参数 vs 输出参数）
# ---------------------------------------------------------------------------

def _describe_video(path):
    """读取一个视频文件的参数，用于报告。失败时返回 (None, 原因)。"""
    try:
        return _probe_video(path), None
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"


def _video_param_lines(label, info, path, error=None, lang="en"):
    """把一个视频的信息格式化成报告行。"""
    lines = [f"{label}: {os.path.basename(path)}"]
    if not info:
        lines.append("  " + _tr("probe_failed", lang, error=error or "?"))
        if not os.path.exists(path):
            lines.append("  " + _tr("file_missing", lang, path=path))
        else:
            lines.append(f"  ({os.path.getsize(path) / (1024 * 1024):.2f} MB)")
        return lines

    container = _container_display(info.get("ext"))
    lines.append(f"  {_tr('container', lang)}: {container}")
    lines.append(f"  {_tr('resolution', lang)}: {info.get('width')}x{info.get('height')}")
    lines.append(f"  {_tr('fps', lang)}: {_fmt_fps(info.get('fps'))}")
    lines.append(
        f"  {_tr('video_codec', lang)}: "
        f"{_encoder_display(_to_encoder_name(info.get('video_codec')))}"
        f"（{info.get('video_codec')}）"
    )
    if info.get("pix_fmt"):
        lines.append(f"  {_tr('pixel_format', lang)}: {info['pix_fmt']}")
    if info.get("duration"):
        lines.append(f"  {_tr('duration', lang)}: {_fmt_duration(info['duration'])}")
    size_mb = info.get("size", 0) / (1024 * 1024)
    lines.append(f"  {_tr('file_size', lang)}: {size_mb:.2f}MB")
    if info.get("has_audio"):
        lines.append(
            f"  {_tr('audio_track', lang)}: {info.get('audio_codec')} / "
            f"{info.get('audio_rate')} Hz / {info.get('audio_channels')} ch"
        )
    else:
        lines.append(f"  {_tr('audio_track', lang)}: {_tr('no_audio_track', lang)}")
    if info.get("video_streams", 1) > 1:
        lines.append(f"  {_tr('video_streams', lang, n=info['video_streams'])}")
    return lines


def build_video_report(video, produced, lang="en"):
    """生成「源参数 vs 输出参数」报告文本。

    供保存节点直接内联调用，输出到面板并作为一个字符串输出点，
    因此不需要单独的报告节点中转。
    """
    lines = [_tr("video_report", lang), "=" * 60, ""]

    source_path = _source_path_of(video)
    if source_path and os.path.isfile(source_path):
        info, err = _describe_video(source_path)
        lines += _video_param_lines(_tr("source_label", lang), info, source_path, err, lang)
    else:
        lines.append(_tr("not_file_input", lang))
    lines.append("")

    if not produced:
        lines.append(_tr("exported_none", lang))
    else:
        lines.append(_tr("exported", lang, n=len(produced)))
        lines.append("-" * 60)
        # produced entries are (kind, path, direct) - see the append calls in
        # SaveVideoConverter.save(). Unpack in that same order.
        for index, (kind, path, direct) in enumerate(produced, start=1):
            how = _tr("direct_copy" if direct else "reencoded", lang)
            info, err = _describe_video(path)
            lines += _video_param_lines(
                _tr("export_label", lang, n=index, kind=kind, how=how),
                info, path, err, lang,
            )
            lines.append("")

    return "\n".join(lines).rstrip()


class VideoReportNode:
    """Report the parameters of a video, for cases where the save node is not used."""

    CATEGORY = "Video"
    FUNCTION = "report"
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("report",)
    OUTPUT_NODE = True
    DESCRIPTION = (
        "Report the parameters of the source video and of every exported file.\n"
        "Note: \"Save Video (Format Converter)\" already prints this report and exposes\n"
        "it as a string output, so this node is only needed when you want to inspect a\n"
        "VIDEO that did not come from the save node."
    )

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "视频": ("VIDEO", {"tooltip": "The VIDEO input of any video output node."}),
                "报告语言": (LANG_CHOICES, {
                    "default": LANG_FOLLOW_UI,
                    "tooltip": "Language of the report text. \"Follow UI language\" produces a "
                               "Chinese report while the ComfyUI interface is Chinese, and an "
                               "English report otherwise.",
                }),
            },
        }

    def report(self, 视频, 报告语言=LANG_FOLLOW_UI):
        if 视频 is None:
            raise ValueError("The Video input is empty (the upstream node produced no video).")

        lang = _resolve_report_lang(报告语言)
        produced = getattr(视频, _PRODUCED_ATTR, None)
        text = build_video_report(视频, produced, lang)
        print("[Video Report] " + text.replace("\n", "\n[Video Report] "))

        preview = []
        candidates = [p for _k, p, _d in produced] if produced else []
        if candidates:
            preview.append(_preview_hint(os.path.basename(candidates[0]), "output"))
        else:
            source_path = _source_path_of(视频)
            if source_path and os.path.isfile(source_path):
                preview.append(_preview_hint(os.path.basename(source_path), "input"))

        return {"ui": {"text": [text] + preview}, "result": (text,)}

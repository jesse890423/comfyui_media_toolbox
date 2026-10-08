"""视频节点冒烟测试：直接调用视频节点的核心逻辑（不经过 ComfyUI 前端）。

素材由 ffmpeg 现场生成后放进 ComfyUI 的 input 目录，走和真实使用一致的
相对名解析路径。ComfyUI 根目录可用环境变量 COMFY_ROOT 覆盖。
"""

import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import types

NODES_DIR = os.path.dirname(os.path.abspath(__file__))
PKG_NAME = "comfyui_media_toolbox_test"

COMFY_ROOT = os.environ.get("COMFY_ROOT") or os.path.dirname(
    os.path.dirname(os.path.dirname(NODES_DIR)))
if os.path.basename(COMFY_ROOT).lower() != "comfyui" and not os.path.isdir(
        os.path.join(COMFY_ROOT, "custom_nodes")):
    # 目录被嵌套安装时向上找带 custom_nodes 的 ComfyUI 根目录
    probe = COMFY_ROOT
    for _ in range(6):
        probe = os.path.dirname(probe)
        if os.path.isdir(os.path.join(probe, "custom_nodes")):
            COMFY_ROOT = probe
            break

INPUT_DIR = os.path.join(COMFY_ROOT, "input")
INPUT_COPY = os.path.join(INPUT_DIR, "__ape_video_test")
WORK = tempfile.mkdtemp(prefix="ape_video_test_")


class _FP(types.ModuleType):
    """folder_paths 的最小替身，只提供节点用到的接口。"""

    def __init__(self):
        super().__init__("folder_paths")
        self.models_dir = os.path.join(COMFY_ROOT, "models")
        self.output_directory = os.path.join(COMFY_ROOT, "output")

    def get_input_directory(self):
        return INPUT_DIR

    def get_output_directory(self):
        return os.path.join(COMFY_ROOT, "output")

    def get_temp_directory(self):
        return os.path.join(COMFY_ROOT, "temp")

    def get_annotated_filepath(self, name, default_dir=None):
        return os.path.join(default_dir or INPUT_DIR, name)

    def get_save_image_path(self, filename_prefix, output_dir, **_kw):
        """复刻 ComfyUI 的返回：(目录, 文件名, 计数器, 子目录, 原前缀)。"""
        subfolder = os.path.dirname(os.path.normpath(filename_prefix))
        filename = os.path.basename(os.path.normpath(filename_prefix))
        full = os.path.join(output_dir, subfolder)
        try:
            counter = max(
                int(a[len(filename) + 1:].split("_")[0])
                for a in os.listdir(full)
                if a.startswith(filename + "_")
            ) + 1
        except (ValueError, FileNotFoundError):
            os.makedirs(full, exist_ok=True)
            counter = 1
        return full, filename, counter, subfolder, filename_prefix

    def add_model_folder_path(self, *a, **k):
        pass


sys.modules["folder_paths"] = _FP()

pkg = types.ModuleType(PKG_NAME)
pkg.__path__ = [NODES_DIR]
sys.modules[PKG_NAME] = pkg


def _load(mod):
    spec = importlib.util.spec_from_file_location(
        PKG_NAME + "." + mod, os.path.join(NODES_DIR, mod + ".py"))
    m = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = m
    spec.loader.exec_module(m)
    return m


# nodes 必须先加载：video_nodes 通过 `from .nodes import ...` 取用它的
# 状态（如上报的界面语言），反序会导入出两个互不相干的模块实例。
ape = _load("nodes")
vn = _load("video_nodes")

PASS = []
FAIL = []


def check(label, fn):
    try:
        fn()
        PASS.append(label)
        print(f"  [OK] {label}")
    except Exception as exc:
        FAIL.append((label, exc))
        print(f"  [FAIL] {label}: {type(exc).__name__}: {exc}")


def expect_raises(label, fn, needle=""):
    def run():
        try:
            fn()
        except Exception as exc:
            if needle and needle.lower() not in str(exc).lower():
                raise AssertionError(f"wrong error: {exc}") from None
            return
        raise AssertionError("no error raised")
    check(label, run)


ffmpeg = ape.find_ffmpeg()
if not ffmpeg:
    raise SystemExit("ffmpeg not found; cannot build test material")
print("ffmpeg =", ffmpeg)
print("ComfyUI =", COMFY_ROOT)

# ------------------------------------------------------------ 准备测试素材
os.makedirs(INPUT_COPY, exist_ok=True)


def _mk(name, args):
    path = os.path.join(INPUT_COPY, name)
    if not os.path.exists(path):
        subprocess.run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error"] + args
                       + [path], check=True)
    return path


src = _mk("src.mp4", [
    "-f", "lavfi", "-i", "testsrc=size=640x360:rate=30:duration=4",
    "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100:duration=4",
    "-af", "aformat=channel_layouts=stereo",
    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest"])
_mk("silent.mp4", ["-i", src, "-an", "-c:v", "copy"])
REL = "__ape_video_test/src.mp4"
REL_SILENT = "__ape_video_test/silent.mp4"

print("\n素材就绪：", sorted(os.listdir(INPUT_COPY)))

loader = vn.LoadVideoAdvanced()
saver = vn.SaveVideoConverter()
GUARD = "only reads and writes inside"


def load(rel, **kw):
    params = {
        "视频文件": rel, "视频流序号": 0,
        "截断方式": vn.TRUNCATE_NONE,
        "起点(秒)": 0.0, "终点(秒)": 0.0, "截取时长(秒)": 60.0,
        "报告语言": vn.LANG_FOLLOW_UI,
    }
    params.update(kw)
    return loader.load(**params)


def out_file(prefix, ext):
    return os.path.join(COMFY_ROOT, "output", prefix + "_00001." + ext)


def save(video, prefix, **kw):
    params = {
        "视频": video, "文件名前缀": prefix,
        "导出MP4": True, "导出MKV": False, "导出WEBM": False,
        "导出AVI": False, "导出MOV": False,
        "视频编码": vn.AUTO, "画质CRF": vn.AUTO,
        "分辨率": vn.KEEP_SOURCE, "帧率": vn.KEEP_SOURCE,
        "音频处理": vn._AUDIO_KEEP, "音频码率": vn.AUTO,
        "报告语言": vn.LANG_FOLLOW_UI,
    }
    params.update(kw)
    return saver.save(**params)


def cleanup(prefix):
    for ext in ("mp4", "mkv", "webm", "avi", "mov"):
        p = out_file(prefix, ext)
        if os.path.isfile(p):
            os.unlink(p)


print("\n===== 1. 加载 =====")


def t_load():
    res = load(REL)
    info = vn._video_source_of(res["result"][0])
    assert info and info["width"] == 640 and info["height"] == 360, info
    assert abs(info["fps"] - 30.0) < 0.01, info
    assert info["has_audio"] is True, info
    assert "Loaded:" in res["result"][1], res["result"][1]


check("从 input 目录加载视频", t_load)


def t_silent():
    info = vn._video_source_of(load(REL_SILENT)["result"][0])
    assert info["has_audio"] is False, info


check("识别无音轨视频", t_silent)


def t_trunc():
    info = vn._video_source_of(
        load(REL, **{"截断方式": vn.TRUNCATE_HEAD, "截取时长(秒)": 2.0})["result"][0])
    assert abs(info["clip_duration"] - 2.0) < 0.05, info


check("头部截断 2 秒", t_trunc)

print("\n===== 2. 安全边界 =====")
expect_raises("拒绝 ..\\ 越界相对路径",
              lambda: load("../../../Windows/win.ini"), GUARD)
expect_raises("拒绝绝对路径", lambda: load("C:/Windows/win.ini"), GUARD)
expect_raises("拒绝下拉占位项",
              lambda: load("(no video files in the input directory)"), "input")
expect_raises("拒绝绝对路径文件名前缀",
              lambda: save(load(REL)["result"][0], "C:/tmp/evil"), "relative")
expect_raises("拒绝含 .. 的文件名前缀",
              lambda: save(load(REL)["result"][0], "../../escape"), "..")
expect_raises("拒绝空输出格式",
              lambda: save(load(REL)["result"][0], "video/none", 导出MP4=False))

print("\n===== 3. 转换 =====")
video = load(REL)["result"][0]


def t_direct():
    cleanup("video/direct")
    r = save(video, "video/direct")
    assert os.path.isfile(out_file("video/direct", "mp4")), out_file("video/direct", "mp4")
    # 同格式 + 全自动 = 直转，报告里应明确写出没有重新编码
    text = r["result"][1]
    assert "no re-encoding" in text or "direct copy" in text.lower(), text
    cleanup("video/direct")


check("同格式直转", t_direct)


def t_direct_blocked_for_foreign_path():
    """复评第 1 点的视频对应项：源路径越界时不得将主机文件复制进 output。"""
    marker = b"HOST-SECRET-DO-NOT-COPY"
    secret = os.path.join(WORK, "secret_video.bin")
    with open(secret, "wb") as fh:
        fh.write(marker + b"\0" * 8192)

    cleanup("video/foreign")
    fresh = load(REL)["result"][0]
    vn._video_source_of(fresh)["path"] = secret      # 模拟被篡改的源路径
    text = save(fresh, "video/foreign")["result"][1]

    assert "(no re-encoding)" not in text and "未重新编码" not in text, text
    out = out_file("video/foreign", "mp4")
    assert os.path.isfile(out), text
    with open(out, "rb") as fh:
        assert marker not in fh.read(), "越界文件被复制进了 output"
    probe = vn._probe_video(out)
    assert probe["width"] == 640, probe
    cleanup("video/foreign")


check("越界源路径的视频改为重新编码", t_direct_blocked_for_foreign_path)


def t_video_source_helpers_reject_foreign():
    class Fake:
        def __init__(self, stream):
            self._stream = stream

        def get_stream_source(self):
            return self._stream

    outside = os.path.join(WORK, "secret_video.bin")
    assert vn._file_source_of(Fake(outside)) is None, "工作空间外的文件不可信"
    assert vn._file_source_of(Fake("\\\\srv\\share\\a.mp4")) is None, "UNC 不可信"
    assert vn._file_source_of(Fake("//srv/share/a.mp4")) is None, "UNC 不可信"
    inside = os.path.join(INPUT_COPY, "src.mp4")
    assert vn._file_source_of(Fake(inside)) == os.path.realpath(inside)
    assert vn._source_path_of(Fake(outside)) is None
    # 报告遇到不可信来源时只标注非文件输入，不去探测该路径
    text = vn.build_video_report(Fake(outside), [], "en")
    assert isinstance(text, str) and text, text


check("视频源路径辅助函数拒绝越界与 UNC", t_video_source_helpers_reject_foreign)


def t_multi():
    cleanup("video/multi")
    save(video, "video/multi", 导出MKV=True, 导出WEBM=True)
    for ext in ("mp4", "mkv", "webm"):
        assert os.path.isfile(out_file("video/multi", ext)), ext
    cleanup("video/multi")


check("一次产出多种格式", t_multi)


def t_scale():
    cleanup("video/scaled")
    save(video, "video/scaled", 分辨率="1280×720", 帧率="30",
         视频编码="H.265 (libx265)", 音频处理="MP3", 音频码率="192 kbps")
    probe = vn._probe_video(out_file("video/scaled", "mp4"))
    assert (probe["width"], probe["height"]) == (1280, 720), probe
    assert abs(probe["fps"] - 30.0) < 0.5, probe
    cleanup("video/scaled")


check("分辨率/帧率/编码转码", t_scale)


def t_drop_audio():
    cleanup("video/mute")
    save(video, "video/mute", 音频处理=vn._AUDIO_DROP)
    probe = vn._probe_video(out_file("video/mute", "mp4"))
    assert probe["has_audio"] is False, probe
    cleanup("video/mute")


check("移除音轨", t_drop_audio)


def t_encoder_fallback():
    # H.264 不在 WEBM 推荐列表，自动模式应回退 VP9
    cleanup("video/webm")
    save(video, "video/webm", 导出MP4=False, 导出WEBM=True)
    probe = vn._probe_video(out_file("video/webm", "webm"))
    assert probe["video_codec"] == "vp9", probe
    cleanup("video/webm")


check("容器不兼容时回退编码", t_encoder_fallback)


def t_clip_encode():
    # 截断后应只编码截出的那一段，而不是整段再切
    cleanup("video/clip")
    clipped = load(REL, **{"截断方式": vn.TRUNCATE_HEAD, "截取时长(秒)": 2.0})["result"][0]
    save(clipped, "video/clip")
    probe = vn._probe_video(out_file("video/clip", "mp4"))
    assert 1.5 < probe["duration"] < 2.6, probe
    cleanup("video/clip")


check("截断视频转码", t_clip_encode)

print("\n===== 4. 报告与报告节点 =====")


def t_report_lang():
    zh = load(REL, 报告语言=ape.LANG_ZH)["result"][1]
    assert "已加载：" in zh, zh
    assert "Loaded:" not in zh, zh
    en = load(REL, 报告语言=ape.LANG_EN)["result"][1]
    assert "Loaded:" in en, en
    assert "已加载：" not in en, en
    # 跟随界面语言由前端上报决定
    ape.set_ui_language("zh")
    follow = load(REL, 报告语言=vn.LANG_FOLLOW_UI)["result"][1]
    assert "已加载：" in follow, follow
    ape.set_ui_language("en")
    follow2 = load(REL, 报告语言=vn.LANG_FOLLOW_UI)["result"][1]
    assert "Loaded:" in follow2, follow2


check("加载报告语言开关", t_report_lang)


def t_save_report_lang():
    video2 = load(REL)["result"][0]
    cleanup("video/lang")
    zh = save(video2, "video/lang", 报告语言=ape.LANG_ZH)["result"][1]
    en = save(video2, "video/lang", 报告语言=ape.LANG_EN)["result"][1]
    # 报告首行是「视频报告 / Video report」标题，两种语言必须各自命中
    assert "视频报告" in zh, zh
    assert "已加载：" in zh or "源文件" in zh, zh
    assert "Video report" in en, en
    assert "Source:" in en, en
    assert "视频报告" not in en, en
    cleanup("video/lang")


check("保存报告语言开关", t_save_report_lang)


def t_report_node():
    node = vn.VideoReportNode()
    zh = node.report(视频=video, 报告语言=ape.LANG_ZH)
    en = node.report(视频=video, 报告语言=ape.LANG_EN)
    # OUTPUT_NODE 节点返回 {"ui": ..., "result": ...}
    zh_text = zh["result"][0]
    en_text = en["result"][0]
    assert "视频报告" in zh_text, zh_text
    assert "Video report" in en_text, en_text
    assert "已加载：" not in en_text, en_text


check("视频报告节点", t_report_node)

print("\n" + "=" * 60)
print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
for label, exc in FAIL:
    print(f"  FAIL {label}: {exc}")
print("=" * 60)

shutil.rmtree(WORK, ignore_errors=True)
shutil.rmtree(INPUT_COPY, ignore_errors=True)
sys.exit(0 if not FAIL else 1)
"""离线冒烟测试：直接调用节点核心逻辑（不经过 ComfyUI 前端）。

覆盖两件事：
1. 正常路径：input 目录相对名 -> 加载 -> 截断 -> 保存 -> 报告
2. 安全边界：确认 ltdrdata 提出的四项要求仍然成立（越界路径被拒绝）
"""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types

NODES_DIR = os.path.dirname(os.path.abspath(__file__))
PKG_NAME = "comfyui_media_toolbox_test"


def _find_comfy_root():
    """定位 ComfyUI 根目录：环境变量优先，其次从本文件位置向上找。"""
    env = os.environ.get("COMFY_ROOT")
    if env and os.path.isdir(env):
        return env
    probe = NODES_DIR
    for _ in range(6):
        probe = os.path.dirname(probe)
        if os.path.isdir(os.path.join(probe, "custom_nodes")) \
                and os.path.isdir(os.path.join(probe, "input")):
            return probe
    raise SystemExit(
        "Cannot locate the ComfyUI root. Set COMFY_ROOT to the directory that "
        "contains custom_nodes/ and input/.")


COMFY_ROOT = _find_comfy_root()
INPUT_DIR = os.path.join(COMFY_ROOT, "input")


class _FP(types.ModuleType):
    def __init__(self):
        super().__init__("folder_paths")
        self._root = COMFY_ROOT
        self.models_dir = os.path.join(COMFY_ROOT, "models")
        self.output_directory = os.path.join(COMFY_ROOT, "output")

    def get_input_directory(self):
        return INPUT_DIR

    def get_output_directory(self):
        return os.path.join(self._root, "output")

    def get_annotated_filepath(self, name, default_dir=None):
        return os.path.join(default_dir or INPUT_DIR, name)

    def get_save_image_path(self, filename_prefix, output_dir, image_width=0, image_height=0):
        """复刻 ComfyUI 的 5 元组返回：(目录, 文件名, 计数器, 子目录, 原前缀)。"""
        subfolder = os.path.dirname(os.path.normpath(filename_prefix))
        filename = os.path.basename(os.path.normpath(filename_prefix))
        full = os.path.join(output_dir, subfolder)
        if os.path.commonpath((os.path.abspath(full), output_dir)) != output_dir:
            raise Exception("Saving outside the output folder is not allowed.")
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

# 以包方式导入，保证 video_nodes 里的 `from .nodes import ...` 能解析到
# 同一个模块实例（它要共享这里上报的界面语言状态）。
pkg = types.ModuleType(PKG_NAME)
pkg.__path__ = [NODES_DIR]
sys.modules[PKG_NAME] = pkg


def _load(mod):
    spec = importlib.util.spec_from_file_location(
        PKG_NAME + "." + mod, os.path.join(NODES_DIR, mod + ".py")
    )
    m = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = m
    spec.loader.exec_module(m)
    return m


ape = _load("nodes")
vn = _load("video_nodes")

WORK = tempfile.mkdtemp(prefix="ape_smoke_")
INPUT_COPY = os.path.join(INPUT_DIR, "__ape_smoke_src")
os.makedirs(INPUT_COPY, exist_ok=True)

ffmpeg = ape.find_ffmpeg()
print("ffmpeg =", ffmpeg)

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


# ------------------------------------------------------------ 准备测试素材
wav_src = os.path.join(WORK, "src_44100_16_stereo.wav")
mp4_src = os.path.join(WORK, "src_video.mp4")
mp3_src = os.path.join(WORK, "src_320k.mp3")

if not os.path.exists(wav_src):
    subprocess.run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                    "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100:duration=3",
                    "-af", "aformat=channel_layouts=stereo", "-c:a", "pcm_s16le", wav_src], check=True)
if not os.path.exists(mp4_src):
    subprocess.run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                    "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100:duration=3",
                    "-f", "lavfi", "-i", "color=c=blue:s=320x240:d=3",
                    "-af", "aformat=channel_layouts=stereo",
                    "-c:a", "aac", "-c:v", "libx264", "-shortest", mp4_src], check=True)
if not os.path.exists(mp3_src):
    subprocess.run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                    "-i", wav_src, "-c:a", "libmp3lame", "-b:a", "320k", mp3_src], check=True)

# 放进 input 目录，走真实的相对名解析路径
for f in (wav_src, mp4_src, mp3_src):
    shutil.copy2(f, os.path.join(INPUT_COPY, os.path.basename(f)))
REL = "__ape_smoke_src/" + os.path.basename(wav_src)
REL_MP4 = "__ape_smoke_src/" + os.path.basename(mp4_src)

print("\n素材就绪：", sorted(os.listdir(INPUT_COPY)))
print("input 列表是否含新素材：", REL in ape.list_input_media())

loader = ape.LoadAudioAdvanced()


def load(name, **kw):
    params = {
        "音频文件": name, "音轨序号": 0,
        "截断方式": ape.TRUNCATE_NONE,
        "起点(秒)": 0.0, "终点(秒)": 0.0, "截取时长(秒)": 60.0,
    }
    params.update(kw)
    return loader.load(**params)


print("\n===== 1. 正常加载 =====")


def t_wav():
    res = load(REL)
    audio = res["result"][0]
    info = audio[ape._SOURCE_KEY]
    assert info["format"] == "wav", info
    assert info["sample_rate"] == 44100, info
    assert info["channels"] == 2, info
    assert info["bits"] == 16, info
    assert "Loaded:" in res["result"][1]


check("从 input 目录加载 WAV", t_wav)


def t_video_audio():
    res = load(REL_MP4)
    info = res["result"][0][ape._SOURCE_KEY]
    assert info["sample_rate"] > 0, info


check("从视频提取音频（保留能力）", t_video_audio)

print("\n===== 2. 截断 =====")


def t_trunc():
    res = load(REL, **{"截断方式": ape.TRUNCATE_HEAD, "截取时长(秒)": 1.0})
    info = res["result"][0][ape._SOURCE_KEY]
    assert info["truncated"] is True, info
    assert abs(info["output_duration"] - 1.0) < 0.2, info


check("头部截断 1 秒", t_trunc)

print("\n===== 3. 安全边界（ltdrdata 四项要求）=====")
GUARD = "only reads and writes inside"

expect_raises("拒绝 ..\\ 越界相对路径",
              lambda: load("../../../Windows/win.ini"), GUARD)
expect_raises("拒绝绝对路径",
              lambda: load("C:/Windows/win.ini"), GUARD)
expect_raises("拒绝 ..\\..\\ 穿越",
              lambda: load("__ape_smoke_src/../../../../etc/passwd"), GUARD)
expect_raises("拒绝指向其它目录的绝对路径",
              lambda: load(os.path.join(WORK, "outside.wav")), GUARD)
expect_raises("空文件名被拒绝", lambda: load(""), "input directory")


def t_placeholder_rejected():
    # 下拉里的占位项不是真实文件，必须报错而不是静默
    try:
        load("(no audio or video files in the input directory)")
    except ValueError:
        return
    raise AssertionError("placeholder accepted")


check("拒绝下拉占位项", t_placeholder_rejected)

print("\n===== 4. 保存节点 =====")
saver = ape.SaveAudioPlatformExport()


def save(audio, prefix, lang=ape.LANG_FOLLOW_UI):
    return saver.save(**{
        "音频": audio,
        "文件名前缀": prefix,
        "导出WAV": True,
        "WAV位深": "16-bit",
        "导出MP3": True,
        "MP3码率": "320 kbps",
        "导出OPUS": False,
        "OPUS码率": "128 kbps",
        "保留FLAC": False,
        "采样率": "44.1 kHz",
        "声道": "Stereo",
        "平台预设": "QQ Music",
        "平台名称": "QQ Music",
        "合格格式": "wav,mp3",
        "最低采样率": 44100,
        "最低位深": 16,
        "最低码率kbps": 320,
        "要求声道": "Stereo",
        "文件大小上限MB": 200,
        "报告语言": lang,
    })


def t_save():
    audio = load(REL)["result"][0]
    res = save(audio, "__ape_smoke_out/ape")
    report = res["result"][0]
    assert "wav" in report.lower(), report
    # 保存节点返回 AUDIO，其源信息应指向写出的文件
    out_info = res["result"][1][ape._SOURCE_KEY]
    assert out_info.get("path"), out_info


check("保存 WAV/MP3 并生成报告", t_save)


def t_report_lang():
    audio = load(REL)["result"][0]
    zh = save(audio, "__ape_smoke_out/zh", ape.LANG_ZH)["result"][0]
    en = save(audio, "__ape_smoke_out/en", ape.LANG_EN)["result"][0]
    assert "结果：" in zh, zh
    assert "Result:" in en, en
    assert "Result:" not in zh, zh
    assert "结果：" not in en, en
    # 跟随界面语言：前端上报 zh 时出中文，上报 en 时出英文
    ape.set_ui_language("zh")
    assert "结果：" in save(audio, "__ape_smoke_out/f1", ape.LANG_FOLLOW_UI)["result"][0]
    ape.set_ui_language("en")
    assert "Result:" in save(audio, "__ape_smoke_out/f2", ape.LANG_FOLLOW_UI)["result"][0]


check("报告语言开关生效（中/英/跟随界面）", t_report_lang)

expect_raises("拒绝绝对路径文件名前缀",
              lambda: save(load(REL)["result"][0], "C:/tmp/evil"), "relative")
expect_raises("拒绝含 .. 的文件名前缀",
              lambda: save(load(REL)["result"][0], "../../escape"), "..")
expect_raises("拒绝盘符路径前缀",
              lambda: save(load(REL)["result"][0], "D:/evil"), "relative")

print("\n===== 5. 预设与中文别名向后兼容 =====")


def t_preset():
    assert ape._norm_choice("双声道") == ape.STEREO
    assert ape._norm_choice("Stereo") == ape.STEREO
    assert ape._norm_choice("单声道") == ape.MONO
    assert ape._norm_choice("自动（跟随源）") == ape.AUTO
    assert ape._norm_choice("V0（最高质量）") == ape.V0_BEST
    assert ape._norm_truncate("只取开头一段（0 秒 → 截取时长）") == ape.TRUNCATE_HEAD
    assert ape._norm_truncate(ape.TRUNCATE_NONE) == ape.TRUNCATE_NONE
    # 平台别名：旧工作流里的中文名仍能解析
    assert ape._PLATFORM_ALIASES["网易云音乐"] == "NetEase Cloud Music"
    assert ape._PLATFORM_ALIASES["QQ音乐"] == "QQ Music"
    assert ape._PLATFORM_ALIASES["自定义"] == ape.CUSTOM_PRESET
    p = ape._PLATFORM_PRESETS["NetEase Cloud Music"]
    assert p["min_rate"] == 44100, p


check("中文别名与英文标准值双向兼容", t_preset)


def t_soda_is_separate():
    # 汽水音乐与 QQ 音乐是两个平台，早期版本里也是分开的两项。
    # 别名表若把汽水映射到 QQ，用户存的老工作流就会静默换成另一个平台。
    assert "Soda Music" in ape._PLATFORM_PRESETS, list(ape._PLATFORM_PRESETS)
    assert ape._PLATFORM_ALIASES["汽水音乐"] == "Soda Music"
    assert ape._PLATFORM_ALIASES["汽水音乐"] != ape._PLATFORM_ALIASES["QQ音乐"]
    # 别名表的每个目标都必须真实存在
    for zh, en in ape._PLATFORM_ALIASES.items():
        assert en in ape._PLATFORM_PRESETS or en in (ape.CUSTOM_PRESET, ape.NO_CHECK_PRESET), (zh, en)
    # 默认预设应为汽水音乐（与初始版本一致）
    spec = ape.SaveAudioPlatformExport.INPUT_TYPES()["required"]["平台预设"]
    assert spec[1]["default"] == "Soda Music", spec[1]["default"]
    assert spec[0][0] == "Soda Music", spec[0][:2]
    assert "汽水音乐" not in spec[0], "旧中文值不应出现在新的下拉选项里"


check("汽水音乐为独立平台且是默认预设", t_soda_is_separate)

print("\n===== 6. 视频节点 =====")


def t_video_load():
    res = vn.LoadVideoAdvanced().load(**{
        "视频文件": REL_MP4,
        "视频流序号": 0,
        "截断方式": vn.TRUNCATE_NONE,
        "起点(秒)": 0.0,
        "终点(秒)": 0.0,
        "截取时长(秒)": 60.0,
    })
    info = vn._video_source_of(res["result"][0])
    assert info and info["width"] == 320, info


check("加载视频", t_video_load)


def t_video_report_lang():
    res = vn.LoadVideoAdvanced().load(**{
        "视频文件": REL_MP4,
        "视频流序号": 0,
        "截断方式": vn.TRUNCATE_NONE,
        "起点(秒)": 0.0,
        "终点(秒)": 0.0,
        "截取时长(秒)": 60.0,
        "报告语言": vn.LANG_ZH,
    })
    zh = res["result"][1]
    assert "已加载：" in zh, zh
    assert "Loaded:" not in zh, zh
    res2 = vn.LoadVideoAdvanced().load(**{
        "视频文件": REL_MP4,
        "视频流序号": 0,
        "截断方式": vn.TRUNCATE_NONE,
        "起点(秒)": 0.0,
        "终点(秒)": 0.0,
        "截取时长(秒)": 60.0,
        "报告语言": vn.LANG_EN,
    })
    en = res2["result"][1]
    assert "Loaded:" in en, en
    assert "已加载：" not in en, en


check("视频加载报告语言开关生效", t_video_report_lang)

expect_raises("视频拒绝越界路径",
              lambda: vn.LoadVideoAdvanced().load(**{
                  "视频文件": "../../../Windows/win.ini",
                  "视频流序号": 0,
                  "截断方式": vn.TRUNCATE_NONE,
                  "起点(秒)": 0.0,
                  "终点(秒)": 0.0,
                  "截取时长(秒)": 60.0,
              }),
              GUARD)

print("\n===== 7. 示例工作流与节点定义对齐 =====")

SPECS = {
    "LoadAudioAdvanced": ape.LoadAudioAdvanced,
    "SaveAudioPlatformExport": ape.SaveAudioPlatformExport,
    "LoadVideoAdvanced": vn.LoadVideoAdvanced,
    "SaveVideoConverter": vn.SaveVideoConverter,
    "VideoReportNode": vn.VideoReportNode,
}
LINK_TYPES = ("AUDIO", "VIDEO", "IMAGE", "LATENT")


def t_workflows():
    # widgets_values 按位置存储，控件一增一删就会整体错位：曾经把文件名填进了
    # 音轨序号、把中文选项写进了下拉值。ComfyUI 的 migrateWidgetsValues 只处理
    # control_after_generate 的增删，补不回被删除的控件，所以必须在这里拦住。
    wf_dir = os.path.join(NODES_DIR, "workflows")
    problems = []
    for fn in sorted(os.listdir(wf_dir)):
        if not fn.endswith(".json"):
            continue
        with open(os.path.join(wf_dir, fn), encoding="utf-8") as fh:
            data = json.load(fh)
        for n in data.get("nodes", []):
            cls = SPECS.get(n.get("type"))
            if cls is None:
                continue
            required = cls.INPUT_TYPES()["required"]
            # 上游连线槽没有 widget，不进 widgets_values
            names = [k for k, v in required.items()
                     if not (isinstance(v[0], str) and v[0] in LINK_TYPES)]
            named = n.get("widgets_values_named") or {}
            values = n.get("widgets_values") or []

            unknown = [k for k in named if k not in names]
            if unknown:
                problems.append(f"{fn}/{n['type']}: 已删除的控件仍在工作流里 {unknown}")
                continue
            if list(named.keys()) != names:
                problems.append(f"{fn}/{n['type']}: 控件顺序与 INPUT_TYPES 不一致")
                continue
            if values != [named[k] for k in names]:
                problems.append(f"{fn}/{n['type']}: widgets_values 与 named 不一致")
            # 每个下拉值都必须在后端列表里，否则运行时会 Value not in list
            for k in names:
                kind = required[k][0]
                if isinstance(kind, list) and named[k] not in kind:
                    # 文件下拉在示例里留空是有意的（它列的是使用者自己的目录）
                    if k not in ("音频文件", "视频文件"):
                        problems.append(
                            f"{fn}/{n['type']}: {k} 的值 {named[k]!r} 不在后端列表内")
    assert not problems, "\n      ".join(problems)


check("示例工作流与 INPUT_TYPES 对齐", t_workflows)

print()
print("=" * 60)
print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
for label, exc in FAIL:
    print(f"  FAIL {label}: {exc}")
print("=" * 60)

shutil.rmtree(WORK, ignore_errors=True)
shutil.rmtree(INPUT_COPY, ignore_errors=True)
sys.exit(0 if not FAIL else 1)

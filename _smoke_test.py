"""离线冒烟测试：直接调用音频节点的核心逻辑（不经过 ComfyUI 前端）。"""

import importlib
import os
import shutil
import subprocess
import sys

NODES_DIR = os.path.dirname(os.path.abspath(__file__))
PKG_NAME = os.path.basename(NODES_DIR)

# ComfyUI 根目录 = 本包的上一级（custom_nodes 的父目录）
COMFY_ROOT = os.path.dirname(os.path.dirname(NODES_DIR))
if COMFY_ROOT not in sys.path:
    sys.path.insert(0, COMFY_ROOT)
if os.path.dirname(NODES_DIR) not in sys.path:
    sys.path.insert(0, os.path.dirname(NODES_DIR))

ape = importlib.import_module(PKG_NAME + ".nodes")

WORK = os.path.join(COMFY_ROOT, "temp", "ape_test")
os.makedirs(WORK, exist_ok=True)

# 清理上一轮产出，保证序号从 00001 开始
for entry in os.listdir(WORK):
    if entry.startswith("out_"):
        target = os.path.join(WORK, entry)
        if os.path.isdir(target):
            shutil.rmtree(target)
        else:
            os.unlink(target)


def out_file(prefix, ext):
    """绝对路径前缀的产出位置：末段为文件名前缀，落在其父目录。"""
    return os.path.join(os.path.dirname(prefix), os.path.basename(prefix) + "_00001." + ext)

ffmpeg = ape.find_ffmpeg()
print("ffmpeg =", ffmpeg)

# ---------------------------------------------------------------- 准备测试素材
wav_src = os.path.join(WORK, "源音频_44100_16_stereo.wav")
mp4_src = os.path.join(WORK, "源视频.mp4")
mp3_src = os.path.join(WORK, "源音频_320k.mp3")

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

print("\n素材：", os.listdir(WORK))

loader = ape.LoadAudioAdvanced()


def load(path, **kw):
    params = {
        "文件路径": path, "音频文件": "", "音轨序号": 0,
        "截断方式": ape.TRUNCATE_NONE, "起点(秒)": 0.0, "终点(秒)": 0.0, "截取时长(秒)": 60.0,
    }
    params.update(kw)
    return loader.load(**params)


print("\n===== 测试 1：绝对路径加载 WAV =====")
res = load(wav_src)
audio = res["result"][0]
info = audio[ape._SOURCE_KEY]
print(res["result"][1])
assert info["format"] == "wav" and info["sample_rate"] == 44100 and info["channels"] == 2, info
assert info["bits"] == 16, info
print(">>> OK  时长=%.3fs 形状=%s" % (info["output_duration"], tuple(audio["waveform"].shape)))

print("\n===== 测试 2：加载视频并提取音频 =====")
res = load(mp4_src)
audio_v = res["result"][0]
info_v = audio_v[ape._SOURCE_KEY]
print(res["result"][1])
assert info_v["has_video"] is True
assert audio_v["waveform"].shape[-1] > 0
print(">>> OK  已从视频提取音频")

print("\n===== 测试 3：只取开头一段（0 秒 → 1.0s）=====")
res = load(wav_src, **{"截断方式": ape.TRUNCATE_HEAD, "截取时长(秒)": 1.0})
a = res["result"][0]
assert abs(a[ape._SOURCE_KEY]["output_duration"] - 1.0) < 0.02, a[ape._SOURCE_KEY]
print(">>> OK 输出时长=%.3fs" % a[ape._SOURCE_KEY]["output_duration"])

print("\n===== 测试 4：只取中间一段（0.5s → 2.0s）=====")
res = load(wav_src, **{"截断方式": ape.TRUNCATE_RANGE, "起点(秒)": 0.5, "终点(秒)": 2.0})
a = res["result"][0]
assert abs(a[ape._SOURCE_KEY]["output_duration"] - 1.5) < 0.02, a[ape._SOURCE_KEY]
print(">>> OK 输出时长=%.3fs" % a[ape._SOURCE_KEY]["output_duration"])

print("\n===== 测试 5：从起点一直到结尾（1.5s → 末）=====")
res = load(wav_src, **{"截断方式": ape.TRUNCATE_TAIL, "起点(秒)": 1.5})
a = res["result"][0]
assert abs(a[ape._SOURCE_KEY]["output_duration"] - 1.5) < 0.02, a[ape._SOURCE_KEY]
print(">>> OK 输出时长=%.3fs" % a[ape._SOURCE_KEY]["output_duration"])

print("\n===== 测试 5b：旧工作流英文/旧中文值仍可用（向后兼容）=====")
for legacy in ("不截断", "从头截取时长", "起点到终点", "起点到结尾"):
    r = load(wav_src, **{"截断方式": legacy, "截取时长(秒)": 1.0,
                        "起点(秒)": 0.5, "终点(秒)": 2.0})
    print("    旧值 %-8s → %.3fs" % (legacy, r["result"][0][ape._SOURCE_KEY]["output_duration"]))
print(">>> OK 旧值兼容")

saver = ape.SaveAudioPlatformExport()


def save(audio, prefix, **kw):
    params = {
        "音频": audio, "文件名前缀": prefix,
        "导出WAV": True, "WAV位深": ape.AUTO, "导出MP3": False, "MP3码率": ape.AUTO,
        "导出OPUS": False, "OPUS码率": ape.AUTO, "保留FLAC": False,
        "采样率": ape.AUTO, "声道": ape.AUTO,
        "平台预设": "不校验", "平台名称": "不校验", "合格格式": "wav,mp3",
        "最低采样率": 44100, "最低位深": 16, "最低码率kbps": 320,
        "要求声道": ape.STEREO, "文件大小上限MB": 200,
    }
    params.update(kw)
    return saver.save(**params)


print("\n===== 测试 6：WAV 源 → WAV（同格式 auto，应直出复制）=====")
res = load(wav_src)
out = os.path.join(WORK, "out_direct")
rep = save(res["result"][0], out)["result"][0]
print(rep)
assert "直接复制" in rep, rep
assert "[直出]" in rep, rep
print(">>> OK 同格式直出")

print("\n===== 测试 7：WAV 源 → MP3 + WAV + FLAC（多格式一次转换）=====")
out = os.path.join(WORK, "out_multi")
rep = save(res["result"][0], out, 导出MP3=True, 保留FLAC=True)["result"][0]
print(rep)
assert "MP3" in rep and "FLAC" in rep
assert os.path.exists(out_file(out, "mp3")), out_file(out, "mp3")
assert os.path.exists(out_file(out, "flac")), out_file(out, "flac")
assert os.path.exists(out_file(out, "wav")), out_file(out, "wav")
print(">>> OK 多格式转换")

print("\n===== 测试 8：MP3 源 → MP3（同格式直出）+ OPUS =====")
res = load(mp3_src)
info_m = res["result"][0][ape._SOURCE_KEY]
print("源 MP3 信息：", {k: info_m.get(k) for k in ("format", "bitrate_kbps", "is_lossless")})
out = os.path.join(WORK, "out_mp3")
rep = save(res["result"][0], out, 导出MP3=True, 导出OPUS=True)["result"][0]
print(rep)
assert os.path.exists(out_file(out, "opus")), out_file(out, "opus")
assert "[直出]" in rep, rep
print(">>> OK MP3→MP3 直出 / MP3→OPUS 转码")

print("\n===== 测试 9：手动指定参数（WAV 16bit → 24bit 48kHz）=====")
out = os.path.join(WORK, "out_manual")
rep = save(res["result"][0], out, WAV位深="24 位", 采样率="48 kHz")["result"][0]
print(rep)
assert "[直出]" not in rep
print(">>> OK 手动参数转码")

print("\n===== 测试 10：平台校验（汽水音乐预设）=====")
out = os.path.join(WORK, "out_platform")
rep = save(res["result"][0], out, 导出MP3=True, 平台预设="汽水音乐")["result"][0]
print(rep)
assert "文件大小≤200MB" in rep, rep

print("\n===== 测试 11：旧英文选项值仍可用（向后兼容）=====")
res = load(wav_src)
out = os.path.join(WORK, "out_legacy")
rep = save(res["result"][0], out, WAV位深="auto", 采样率="auto", 声道="auto",
          要求声道="stereo", 导出MP3=True, MP3码率="auto")["result"][0]
print(rep)
assert "44.1kHz / 16bit / 2ch" in rep, rep
print(">>> OK 旧英文值兼容")

print("\n===== 测试 12：中文选项解析正确（44.1 kHz / 16 位）=====")
out = os.path.join(WORK, "out_zh")
rep = save(res["result"][0], out, WAV位深="16 位", 采样率="44.1 kHz", 声道="双声道")["result"][0]
print(rep)
assert "44.1kHz / 16bit / 2ch" in rep, rep
print(">>> OK 中文选项解析正确")

print("\n全部测试通过 ✅")

"""视频节点冒烟测试：直接调用两个视频节点的核心逻辑。"""

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

# 以包方式导入，保证 `from .nodes import ...` 相对导入可用
if os.path.dirname(NODES_DIR) not in sys.path:
    sys.path.insert(0, os.path.dirname(NODES_DIR))

video_nodes = importlib.import_module(PKG_NAME + ".video_nodes")
audio_nodes = importlib.import_module(PKG_NAME + ".nodes")

WORK = os.path.join(COMFY_ROOT, "temp", "vtest")
os.makedirs(WORK, exist_ok=True)
for entry in os.listdir(WORK):
    if entry.startswith("out_"):
        target = os.path.join(WORK, entry)
        shutil.rmtree(target) if os.path.isdir(target) else os.unlink(target)

ffmpeg = audio_nodes.find_ffmpeg()
print("ffmpeg =", ffmpeg)

# ---------------------------------------------------------------- 准备素材
mp4_src = os.path.join(WORK, "源视频.mp4")
mkv_src = os.path.join(WORK, "源视频.mkv")
silent_src = os.path.join(WORK, "无声视频.mp4")

if not os.path.exists(mp4_src):
    subprocess.run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                    "-f", "lavfi", "-i", "testsrc=size=640x360:rate=30:duration=4",
                    "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100:duration=4",
                    "-af", "aformat=channel_layouts=stereo",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    "-shortest", mp4_src], check=True)
if not os.path.exists(mkv_src):
    subprocess.run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                    "-i", mp4_src, "-c", "copy", mkv_src], check=True)
if not os.path.exists(silent_src):
    subprocess.run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                    "-i", mp4_src, "-an", "-c:v", "copy", silent_src], check=True)

print("\n素材：", sorted(os.listdir(WORK)))

loader = video_nodes.LoadVideoAdvanced()
saver = video_nodes.SaveVideoConverter()


def load(path, **kw):
    params = {
        "文件路径": path, "视频文件": "", "视频流序号": 0,
        "截断方式": audio_nodes.TRUNCATE_NONE,
        "起点(秒)": 0.0, "终点(秒)": 0.0, "截取时长(秒)": 60.0,
    }
    params.update(kw)
    return loader.load(**params)


def out_file(prefix, ext):
    return os.path.join(os.path.dirname(prefix),
                        os.path.basename(prefix) + "_00001." + ext)


def save(video, prefix, **kw):
    params = {
        "视频": video, "文件名前缀": prefix,
        "导出MP4": True, "导出MKV": False, "导出WEBM": False,
        "导出AVI": False, "导出MOV": False,
        "视频编码": video_nodes.AUTO, "画质CRF": video_nodes.AUTO,
        "分辨率": video_nodes.KEEP_SOURCE, "帧率": video_nodes.KEEP_SOURCE,
        "音频处理": video_nodes._AUDIO_KEEP, "音频码率": video_nodes.AUTO,
    }
    params.update(kw)
    return saver.save(**params)


print("\n===== 测试 1：绝对路径加载视频 =====")
res = load(mp4_src)
video = res["result"][0]
info = video_nodes._video_source_of(video)
print(res["result"][1])
assert info["width"] == 640 and info["height"] == 360, info
assert abs(info["fps"] - 30.0) < 0.01, info
assert info["has_audio"] is True, info
print(">>> OK 视频参数正确")

print("\n===== 测试 2：无音频视频 =====")
res_s = load(silent_src)
info_s = video_nodes._video_source_of(res_s["result"][0])
print(res_s["result"][1])
assert info_s["has_audio"] is False, info_s
print(">>> OK 正确识别无音轨")

print("\n===== 测试 3：加载非视频文件应报错 =====")
try:
    load(os.path.join(WORK, "源视频.mp4").replace("源视频.mp4", "不存在.mp4"))
    raise AssertionError("应当报错")
except ValueError as exc:
    print(">>> OK 已拒绝无效路径")

print("\n===== 测试 4：截断（只取开头 2s）=====")
res = load(mp4_src, **{"截断方式": audio_nodes.TRUNCATE_HEAD, "截取时长(秒)": 2.0})
info_t = video_nodes._video_source_of(res["result"][0])
print(res["result"][1])
assert abs(info_t["clip_duration"] - 2.0) < 0.01, info_t
print(">>> OK 截取窗口 2.0s")

print("\n===== 测试 5：截断（中间 0.5s → 2.5s）=====")
res = load(mp4_src, **{"截断方式": audio_nodes.TRUNCATE_RANGE,
                       "起点(秒)": 0.5, "终点(秒)": 2.5})
info_t = video_nodes._video_source_of(res["result"][0])
print(res["result"][1])
assert abs(info_t["clip_duration"] - 2.0) < 0.01, info_t
assert abs(info_t["start_time"] - 0.5) < 0.01, info_t
print(">>> OK 起点 0.5s / 时长 2.0s")

print("\n===== 测试 6：同格式直出（MP4 → MP4，全自动）=====")
res = load(mp4_src)
out = os.path.join(WORK, "out_direct")
r = save(res["result"][0], out)
print(r["ui"]["text"][0])
assert os.path.exists(out_file(out, "mp4")), out_file(out, "mp4")
print(">>> OK 同格式直出")

print("\n===== 测试 7：多格式一次转换（MP4 + MKV + WEBM）=====")
out = os.path.join(WORK, "out_multi")
r = save(res["result"][0], out, 导出MKV=True, 导出WEBM=True)
print(r["ui"]["text"][0])
for ext in ("mp4", "mkv", "webm"):
    assert os.path.exists(out_file(out, ext)), out_file(out, ext)
    print("    产出：", os.path.basename(out_file(out, ext)),
          os.path.getsize(out_file(out, ext)) // 1024, "KB")
print(">>> OK 多格式转换")

print("\n===== 测试 8：转码参数（720p + 30fps + H.265 + MP3 音频）=====")
out = os.path.join(WORK, "out_scaled")
r = save(res["result"][0], out, 分辨率="1280×720", 帧率="30",
         视频编码="H.265 (libx265)", 音频处理="MP3", 音频码率="192 kbps")
print(r["ui"]["text"][0])
path = out_file(out, "mp4")
assert os.path.exists(path), path
probe = video_nodes._probe_video(path)
print(f"    输出：{probe['width']}×{probe['height']} @ {probe['fps']:.3f}fps "
      f"编码={probe['video_codec']} 音频={probe['audio_codec']}")
assert (probe["width"], probe["height"]) == (1280, 720), probe
assert abs(probe["fps"] - 30.0) < 0.5, probe
print(">>> OK 转码参数生效")

print("\n===== 测试 9：移除音频 =====")
out = os.path.join(WORK, "out_mute")
r = save(res["result"][0], out, 音频处理=video_nodes._AUDIO_DROP)
print(r["ui"]["text"][0])
probe = video_nodes._probe_video(out_file(out, "mp4"))
assert probe["has_audio"] is False, probe
print(">>> OK 已移除音轨")

print("\n===== 测试 10：截断视频转码（应真实只编码 2s）=====")
res = load(mp4_src, **{"截断方式": audio_nodes.TRUNCATE_HEAD, "截取时长(秒)": 2.0})
out = os.path.join(WORK, "out_clip")
r = save(res["result"][0], out, 导出MKV=True)
print(r["ui"]["text"][0])
probe = video_nodes._probe_video(out_file(out, "mp4"))
print(f"    输出时长：{probe['duration']:.2f}s")
assert 1.5 < probe["duration"] < 2.6, probe
print(">>> OK 截断转码生效")

print("\n===== 测试 11：AVI 自动模式（沿用源 H.264）=====")
out = os.path.join(WORK, "out_avi")
r = save(res["result"][0], out, 导出MP4=False, 导出AVI=True)
print(r["ui"]["text"][0])
probe = video_nodes._probe_video(out_file(out, "avi"))
print(f"    输出：{probe['width']}×{probe['height']} 视频={probe['video_codec']} 音频={probe['audio_codec']}")
# 源是 H.264，AVI 兼容列表含 H.264 → 自动模式应沿用源编码
assert probe["video_codec"] == "h264", probe
assert probe["has_audio"] is True, probe
print(">>> OK 自动沿用源编码")

print("\n===== 测试 11b：显式指定 MPEG-4 编码器 =====")
out = os.path.join(WORK, "out_mpeg4")
r = save(res["result"][0], out, 导出MP4=False, 导出AVI=True,
         视频编码="MPEG-4 (mpeg4)")
print(r["ui"]["text"][0])
probe = video_nodes._probe_video(out_file(out, "avi"))
print(f"    输出：{probe['width']}×{probe['height']} 视频={probe['video_codec']}")
assert probe["video_codec"] == "mpeg4", probe
print(">>> OK 显式编码器生效")

print("\n===== 测试 11c：WEBM 自动模式应回退 VP9（源 H.264 不兼容）=====")
out = os.path.join(WORK, "out_webm")
r = save(res["result"][0], out, 导出MP4=False, 导出WEBM=True)
print(r["ui"]["text"][0])
probe = video_nodes._probe_video(out_file(out, "webm"))
print(f"    输出：{probe['width']}×{probe['height']} 视频={probe['video_codec']} 音频={probe['audio_codec']}")
# H.264 不在 WEBM 推荐列表 → 应回退到 VP9
assert probe["video_codec"] == "vp9", probe
print(">>> OK 不兼容编码正确回退")

print("\n===== 测试 12：不勾选任何格式应报错 =====")
try:
    save(res["result"][0], os.path.join(WORK, "out_none"), 导出MP4=False)
    raise AssertionError("应当报错")
except ValueError:
    print(">>> OK 已拒绝空输出")

print("\n===== 测试 13：绝对路径保存（含扩展名应被剥离）=====")
abs_dir = os.path.join(WORK, "绝对路径输出")
shutil.rmtree(abs_dir, ignore_errors=True)
res = load(mp4_src)
r = save(res["result"][0], os.path.join(abs_dir, "成片.mp4"))
produced = r["ui"]["images"]
names = [i["filename"] for i in produced]
print("    产出：", names)
assert names == ["成片_00001.mp4"], names
assert os.path.isfile(os.path.join(abs_dir, "成片_00001.mp4"))
# 不带扩展名也应正确命名
r2 = save(res["result"][0], os.path.join(abs_dir, "第二个"))
names2 = [i["filename"] for i in r2["ui"]["images"]]
print("    产出：", names2)
assert names2 == ["第二个_00001.mp4"], names2
print(">>> OK 绝对路径保存正常")

print("\n全部视频测试通过 ✅")

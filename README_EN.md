# ComfyUI Media Toolbox

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![中文](https://img.shields.io/badge/README-中文-lightgrey.svg)](README.md)

A pair of **audio / video loading and format conversion** custom nodes for ComfyUI.
The interface is fully localised in Chinese out of the box.

Key features: load any local file by absolute path (**no copies created**), export
multiple formats in a single run, parameters that adapt to the source automatically,
and instant passthrough when the target format matches the source.

> **English documentation · [中文文档](README.md)**

---

## Interface language

The node interface is **Chinese by default** — no configuration needed.

If your ComfyUI runs in English, the plugin follows the system language automatically
(an English language pack ships in `locales/en/`, covering every node name, parameter,
dropdown option and tooltip).

Just switch the language in ComfyUI under **Interface → Language**.

---

## Nodes

| Node | Purpose |
|---|---|
| **加载音频(增强)** / Load Audio (Advanced) | Load local audio, or the audio track of a video; supports absolute paths and long-audio truncation |
| **保存音频(平台发布)** / Save Audio (Platform Export) | Produce WAV / MP3 / FLAC / OPUS in one run, with platform compliance checks |
| **加载视频(增强)** / Load Video (Advanced) | Load local video; supports absolute paths and long-video truncation |
| **保存视频(格式转换)** / Save Video (Format Converter) | Produce MP4 / MKV / WEBM / AVI / MOV in one run; no report |

Audio and video follow exactly the same logic: the load node reads the file (optionally
trimmed), the save node writes it out in the target formats. Connect them directly to
convert formats.

---

## Where files are saved

The `文件名前缀` (Filename prefix) input of both save nodes accepts two forms:

| Form | Destination |
|---|---|
| Relative, e.g. `video/output` | Inside the ComfyUI `output` directory (standard behaviour) |
| **Absolute path**, e.g. `D:\MyVideos\clip` | Any local directory; it is created automatically |

Absolute paths may include the extension or not:

- `D:\MyVideos\clip.mp4` → `D:\MyVideos\clip_00001.mp4`
- `D:\MyVideos\clip` → `D:\MyVideos\clip_00001.mp4`

The trailing `_00001` is an incrementing counter that prevents overwriting existing
files. When the video node produces several formats at once, they all share the same
number so they stay grouped.

---

## Installation

Two steps: **① drop the plugin into ComfyUI** → **② make sure ffmpeg is available**.
Restart ComfyUI afterwards and the nodes appear under the 音频 / Video categories.

### Step ①: install the plugin (pick one)

#### Method A — ComfyUI-Manager one-click (easiest, best for beginners)

Requires [ComfyUI-Manager](https://github.com/ltdrdata/ComfyUI-Manager) (bundled by most
ComfyUI distributions).

1. Start ComfyUI and open the UI
2. Click **Manager** at the top right
3. Click **Install Custom Nodes**
4. Search for `comfyui_media_toolbox`
5. Click **Install** on this project
6. Click **Restart** when it finishes (or restart ComfyUI manually)

#### Method B — git clone

From ComfyUI's `custom_nodes` directory:

```bash
cd custom_nodes
git clone https://github.com/jesse890423/comfyui_media_toolbox.git
```

**In mainland China, GitHub can be slow — use the GitCode mirror instead (auto-synced daily, no VPN needed):**

```bash
git clone https://gitcode.com/jesse0423/comfyui_media_toolbox.git
```

Then restart ComfyUI.

#### Method C — download ZIP and place manually

1. Click the green **Code** button on this page → **Download ZIP**
2. Unzip — you get a folder named `comfyui_media_toolbox-main`
3. **Rename** it to `comfyui_media_toolbox` (remove the `-main` suffix)
4. Move the whole folder into ComfyUI's `custom_nodes` directory
5. Restart ComfyUI

### Where is your `custom_nodes` folder?

It always sits directly under the ComfyUI root:

| Your ComfyUI setup | Example path |
|---|---|
| Windows portable | `ComfyUI_windows_portable\ComfyUI\custom_nodes\` |
| Manual / git install | `ComfyUI\custom_nodes\` |
| ComfyUI Desktop | Open the folder from settings, or use Method A |

The final layout should look like:

```
ComfyUI/
└── custom_nodes/
    └── comfyui_media_toolbox/      ← the whole repo goes here
        ├── __init__.py
        ├── nodes.py
        ├── video_nodes.py
        ├── web/
        ├── locales/
        └── workflows/
```

> Avoid a **double-nested** `comfyui_media_toolbox/comfyui_media_toolbox/` folder — this
> is the most common zip-install mistake. If the nodes don't show up, check this first.

### Verify the install

After restarting ComfyUI, right-click an empty canvas → **Add Node**. Under the
**音频** or **Video** categories you should see all four nodes. If you do, the
installation succeeded.

### Step ②: Dependencies

| Dependency | Source | Notes |
|---|---|---|
| `av` (PyAV) | Bundled with ComfyUI | Decoding audio/video — **nothing to install** |
| `numpy` / `torch` | Bundled with ComfyUI | **Nothing to install** |
| `ffmpeg` | See below | **Required for conversion and transcoding** |

#### About ffmpeg

**ffmpeg is NOT bundled with the ComfyUI portable build**; conversion/transcoding needs
it. The node looks for it in this order:

1. Environment variable `FFMPEG_BINARY`
2. Environment variable `IMAGEIO_FFMPEG_EXE`
3. The binary shipped with the Python package `imageio-ffmpeg`
4. System `PATH`

**In most cases you don't need to do anything**: most ComfyUI installations (including
the official Windows portable build) already ship `imageio-ffmpeg`, which bundles a
working ffmpeg. To check, run the command below — if it prints a `.exe` path, you're
done and can jump to "Core features":

**Windows portable** (from the `ComfyUI_windows_portable` directory):

```bat
python_embeded\python.exe -c "import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())"
```

**Manual / git install** (inside ComfyUI's activated environment):

```bash
python -c "import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())"
```

If that **errors**, ffmpeg is missing. Install it into **the same Python environment
ComfyUI uses** (not some unrelated system Python). Pick either way:

**Way 1 — pip (recommended, easiest)**

- Windows portable:

  ```bat
  python_embeded\python.exe -m pip install imageio-ffmpeg
  ```

- Other setups (activate the ComfyUI environment first):

  ```bash
  python -m pip install imageio-ffmpeg
  ```

**Way 2 — download a full ffmpeg build**

1. Download from [gyan.dev](https://www.gyan.dev/ffmpeg/builds/) (Windows) or
   [BtbN](https://github.com/BtbN/FFmpeg-Builds/releases) (cross-platform)
2. Add its `bin` directory to `PATH`, or set `FFMPEG_BINARY` to the `ffmpeg.exe` path
3. Restart ComfyUI

> Use a **full** build. Stripped builds may lack H.265 / VP9 / AV1 or the audio
> encoders, which causes transcoding errors. gyan.dev's "ffmpeg-release-full" and
> BtbN's default builds are complete.

**Still unsure?** Run the bundled check — it prints the ffmpeg the node actually finds:

```bash
python _smoke_test.py
```

The first line `ffmpeg = ...` is the answer; `ffmpeg = None` means it wasn't found.

> When ffmpeg is missing, the node fails with a clear message instead of failing silently.

### Troubleshooting

**Nodes don't show up after install?** Confirm there is no double-nested folder and
that `__init__.py` is at the first level of `comfyui_media_toolbox`. Otherwise check the
ComfyUI console for `[音视频转换]` errors.

**"未找到 ffmpeg" (ffmpeg not found) error?** Install it per Step ②, into ComfyUI's own
Python environment (the portable build uses `python_embeded`).

**Loads fine but transcoding fails / output won't play?** Usually a stripped ffmpeg.
Switch to a full build or `pip install imageio-ffmpeg`.

**"📁 选择本地文件" button does nothing (Linux / macOS)?** That button uses the native
Windows dialog and is Windows-only. On other systems just type an absolute path into
`文件路径`; it works identically.

---

## Core features

### 1. Absolute paths, no copies

ComfyUI's upload flow copies files into the `input` directory. These load nodes accept
any **local absolute path** and read the original file directly — nothing extra is
written to disk.

Two convenience buttons are added to the node:

- **📁 选择本地文件（不复制副本）** — opens the native system file picker
- **🔄 刷新 input 列表** — refreshes the dropdown (also picks up files added after startup)

Both work for audio and video.

### 2. Video files work as audio input

The audio load node reads video files (mp4 / mkv / mov / avi / webm / flv / ts…) and
extracts the audio track, so there is no need to export audio from another editor first.

### 3. Long-media truncation

| Option | Meaning | Fields to fill |
|---|---|---|
| 不截断（使用完整音频） | Use the whole file | none |
| 只取开头一段（0 秒 → 截取时长） | Head segment | `截取时长(秒)` |
| 只取中间一段（起点 → 终点） | Any middle range | `起点(秒)`, `终点(秒)` |
| 从起点一直到结尾（起点 → 末尾） | From a point to the end | `起点(秒)` |

### 4. Automatic parameter adaptation

Options marked "Auto (follow source)" adapt to the input:

- **Audio** — sample rate, channels, WAV bit depth, MP3 / OPUS bitrate
- **Video** — resolution, frame rate, video codec, audio handling

### 5. Instant passthrough

When the target format matches the source and all parameters are "Auto", the node
**copies the source file** without re-encoding — essentially instant.

Note: the video node re-encodes when truncation, resolution, frame rate or codec
overrides are used, since those cannot be achieved by copying.

### 6. Multiple formats at once

Tick several targets and run once — all targets share a single ffmpeg invocation,
which is faster than converting one by one.

### 7. Platform compliance (audio)

The audio save node includes presets for major Chinese platforms (QQ Music, NetEase
Cloud Music, Kugou, Douyin, Kuaishou, Bilibili, …), checking format, sample rate, bit
depth, bitrate, channels and file size. Choose "Custom" to set your own rules, or
"No check" to only export files.

---

## Compatibility

- Video nodes output ComfyUI's native `VIDEO` type and interoperate with official video nodes
- Transcoding depends on available ffmpeg encoders (see the dependency section above)
- If the upstream video is not file-backed, the node serialises it to an in-memory
  buffer first, which is slower — prefer "Load Video (Advanced)" as the upstream node
- **The "📁 选择本地文件" (pick local file) button is Windows-only** — it opens the native
  system dialog. On other platforms just type an absolute path into `文件路径`; every other
  feature (refresh input list, in-node audio/video preview) is cross-platform

---

## Development

Two smoke tests are included:

```bash
python _smoke_test.py    # audio nodes
python _video_test.py    # video nodes
```

## License

[MIT](LICENSE)

This is an original open-source project. The author grants permission to use, copy,
modify, merge, publish and redistribute it under the MIT license, but **any distribution
or derivative work must retain the original author's attribution and copyright notice.**
See [NOTICE](NOTICE), which also lists the third-party components
(PyAV / NumPy / PyTorch / FFmpeg) and their respective copyright holders.

# ComfyUI 音视频工具箱

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![English](https://img.shields.io/badge/README-English-lightgrey.svg)](README_EN.md)

一套面向 ComfyUI 的**音频 / 视频加载与格式转换**自定义节点。
核心特点：一次运行产出多种格式、参数自动适配源文件、同格式转换零成本直出，
并内置国内主流音乐平台的合规校验。

> **中文文档 · [English documentation](README_EN.md)**

---

## 界面语言

界面**默认英文**，内置简体中文翻译，可在 ComfyUI 设置的
**Interface → Language** 中切换，节点名、参数名、下拉选项与提示文字全部跟随切换。

报告文字的语言由各节点上的 `报告语言` 控制，默认「跟随界面语言」，
也可强制中文或英文。

---

## 节点一览

| 节点 | 作用 |
|---|---|
| **Load Audio (Advanced)** | 加载 `input` 目录里的音频 / 视频中的音频，支持长音频截断 |
| **Save Audio (Platform Export)** | 一次产出 WAV / MP3 / FLAC / OPUS，并做平台合规校验 |
| **Load Video (Advanced)** | 加载 `input` 目录里的视频，支持长视频截断 |
| **Save Video (Format Converter)** | 一次产出 MP4 / MKV / WEBM / AVI / MOV |
| **Video Report** | 只看参数报告、不导出文件时使用 |

音频与视频的加载 / 保存逻辑完全一致：加载节点负责「读进来 + 可选截断」，
保存节点负责「按目标格式写出去」，两者直连即为格式转换。

---

## 文件从哪里来

插件只读写 ComfyUI 的 `input` 与 `output` 两个目录，**不会访问其它任何位置**。
这是刻意的设计：节点不接受任意本地路径，避免被当成读取本机任意文件的入口。

要把本机文件用进来，有两种方式：

- **拖拽**：把文件直接拖到 ComfyUI 画布上，ComfyUI 会复制一份到 `input` 目录
- **节点内上传**：加载节点上的上传按钮会把文件存进 `input` 目录，
  并自动填好文件名；选好后**无需运行即可试听 / 预览**

加载节点还带一个 **Refresh** 按钮，用于刷新下拉列表——
ComfyUI 运行期间新增到 `input` 目录的文件，刷新后就能选到。

下拉列表会递归子目录，直接填 `子目录/文件名` 即可。

---

## 保存位置

两个保存节点的 `文件名前缀` 都是 `output` 目录下的**相对路径**，
例如 `video/格式转换` 会存成 `output/video/格式转换_00001.mp4`。

末尾的 `_00001` 是防覆盖用的递增序号，重复运行不会覆盖已有文件。
视频节点一次产出多个格式时，各格式共用同一个序号，便于成组对应。
绝对路径与 `..` 会被拒绝。

---

## 安装

安装分两步：**① 把插件放进 ComfyUI** → **② 确认 ffmpeg 依赖**。装好后重启 ComfyUI 即可在节点列表的 Audio / Video 分类下看到节点。

### 第 ① 步：安装插件（三选一）

#### 方式 A：ComfyUI-Manager 一键安装（最简单，强烈推荐新手）

前提：你的 ComfyUI 已安装 [ComfyUI-Manager](https://github.com/ltdrdata/ComfyUI-Manager)（大多数整合包默认已带）。

1. 启动 ComfyUI，浏览器打开界面
2. 点右上角 **Manager**（管理器）
3. 点 **Install Custom Nodes**（安装自定义节点）
4. 在搜索框输入 `comfyui_media_toolbox`
5. 找到本项目（Media Toolbox），点右侧 **Install**
6. 安装完成后点 **Restart**（重启），或手动关闭再启动 ComfyUI

#### 方式 B：git 命令安装

进入 ComfyUI 的 `custom_nodes` 目录后执行：

```bash
cd custom_nodes
git clone https://github.com/jesse890423/comfyui_media_toolbox.git
```

**国内网络较慢时，用 GitCode 镜像地址（每日自动同步，无需翻墙）：**

```bash
git clone https://gitcode.com/jesse0423/comfyui_media_toolbox.git
```

然后重启 ComfyUI。

#### 方式 C：下载压缩包手动放置

1. 点本页面上方绿色 **Code** 按钮 → **Download ZIP**，下载源码压缩包
2. 解压，得到文件夹 `comfyui_media_toolbox-main`
3. 把它**重命名**为 `comfyui_media_toolbox`（去掉 `-main` 后缀）
4. 整个文件夹移动到 ComfyUI 的 `custom_nodes` 目录下
5. 重启 ComfyUI

### 找到你的 `custom_nodes` 目录

不同安装方式的路径不同，`custom_nodes` 始终在 ComfyUI 根目录下：

| 你的 ComfyUI 版本 | custom_nodes 路径示例 |
|---|---|
| Windows 便携版 | `ComfyUI_windows_portable\ComfyUI\custom_nodes\` |
| 手动 / git 安装 | `ComfyUI\custom_nodes\` |
| ComfyUI Desktop | 设置中可打开所在目录，或在 Manager 里用方式 A 安装 |

安装后的最终结构应为：

```
ComfyUI/
└── custom_nodes/
    └── comfyui_media_toolbox/      ← 本仓库全部内容放在这里
        ├── __init__.py
        ├── nodes.py
        ├── video_nodes.py
        ├── web/
        ├── locales/
        └── workflows/
```

> 注意：不要出现 `comfyui_media_toolbox/comfyui_media_toolbox/` 的**双层嵌套**，
> 这是解压安装最常见的错误。若节点没出现，先检查是不是多套了一层文件夹。

### 安装后自检

重启 ComfyUI 后，在画布空白处右键 → **Add Node（添加节点）**，
在分类 **Audio** 或 **Video** 下应能看到 5 个节点：

- Load Audio (Advanced)、Save Audio (Platform Export)
- Load Video (Advanced)、Save Video (Format Converter)
- Video Report

看得到即表示安装成功。若看不到，请查看第 ② 步与下方「常见问题」。

### 第 ② 步：依赖说明

| 依赖 | 来源 | 说明 |
|---|---|---|
| `av` (PyAV) | ComfyUI 核心自带 | 用于解码音视频，**无需安装** |
| `numpy` / `torch` | ComfyUI 核心自带 | **无需安装** |
| `ffmpeg` | 见下方说明 | **格式转换、转码、混流必需** |

#### 关于 ffmpeg

**ffmpeg 不是 ComfyUI 便携版自带的**，格式转换 / 转码功能需要它。节点按以下顺序自动查找：

1. 环境变量 `FFMPEG_BINARY`
2. 环境变量 `IMAGEIO_FFMPEG_EXE`
3. Python 包 `imageio-ffmpeg` 自带的二进制
4. 系统 `PATH`

**多数情况你无需任何操作**：绝大多数 ComfyUI 安装（含官方 Windows 便携版）都已预装
`imageio-ffmpeg`，它自带一份可用的 ffmpeg。想确认是否就绪，运行下面命令，能打印出
一个 `.exe` 路径就说明已经装好了，可直接跳到「快速开始」：

**Windows 便携版**（在本仓库同级目录 `ComfyUI_windows_portable` 下打开命令行）：

```bat
python_embeded\python.exe -c "import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())"
```

**手动 / git 安装版**（在已激活的 ComfyUI 虚拟环境里）：

```bash
python -c "import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())"
```

如果上面命令**报错**，说明缺少 ffmpeg，用下面的方式补齐（任选其一）。关键是
**装到 ComfyUI 所用的那个 Python 环境里**，而不是系统随便一个 Python。

**方式一：pip 安装（推荐，最简单）**

- Windows 便携版：

  ```bat
  python_embeded\python.exe -m pip install imageio-ffmpeg
  ```

- 其他版本（先激活 ComfyUI 环境）：

  ```bash
  python -m pip install imageio-ffmpeg
  ```

**方式二：手动下载完整 ffmpeg**

1. 到官方构建页下载：[gyan.dev](https://www.gyan.dev/ffmpeg/builds/)（Windows）或
   [BtbN](https://github.com/BtbN/FFmpeg-Builds/releases)（跨平台）
2. 解压后，把 `bin` 目录加入系统 `PATH`；或设置环境变量
   `FFMPEG_BINARY` 指向其中的 `ffmpeg.exe`
3. 重启 ComfyUI

> 请使用**完整版** ffmpeg。精简版可能缺少 H.265 / VP9 / AV1 或音频编码器，
> 会导致转码报错。gyan.dev 的「ffmpeg-release-full」与 BtbN 的默认构建功能完整。

**仍然不确定？** 直接运行本仓库自带的检测，它会打印节点实际找到的 ffmpeg：

```bash
python _smoke_test.py
```

第一行 `ffmpeg = ...` 即结果；显示 `ffmpeg = None` 就是没找到，按方式一/二补齐。

> 提示：视频 / 音频节点缺少 ffmpeg 时会明确提示「未找到 ffmpeg」，不会静默失败。

### 常见问题

**Q：装完在节点列表里找不到节点？**
先确认没有双层嵌套文件夹（见上文注意），再确认 `__init__.py` 在
`comfyui_media_toolbox` 文件夹第一层。仍不行就看 ComfyUI 启动窗口是否有
`[Media Toolbox]` 相关报错。

**Q：转换时报「未找到 ffmpeg」？**
按第 ② 步装 ffmpeg。注意一定装到 **ComfyUI 使用的 Python 环境**里
（便携版是 `python_embeded`），不是随便一个系统 Python。

**Q：能加载但转码报错、或产出文件打不开？**
多半是用了精简版 ffmpeg，缺少相应编码器。换 gyan.dev 的 full 版本或
`pip install imageio-ffmpeg`。

**Q：下拉列表里看不到刚放进 input 的文件？**
点加载节点上的 **Refresh** 按钮。ComfyUI 只在启动时扫描一次目录，
运行期间新增的文件需要刷新才会出现。

**Q：界面语言怎么切换？**
ComfyUI 设置里的 **Interface → Language**。节点界面与报告文字都会跟随，
报告语言也可在节点上单独指定。

---

## 快速开始

插件目录的 `workflows/` 下提供了两个可直接加载的示例工作流：

- `示例_音频格式转换.json` — 加载音频 → 保存音频
- `示例_视频格式转换.json` — 加载视频 → 保存视频

两个工作流内都附有中文使用说明注释。

---

## 核心特性

### 1. 只碰 input 与 output 目录

插件的所有文件操作都限定在 ComfyUI 的 `input` 与 `output` 目录内，
每个路径都会先经 `realpath` 规范化、再用 `commonpath` 确认没有逃出这两个目录。
绝对路径、`..` 穿越、以及指向目录外的符号链接都会被拒绝。

需要把本机文件用进来时，用画布拖拽或节点内的上传按钮即可。

### 2. 视频文件直接当音频用

音频加载节点能直接读 mp4 / mkv / mov / avi / webm / flv / ts 等视频文件，
自动提取其中的音轨，不必先从剪映之类的软件导出音频再导入。

### 3. 长媒体截断

加载节点提供三种截断方式，下拉项自带说明：

| 选项 | 含义 | 需要填的参数 |
|---|---|---|
| 不截断（使用完整音频） | 完整使用 | 无 |
| 只取开头一段（0 秒 → 截取时长） | 从 0 秒开始取一段 | `截取时长(秒)` |
| 只取中间一段（起点 → 终点） | 取任意区间 | `起点(秒)`、`终点(秒)` |
| 从起点一直到结尾（起点 → 末尾） | 从某点到结束 | `起点(秒)` |

音频与视频节点的截断逻辑一致。

### 4. 参数自动适配

保存节点中标注「自动（跟随源）」的参数会跟随源文件：

- 音频：采样率、声道、WAV 位深、MP3 / OPUS 码率
- 视频：分辨率、帧率、视频编码、音频处理策略

以音频为例，源为无损 44.1kHz / 16bit / 立体声时，导出 MP3 会自动使用 320kbps。

### 5. 同格式零成本直出

目标格式与源格式一致、且所有参数都是「自动」时，节点会**直接复制源文件**，
不重新编码，几乎瞬间完成。报告 / 摘要中会标记为「直出」。

注意：视频节点若使用了截断，或指定了分辨率 / 帧率 / 编码器，则会正常转码。

### 6. 一次产出多种格式

勾选多个目标即可一次运行完成，**多个目标共用一次 ffmpeg 调用**，比分多次转换更快。

### 7. 平台合规校验（音频）

「保存音频(平台发布)」内置国内主流平台预设（汽水音乐、网易云音乐、QQ音乐、
酷狗音乐、抖音、快手、哔哩哔哩），会校验格式、采样率、位深、码率、声道与文件大小，
并给出是否满足的结论。选择「自定义」可手动填写要求，选择「不校验」则只出文件。

---

## 节点参数速查

### 加载音频(增强) / 加载视频(增强)

| 参数 | 说明 |
|---|---|
| `音频文件` / `视频文件` | 从 ComfyUI `input` 目录选择，可直接填 `子目录/文件名` |
| `音轨序号` / `视频流序号` | 多音轨 / 多流文件时使用哪一条，0 为第一条 |
| `截断方式` | 见下方「长媒体截断」 |
| `起点(秒)` / `终点(秒)` / `截取时长(秒)` | 配合截断方式使用 |
| `报告语言` | 跟随界面语言 / English / 中文 |

### 保存音频(平台发布)

| 参数 | 说明 |
|---|---|
| `文件名前缀` | `output` 目录下的相对路径 |
| `导出WAV` / `导出MP3` / `导出OPUS` / `保留FLAC` | 勾选需要的格式 |
| `WAV位深` / `MP3码率` / `OPUS码率` / `采样率` / `声道` | 「自动（跟随源）」或指定值 |
| `平台预设` | 平台名称 / 自定义 / 不校验 |
| `文件大小上限MB` | 合规校验用的单文件体积上限 |
| `报告语言` | 跟随界面语言 / English / 中文 |

### 保存视频(格式转换)

| 参数 | 说明 |
|---|---|
| `文件名前缀` | `output` 目录下的相对路径 |
| `导出MP4` / `导出MKV` / `导出WEBM` / `导出AVI` / `导出MOV` | 勾选需要的格式 |
| `视频编码` | 自动 / H.264 / H.265 / VP9 / AV1 / VP8 / MPEG-4 |
| `画质CRF` | 数值越小越清晰、体积越大 |
| `分辨率` | 保持原始，或按标注尺寸等比缩放 |
| `帧率` | 保持原始，或指定数值 |
| `音频处理` | 保留原音频 / 自动兼容 / 移除 / 强制转码 |
| `音频码率` | 音频转码码率 |
| `报告语言` | 跟随界面语言 / English / 中文 |

自动模式下，若源编码与目标容器不兼容（例如 H.264 转 WEBM），会自动回退到
该容器的推荐编码器（WEBM → VP9），避免产出无法播放的文件。

---

## 兼容性说明

- 视频节点输出 ComfyUI 原生 `VIDEO` 类型，可与官方视频节点互连
- 视频转码依赖 ffmpeg 编码器可用性。`imageio-ffmpeg` 自带的构建已包含
  libx264 / libx265 / libvpx-vp9 / libaom-av1 与常见音频编码器
- 若上游节点输出的视频不是文件型输入（非本插件加载节点），
  转码会先将其序列化为内存缓冲，效率较低，建议优先用「加载视频(增强)」
- 上传、刷新、试听 / 预览均在浏览器侧完成，跨平台可用

---

## 开发

仓库内附带两个冒烟测试脚本，会自行用 ffmpeg 生成素材并放进 `input` 目录，
直接运行即可验证核心功能：

```bash
python _smoke_test.py    # 音频节点
python _video_test.py    # 视频节点
```

两者都会在结尾打印通过 / 失败统计。脚本会自动向上查找 ComfyUI 根目录，
也可显式指定：

```bash
COMFY_ROOT=/path/to/ComfyUI python _smoke_test.py
```

## 许可证

[MIT](LICENSE)

本项目为原创开源项目。作者依 MIT 许可授权他人使用、复制、修改、合并、发布与再分发，
但**分发或发布衍生作品时必须保留原作者署名与版权声明**。
详见 [NOTICE](NOTICE)，其中亦列明了第三方组件（PyAV / NumPy / PyTorch / FFmpeg）的著作权归属。

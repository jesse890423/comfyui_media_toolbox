# 贡献指南

感谢你考虑为这个项目做贡献。

## 开发环境

1. 克隆仓库到 `ComfyUI/custom_nodes/comfyui_media_toolbox`
2. 启动 ComfyUI，确认节点能正常加载
3. 修改后重启 ComfyUI 生效

无需安装额外依赖。视频功能需要 `ffmpeg`：ComfyUI Windows 版自带，
节点会依次在 `FFMPEG_BINARY` → `IMAGEIO_FFMPEG_EXE` → `PATH` 中查找。

## 目录结构

```
comfyui_media_toolbox/
├── __init__.py          节点注册
├── nodes.py             音频节点
├── video_nodes.py       视频节点
├── routes.py            后端 HTTP 路由（文件选择 / 预览 / 列表刷新）
├── web/
│   └── media_toolbox.js 前端扩展
├── locales/
│   └── en/main.json     英文语言包（界面默认中文，此包供英文界面使用）
├── workflows/           示例工作流
├── _smoke_test.py       音频冒烟测试
├── _video_test.py       视频冒烟测试
├── README.md            中文文档
├── README_EN.md         英文文档
├── NOTICE               署名与第三方组件声明
├── LICENSE              MIT 许可（标准原文，勿翻译或改写）
├── CONTRIBUTING.md
├── requirements.txt
└── pyproject.toml
```

## 代码约定

- **界面文字全部中文**，包括节点名、参数名、下拉选项、提示与报错信息
- **改动界面文字时必须同步更新 `locales/en/main.json`**，
  否则英文界面用户会看到半中半英。语言包键名与中文原文一一对应，
  新增控件后需同时补 `inputs`、`inputTips`、`outputs`、`outputNames`
- 下拉选项的**显示值即语义**，内部计算前用 `_norm_choice()` / `_norm_truncate()` 归一化
- 改动已有下拉选项时，**必须保留旧值的映射**，否则用户旧工作流会报错
- 音视频的加载 / 保存逻辑尽量保持对称，新增能力时两边一起改
- 参数名（`save()` 的形参）必须与 `INPUT_TYPES()` 的键**完全一致**，
  ComfyUI 按关键字传参，不一致会在运行时报 `unexpected keyword argument`
- 注释解释「为什么」，不复述「做了什么」

## 提交前自检

```bash
python _smoke_test.py    # 音频节点
python _video_test.py    # 视频节点
```

两个脚本都应输出「全部测试通过」。新增功能时请同步补充对应测试。

## 提交信息

使用清晰的中文描述，例如：

- `修复：视频转码在自动模式下未沿用源编码`
- `新增：音频加载支持 opus 容器`
- `文档：补充视频参数速查表`

## 报告问题

请附上：

- ComfyUI 版本与操作系统
- 使用的节点与完整参数
- 控制台报错全文
- 源文件的基本参数（格式、编码、分辨率、采样率等）

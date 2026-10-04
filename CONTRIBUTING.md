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
├── routes.py            后端 HTTP 路由（列表 / 预览 / 语言上报）
├── web/
│   └── media_toolbox.js 前端扩展（播放器、上传、刷新、本地化）
├── locales/
│   ├── en/main.json     英文（界面默认）
│   ├── en/nodeDefs.json 英文节点名 / 参数名 / 提示
│   └── zh/              简体中文翻译（main.json 的 nodeInputOptions 为下拉选项表）
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

- **界面文字以英文为准**，简体中文作为翻译存在 `locales/zh/`。
  新增或修改界面文字时，**必须同步更新 `locales/zh/nodeDefs.json`**，
  否则中文界面用户会看到半中半英
- **下拉选项的值永远是英文**，翻译只发生在显示层。
  中文映射表放在 `locales/zh/main.json` 的 `nodeInputOptions`，
  由 `web/media_toolbox.js` 通过官方的 `options.getOptionLabel` 钩子渲染。
  **绝不要把中文写进 `options.values`**：LiteGraph 会把用户点击的文本直接
  赋给 `widget.value`，中文会一路泄漏到工作流文件与后端校验，导致
  `Value not in list` 报错
- 改动已有下拉选项时，**必须保留旧值的映射**（`_PLATFORM_ALIASES` 等），
  否则用户旧工作流会报错
- 音视频的加载 / 保存逻辑尽量保持对称，新增能力时两边一起改
- 参数名（`save()` 的形参）必须与 `INPUT_TYPES()` 的键**完全一致**，
  ComfyUI 按关键字传参，不一致会在运行时报 `unexpected keyword argument`
- 前端包装函数一律用闭包捕获 `widget`，不要依赖 `this`：
  ComfyUI 在某些路径上会脱离调用（解绑时 `this` 为 `undefined`）
- **所有文件操作限定在 ComfyUI 的 `input` / `output` 目录内**，
  路径先 `realpath` 再 `commonpath` 校验，不要引入目录外访问
- 注释解释「为什么」，不复述「做了什么」

## 提交前自检

```bash
python _smoke_test.py    # 音频节点
python _video_test.py    # 视频节点
```

两者都会在结尾打印通过 / 失败统计。新增功能时请同步补充对应测试。
若改动了 `INPUT_TYPES`，还要确认 `workflows/` 下的示例工作流仍然对齐——
`widgets_values` 是按位置存储的，控件数量变化会导致整体错位。

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

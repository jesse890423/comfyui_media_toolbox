"""ComfyUI 音频 / 视频加载与格式转换节点（全中文界面）。

节点列表：
- 加载音频(增强)        LoadAudioAdvanced
- 保存音频(平台发布)    SaveAudioPlatformExport
- 加载视频(增强)        LoadVideoAdvanced
- 保存视频(格式转换)    SaveVideoConverter
"""

from .nodes import LoadAudioAdvanced, SaveAudioPlatformExport
from .video_nodes import LoadVideoAdvanced, SaveVideoConverter

NODE_CLASS_MAPPINGS = {
    "LoadAudioAdvanced": LoadAudioAdvanced,
    "SaveAudioPlatformExport": SaveAudioPlatformExport,
    "LoadVideoAdvanced": LoadVideoAdvanced,
    "SaveVideoConverter": SaveVideoConverter,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "LoadAudioAdvanced": "加载音频(增强)",
    "SaveAudioPlatformExport": "保存音频(平台发布)",
    "LoadVideoAdvanced": "加载视频(增强)",
    "SaveVideoConverter": "保存视频(格式转换)",
}

WEB_DIRECTORY = "./web"

# 注册后端路由（本地文件选择 / 预览 / input 列表刷新）
try:
    from . import routes  # noqa: F401
except Exception as exc:  # pragma: no cover
    print(f"[音视频转换] 路由注册失败：{exc}")

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]

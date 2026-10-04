"""ComfyUI audio / video loading and format conversion nodes.

Node list:
- Load Audio (Advanced)          LoadAudioAdvanced
- Save Audio (Platform Export)   SaveAudioPlatformExport
- Load Video (Advanced)          LoadVideoAdvanced
- Save Video (Format Converter)  SaveVideoConverter
- Video Report                   VideoReportNode

The UI is English by default. A Simplified Chinese translation is shipped in
locales/zh/ and can be enabled from ComfyUI's Interface -> Language menu.

Security boundary: these nodes only read and write inside ComfyUI's input and
output directories. They never touch files outside those two directories.
"""

from .nodes import LoadAudioAdvanced, SaveAudioPlatformExport
from .video_nodes import LoadVideoAdvanced, SaveVideoConverter, VideoReportNode

NODE_CLASS_MAPPINGS = {
    "LoadAudioAdvanced": LoadAudioAdvanced,
    "SaveAudioPlatformExport": SaveAudioPlatformExport,
    "LoadVideoAdvanced": LoadVideoAdvanced,
    "SaveVideoConverter": SaveVideoConverter,
    "VideoReportNode": VideoReportNode,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "LoadAudioAdvanced": "Load Audio (Advanced)",
    "SaveAudioPlatformExport": "Save Audio (Platform Export)",
    "LoadVideoAdvanced": "Load Video (Advanced)",
    "SaveVideoConverter": "Save Video (Format Converter)",
    "VideoReportNode": "Video Report",
}

WEB_DIRECTORY = "./web"

# Register backend routes (input listing / in-directory preview)
try:
    from . import routes  # noqa: F401
except Exception as exc:  # pragma: no cover
    print(f"[Media Toolbox] route registration failed: {exc}")

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
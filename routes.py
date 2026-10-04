"""音视频转换节点的后端 HTTP 路由。

提供以下能力：

1. GET  /audio_platform_export/list_input        列出 input 目录下的音频/视频文件
2. GET  /audio_platform_export/list_input_video  列出 input 目录下的视频文件
3. GET  /audio_platform_export/view              串流 input / output 目录内的音视频文件
4. POST /audio_platform_export/ui_language       记住前端当前界面语言

安全边界：除第 4 项外，所有路由只允许访问 ComfyUI 的 input 与 output 目录。
本插件不提供任何途径去读取或写入这两个目录之外的文件。
第 4 项只把一个短字符串记在内存里，不碰文件系统。
"""

import os

from aiohttp import web

import folder_paths
from server import PromptServer

from .nodes import MEDIA_EXTENSIONS, list_input_media, set_ui_language

# ComfyUI 正常启动时 PromptServer 已创建完成（main.py 先建服务再加载自定义节点），
# 这里直接取实例即可。取不到时抛错，交由 __init__.py 兜底提示。
routes = PromptServer.instance.routes


def _allowed_roots():
    """返回允许访问的目录列表（已 realpath 规范化）。

    仅包含 ComfyUI 的 input 与 output 目录。temp 目录不开放，
    因为它通常位于系统临时目录之外且不属于用户的数据目录。
    """
    roots = []
    for getter in (folder_paths.get_input_directory, folder_paths.get_output_directory):
        try:
            roots.append(os.path.realpath(getter()))
        except Exception:  # pragma: no cover - 目录不可用时跳过该根目录
            continue
    return roots


def _resolve_within_roots(raw):
    """把请求的路径解析到允许的根目录内。

    先做 realpath 消解符号链接与 ``..``，再用 commonpath 确认结果确实位于
    某个根目录内部。两步都必要：realpath 单独用挡不住指向目录内的符号链接，
    commonpath 单独用挡不住尚未展开的 ``..``。

    返回解析后的绝对路径；不在允许范围内时返回 None。
    """
    if not raw:
        return None

    candidate = os.path.realpath(os.path.expanduser(os.path.expandvars(raw)))

    for root in _allowed_roots():
        if candidate == root:
            continue
        try:
            if os.path.commonpath([candidate, root]) == root:
                return candidate
        except ValueError:
            # Windows 上跨盘符时 commonpath 会抛 ValueError，视为不匹配
            continue

    return None


@routes.get("/audio_platform_export/list_input")
async def list_input_files(request):
    return web.json_response({"files": list_input_media()})


@routes.get("/audio_platform_export/list_input_video")
async def list_input_video_files(request):
    from .video_nodes import list_input_video

    return web.json_response({"files": list_input_video()})


@routes.get("/audio_platform_export/view")
async def view_local_file(request):
    raw = request.query.get("path", "")
    if not raw:
        raise web.HTTPBadRequest(text="missing path")

    path = _resolve_within_roots(raw)
    if path is None:
        raise web.HTTPForbidden(text="path is outside the allowed input/output directories")
    if not os.path.isfile(path):
        raise web.HTTPNotFound(text="file not found")

    ext = os.path.splitext(path)[1].lower().lstrip(".")
    if ext not in MEDIA_EXTENSIONS:
        raise web.HTTPForbidden(text="unsupported media type")

    return web.FileResponse(path)


@routes.post("/audio_platform_export/ui_language")
async def report_ui_language(request):
    """记住前端当前的界面语言，供报告语言开关的「跟随界面语言」使用。

    ComfyUI 的界面语言只存在于浏览器端，后端拿不到，所以由前端主动上报。
    这里只接受一个短字符串，仅保存在内存里，不做任何文件系统操作。
    """
    try:
        body = await request.json()
    except Exception:
        raise web.HTTPBadRequest(text="expected a JSON body")

    if not isinstance(body, dict):
        raise web.HTTPBadRequest(text="expected a JSON object")

    set_ui_language(str(body.get("locale", ""))[:16])
    return web.json_response({"ok": True})
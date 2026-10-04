"""音视频转换节点的后端 HTTP 路由。

提供以下能力：

1. GET  /audio_platform_export/list_input        列出 input 目录下的音频/视频文件
2. GET  /audio_platform_export/list_input_video  列出 input 目录下的视频文件
3. POST /audio_platform_export/ui_language       记住前端当前界面语言

早期版本有一个 ``/audio_platform_export/view`` 用来串流本地文件。预览已改为
直接使用 ComfyUI 自己的 ``/view`` 端点，该路由因此没有任何调用方，已删除：
一个无人调用的文件读取端点只会徒增攻击面。

安全边界：第 1、2 项只读取 input 目录，路径解析在 nodes._resolve_within_roots
里统一做 realpath + commonpath 校验。第 3 项只把一个短字符串记在内存里，
不碰文件系统。本插件没有任何途径访问这两个目录之外的文件。
"""

from aiohttp import web

from server import PromptServer

from .nodes import list_input_media, set_ui_language

# ComfyUI 正常启动时 PromptServer 已创建完成（main.py 先建服务再加载自定义节点），
# 这里直接取实例即可。取不到时抛错，交由 __init__.py 兜底提示。
routes = PromptServer.instance.routes


@routes.get("/audio_platform_export/list_input")
async def list_input_files(request):
    return web.json_response({"files": list_input_media()})


@routes.get("/audio_platform_export/list_input_video")
async def list_input_video_files(request):
    from .video_nodes import list_input_video

    return web.json_response({"files": list_input_video()})


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
"""音视频转换节点的后端 HTTP 路由。

提供以下能力（都不涉及把文件复制到 input 目录）：

1. GET  /audio_platform_export/list_input        列出 input 目录下的音频/视频文件
2. GET  /audio_platform_export/list_input_video  列出 input 目录下的视频文件
3. POST /audio_platform_export/pick_file         弹出系统原生「打开文件」对话框，返回绝对路径
4. GET  /audio_platform_export/view              直接串流本地任意音频/视频文件（用于节点内试听/预览）
"""

import asyncio
import os
import subprocess
import tempfile

from aiohttp import web

from server import PromptServer

from .nodes import MEDIA_EXTENSIONS, list_input_media

# ComfyUI 正常启动时 PromptServer 已创建完成（main.py 先建服务再加载自定义节点），
# 这里直接取实例即可。取不到时抛错，交由 __init__.py 兜底提示。
routes = PromptServer.instance.routes

# Windows 下用 PowerShell + WinForms 弹出系统原生文件选择框（不经过浏览器上传，因而不产生任何副本）
_PS_PICK_SCRIPT = r"""
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName System.Windows.Forms | Out-Null
Add-Type -AssemblyName System.Drawing | Out-Null
$owner = New-Object System.Windows.Forms.Form
$owner.TopMost = $true
$owner.ShowInTaskbar = $false
$owner.WindowState = 'Minimized'
$owner.StartPosition = 'Manual'
$owner.Location = New-Object System.Drawing.Point(-3000, -3000)
$dlg = New-Object System.Windows.Forms.OpenFileDialog
$dlg.Title = '选择音频或视频文件（不会复制副本）'
$dlg.Filter = '音频/视频文件|*.wav;*.mp3;*.flac;*.opus;*.ogg;*.m4a;*.aac;*.wma;*.aiff;*.mp4;*.mkv;*.mov;*.avi;*.webm;*.flv;*.ts;*.mts;*.wmv;*.mpg|所有文件|*.*'
$dlg.Multiselect = $false
$dlg.CheckFileExists = $true
$dlg.CheckPathExists = $true
$result = $dlg.ShowDialog($owner)
if ($result -eq [System.Windows.Forms.DialogResult]::OK) {
    [System.IO.File]::WriteAllText($env:APE_RESULT_FILE, $dlg.FileName, (New-Object System.Text.UTF8Encoding($false)))
}
$owner.Dispose()
"""


def _pick_file_blocking():
    """在 Windows 上弹出系统文件选择框，返回所选文件的绝对路径（取消则返回空串）。"""
    if os.name != "nt":
        return ""

    result_handle = tempfile.NamedTemporaryFile(suffix=".txt", delete=False)
    result_handle.close()
    script_handle = tempfile.NamedTemporaryFile(suffix=".ps1", delete=False, mode="w", encoding="utf-8-sig")
    script_handle.write(_PS_PICK_SCRIPT)
    script_handle.close()

    env = os.environ.copy()
    env["APE_RESULT_FILE"] = result_handle.name
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-STA", "-ExecutionPolicy", "Bypass", "-File", script_handle.name],
            env=env,
            capture_output=True,
            timeout=900,
            creationflags=0x08000000,  # CREATE_NO_WINDOW：隐藏 powershell 控制台，仅显示文件选择框
        )
        if os.path.getsize(result_handle.name) > 0:
            with open(result_handle.name, "r", encoding="utf-8-sig") as fh:
                return fh.read().strip()
        return ""
    except Exception as exc:  # 例如系统无 powershell、超时
        print(f"[音频(平台发布)] 打开文件选择框失败：{exc}")
        return ""
    finally:
        for path in (result_handle.name, script_handle.name):
            try:
                os.unlink(path)
            except OSError:
                pass


@routes.get("/audio_platform_export/list_input")
async def list_input_files(request):
    return web.json_response({"files": list_input_media()})


@routes.get("/audio_platform_export/list_input_video")
async def list_input_video_files(request):
    from .video_nodes import list_input_video

    return web.json_response({"files": list_input_video()})


@routes.post("/audio_platform_export/pick_file")
async def pick_file(request):
    loop = asyncio.get_event_loop()
    path = await loop.run_in_executor(None, _pick_file_blocking)
    return web.json_response({"path": path})


@routes.get("/audio_platform_export/view")
async def view_local_file(request):
    raw = request.query.get("path", "")
    if not raw:
        raise web.HTTPBadRequest(text="missing path")

    path = os.path.abspath(raw)
    if not os.path.isfile(path):
        raise web.HTTPNotFound(text="file not found")

    ext = os.path.splitext(path)[1].lower().lstrip(".")
    if ext not in MEDIA_EXTENSIONS:
        raise web.HTTPForbidden(text="unsupported media type")

    return web.FileResponse(path)

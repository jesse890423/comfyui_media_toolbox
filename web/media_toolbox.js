import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

// 本扩展为自定义节点补两件事：
// 1) 节点内音频播放器：前端只为内置 SaveAudio 系列自动挂载播放器，
//    自定义节点不在白名单内，这里自行补一个 audioUI 组件并监听执行结果。
// 2) 「加载音频(增强)」的本地文件选择按钮：调用后端弹出系统原生文件对话框，
//    直接返回绝对路径，不经过浏览器上传，因此不会复制任何副本到 input 目录。

const SAVE_NODE = "SaveAudioPlatformExport";
const LOAD_NODE = "LoadAudioAdvanced";
const SAVE_VIDEO_NODE = "SaveVideoConverter";
const LOAD_VIDEO_NODE = "LoadVideoAdvanced";

// 与 nodes.py 的 _PLATFORM_PRESETS 保持一致
const PRESETS = {
  "汽水音乐": { 平台名称: "汽水音乐", 合格格式: "wav,mp3", 最低采样率: 44100, 最低位深: 16, 最低码率kbps: 320, 要求声道: "双声道", 文件大小上限MB: 200 },
  "网易云音乐": { 平台名称: "网易云音乐", 合格格式: "wav,mp3", 最低采样率: 44100, 最低位深: 16, 最低码率kbps: 320, 要求声道: "双声道", 文件大小上限MB: 200 },
  "QQ音乐": { 平台名称: "QQ音乐", 合格格式: "wav,mp3", 最低采样率: 44100, 最低位深: 16, 最低码率kbps: 320, 要求声道: "双声道", 文件大小上限MB: 200 },
  "酷狗音乐": { 平台名称: "酷狗音乐", 合格格式: "wav,mp3", 最低采样率: 44100, 最低位深: 16, 最低码率kbps: 320, 要求声道: "双声道", 文件大小上限MB: 200 },
  "抖音": { 平台名称: "抖音", 合格格式: "wav,mp3", 最低采样率: 44100, 最低位深: 16, 最低码率kbps: 320, 要求声道: "双声道", 文件大小上限MB: 200 },
  "快手": { 平台名称: "快手", 合格格式: "wav,mp3", 最低采样率: 44100, 最低位深: 16, 最低码率kbps: 192, 要求声道: "双声道", 文件大小上限MB: 200 },
  "哔哩哔哩": { 平台名称: "哔哩哔哩", 合格格式: "wav,flac,mp3", 最低采样率: 44100, 最低位深: 16, 最低码率kbps: 320, 要求声道: "双声道", 文件大小上限MB: 200 },
};

const MANAGED_WIDGETS = ["平台名称", "合格格式", "最低采样率", "最低位深", "最低码率kbps", "要求声道", "文件大小上限MB"];

function findWidget(node, name) {
  return (node.widgets || []).find((w) => w.name === name);
}

function buildAudioURL(item) {
  if (!item) return "";
  if (item.url) return api.apiURL(item.url);
  const params = new URLSearchParams();
  params.set("filename", item?.filename ?? "");
  params.set("type", item?.type ?? "output");
  if (item?.subfolder) params.set("subfolder", item.subfolder);
  params.set("rand", Math.random().toString());
  return api.apiURL("/view?" + params.toString());
}

// ---------------------------------------------------------------------------
// 音频播放器
// ---------------------------------------------------------------------------

function attachAudioPlayer(node) {
  if (node.__apePlayer) return;
  node.__apePlayer = true;

  const element = document.createElement("audio");
  element.controls = true;
  element.classList.add("comfy-audio", "empty-audio-widget");
  element.setAttribute("name", "media");

  const widget = node.addDOMWidget("audioUI", "audioUI", element);
  widget.serialize = false;
  widget.options = widget.options || {};
  widget.options.serialize = false;

  node.__apeEl = element;

  const originalExecuted = node.onExecuted;
  node.onExecuted = function (message) {
    originalExecuted?.apply(this, arguments);
    if (!message || !("audio" in message)) return;
    const items = message.audio;
    const url = Array.isArray(items) && items.length ? buildAudioURL(items[0]) : "";
    element.src = url || "";
    element.classList.toggle("empty-audio-widget", !url);
  };
}

function updateLocalPreview(node, path) {
  const element = node.__apeEl;
  if (!element) return;
  element.src = path
    ? api.apiURL("/audio_platform_export/view?path=" + encodeURIComponent(path))
    : "";
  element.classList.toggle("empty-audio-widget", !path);
}

// ---------------------------------------------------------------------------
// 保存音频(平台发布)：平台预设联动
// ---------------------------------------------------------------------------

function applyPreset(node) {
  const presetWidget = findWidget(node, "平台预设");
  const cfg = PRESETS[presetWidget?.value];
  if (!cfg) return; // 自定义 / 不校验：保留手填值
  for (const name of MANAGED_WIDGETS) {
    const widget = findWidget(node, name);
    if (widget && cfg[name] !== undefined) widget.value = cfg[name];
  }
  node.setDirtyCanvas?.(true, true);
}

function hookPreset(node) {
  const presetWidget = findWidget(node, "平台预设");
  if (!presetWidget || presetWidget.__presetHooked) return;
  presetWidget.__presetHooked = true;

  const original = presetWidget.callback;
  presetWidget.callback = function () {
    original?.apply(this, arguments);
    applyPreset(node);
  };

  applyPreset(node);
}

// ---------------------------------------------------------------------------
// 加载音频(增强)：本地文件选择 / 列表刷新 / 即时试听
// ---------------------------------------------------------------------------

async function pickLocalFile(node) {
  const pathWidget = findWidget(node, "文件路径");
  if (!pathWidget) return;
  try {
    const response = await api.fetchApi("/audio_platform_export/pick_file", { method: "POST" });
    const data = await response.json();
    if (data && data.path) {
      pathWidget.value = data.path;
      if (node.__apeEl) {
        updateLocalPreview(node, data.path);
      } else if (node.__apeVideoEl) {
        updateLocalVideoPreview(node, data.path);
      }
      node.setDirtyCanvas?.(true, true);
    }
  } catch (err) {
    console.error("[音视频] 打开系统文件选择框失败：", err);
  }
}

function hookPathWidget(node) {
  const widget = findWidget(node, "文件路径");
  if (!widget || widget.__apeHooked) return;
  widget.__apeHooked = true;
  const original = widget.callback;
  widget.callback = function (value) {
    original?.apply(this, arguments);
    updateLocalPreview(node, value);
  };
}

function addButton(node, label, handler) {
  const widget = node.addWidget("button", label, null, () => handler(node));
  widget.serialize = false;
  widget.options = widget.options || {};
  widget.options.serialize = false;
  return widget;
}

// ---------------------------------------------------------------------------
// 视频播放器
// ---------------------------------------------------------------------------

function attachVideoPlayer(node) {
  if (node.__apeVideoPlayer) return;
  node.__apeVideoPlayer = true;

  const element = document.createElement("video");
  element.controls = true;
  element.classList.add("comfy-video");
  element.setAttribute("name", "media");
  element.style.width = "100%";
  element.style.maxWidth = "100%";

  const widget = node.addDOMWidget("videoUI", "videoUI", element);
  widget.serialize = false;
  widget.options = widget.options || {};
  widget.options.serialize = false;

  node.__apeVideoEl = element;

  const originalExecuted = node.onExecuted;
  node.onExecuted = function (message) {
    originalExecuted?.apply(this, arguments);
    if (!message) return;
    const items = message.images;
    const url = Array.isArray(items) && items.length ? buildAudioURL(items[0]) : "";
    element.src = url || "";
  };
}

function updateLocalVideoPreview(node, path) {
  const element = node.__apeVideoEl;
  if (!element) return;
  element.src = path
    ? api.apiURL("/audio_platform_export/view?path=" + encodeURIComponent(path))
    : "";
}

function hookPathWidgetVideo(node) {
  const widget = findWidget(node, "文件路径");
  if (!widget || widget.__apeHooked) return;
  widget.__apeHooked = true;
  const original = widget.callback;
  widget.callback = function (value) {
    original?.apply(this, arguments);
    updateLocalVideoPreview(node, value);
  };
}

async function refreshInputList(node, kind) {
  const combo = findWidget(node, kind);
  if (!combo) return;
  const endpoint = kind === "视频文件"
    ? "/audio_platform_export/list_input_video"
    : "/audio_platform_export/list_input";
  try {
    const response = await api.fetchApi(endpoint);
    const data = await response.json();
    const files = (data && data.files) || [];
    const empty = kind === "视频文件" ? "(input 目录暂无视频文件)" : "(input 目录暂无音频/视频文件)";
    const options = files.length ? files : [empty];
    combo.options = combo.options || {};
    combo.options.values = options;
    if (!options.includes(combo.value)) combo.value = options[0];
    node.setDirtyCanvas?.(true, true);
  } catch (err) {
    console.error("[" + kind + "] 刷新 input 列表失败：", err);
  }
}

// ---------------------------------------------------------------------------
// 注册
// ---------------------------------------------------------------------------

app.registerExtension({
  name: "Comfy.AudioPlatformExport.UI",
  nodeCreated(node) {
    if (node.comfyClass === SAVE_NODE) {
      hookPreset(node);
      attachAudioPlayer(node);
      return;
    }

    if (node.comfyClass === LOAD_NODE) {
      addButton(node, "📁 选择本地文件（不复制副本）", pickLocalFile);
      addButton(node, "🔄 刷新 input 列表", (n) => refreshInputList(n, "音频文件"));
      attachAudioPlayer(node);
      hookPathWidget(node);
      return;
    }

    if (node.comfyClass === LOAD_VIDEO_NODE) {
      addButton(node, "📁 选择本地视频（不复制副本）", pickLocalFile);
      addButton(node, "🔄 刷新 input 列表", (n) => refreshInputList(n, "视频文件"));
      attachVideoPlayer(node);
      hookPathWidgetVideo(node);
      return;
    }

    if (node.comfyClass === SAVE_VIDEO_NODE) {
      attachVideoPlayer(node);
    }
  },
});

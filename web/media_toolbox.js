import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

// Frontend helpers for the media nodes.
//
// Three jobs:
// 1) Platform preset linkage on Save Audio (Platform Export).
// 2) Preview players for the two load nodes and the two save nodes.
//    Load nodes preview the source as soon as a file is picked (no run needed);
//    save nodes preview the file they produced after a run.
// 3) Refreshing the input-directory file lists.
//
// Security note: this plugin reads and writes only inside ComfyUI's input and
// output directories. Local files must be uploaded into the input directory
// (drag and drop), so no host-wide file picker is exposed here.

const SAVE_NODE = "SaveAudioPlatformExport";
const LOAD_NODE = "LoadAudioAdvanced";
const SAVE_VIDEO_NODE = "SaveVideoConverter";
const LOAD_VIDEO_NODE = "LoadVideoAdvanced";
const REPORT_NODE = "VideoReportNode";

// Keys must match nodes.py's _PLATFORM_PRESETS exactly. They are the combo
// option values, i.e. the internal (English) values, not any localized label.
// Soda Music (汽水音乐) and QQ Music are separate platforms and must stay separate.
const PRESETS = {
  "Soda Music": { 平台名称: "Soda Music", 合格格式: "wav,mp3", 最低采样率: 44100, 最低位深: 16, 最低码率kbps: 320, 要求声道: "Stereo", 文件大小上限MB: 200 },
  "QQ Music": { 平台名称: "QQ Music", 合格格式: "wav,mp3", 最低采样率: 44100, 最低位深: 16, 最低码率kbps: 320, 要求声道: "Stereo", 文件大小上限MB: 200 },
  "NetEase Cloud Music": { 平台名称: "NetEase Cloud Music", 合格格式: "wav,mp3", 最低采样率: 44100, 最低位深: 16, 最低码率kbps: 320, 要求声道: "Stereo", 文件大小上限MB: 200 },
  "Kugou Music": { 平台名称: "Kugou Music", 合格格式: "wav,mp3", 最低采样率: 44100, 最低位深: 16, 最低码率kbps: 320, 要求声道: "Stereo", 文件大小上限MB: 200 },
  "Douyin": { 平台名称: "Douyin", 合格格式: "wav,mp3", 最低采样率: 44100, 最低位深: 16, 最低码率kbps: 320, 要求声道: "Stereo", 文件大小上限MB: 200 },
  "Kuaishou": { 平台名称: "Kuaishou", 合格格式: "wav,mp3", 最低采样率: 44100, 最低位深: 16, 最低码率kbps: 192, 要求声道: "Stereo", 文件大小上限MB: 200 },
  "Bilibili": { 平台名称: "Bilibili", 合格格式: "wav,flac,mp3", 最低采样率: 44100, 最低位深: 16, 最低码率kbps: 320, 要求声道: "Stereo", 文件大小上限MB: 200 },
};

const MANAGED_WIDGETS = ["平台名称", "合格格式", "最低采样率", "最低位深", "最低码率kbps", "要求声道", "文件大小上限MB"];

// Selections that carry no preset definition. Must match nodes.py's
// CUSTOM_PRESET / NO_CHECK_PRESET.
const CUSTOM_PRESET = "Custom";
const NO_CHECK_PRESET = "No validation";

function findWidget(node, name) {
  return (node.widgets || []).find((w) => w.name === name);
}

// ComfyUI's own /view endpoint serves files that live in the input/output
// directories, which is exactly what this plugin is restricted to.
function buildViewURL(filename, subfolder, type) {
  if (!filename) return "";
  const params = new URLSearchParams();
  params.set("filename", filename);
  params.set("type", type || "input");
  if (subfolder) params.set("subfolder", subfolder);
  params.set("rand", Math.random().toString());
  return api.apiURL("/view?" + params.toString());
}

// Turn a {filename, subfolder, type} payload entry into a /view URL.
// A widget value may carry ComfyUI's "[output]" / "[temp]" annotation, which
// belongs in the type parameter rather than the filename.
function urlFromItem(item) {
  if (!item) return "";
  if (item.url) return api.apiURL(item.url);

  let filename = item.filename || "";
  let type = item.type || "output";
  const annotated = filename.match(/^(.*?)\s*\[(input|output|temp)\]$/);
  if (annotated) {
    filename = annotated[1];
    type = annotated[2];
  }
  return buildViewURL(filename, item.subfolder, type);
}

// ---------------------------------------------------------------------------
// Platform preset linkage
// ---------------------------------------------------------------------------

// Resolve a preset widget's current selection to its internal (English) value.
//
// The widget's own value is normally kept in English by the reverse-mapping
// callback in applyLocaleLabels(). A combo however may also end up holding the
// displayed label: LiteGraph assigns the clicked item, and the exact order
// relative to callback() varies between code paths. Looking the value up in
// both forms makes the linkage work either way.
//
// "Custom" and "No validation" are valid selections without a PRESETS entry, so
// they are resolved through the same reverse mapping and returned as-is. Only a
// value that matches nothing at all yields an empty string.
function presetKeyOf(node, presetWidget) {
  const raw = presetWidget.value;
  if (typeof raw !== "string" || !raw) return "";
  if (PRESETS[raw] || raw === CUSTOM_PRESET || raw === NO_CHECK_PRESET) return raw;

  const original = presetWidget.__apeOriginalOptions || [];
  // Displayed label -> internal value.
  const back = original.find((o) => optionLabel(node, presetWidget, o, "zh") === raw);
  if (back && (PRESETS[back] || back === CUSTOM_PRESET || back === NO_CHECK_PRESET)) {
    return back;
  }
  return "";
}

function applyPreset(node) {
  const presetWidget = findWidget(node, "平台预设");
  if (!presetWidget) return;
  const key = presetKeyOf(node, presetWidget);
  const cfg = key ? PRESETS[key] : undefined;
  // Custom / No validation keep whatever the user typed.
  for (const name of MANAGED_WIDGETS) {
    const widget = findWidget(node, name);
    if (!widget) continue;
    if (cfg && cfg[name] !== undefined) {
      widget.value = cfg[name];
      // The preset fills in the internal English name; remember it so a later
      // language switch can show the localized label and switch back.
      if (name === "平台名称") widget.__apeRawPlatformName = widget.value;
    } else if (name === "平台名称" && (key === CUSTOM_PRESET || key === NO_CHECK_PRESET)) {
      widget.value = "";
      widget.__apeRawPlatformName = "";
    }
  }
  // Show the localized platform name right away.
  localisePlatformName(node, isChineseLocale() ? "zh" : "en");
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

  // A workflow stores the widget value, so apply the preset once on load too.
  applyPreset(node);
}

// ---------------------------------------------------------------------------
// Players
// ---------------------------------------------------------------------------

// Widget type used for the player. "hidden" makes ComfyUI's DOMWidgetImpl
// report a zero layout size (computeLayoutSize returns 0 for that type), which
// is the supported way to collapse a widget that has nothing to show. Toggling
// between "hidden" and this normal type is what removes the stray grey bar.
const PLAYER_TYPE = "mediaToolboxPlayer";

function createPlayer(node, kind) {
  // Never build a second player for the same node: nodeCreated can fire more
  // than once (adding a node, then loading a workflow that contains it), and a
  // duplicate DOMWidget would show up as two stacked players.
  if (node.__apeOwnsPlayer && node.__apeEl) return node.__apeEl;

  const isVideo = kind === "video";
  const element = document.createElement(isVideo ? "video" : "audio");
  element.controls = true;
  element.classList.add(isVideo ? "comfy-video" : "comfy-audio", "empty-audio-widget");
  element.setAttribute("name", "media");
  if (isVideo) {
    element.style.width = "100%";
    element.style.maxWidth = "100%";
  }

  // addDOMWidget(name, type, element, options): the first argument is the
  // widget name, the second the widget type. A private type is used so ComfyUI
  // does not confuse this widget with its built-in "audioUI" / "videoUI" ones.
  const name = isVideo ? "media_toolbox_player" : "media_toolbox_player";
  const widget = node.addDOMWidget(name, PLAYER_TYPE, element, {
    canvasOnly: false,
    getMinHeight: () => (isVideo ? 120 : 34),
  });
  widget.serialize = false;
  widget.options = widget.options || {};
  widget.options.serialize = false;

  node.__apeEl = element;
  node.__apeWidget = widget;
  node.__apePlayerType = PLAYER_TYPE;
  node.__apeOwnsPlayer = true;
  return element;
}

function setSource(node, url) {
  const element = node?.__apeEl;
  if (!element) return;
  element.src = url || "";
  element.classList.toggle("empty-audio-widget", !url);
  // A stale src would keep playing the previous file after a new selection.
  if (!url) {
    try {
      element.removeAttribute("src");
      element.load();
    } catch (e) {
      /* element may not be a media element yet */
    }
  }
  // "empty-audio-widget" is ComfyUI's marker for a player with no source and it
  // renders as a grey placeholder bar. Switching the widget type to "hidden"
  // makes DOMWidgetImpl.computeLayoutSize report a zero size, which removes the
  // bar without touching node.size (node.size is unreliable during nodeCreated,
  // and calling setSize there squashed the whole node).
  const collapsed = !url;
  const widget = node.__apeWidget;
  if (widget && widget.__apeCollapsed !== collapsed) {
    widget.__apeCollapsed = collapsed;
    widget.type = collapsed ? "hidden" : node.__apePlayerType;
    const parent = element.parentElement;
    if (parent) parent.style.display = collapsed ? "none" : "";
    node.setDirtyCanvas?.(true, true);
  }
}

// Hook a combo widget so that picking a file previews it without running.
function hookFileCombo(node, widgetName, kind) {
  const widget = findWidget(node, widgetName);
  if (!widget || widget.__apeHooked) return;
  widget.__apeHooked = true;

  const original = widget.callback;
  widget.callback = function (value) {
    original?.apply(this, arguments);
    // The locale wrapper (chained through `original`) has already restored the
    // internal value; read it from the widget rather than the raw argument.
    const name = widget.value ?? value;
    const url = name ? buildViewURL(name) : "";
    setSource(node, url);
  };

  // A reopened workflow already has a value stored, so preview it right away.
  if (widget.value && !widget.value.startsWith("(")) {
    setSource(node, buildViewURL(widget.value));
  }
}

// Preview whatever the node returned in its "ui" payload.
//
// The backend deliberately reports the file to preview as an inert text line
// ("__preview__|type|subfolder|filename") instead of an "images" entry: an
// "images" entry makes ComfyUI add its own canvas preview, which would appear
// as a second bar next to the player this plugin adds.
function hookExecution(node, kind) {
  const original = node.onExecuted;
  node.onExecuted = function (message) {
    original?.apply(this, arguments);
    if (!message) return;

    const fromText = previewHintFrom(message);
    if (fromText) {
      setSource(node, fromText);
      return;
    }

    if (kind === "video") {
      const items = message.images;
      const item = Array.isArray(items) && items.length ? items[0] : null;
      setSource(node, item ? urlFromItem(item) : "");
      return;
    }

    if ("audio" in message) {
      const items = message.audio;
      const item = Array.isArray(items) && items.length ? items[0] : null;
      // The backend returns {filename, subfolder, type}; older payloads may
      // carry an explicit {url}. Handle both.
      if (!item) {
        setSource(node, "");
      } else if (item.url) {
        setSource(node, api.apiURL(item.url));
      } else {
        setSource(node, urlFromItem(item));
      }
    }
  };
}

// Pull the preview target out of a "text" payload, if the node sent one.
function previewHintFrom(message) {
  const lines = Array.isArray(message.text) ? message.text : [];
  for (const line of lines) {
    if (typeof line !== "string") continue;
    if (!line.startsWith("__preview__|")) continue;
    const parts = line.split("|");
    if (parts.length < 4) continue;
    const type = parts[1] || "output";
    const subfolder = parts[2] || "";
    const filename = parts.slice(3).join("|");
    if (!filename) continue;
    return buildViewURL(filename, subfolder, type);
  }
  return "";
}

function addButton(node, label, handler) {
  const widget = node.addWidget("button", label, null, () => handler(node));
  widget.serialize = false;
  widget.options = widget.options || {};
  widget.options.serialize = false;
  return widget;
}

// Labels for the buttons this plugin adds. They are not part of any node
// definition - ComfyUI only knows about widgets declared in INPUT_TYPES - so
// they are translated here and kept in sync with the interface language.
const BUTTON_LABELS = {
  uploadAudio: ["上传音频文件", "Upload audio file"],
  uploadVideo: ["上传视频文件", "Upload video file"],
  refreshList: ["刷新文件列表", "Refresh file list"],
};

function buttonLabel(key) {
  const entry = BUTTON_LABELS[key];
  if (!entry) return key;
  return entry[isChineseLocale() ? 0 : 1];
}

// ---------------------------------------------------------------------------
// Output port labels
//
// ComfyUI ships resolveNodeDefSlotText() with a path of `outputs.<name>.<slot>`,
// but nothing in the frontend ever calls it, so output port names are not
// translated by the official mechanism. They are mapped here instead. The node
// type (AUDIO / VIDEO / STRING) stays as-is, because that is what the link
// compatibility check uses.
//
// The stored value of an output slot must keep the English name: it is what
// identifies the slot in the workflow and in link data.
// ---------------------------------------------------------------------------

const OUTPUT_LABELS = {
  audio: ["音频", "audio"],
  video: ["视频", "video"],
  info: ["信息", "info"],
  report: ["报告", "report"],
  summary: ["摘要", "summary"],
};

function outputLabel(name, lang) {
  const entry = OUTPUT_LABELS[name];
  if (!entry) return name;
  return entry[lang === "zh" ? 0 : 1];
}

function applyOutputLabels(node, lang) {
  const slots = node.outputs;
  if (!Array.isArray(slots)) return;
  for (const slot of slots) {
    if (!slot || typeof slot.name !== "string") continue;
    // Remember the internal (English) name once; every language switch then
    // derives the label from it instead of from the previous translation.
    if (!slot.__apeRawName) slot.__apeRawName = slot.name;
    slot.label = outputLabel(slot.__apeRawName, lang);
  }
}

// ---------------------------------------------------------------------------
// Platform name value
//
// "平台名称" is a plain string input, not a combo, so optionLabel() does not
// apply and the preset linkage fills in the internal English name.
//
// The displayed value is localized but the stored value must stay English: the
// backend resolves the preset through _PLATFORM_ALIASES, which only knows the
// English names, and a workflow that stored "汽水音乐" would not be portable.
// LiteGraph serialises `widget.serializeValue()` when it exists and falls back
// to `widget.value` otherwise, so overriding serializeValue keeps the workflow
// clean while the canvas shows the localized label.
// ---------------------------------------------------------------------------

const PLATFORM_NAMES = {
  "Soda Music": "汽水音乐",
  "QQ Music": "QQ音乐",
  "NetEase Cloud Music": "网易云音乐",
  "Kugou Music": "酷狗音乐",
  "Douyin": "抖音",
  "Kuaishou": "快手",
  "Bilibili": "哔哩哔哩",
};

function platformDisplay(raw, lang) {
  if (lang !== "zh") return raw;
  return PLATFORM_NAMES[raw] || raw;
}

function localisePlatformName(node, lang) {
  const widget = findWidget(node, "平台名称");
  if (!widget) return;

  if (widget.__apeNameHooked) {
    // Already wired; just refresh the display for the active language.
    widget.value = platformDisplay(widget.__apeRawPlatformName ?? widget.value, lang);
    return;
  }

  widget.__apeNameHooked = true;
  let raw = widget.value;
  // A workflow saved earlier may hold the localized label; normalise it back
  // to the internal English name before anything else keys off it.
  const inverse = Object.entries(PLATFORM_NAMES).find(([, zh]) => zh === raw);
  if (inverse) raw = inverse[0];
  widget.__apeRawPlatformName = raw;

  // What the canvas draws.
  widget.value = platformDisplay(widget.__apeRawPlatformName, lang);

  // What gets written to the workflow and sent to the backend: always English.
  // A closure over widget, not `this`: ComfyUI calls serializeValue detached
  // on some paths, where `this` is undefined.
  widget.serializeValue = function () {
    return widget.__apeRawPlatformName ?? "";
  };

  // Typing replaces the value; remember it so a later language switch does not
  // resurrect the previous platform's label.
  const cb = widget.callback;
  widget.callback = function (v) {
    const r = cb?.call(this ?? widget, v);
    widget.__apeRawPlatformName = widget.value ?? v;
    return r;
  };
}

// ---------------------------------------------------------------------------
// Upload
//
// ComfyUI's own "audio_upload" / "video_upload" markers are deliberately not
// used on the backend. They drag in ComfyUI's own button plus a built-in
// player, which would duplicate the player added here, and "audio_upload"
// additionally only works on a widget literally named "audio".
//
// The upload posts to ComfyUI's standard /upload/image endpoint, which stores
// the file in the input directory, performs its own path checks and returns
// {name, subfolder, type} - the same shape this plugin's input-directory
// resolution already expects.
// ---------------------------------------------------------------------------

function acceptFor(kind) {
  return kind === "video" ? "video/*" : "audio/*,video/*";
}

function matchesKind(file, kind) {
  const type = file.type || "";
  if (kind === "video") return type.startsWith("video/");
  return type.startsWith("audio/") || type.startsWith("video/");
}

async function uploadFiles(node, comboName, kind, files) {
  if (!files || !files.length) return;
  if (node.__apeUploading) return;
  node.__apeUploading = true;
  try {
    for (const file of files) {
      const body = new FormData();
      body.append("image", file);
      body.append("type", "input");
      body.append("overwrite", "true");
      const res = await api.fetchApi("/upload/image", { method: "POST", body });
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      const info = await res.json();
      const name = info?.subfolder ? `${info.subfolder}/${info.name}` : info?.name;
      if (!name) continue;

      // refreshInputList re-reads the directory (now including the uploaded
      // file), keeps the option lists in their internal form and selects name.
      await refreshInputList(node, kind === "video" ? "视频文件" : "音频文件", { keepValue: name });
    }
  } catch (err) {
    console.error("[Media Toolbox] upload failed:", err);
  } finally {
    node.__apeUploading = false;
    node.setDirtyCanvas?.(true, true);
  }
}

function addUploadButton(node, comboName, kind) {
  const input = document.createElement("input");
  input.type = "file";
  input.accept = acceptFor(kind);
  input.multiple = true;
  input.style.display = "none";
  input.addEventListener("change", () => {
    const files = Array.from(input.files || []);
    input.value = "";
    uploadFiles(node, comboName, kind, files);
  });
  document.body.appendChild(input);

  const button = addButton(node, buttonLabel(kind === "video" ? "uploadVideo" : "uploadAudio"), () => {
    input.click();
  });
  button.__apeFileInput = input;
  node.__apeUploadInput = input;

  // Expose a hook so a drop or paste on the node can reuse the same path.
  node.__apeUpload = (files) => {
    const accepted = (files || []).filter((f) => matchesKind(f, kind));
    if (accepted.length) uploadFiles(node, comboName, kind, accepted);
  };
}

// ---------------------------------------------------------------------------
// input list refresh
// ---------------------------------------------------------------------------

async function refreshInputList(node, kind, opts) {
  const combo = findWidget(node, kind);
  if (!combo) return;
  const isVideo = kind === "视频文件";
  const keepValue = opts && opts.keepValue;
  const endpoint = isVideo
    ? "/audio_platform_export/list_input_video"
    : "/audio_platform_export/list_input";
  try {
    const response = await api.fetchApi(endpoint);
    const data = await response.json();
    const files = (data && data.files) || [];
    const empty = isVideo ? "(no video files in the input directory)" : "(no audio or video files in the input directory)";
    const options = files.length ? files : [empty];
    combo.options = combo.options || {};
    // Keep the raw (internal) list: options.values stays untranslated and
    // options.getOptionLabel (installed by installComboDraw) renders the label,
    // translating the placeholder entry and leaving real file names as-is.
    combo.__apeOriginalOptions = options;
    combo.options.values = options;
    const wanted = internalValueOf(node, combo, keepValue || combo.value);
    if (wanted && options.includes(wanted)) {
      combo.value = wanted;
    } else if (!options.includes(combo.value)) {
      combo.value = options[0];
    }
    node.setDirtyCanvas?.(true, true);
    // Reflect the current selection in the preview without running the node.
    if (combo.value && !combo.value.startsWith("(")) {
      setSource(node, buildViewURL(combo.value));
    }
  } catch (err) {
    console.error("[" + kind + "] failed to refresh input list:", err);
  }
}

// ---------------------------------------------------------------------------
// Registration
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// Localisation fallback
//
// ComfyUI merges custom-node translations only for locales that are already
// loaded when /api/i18n arrives. English is always loaded, so the English
// nodeDefs apply, but a locale loaded later (e.g. switching to Chinese after
// startup) can miss the merge. Node titles and widget labels then fall back to
// whatever the workflow happened to store.
//
// To make the shipped Chinese translation reliable regardless of load order,
// apply it here whenever the active locale is not English.
// ---------------------------------------------------------------------------

let enDefs = null;
let zhDefs = null;
let zhOptions = null;
let localeLoaded = false;

// Resolve the active interface language.
//
// ComfyUI keeps it in the "Comfy.Locale" setting, reachable through the app's
// settings facade (app.ui.settings.get / app.ui.settings.getSettingValue).
// There is no global `comfy.locale` any more - the old name is gone from the
// frontend, which is why reading it returned an empty string and every dropdown
// stayed English.
//
// The `app` imported from scripts/app.js is a re-export of window.comfyAPI.app.app,
// so this is the real application instance. The extra candidates below are only
// fallbacks for older frontends.
function currentLocale() {
  const facades = [
    app?.ui?.settings,
    app?.ui?.settings?.settingsById,
    globalThis.window?.comfyAPI?.app?.app?.ui?.settings,
  ];
  for (const s of facades) {
    if (!s) continue;
    for (const get of ["get", "getSettingValue"]) {
      try {
        const v = s[get]?.("Comfy.Locale");
        if (typeof v === "string" && v) return v;
      } catch (e) {
        /* try the next accessor */
      }
    }
    try {
      const v = s.value?.["Comfy.Locale"];
      if (typeof v === "string" && v) return v;
    } catch (e) {
      /* try the next facade */
    }
  }

  for (const c of [globalThis.window?.locale, globalThis.LiteGraph?.locale]) {
    if (typeof c === "string" && c) return c;
  }
  return "";
}

// Chinese is the only locale this plugin ships a translation for, so a
// zh-prefixed code (zh, zh-CN, zh-TW) is enough to decide.
function isChineseLocale() {
  return String(currentLocale()).toLowerCase().startsWith("zh");
}

async function loadLocaleData(force) {
  // A failed attempt must not latch: nodeCreated can run before /i18n is
  // serving this plugin's locales, and a latched "loaded" flag would leave the
  // dropdowns stuck in English for the rest of the session.
  if (localeLoaded && !force) return localeLoaded;
  try {
    const res = await api.fetchApi("/i18n");
    const all = await res.json();
    // Node definitions ship per locale: locales/en holds the English source
    // strings, locales/zh adds the Chinese ones. Both are needed because the
    // dropdown labels have to switch in both directions.
    const nextEn = all?.en?.nodeDefs || null;
    const nextZh = all?.zh?.nodeDefs || null;
    if (!nextEn && !nextZh) {
      localeLoaded = false;
      return false;
    }
    enDefs = nextEn;
    zhDefs = nextZh;
    // This plugin keeps its own option-label table in locales/zh/main.json under
    // "nodeInputOptions". ComfyUI merges main.json verbatim into the /api/i18n
    // payload, so the table is readable here even though the frontend itself has
    // no notion of it: option values stay untranslated and only labels are swapped.
    zhOptions = all?.zh?.nodeInputOptions || null;
    localeLoaded = true;
    return true;
  } catch (err) {
    localeLoaded = false;
    return false;
  }
}

// Label shown for an option in the given language. Technical terms (bitrates,
// resolutions, codec names, file names) have no entry and stay untouched, which
// is the desired result.
function optionLabel(node, widget, value, lang) {
  if (lang !== "zh" || typeof value !== "string") return value;
  const group = zhOptions?.[widget.name];
  if (group && Object.prototype.hasOwnProperty.call(group, value)) return group[value];
  const entry = zhDefs?.[node.comfyClass]?.inputs?.[widget.name];
  const inline = entry?.options;
  if (inline && typeof inline === "object" && Object.prototype.hasOwnProperty.call(inline, value)) {
    return inline[value];
  }
  return value;
}

// Backing map: a displayed label (e.g. "汽水音乐") to its internal value
// (e.g. "Soda Music"). Legacy LiteGraph combo menus assign the clicked entry
// straight into widget.value, so on older frontends the label could leak into
// serialization and fail backend validation. The current frontend avoids this
// through options.getOptionLabel (see installComboDraw); this map keeps the
// repair + callback fallbacks that cover both worlds.
function internalValueOf(node, widget, shown) {
  const original = widget.__apeOriginalOptions || [];
  if (typeof shown !== "string" || original.includes(shown)) return shown;
  const back = original.find((o) => optionLabel(node, widget, o, "zh") === shown);
  return back !== undefined ? back : shown;
}

// Translate what the widget displays without touching its stored value, so the
// node shows the localized label while workflow serialization stays English.
//
// The primary hook is options.getOptionLabel: ComboWidget._displayValue asks it
// first (verified against the shipped frontend:
//   get _displayValue(){ ... let e=this.options.getOptionLabel; if(e) return e(String(this.value)) ... }).
// It receives the internal value and returns the text to render, so value,
// serialization and validation all keep using the English string.
//
// Two fallbacks cover other render paths and older frontends: the legacy
// widget.draw swap, and an instance-level _displayValue getter that shadows the
// class one. Closures capture `widget` deliberately: some frontend paths invoke
// these functions detached, where `this` is undefined.
function installComboDraw(node, widget) {
  widget.options = widget.options || {};

  // Primary: the official label hook.
  widget.options.getOptionLabel = function (value) {
    const text = typeof value === "string" ? value : String(value ?? "");
    return optionLabel(node, widget, text, isChineseLocale() ? "zh" : "en");
  };

  if (!widget.__apeDrawHooked) {
    const orig = widget.draw;
    if (typeof orig === "function") {
      widget.__apeDrawHooked = true;
      widget.draw = function (...args) {
        const self = this ?? widget;
        const real = widget.value;
        const shown = optionLabel(node, widget, real, isChineseLocale() ? "zh" : "en");
        if (shown !== real) self.value = shown;
        try {
          return orig.call(self, ...args);
        } finally {
          self.value = real;
        }
      };
    }
  }

  try {
    Object.defineProperty(widget, "_displayValue", {
      configurable: true,
      get() {
        if (this.computedDisabled) return "";
        const raw = widget.value;
        const text = typeof raw === "string" ? raw : String(raw ?? "");
        return optionLabel(node, widget, text, isChineseLocale() ? "zh" : "en");
      },
    });
  } catch (err) {
    /* frozen widget: getOptionLabel and the draw hook still cover rendering */
  }
}

// Last line of defence: ComfyUI serialises a widget with serializeValue() when
// it exists (both prompt submission and node data export go through it), so
// normalising there guarantees the backend receives internal values even if a
// frontend code path assigned a localized label without invoking the callback.
// Built as a closure over `widget` because ComfyUI may call it detached.
function installComboSerialize(node, widget) {
  if (widget.__apeSerializeHooked) return;
  widget.__apeSerializeHooked = true;
  const orig = typeof widget.serializeValue === "function" ? widget.serializeValue : null;
  widget.serializeValue = function (...args) {
    const raw = orig ? orig.apply(this ?? widget, args) : widget.value;
    return internalValueOf(node, widget, raw);
  };
}

// The report switch's "Follow UI language" option.
const REPORT_LANG_WIDGET = "报告语言";
const FOLLOW_UI_LANG = "Follow UI language";

// Resolve "Follow UI language" into a concrete language while the prompt is
// being built.
//
// The backend cannot read the interface language, and having it remember a
// value the frontend reported on node creation goes stale: switching the
// interface back to Chinese kept producing English reports, because the last
// reported language was English and nothing re-reported until a node was
// rebuilt. Reading the live setting at serialization time makes the option mean
// what it says in both directions, and needs no backend state.
//
// Runs after installComboSerialize, so `raw` is already the internal value.
function installReportLang(widget) {
  if (widget.__apeReportLangHooked) return;
  widget.__apeReportLangHooked = true;
  const orig = typeof widget.serializeValue === "function" ? widget.serializeValue : null;
  widget.serializeValue = function (...args) {
    const raw = orig ? orig.apply(this ?? widget, args) : widget.value;
    if (raw !== FOLLOW_UI_LANG) return raw;
    return isChineseLocale() ? "中文" : "English";
  };
}

// Pick the node definition for the active language. English is the base and so
// doubles as the fallback whenever a Chinese translation is missing.
function activeDefs() {
  return isChineseLocale() ? (zhDefs || enDefs) : enDefs;
}

// Apply the node title, widget labels and option labels for the active language.
//
// A widget's own value always stays the internal English string: that is what the
// backend expects and what gets stored in the workflow. Clicking an option writes
// the displayed label, so the callback maps it back to the internal value before
// delegating. That mapping is rebuilt on every language change, which is what lets
// the dropdown switch back to English instead of only towards Chinese.
function applyLocaleLabels(node) {
  const defs = activeDefs();
  const lang = isChineseLocale() ? "zh" : "en";

  // Output ports are translated even when the node has no definition entry:
  // the names come from RETURN_NAMES and are not covered by nodeDefs.
  applyOutputLabels(node, lang);

  if (!defs) return;
  const def = defs[node.comfyClass];
  if (!def) return;

  if (def.display_name) node.title = def.display_name;

  const inputs = def.inputs || {};
  for (const widget of node.widgets || []) {
    const entry = inputs[widget.name];
    if (entry && entry.name) {
      widget.label = entry.name;
      widget.localized_name = entry.name;
    }
    if (entry && entry.tooltip) {
      widget.tooltip = entry.tooltip;
    }

    if (widget.type === "combo" && widget.options && Array.isArray(widget.options.values)) {
      // options.values stays the untranslated (English) list: the frontend's
      // ComboWidget asks options.getOptionLabel for the text to render in both
      // the widget and the dropdown menu, and the menu hands back the original
      // value on selection. Translating values here instead would leak labels
      // into widget.value, workflows, and backend validation.
      const original = widget.__apeOriginalOptions || widget.options.values;
      widget.__apeOriginalOptions = original;

      // Repair a stored value that holds a displayed label (a workflow saved
      // while selection assigned labels directly into the value, or a stale
      // value after a language switch) back to its internal value.
      widget.value = internalValueOf(node, widget, widget.value);

      installComboDraw(node, widget);
      installComboSerialize(node, widget);
      if (widget.name === REPORT_LANG_WIDGET) installReportLang(widget);

      if (!widget.__apeValueHooked) {
        widget.__apeValueHooked = true;
        const cb = widget.callback;
        widget.callback = function (shown) {
          // Legacy fallback: if a frontend path assigned the displayed label to
          // value before invoking the callback, restore the internal value.
          const back = internalValueOf(node, widget, shown);
          if (back !== shown) widget.value = back;
          cb?.call(this ?? widget, back);
        };
      }
    }
  }

  // Localise the platform name value after the widgets. This also installs the
  // serializeValue hook that keeps the stored value English.
  localisePlatformName(node, lang);

  node.setDirtyCanvas?.(true, true);
}

// The backend cannot read the interface language on its own, so report it there.
// This is what drives the "Follow UI language" report switch.
async function reportUiLanguage() {
  try {
    await api.fetchApi("/audio_platform_export/ui_language", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ locale: String(currentLocale()) }),
    });
  } catch (err) {
    /* the report falls back to English, which is not worth an error banner */
  }
}

// Let a file dropped or pasted over the player area go through the same upload
// path as the button. The player element is the node's only real DOM surface,
// so it doubles as the drop target.
function enableDropUpload(node) {
  const element = node.__apeEl;
  if (!element || node.__apeDropHooked) return;
  node.__apeDropHooked = true;

  const stop = (e) => {
    e.preventDefault();
    e.stopPropagation();
  };

  element.addEventListener("dragover", (e) => {
    stop(e);
    e.dataTransfer.dropEffect = "copy";
  });
  element.addEventListener("dragenter", stop);
  element.addEventListener("drop", (e) => {
    stop(e);
    const files = Array.from(e.dataTransfer?.files || []);
    node.__apeUpload?.(files);
  });
  element.addEventListener("paste", (e) => {
    const files = Array.from(e.clipboardData?.files || []);
    if (!files.length) return;
    stop(e);
    node.__apeUpload?.(files);
  });
}

// Nodes whose interface this extension customises.
const MANAGED_CLASSES = [SAVE_NODE, LOAD_NODE, LOAD_VIDEO_NODE, SAVE_VIDEO_NODE, REPORT_NODE];

app.registerExtension({
  name: "Comfy.AudioPlatformExport.UI",
  async nodeCreated(node) {
    if (!MANAGED_CLASSES.includes(node.comfyClass)) return;

    // Retry the fetch until it succeeds: nodeCreated can run before /i18n is
    // serving this plugin's locales, and giving up would leave the dropdowns
    // stuck in English for the rest of the session.
    await loadLocaleData();
    applyLocaleLabels(node);

    // Tell the backend which language the interface is in, so the report
    // switch's "Follow UI language" has something to follow. The backend only
    // needs to be told once per language, and nodeCreated runs again after a
    // language switch (ComfyUI refreshes the node definitions and reloads the
    // workflow), which is what makes this fire again on a switch.
    reportUiLanguage();

    // ComfyUI tears the node down and rebuilds it on a language switch, so the
    // labels are applied from scratch each time. Only the DOM helpers need
    // guarding against a double call.
    if (node.__apeLocaleHooked) return;
    node.__apeLocaleHooked = true;

    if (node.comfyClass === SAVE_NODE) {
      hookPreset(node);
      createPlayer(node, "audio");
      hookExecution(node, "audio");
      return;
    }

    if (node.comfyClass === LOAD_NODE) {
      addUploadButton(node, "音频文件", "audio");
      addButton(node, buttonLabel("refreshList"), (n) => refreshInputList(n, "音频文件"));
      createPlayer(node, "audio");
      hookFileCombo(node, "音频文件", "audio");
      hookExecution(node, "audio");
      enableDropUpload(node);
      return;
    }

    if (node.comfyClass === LOAD_VIDEO_NODE) {
      addUploadButton(node, "视频文件", "video");
      addButton(node, buttonLabel("refreshList"), (n) => refreshInputList(n, "视频文件"));
      createPlayer(node, "video");
      hookFileCombo(node, "视频文件", "video");
      hookExecution(node, "video");
      enableDropUpload(node);
      return;
    }

    if (node.comfyClass === SAVE_VIDEO_NODE) {
      createPlayer(node, "video");
      hookExecution(node, "video");
    }

    if (node.comfyClass === REPORT_NODE) {
      createPlayer(node, "video");
    }
  },
});
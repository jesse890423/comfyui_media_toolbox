// 真实执行 media_toolbox.js，按线上 ComboWidget 的确切机制模拟渲染与选中：
//   渲染: get _displayValue(){ const f=this.options.getOptionLabel; if(f) return f(String(this.value)); ... }
//   菜单: options.values.map(v => getOptionLabel(v)) 显示，点击回调收到的是**原始值 v**
//   提交: serializeValue 解绑调用（this=undefined，线上崩溃点）
// 要求：options.values 始终为内部英文值；显示中文；提交值必在后端列表内；任何调用不崩。
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const HERE = dirname(fileURLToPath(import.meta.url));
const SRC = readFileSync(join(HERE, "web", "media_toolbox.js"), "utf8");

let ok = true;
const check = (label, pass, extra = "") => {
  console.log(`  [${pass ? "OK" : "FAIL"}] ${label}${extra ? "  " + extra : ""}`);
  ok = ok && pass;
};

let LOCALE = "zh";
const appStub = {
  ui: { settings: { getSettingValue: (id) => (id === "Comfy.Locale" ? LOCALE : undefined) } },
  registerExtension: () => {},
  graph: { on: () => {} },
  canvas: { setDirty: () => {} },
};

// 后端 INPUT_TYPES 的镜像（validation 用它比对提交值）
const SERVER_OPTIONS = {
  平台预设: ["Soda Music", "QQ Music", "NetEase Cloud Music", "Kugou Music",
    "Douyin", "Kuaishou", "Bilibili", "Custom", "No validation"],
  要求声道: ["Stereo", "Mono", "Any"],
  声道: ["Auto (follow source)", "Stereo", "Mono"],
  报告语言: ["Follow UI language", "English", "中文"],
};

const mkDefs = (name, labels) => ({
  SaveAudioPlatformExport: {
    display_name: name,
    inputs: {
      平台预设: { name: labels[0] }, 平台名称: { name: labels[1] },
      要求声道: { name: labels[2] }, 报告语言: { name: labels[3] },
    },
  },
});

const ZH_OPTIONS = {
  平台预设: {
    "Soda Music": "汽水音乐", "QQ Music": "QQ音乐",
    "NetEase Cloud Music": "网易云音乐", "Kugou Music": "酷狗音乐",
    "Douyin": "抖音", "Kuaishou": "快手", "Bilibili": "哔哩哔哩",
    "Custom": "自定义", "No validation": "不校验",
  },
  要求声道: { Stereo: "双声道", Mono: "单声道", Any: "不限" },
  声道: { "Auto (follow source)": "自动（跟随源）", Stereo: "双声道", Mono: "单声道" },
  报告语言: { "Follow UI language": "跟随界面语言", English: "英文", "中文": "中文" },
};

const apiStub = {
  apiURL: (p) => "http://x" + p,
  fetchApi: async (p) => {
    if (String(p).includes("/i18n")) {
      return { ok: true, json: async () => ({
        en: { nodeDefs: mkDefs("Save Audio (Platform Export)",
          ["Platform preset", "Platform name", "Required channels", "Report language"]) },
        zh: {
          nodeDefs: mkDefs("保存音频(平台发布)", ["平台预设", "平台名称", "要求声道", "报告语言"]),
          nodeInputOptions: ZH_OPTIONS,
        },
      }) };
    }
    return { ok: true, json: async () => ({ files: [] }) };
  },
};

const documentStub = {
  createElement: () => ({
    style: {}, classList: { add() {}, remove() {}, toggle() {} },
    setAttribute() {}, addEventListener() {}, appendChild() {}, remove() {},
    get src() { return ""; }, set src(v) {},
  }),
  body: { appendChild() {} },
};
const windowStub = { comfyAPI: { app: { app: appStub } } };

let code = SRC
  .replace(/^\s*import\s+\{[^}]*\}\s+from\s+"[^"]*";\s*$/gm, "")
  .replace(/^\s*import\s+"[^"]*";\s*$/gm, "");
code += `
;return { applyLocaleLabels, loadLocaleData, applyPreset, hookPreset,
          findWidget, optionLabel };
`;
const fn = new Function("app", "api", "document", "window", "console",
  "setInterval", "clearInterval", "FormData", "URLSearchParams", code);
const A = fn(appStub, apiStub, documentStub, windowStub, console,
  () => 0, () => {}, class {}, URLSearchParams);

const w = (n, name) => n.widgets.find((x) => x.name === name);

// ---- 按线上一致的方式造 combo：原型上有 _displayValue getter，
//     先问 options.getOptionLabel；drawWidget 读取 _displayValue。----
const comboProto = {
  get _displayValue() {
    if (this.computedDisabled) return "";
    const f = this.options && this.options.getOptionLabel;
    if (f) { try { return f(this.value ? String(this.value) : null); } catch (e) { return String(this.value); } }
    return this.value == null ? "" : String(this.value);
  },
  drawWidget() { this.__drawnText = this._displayValue; },
};

function makeSaveNode() {
  const mk = (name, type, value, values) => {
    const base = {
      name, type, label: name, value,
      options: values ? { values: [...values] } : undefined,
      callback(v) { this.value = v; },
    };
    if (values) {
      const o = Object.create(comboProto);
      Object.assign(o, base);
      return o;
    }
    return base;
  };
  return {
    comfyClass: "SaveAudioPlatformExport",
    title: "Save Audio (Platform Export)",
    outputs: [
      { name: "report", type: "STRING", label: "report" },
      { name: "audio", type: "AUDIO", label: "audio" },
    ],
    widgets: [
      mk("平台预设", "combo", "Soda Music", SERVER_OPTIONS.平台预设),
      mk("平台名称", "string", "Soda Music"),
      mk("要求声道", "combo", "Stereo", SERVER_OPTIONS.要求声道),
      mk("声道", "combo", "Auto (follow source)", SERVER_OPTIONS.声道),
      mk("报告语言", "combo", "Follow UI language", SERVER_OPTIONS.报告语言),
    ],
    setDirtyCanvas() {},
  };
}

// 真实菜单点击：getOptionLabel 渲染，回调收到原始值
function menuSelect(widget, internalValue) {
  widget.value = internalValue;           // setValue(原始值)
  widget.callback(internalValue);
}

function submitted(widget) {
  return typeof widget.serializeValue === "function"
    ? widget.serializeValue.call(undefined, null, 0) : widget.value;
}

await A.loadLocaleData(true);

console.log("== 1. 中文界面：values 保持英文，渲染走 getOptionLabel ==");
LOCALE = "zh";
{
  const n = makeSaveNode();
  A.applyLocaleLabels(n);
  A.hookPreset(n);
  const p = w(n, "平台预设");
  check("options.values 未被翻译（保持内部值）",
    p.options.values[0] === "Soda Music" && !p.options.values.includes("汽水音乐"),
    JSON.stringify(p.options.values.slice(0, 3)));
  check("getOptionLabel 已安装", typeof p.options.getOptionLabel === "function");
  check("节点渲染文本=汽水音乐", (() => { p.drawWidget(); return p.__drawnText === "汽水音乐"; })(),
    p.__drawnText);
  // 菜单条目 = values.map(getOptionLabel)
  const menu = p.options.values.map((v) => p.options.getOptionLabel(v));
  check("菜单条目全中文", menu[0] === "汽水音乐" && menu[7] === "自定义" && menu[8] === "不校验",
    JSON.stringify(menu.slice(0, 3)));
  check("value 保持内部值", p.value === "Soda Music", p.value);
}

console.log("\n== 2. 全 combo × 全选项：菜单点选后提交值必在后端列表内 ==");
LOCALE = "zh";
{
  const n = makeSaveNode();
  A.applyLocaleLabels(n);
  let allGood = true;
  const bad = [];
  for (const c of n.widgets.filter((x) => x.type === "combo")) {
    for (const v of c.options.values) {
      menuSelect(c, v);
      const s = submitted(c);
      if (!SERVER_OPTIONS[c.name].includes(s)) { allGood = false; bad.push(`${c.name}:${v}->${s}`); }
      // 渲染文本此时应为中文
      c.drawWidget();
      if (c.__drawnText !== (ZH_OPTIONS[c.name]?.[v] ?? v)) {
        allGood = false; bad.push(`渲染 ${c.name}:${v}->${c.__drawnText}`);
      }
    }
  }
  check("点选+提交+渲染 三处全部正确", allGood, bad.slice(0, 3).join(" ; "));
}

console.log("\n== 3. 旧版路径（把显示值写进 value 的模拟）仍被兜住 ==");
LOCALE = "zh";
{
  const n = makeSaveNode();
  A.applyLocaleLabels(n);
  const p = w(n, "平台预设");
  p.value = "抖音";
  p.callback("抖音");
  check("legacy 赋值被反查修复", p.value === "Douyin", p.value);
  check("提交值=Douyin", submitted(p) === "Douyin", submitted(p));
}

console.log("\n== 4. 加载脏工作流（值=中文标签）自动修复 ==");
LOCALE = "zh";
{
  const n = makeSaveNode();
  for (const c of n.widgets) {
    if (c.type !== "combo") continue;
    const first = SERVER_OPTIONS[c.name][0];
    c.value = ZH_OPTIONS[c.name]?.[first] ?? first;
  }
  A.applyLocaleLabels(n);
  check("平台预设 汽水音乐 -> Soda Music", w(n, "平台预设").value === "Soda Music",
    w(n, "平台预设").value);
  check("报告语言 跟随界面语言 -> 内部值",
    w(n, "报告语言").value === "Follow UI language", w(n, "报告语言").value);
}

console.log("\n== 5. 平台名称：显示中文，解绑 serializeValue 返回英文且不崩 ==");
LOCALE = "zh";
{
  const n = makeSaveNode();
  A.applyLocaleLabels(n);
  A.hookPreset(n);
  const pn = w(n, "平台名称");
  check("显示=汽水音乐", pn.value === "汽水音乐", pn.value);
  let threw = null;
  let val = null;
  try { val = submitted(pn); } catch (e) { threw = e; }
  check("serializeValue 解绑不崩", threw === null, threw ? String(threw.message) : "");
  check("serializeValue 返回 Soda Music", val === "Soda Music", val);
  // 用户手输
  pn.callback("自填平台");
  check("手输保留", pn.value === "自填平台" && submitted(pn) === "自填平台",
    `${pn.value}/${submitted(pn)}`);
  // 切英文再切回
  LOCALE = "en";
  A.applyLocaleLabels(n);
  check("英文界面 serializeValue 正常", submitted(pn) !== undefined);
}

console.log("\n== 6. 切英文：渲染/菜单全部恢复英文 ==");
{
  const n = makeSaveNode();
  A.applyLocaleLabels(n);
  A.hookPreset(n);
  LOCALE = "en";
  A.applyLocaleLabels(n);
  const p = w(n, "平台预设");
  p.drawWidget();
  check("渲染=Soda Music", p.__drawnText === "Soda Music", p.__drawnText);
  const menu = p.options.values.map((v) => p.options.getOptionLabel(v));
  check("菜单=英文", menu[0] === "Soda Music", menu[0]);
  check("标题恢复", n.title === "Save Audio (Platform Export)", n.title);
  check("平台名称显示恢复英文", w(n, "平台名称").value === "Soda Music",
    w(n, "平台名称").value);
  // 中文界面下点选英文值也不能崩（语言切换竞态）
  LOCALE = "zh";
  menuSelect(p, "QQ Music");
  check("竞态点选提交仍为内部值", submitted(p) === "QQ Music", submitted(p));
}

console.log("\n== 7. 输出端口与 preset 联动回归 ==");
LOCALE = "zh";
{
  const n = makeSaveNode();
  A.applyLocaleLabels(n);
  A.hookPreset(n);
  check("report -> 报告", n.outputs[0].label === "报告", n.outputs[0].label);
  check("audio -> 音频", n.outputs[1].label === "音频", n.outputs[1].label);
  const p = w(n, "平台预设");
  menuSelect(p, "Bilibili");
  check("联动: 平台名称显示=哔哩哔哩", w(n, "平台名称").value === "哔哩哔哩",
    w(n, "平台名称").value);
  check("渲染文本随 value 更新", (() => { p.drawWidget(); return p.__drawnText === "哔哩哔哩"; })(),
    p.__drawnText);
  menuSelect(p, "自定义");
  check("联动: 自定义清空", w(n, "平台名称").value === "",
    JSON.stringify(w(n, "平台名称").value));
}

console.log("\n== 8. 报告语言「跟随界面语言」按队列时刻的语言解析 ==");
{
  const n = makeSaveNode();
  LOCALE = "zh";
  A.applyLocaleLabels(n);
  const rl = w(n, "报告语言");
  check("中文界面提交值=中文", submitted(rl) === "中文", submitted(rl));
  // 关键回归：切回中文界面无需重建节点，提交值就必须跟着回来
  LOCALE = "en";
  check("切英文后提交值=English（不重建节点）", submitted(rl) === "English", submitted(rl));
  LOCALE = "zh";
  check("切回中文后提交值=中文（原 bug 点）", submitted(rl) === "中文", submitted(rl));
  menuSelect(rl, "English");
  check("显式英文不受界面语言影响", submitted(rl) === "English", submitted(rl));
  menuSelect(rl, "中文");
  check("显式中文不受界面语言影响", submitted(rl) === "中文", submitted(rl));
  menuSelect(rl, "Follow UI language");
  LOCALE = "en";
  check("重新跟随：英文界面=English", submitted(rl) === "English", submitted(rl));
  LOCALE = "zh";
  check("重新跟随：中文界面=中文", submitted(rl) === "中文", submitted(rl));
}

console.log("\n结果:", ok ? "全部通过" : "存在问题");
process.exit(ok ? 0 : 1);
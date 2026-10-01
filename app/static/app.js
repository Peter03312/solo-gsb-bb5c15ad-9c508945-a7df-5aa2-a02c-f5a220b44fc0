"use strict";

const PALETTE = [
  ["#4e79a7", "#dce6f1"], ["#f28e2b", "#fbe7d4"], ["#59a14f", "#e2f0dd"],
  ["#e15759", "#fbe0e1"], ["#b07aa1", "#efe5ec"], ["#76b7b2", "#e0efee"],
  ["#edc948", "#faf2d6"], ["#b6992d", "#f0e9d2"], ["#ff9da7", "#ffe6ea"],
  ["#9c755f", "#ece2da"], ["#bab0ac", "#f0eeec"], ["#86bc42", "#e7f1d8"],
];

let STATE = { inputs: null, solution: null };
let LAST_RESULT = null;   // 最近一次试算结果（含 input_version）
let LAST_VERSION = null;  // 试算时所用输入版本

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => (
  { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
// ---------- 表单 ----------
function productRow(p = {}) {
  const tr = document.createElement("tr");
  tr.className = "prow";
  tr.innerHTML = `
    <td><input type="text" class="f-name" maxlength="40"
        value="${esc(p.name || "")}" placeholder="如 名片"></td>
    <td><input type="number" class="f-length" min="1" step="1"
        value="${p.length ?? ""}"></td>
    <td><input type="number" class="f-width" min="1" step="1"
        value="${p.width ?? ""}"></td>
    <td><input type="number" class="f-qty" min="1" step="1"
        value="${p.qty ?? ""}"></td>
    <td class="rotcell"><input type="checkbox" class="f-rot"
        ${p.rotatable ? "checked" : ""}></td>
    <td class="delcell"><button type="button" class="icon-btn"
        title="删除该成品">✕</button></td>`;
  tr.querySelector(".icon-btn").addEventListener("click", () => {
    if (document.querySelectorAll(".prow").length > 1) tr.remove();
    else alert("至少保留一种成品");
  });
  return tr;
}

// ---------- 瑕疵登记 ----------
function defectRegionRow(r = {}) {
  const tr = document.createElement("tr");
  tr.className = "dreg";
  tr.innerHTML = `
    <td><input type="number" class="d-x" min="0" step="1"
        value="${r.x ?? ""}"></td>
    <td><input type="number" class="d-y" min="0" step="1"
        value="${r.y ?? ""}"></td>
    <td><input type="number" class="d-w" min="1" step="1"
        value="${r.w ?? ""}"></td>
    <td><input type="number" class="d-h" min="1" step="1"
        value="${r.h ?? ""}"></td>
    <td class="delcell"><button type="button" class="icon-btn"
        title="删除该瑕疵区">✕</button></td>`;
  tr.querySelector(".icon-btn").addEventListener("click", () => {
    const tbody = tr.parentElement;
    if (tbody.querySelectorAll(".dreg").length > 1) tr.remove();
    else alert("每张已登记母纸至少保留一个瑕疵区；如该纸完好请整行删除");
  });
  return tr;
}

function defectSheetRow(entry = {}) {
  const div = document.createElement("div");
  div.className = "defect-sheet";
  div.innerHTML = `
    <div class="defect-head">
      <label class="defect-no">纸号（1 起，每张母纸至多登记一次）
        <input type="number" class="f-sheetno" min="1" step="1"
          value="${entry.sheet_no ?? ""}"></label>
      <button type="button" class="btn btn-ghost del-sheet">删除该纸登记</button>
    </div>
    <table class="dtable">
      <thead><tr><th>左上角 x (mm)</th><th>左上角 y (mm)</th>
        <th>宽 (mm)</th><th>高 (mm)</th><th></th></tr></thead>
      <tbody class="dregions"></tbody>
    </table>
    <button type="button" class="btn btn-ghost add-region">+ 添加瑕疵区</button>`;
  const tbody = div.querySelector(".dregions");
  (entry.regions && entry.regions.length
    ? entry.regions : [{}]).forEach((r) =>
    tbody.appendChild(defectRegionRow(r)));
  div.querySelector(".add-region").addEventListener("click", () =>
    tbody.appendChild(defectRegionRow()));
  div.querySelector(".del-sheet").addEventListener("click", () => div.remove());
  return div;
}

function fillForm(inputs) {
  $("sheet_w").value = inputs.sheet_w;
  $("sheet_h").value = inputs.sheet_h;
  $("kerf").value = inputs.kerf;
  $("max_sheets").value = inputs.max_sheets;
  $("state_limit").value = inputs.state_limit;
  const tb = $("productRows");
  tb.innerHTML = "";
  inputs.products.forEach((p) => tb.appendChild(productRow(p)));
  const dl = $("defectList");
  dl.innerHTML = "";
  (inputs.defects || []).forEach((e) => dl.appendChild(defectSheetRow(e)));
}

function readForm() {
  const num = (el, name) => {
    const v = el.value.trim();
    if (!/^-?\d+$/.test(v)) throw { field: el.id || null, msg: `${name}必须为整数` };
    return parseInt(v, 10);
  };
  const products = [];
  document.querySelectorAll(".prow").forEach((tr) => {
    products.push({
      name: tr.querySelector(".f-name").value.trim(),
      length: num(tr.querySelector(".f-length"), "成品长度"),
      width: num(tr.querySelector(".f-width"), "成品宽度"),
      qty: num(tr.querySelector(".f-qty"), "成品份数"),
      rotatable: tr.querySelector(".f-rot").checked,
    });
  });
  const defects = [];
  document.querySelectorAll(".defect-sheet").forEach((box) => {
    const sheet_no = num(box.querySelector(".f-sheetno"), "瑕疵纸号");
    const regions = [];
    box.querySelectorAll(".dreg").forEach((tr) => {
      regions.push({
        x: num(tr.querySelector(".d-x"), "瑕疵区 x"),
        y: num(tr.querySelector(".d-y"), "瑕疵区 y"),
        w: num(tr.querySelector(".d-w"), "瑕疵区宽"),
        h: num(tr.querySelector(".d-h"), "瑕疵区高"),
      });
    });
    defects.push({ sheet_no, regions });
  });
  return {
    sheet_w: num($("sheet_w"), "母纸长度"),
    sheet_h: num($("sheet_h"), "母纸宽度"),
    kerf: num($("kerf"), "刀缝宽度"),
    max_sheets: num($("max_sheets"), "张数上限"),
    state_limit: num($("state_limit"), "搜索状态数上限"),
    products,
    defects,
  };
}

function clearInvalid() {
  document.querySelectorAll("input.invalid").forEach((e) =>
    e.classList.remove("invalid"));
  $("formError").hidden = true;
}

function showFormError(err) {
  const box = $("formError");
  box.hidden = false;
  box.textContent = err.error || err.msg || "输入有误";
}

// ---------- 渲染 ----------
function statusPill() {
  const el = $("saveState");
  const sol = STATE.solution;
  $("inputVersion").textContent = "v" + STATE.inputs.input_version;
  el.className = "pill";
  if (sol.valid) {
    el.classList.add("pill-ok");
    el.textContent = `已保存有效方案 v${sol.input_version}`;
  } else if (sol.status === "found") {
    el.classList.add("pill-stale");
    el.textContent = "旧方案已失效";
  } else {
    el.classList.add("pill-none");
    el.textContent = "无有效方案";
  }
}

function bannerFor(result) {
  const st = result.status;
  if (st === "found") {
    return `<div class="banner found"><span class="big">✔ 找到可行方案</span>
      使用母纸 <b>${result.sheets_used}</b> 张
      （上限 ${STATE.inputs.max_sheets} 张），
      消耗搜索状态 <b>${result.states_used.toLocaleString()}</b>。
      方案未保存前，修改任何输入都会使其失效。</div>`;
  }
  if (st === "infeasible") {
    return `<div class="banner infeasible"><span class="big">✘ 已穷尽搜索，确认无解</span>
      在当前输入与旋转限制下，已在搜索空间内穷尽所有贯通刀切法，
      不存在满足全部份数的排样。${result.note ? "<br>原因：" + esc(result.note) : ""}
      <br>（已尝试状态 ${result.states_used.toLocaleString()}；
      如需放宽，请减少份数、增大张数上限或调整尺寸后重新搜索。）</div>`;
  }
  // budget_exhausted
  return `<div class="banner budget"><span class="big">⏳ 搜索预算耗尽（并非无解）</span>
    在 <b>${result.states_used.toLocaleString()}</b> 个状态内尚未找到方案，
    但搜索空间<strong>未穷尽</strong>，<u>不能据此声称无解</u>。
    <br>可增大“搜索状态数上限”后重试（当前上限
    <code>${Number(STATE.inputs.state_limit).toLocaleString()}</code>）。</div>`;
}

function renderCounts(result) {
  const rows = result.counts.map((c) => {
    const ok = c.actual === c.demand;
    return `<tr>
      <td><span class="lg-dot" data-i="${result.counts.indexOf(c)}"></span>${esc(c.name)}</td>
      <td>${c.length}×${c.width} mm</td>
      <td>${c.rotatable ? "允许" : "禁止"}</td>
      <td>${c.demand}</td>
      <td class="${ok ? "ok" : "bad"}">${c.actual} ${ok ? "✔" : "✘"}</td></tr>`;
  }).join("");
  $("countsBox").innerHTML = `
    <table class="counts-table"><thead><tr>
      <th>成品</th><th>原始长×宽</th><th>旋转</th>
      <th>需求份数</th><th>实际成品数</th>
    </tr></thead><tbody>${rows}</tbody></table>`;
}

function sheetSVG(sheet, products, kerf) {
  const W = sheet.width, H = sheet.height;
  const maxPx = 480;
  const scale = Math.min(maxPx / W, maxPx / H);
  const dw = W * scale, dh = H * scale, pad = 22;
  const p = [];
  p.push(`<svg class="layout" viewBox="0 ${-pad - 16} ${dw + pad * 2} ${dh + pad * 2 + 16}"
    width="${dw + pad * 2}" height="${dh + pad * 2 + 16}" xmlns="http://www.w3.org/2000/svg">`);
  // 瑕疵区（紫色斜纹块，在最底层；成品避让、刀路/废料可穿过）
  const patId = `defpat-${sheet.sheet_index}`;
  p.push(`<defs><pattern id="${patId}" patternUnits="userSpaceOnUse"
    width="7" height="7" patternTransform="rotate(45)">
    <rect width="7" height="7" fill="#f3e8ff"/>
    <line x1="0" y1="0" x2="0" y2="7" stroke="#9b59b6" stroke-width="2"/>
    </pattern></defs>`);
  (sheet.defects || []).forEach((r) => {
    p.push(`<rect x="${pad + r.x * scale}" y="${pad + r.y * scale}"
      width="${r.w * scale}" height="${r.h * scale}"
      fill="url(#${patId})" stroke="#9b59b6" stroke-width="1.2"
      stroke-dasharray="4 2"/>`);
  });
  p.push(`<rect x="${pad}" y="${pad}" width="${dw.toFixed(1)}"
    height="${dh.toFixed(1)}" fill="none" stroke="#222" stroke-width="2"/>`);
  sheet.gaps.forEach((g) => {
    p.push(`<rect x="${pad + g.x * scale}" y="${pad + g.y * scale}"
      width="${Math.max(g.w * scale, 1.5)}" height="${g.h * scale}"
      fill="#d62728" opacity="0.85"/>`);
  });
  sheet.leaves.forEach((lf) => {
    const [stroke, fill] = PALETTE[lf.product % PALETTE.length];
    const x = pad + lf.x * scale, y = pad + lf.y * scale;
    const w = lf.w * scale, h = lf.h * scale;
    p.push(`<rect x="${x}" y="${y}" width="${w}" height="${h}"
      fill="${fill}" stroke="${stroke}" stroke-width="1.4"/>`);
    if (w >= 30 && h >= 18) {
      const name = products[lf.product].name + (lf.rotated ? " ↻" : "");
      const fs = Math.max(8.5, Math.min(12.5, w / Math.max(name.length, 2) * 1.5));
      p.push(`<text x="${x + w / 2}" y="${y + h / 2}" font-size="${fs}"
        text-anchor="middle" dominant-baseline="middle">${esc(name)}</text>`);
    }
  });
  p.push(`<text x="${pad}" y="2" font-size="11" fill="#7d3c98">
    纸号 ${sheet.sheet_no}（物理母纸编号；未使用的纸号不会出现）</text>`);
  p.push("</svg>");
  return p.join("");
}

function renderCuts(sheet) {
  const rows = sheet.cuts.map((c) => `
    <tr><td class="n">${c.seq}</td>
      <td>${esc(c.region)}</td>
      <td>${esc(c.direction)}</td>
      <td>${esc(c.at_label)}</td>
      <td class="n">${c.length}</td>
      <td>${esc(c.size_a)} → ${esc(c.edge_a)}</td>
      <td>${esc(c.size_b)} → ${esc(c.edge_b)}</td>
    </tr>`).join("");
  return `<div class="cutlist"><table><thead><tr>
    <th class="n">#</th><th>裁切区域（执行顺序）</th><th>刀向</th>
    <th>刀口位置</th><th class="n">刀长</th><th>一刀后余料</th>
    <th>二刀后余料</th></tr></thead><tbody>${rows}</tbody></table></div>`;
}

function renderResult(result) {
  const products = STATE.inputs.products;
  $("statusArea").innerHTML = bannerFor(result);
  if (result.status !== "found") {
    $("resultCard").hidden = true;
    return;
  }
  $("resultCard").hidden = false;
  renderCounts(result);

  const legend = products.map((pr, i) => {
    const [stroke, fill] = PALETTE[i % PALETTE.length];
    return `<span><i style="background:${fill};border-color:${stroke}"></i>${esc(pr.name)}
      （${pr.length}×${pr.width}）</span>`;
  }).join("") + `<span><i style="background:#d62728;border-color:#d62728"></i>刀缝
    （宽 ${STATE.inputs.kerf}mm，已扣除）</span>`
    + `<span><i class="defect-lg"></i>瑕疵区（成品避让；刀路/废料可穿过）</span>`;

  const usedNos = result.sheets.map((s) => s.sheet_no).sort((a, b) => a - b);
  const blocks = result.sheets.map((sheet) => {
    const defTxt = (sheet.defects || []).length
      ? `本纸登记瑕疵 ${sheet.defects.length} 处` : "本纸完好";
    return `
    <div class="sheet-block">
      <h3>用纸 ${sheet.sheet_index} 张 · 物理纸号 <b>${sheet.sheet_no}</b>
        <span class="muted">（${sheet.width}×${sheet.height}mm ·
        ${defTxt} · ${sheet.leaves.length} 件 · ${sheet.cuts.length} 刀）</span></h3>
      <div class="legend">${legend}</div>
      <div class="sheet-cols">
        <div class="sheet-svg-wrap">${sheetSVG(sheet, products, STATE.inputs.kerf)}</div>
        ${renderCuts(sheet)}
      </div>
    </div>`;
  }).join("");
  $("sheetsBox").innerHTML =
    `<p class="muted used-nos">实际使用物理纸号：${usedNos.join("、")}
      （其余纸号跳过未用）</p>` + blocks;
  $("cutsBox").innerHTML = "";

  // 保存/导出按钮状态
  updateSaveUI();
}

function updateSaveUI() {
  const saveBtn = $("savePlan");
  const exportLink = $("exportLink");
  const hint = $("saveHint");
  const sol = STATE.solution;
  const curVer = STATE.inputs.input_version;
  const canSave = LAST_RESULT && LAST_RESULT.status === "found" &&
    LAST_VERSION === curVer;
  saveBtn.disabled = !canSave;

  if (sol.valid) {
    exportLink.classList.remove("disabled");
    hint.innerHTML = `当前已保存与输入 <b>v${sol.input_version}</b> 匹配的有效方案，
      可导出裁切单。保存后若修改任一输入，该方案将立即失效并拒绝导出。`;
  } else {
    exportLink.classList.add("disabled");
    if (canSave) {
      hint.textContent = "这是尚未保存的试算方案。保存后才能导出裁切单。";
    } else {
      hint.textContent = "当前没有有效方案。";
    }
  }
}

// ---------- API ----------
async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  let data = null;
  try { data = await res.json(); } catch (e) { data = {}; }
  return { ok: res.ok, status: res.status, data };
}

async function loadState() {
  const { data } = await api("/api/state");
  STATE = data;
  fillForm(data.inputs);
  statusPill();
  if (data.solution.valid && data.solution.payload) {
    $("statusArea").innerHTML =
      `<div class="banner found"><span class="big">✔ 已有保存的有效方案
       （v${data.solution.input_version}）</span>使用母纸
       <b>${data.solution.sheets_used}</b> 张。下方为已保存排样。</div>`;
    LAST_RESULT = data.solution.payload;
    LAST_VERSION = data.solution.input_version;
    renderResult(data.solution.payload);
  } else {
    $("resultCard").hidden = true;
    updateSaveUI();
  }
}

async function submitAndSolve(saveInputsFirst) {
  clearInvalid();
  let payload;
  try {
    payload = readForm();
  } catch (e) {
    showFormError({ error: e.msg });
    return;
  }

  setBusy(true);
  try {
    let ver;
    if (saveInputsFirst) {
      // 1) 保存输入（校验 + 版本 +1 + 旧方案失效）
      const r1 = await api("/api/inputs", {
        method: "POST", body: JSON.stringify(payload),
      });
      if (!r1.ok) {
        showFormError(r1.data);
        return;
      }
      STATE = r1.data;
      fillForm(STATE.inputs);
      statusPill();
      ver = STATE.inputs.input_version;
    } else {
      // 仅试算：表单里的 state_limit 之外的输入尚未保存，直接试算
      // 已保存的最新输入（以 DB 版本为准）。若表单与已保存输入不一致，
      // 给出明确提示，避免误以为试算的是屏幕上的未保存数值。
      ver = STATE.inputs.input_version;
      const same = formMatchesSaved(payload, STATE.inputs);
      if (!same) {
        setBusy(false);
        if (!confirm("表单中有尚未保存的修改。\n“仅试算”使用的是上一次保存的输入，"
                   + "不会采用屏幕上的新数值。\n是否改为“保存输入并搜索”？")) {
          return;
        }
        return submitAndSolve(true);
      }
    }

    // 2) 试算（带当前版本）
    const r2 = await api("/api/solve", {
      method: "POST",
      body: JSON.stringify({ input_version: ver,
        state_limit: payload.state_limit }),
    });
    if (!r2.ok) {
      showFormError(r2.data);
      if (r2.data.inputs) { STATE = r2.data; fillForm(STATE.inputs); statusPill(); }
      return;
    }
    const result = r2.data.result;
    LAST_RESULT = result;
    LAST_VERSION = r2.data.input_version;
    STATE.inputs.state_limit = payload.state_limit;
    renderResult(result);
  } finally {
    setBusy(false);
  }
}

function formMatchesSaved(payload, saved) {
  const p = saved;
  if (payload.sheet_w !== p.sheet_w || payload.sheet_h !== p.sheet_h
      || payload.kerf !== p.kerf || payload.max_sheets !== p.max_sheets) {
    return false;
  }
  if (payload.products.length !== p.products.length) return false;
  if (!payload.products.every((x, i) => {
    const y = p.products[i];
    return x.name === y.name && x.length === y.length && x.width === y.width
      && x.qty === y.qty && x.rotatable === y.rotatable;
  })) {
    return false;
  }
  // 瑕疵登记：服务端已按纸号/坐标排序，这里按同一规则比较
  const norm = (list) => (list || []).map((e) => ({
    sheet_no: e.sheet_no,
    regions: e.regions.map((r) => ({ x: r.x, y: r.y, w: r.w, h: r.h }))
      .sort((a, b) => a.x - b.x || a.y - b.y || a.w - b.w || a.h - b.h),
  })).sort((a, b) => a.sheet_no - b.sheet_no);
  return JSON.stringify(norm(payload.defects))
    === JSON.stringify(norm(p.defects || []));
}

async function savePlan() {
  if (!LAST_RESULT || LAST_VERSION !== STATE.inputs.input_version) return;
  const r = await api("/api/solution", {
    method: "POST",
    body: JSON.stringify({ input_version: LAST_VERSION, result: LAST_RESULT }),
  });
  if (!r.ok) { alert(r.data.error || "保存失败"); return; }
  STATE = r.data;
  statusPill();
  LAST_RESULT = r.data.solution.payload;
  renderResult(r.data.solution.payload);
}

async function discardPlan() {
  if (!confirm("确定废弃当前已保存的方案吗？")) return;
  const r = await api("/api/solution", { method: "DELETE" });
  if (r.ok) { STATE = r.data; statusPill(); updateSaveUI();
    $("statusArea").innerHTML = '<p class="muted">方案已废弃。</p>';
    $("resultCard").hidden = true; }
}

function setBusy(on) {
  const area = $("statusArea");
  if (on) {
    area.innerHTML = '<div class="banner budget"><span class="spinner"></span>' +
      "正在搜索贯通刀排样，请稍候…</div>";
    $("resultCard").hidden = true;
  }
  document.querySelectorAll(".actions .btn, #savePlan, #discardPlan")
    .forEach((b) => { if (!b.id || on) b.disabled = on; });
}

// ---------- 事件 ----------
$("addProduct").addEventListener("click", () =>
  $("productRows").appendChild(productRow()));
$("addDefectSheet").addEventListener("click", () =>
  $("defectList").appendChild(defectSheetRow()));
$("inputForm").addEventListener("submit", (e) => {
  e.preventDefault();
  submitAndSolve(true);
});
$("solveOnly").addEventListener("click", () => submitAndSolve(false));
$("savePlan").addEventListener("click", savePlan);
$("discardPlan").addEventListener("click", discardPlan);
// 任何输入改动：提示当前展示方案可能过期
document.getElementById("inputForm").addEventListener("input", () => {
  if (LAST_RESULT) {
    $("saveHint").textContent =
      "检测到输入尚未保存：当前展示的是旧输入的结果，保存输入后需重新搜索。";
  }
});

loadState();

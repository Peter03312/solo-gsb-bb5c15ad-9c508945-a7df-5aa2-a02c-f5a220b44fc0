"""裁切单导出：生成自包含、可打印的 HTML（内嵌 SVG 排样图）。

裁切单标明所依据的输入版本与保存时间；仅对当前有效（valid 且版本匹配、
已通过几何核验）的方案生成，否则由调用方拒绝。
"""
from __future__ import annotations

import datetime
import html as _html

PALETTE = [
    ("#4e79a7", "#dce6f1"), ("#f28e2b", "#fbe7d4"), ("#59a14f", "#e2f0dd"),
    ("#e15759", "#fbe0e1"), ("#b07aa1", "#efe5ec"), ("#76b7b2", "#e0efee"),
    ("#edc948", "#faf2d6"), ("#b6992d", "#f0e9d2"), ("#ff9da7", "#ffe6ea"),
    ("#9c755f", "#ece2da"), ("#bab0ac", "#f0eeec"), ("#86bc42", "#e7f1d8"),
]


def _esc(v) -> str:
    return _html.escape(str(v), quote=True)


def _sheet_svg(sheet: dict, products: list, kerf: int, px: int = 520) -> str:
    W, H = sheet["width"], sheet["height"]
    scale = min(px / W, px / H)
    dw, dh = W * scale, H * scale
    top = 34  # 顶部留出纸号标注

    parts = [
        f'<svg viewBox="0 0 {dw + 40:.1f} {dh + 40 + top:.1f}" '
        f'width="{dw + 40:.1f}" height="{dh + 40 + top:.1f}" '
        'class="layout" xmlns="http://www.w3.org/2000/svg">',
        f'<text x="20" y="14" font-size="12" fill="#7d3c98">'
        f'物理纸号 {sheet.get("sheet_no", sheet["sheet_index"])}'
        f'（实际用纸第 {sheet["sheet_index"]} 张）</text>',
        # 瑕疵区（紫色斜纹，最底层；成品避让、刀路/废料可穿过）
        f'<defs><pattern id="defpat{sheet["sheet_index"]}" '
        'patternUnits="userSpaceOnUse" width="8" height="8" '
        'patternTransform="rotate(45)">'
        '<rect width="8" height="8" fill="#f3e8ff"/>'
        '<line x1="0" y1="0" x2="0" y2="8" stroke="#9b59b6" stroke-width="2"/>'
        '</pattern></defs>',
    ]
    for r in sheet.get("defects", []):
        x = 20 + r["x"] * scale
        y = top + 20 * 0 + r["y"] * scale
        parts.append(
            f'<rect x="{x:.2f}" y="{y:.2f}" width="{r["w"] * scale:.2f}" '
            f'height="{r["h"] * scale:.2f}" '
            f'fill="url(#defpat{sheet["sheet_index"]})" '
            'stroke="#9b59b6" stroke-width="1.2" stroke-dasharray="5 2"/>')
    parts.append(
        f'<rect x="20" y="{top}" width="{dw:.1f}" height="{dh:.1f}" '
        'fill="none" stroke="#222" stroke-width="2"/>')
    # 刀缝（红色细条）
    for g in sheet["gaps"]:
        x = 20 + g["x"] * scale
        y = top + g["y"] * scale
        parts.append(
            f'<rect x="{x:.2f}" y="{y:.2f}" width="{max(g["w"] * scale,1):.2f}" '
            f'height="{g["h"] * scale:.2f}" fill="#d62728" opacity="0.85"/>')
    # 成品
    for lf in sheet["leaves"]:
        pi = lf["product"]
        stroke, fill = PALETTE[pi % len(PALETTE)]
        x = 20 + lf["x"] * scale
        y = top + lf["y"] * scale
        w = lf["w"] * scale
        h = lf["h"] * scale
        prod = products[pi]
        parts.append(
            f'<rect x="{x:.2f}" y="{y:.2f}" width="{w:.2f}" height="{h:.2f}" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="1.5"/>')
        if w >= 34 and h >= 20:
            label = prod["name"]
            if lf["rotated"]:
                label += " ↻"
            fs = max(9, min(13, w / max(len(label), 2) * 1.4))
            parts.append(
                f'<text x="{x + w / 2:.2f}" y="{y + h / 2:.2f}" '
                f'font-size="{fs:.1f}" text-anchor="middle" '
                f'dominant-baseline="middle" fill="#222">{_esc(label)}</text>')
    parts.append("</svg>")
    return "".join(parts)


def render_cutting_sheet(inputs: dict, solution_row: dict,
                         result: dict) -> str:
    products = inputs["products"]
    version = inputs["input_version"]
    saved_at = solution_row.get("saved_at") or ""
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 输入摘要
    def _count_of(name):
        return next((c["actual"] for c in result["counts"]
                     if c["name"] == name), 0)

    prod_rows = "".join(
        f"<tr><td>{_esc(p['name'])}</td>"
        f"<td>{p['length']}×{p['width']}</td>"
        f"<td>{'允许' if p['rotatable'] else '禁止'}</td>"
        f"<td>{p['qty']}</td>"
        f"<td>{_count_of(p['name'])}</td></tr>"
        for p in products)

    sheets_html = []
    for sheet in result["sheets"]:
        idx = sheet["sheet_index"]
        no = sheet.get("sheet_no", idx)
        svg = _sheet_svg(sheet, products, inputs["kerf"])
        cut_rows = []
        for c in sheet["cuts"]:
            cut_rows.append(
                "<tr>"
                f"<td class='num'>{c['seq']}</td>"
                f"<td>{_esc(c['region'])}</td>"
                f"<td>{_esc(c['direction'])}</td>"
                f"<td>{_esc(c['at_label'])}</td>"
                f"<td class='num'>{c['length']}</td>"
                f"<td>{_esc(c['size_a'])} → {_esc(c['edge_a'])}</td>"
                f"<td>{_esc(c['size_b'])} → {_esc(c['edge_b'])}</td>"
                "</tr>")
        cuts_table = (
            '<table class="cuts"><thead><tr>'
            "<th>#</th><th>裁切区域（按执行顺序）</th><th>刀向</th>"
            "<th>刀口位置</th><th>刀长(mm)</th><th>一刀后余料</th>"
            "<th>二刀后余料</th></tr></thead><tbody>"
            + "".join(cut_rows) + "</tbody></table>")
        defects = sheet.get("defects", [])
        if defects:
            def_rows = "".join(
                f"<li>({r['x']}, {r['y']})，{r['w']}×{r['h']}mm"
                f"（覆盖 x∈[{r['x']},{r['x'] + r['w']})，"
                f"y∈[{r['y']},{r['y'] + r['h']})）</li>"
                for r in defects)
            def_box = (
                '<div class="defbox"><b>本纸瑕疵区（成品已避让；'
                '刀路/废料可穿过）：</b>'
                f'<ul>{def_rows}</ul></div>')
        else:
            def_box = '<div class="defbox muted">本纸无登记瑕疵（完好母纸）。</div>'
        sheets_html.append(
            f'<section class="sheet-block"><h2>实际用纸第 {idx} 张 · '
            f'物理纸号 <b>{no}</b> '
            f'<span class="muted">（{sheet["width"]}×{sheet["height"]}mm，'
            f'本张 {len(sheet["leaves"])} 件 / {len(sheet["cuts"])} 刀）'
            f'</span></h2>{def_box}'
            f'<div class="cols"><div>{svg}</div>'
            f"<div>{cuts_table}</div></div></section>")

    used_nos = sorted(s.get("sheet_no", i + 1)
                      for i, s in enumerate(result["sheets"]))
    legend = "".join(
        f'<span class="lg"><i style="background:{PALETTE[i % len(PALETTE)][1]};'
        f'border-color:{PALETTE[i % len(PALETTE)][0]}"></i>{_esc(p["name"])}'
        f'（{p["length"]}×{p["width"]}mm）</span>'
        for i, p in enumerate(products))

    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>裁切单 · 输入版本 v{version}</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{ font-family: "Noto Sans CJK SC","Microsoft YaHei",sans-serif;
         margin: 24px; color:#1b1b1b; }}
  h1 {{ font-size: 22px; margin:0 0 4px; }}
  .meta {{ color:#444; font-size:13px; margin-bottom:14px; }}
  .badge {{ display:inline-block; background:#1b6ec2; color:#fff;
           padding:2px 10px; border-radius:10px; font-size:12px; margin-left:6px;}}
  table {{ border-collapse: collapse; width:100%; font-size:12.5px; }}
  th,td {{ border:1px solid #c4c4c4; padding:5px 8px; text-align:left; }}
  th {{ background:#f0f3f7; }}
  td.num,th:nth-child(1) {{ text-align:center; }}
  .summary {{ display:flex; gap:24px; margin:10px 0 18px; flex-wrap:wrap; }}
  .summary div {{ background:#f7f9fb; border:1px solid #dde3ea;
                 padding:8px 14px; border-radius:6px; font-size:13px; }}
  .sheet-block {{ page-break-inside: avoid; margin-bottom:26px;
                 border:1px solid #e0e4e9; border-radius:8px; padding:14px 16px; }}
  .sheet-block h2 {{ font-size:16px; margin:0 0 10px; }}
  .muted {{ color:#777; font-weight:400; font-size:13px; }}
  .cols {{ display:flex; gap:18px; align-items:flex-start; flex-wrap:wrap; }}
  .cols > div:first-child {{ flex:0 0 auto; }}
  .cols > div:last-child {{ flex:1 1 360px; }}
  .layout {{ background:#fff; border:1px solid #ddd; border-radius:4px; }}
  .legend {{ margin:6px 0 16px; }}
  .lg {{ display:inline-flex; align-items:center; gap:5px; margin-right:14px;
        font-size:12.5px; }}
  .lg i {{ width:14px; height:14px; display:inline-block; border:1px solid;
          border-radius:2px; }}
  .lg i.defect-sw {{ border:1px dashed #9b59b6 !important;
    background: repeating-linear-gradient(45deg,#f3e8ff,#f3e8ff 2px,
    #9b59b6 2px,#9b59b6 4px) !important; }}
  .defbox {{ border:1px dashed #c9a7dd; background:#fbf7fe; border-radius:6px;
             padding:8px 12px; margin:0 0 12px; font-size:12.5px; }}
  .defbox ul {{ margin:6px 0 2px; padding-left:20px; }}
  .defbox.muted {{ border-style:solid; border-color:#dde3ea;
                   background:#f7f9fb; color:#777; }}
  .note {{ color:#a33; font-size:12px; }}
  @media print {{ body {{ margin:8mm; }} .sheet-block {{ border:none; }} }}
</style></head>
<body>
  <h1>矩形纸张裁切单<span class="badge">输入版本 v{version}</span></h1>
  <div class="meta">
    依据输入版本：<b>v{version}</b> ｜ 方案保存时间：{_esc(saved_at)}
    ｜ 裁切单导出时间：{_esc(now)} ｜ 状态：已通过几何核验的有效方案
  </div>

  <div class="summary">
    <div>母纸：<b>{inputs['sheet_w']}×{inputs['sheet_h']}mm</b></div>
    <div>刀缝：<b>{inputs['kerf']}mm</b></div>
    <div>实际用纸：<b>{result['sheets_used']} 张</b>
      （上限 {inputs['max_sheets']}）</div>
    <div>实际使用纸号：<b>{_esc("、".join(map(str, used_nos)))}</b>
      （其余纸号跳过）</div>
    <div>搜索状态数：<b>{result.get('states_used','')}</b></div>
  </div>

  <table><thead><tr><th>成品</th><th>原始长×宽(mm)</th>
    <th>旋转</th><th>需求份数</th><th>实际成品数</th></tr></thead>
  <tbody>{prod_rows}</tbody></table>

  <div class="legend"><b>图例：</b>{legend}
    <span class="lg"><i style="background:#d62728;border-color:#d62728"></i>刀缝</span>
    <span class="lg"><i class="defect-sw"></i>瑕疵区</span>
    <span class="note">（↻ 表示该件为旋转摆放；刀缝宽度 {inputs['kerf']}mm 已扣除；
    成品矩形不与瑕疵区有面积重叠，刀路、刀缝与废料允许穿过瑕疵区）</span>
  </div>

  {''.join(sheets_html)}

  <script>window.onload=function(){{window.print();}}</script>
</body></html>"""

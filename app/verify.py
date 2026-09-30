"""方案安全校验：对求解结果或任何待保存/导出的方案做独立几何核验。

服务端不盲信求解器或客户端回传的数据；只有通过全部下列检查才允许落库/
导出裁切单：
- 每张母纸尺寸正确、用纸张数不超过张数上限；
- 每个成品叶子尺寸等于某类成品（旋转需被允许）、在母纸内不越界；
- 成品之间互不重叠（整数毫米逐格检查），且不侵入任何刀缝；
- 裁切步骤按执行顺序每刀都贯穿“当前”余料，刀口位置、刀后两块尺寸、
  刀缝宽度与排样完全自洽；
- 各类成品实际数量恰好等于需求份数。
"""
from __future__ import annotations

import re

_POS_RE = re.compile(r"(-?\d+)mm")


class PlanError(Exception):
    pass


def _as_int(v, what):
    if isinstance(v, bool):
        raise PlanError(f"{what} 必须为整数")
    if not isinstance(v, int):
        raise PlanError(f"{what} 必须为整数")
    return v


def verify(result: dict, inputs: dict) -> None:
    """通过则返回 None，否则抛 PlanError。"""
    if not isinstance(result, dict):
        raise PlanError("方案数据格式错误")
    if result.get("status") != "found":
        raise PlanError("仅可保存/导出找到可行解的方案")

    W = _as_int(inputs["sheet_w"], "母纸长度")
    H = _as_int(inputs["sheet_h"], "母纸宽度")
    k = _as_int(inputs["kerf"], "刀缝宽度")
    max_sheets = _as_int(inputs["max_sheets"], "张数上限")
    products = inputs["products"]
    n = len(products)
    demand = [_as_int(p["qty"], "份数") for p in products]

    sheets = result.get("sheets")
    if not isinstance(sheets, list) or not sheets:
        raise PlanError("方案缺少排样数据")
    if len(sheets) > max_sheets:
        raise PlanError("方案用纸张数超过张数上限")

    produced = [0] * n

    for sh in sheets:
        if not isinstance(sh, dict):
            raise PlanError("排样页格式错误")
        if _as_int(sh.get("width"), "母纸宽") != W or \
           _as_int(sh.get("height"), "母纸高") != H:
            raise PlanError("排样母纸尺寸与输入不一致")

        leaves = sh.get("leaves")
        gaps = sh.get("gaps")
        cuts = sh.get("cuts")
        if not isinstance(leaves, list) or not isinstance(gaps, list) \
                or not isinstance(cuts, list):
            raise PlanError("排样页数据不完整")

        # ---- 叶子：尺寸、旋转、越界 ----
        leaf_rects = []  # (x, y, w, h, product_index)
        for lf in leaves:
            pi = _as_int(lf.get("product"), "成品编号")
            if not (0 <= pi < n):
                raise PlanError("成品编号越界")
            prod = products[pi]
            rotated = lf.get("rotated")
            if not isinstance(rotated, bool):
                raise PlanError("旋转标记必须为布尔值")
            x = _as_int(lf.get("x"), "x")
            y = _as_int(lf.get("y"), "y")
            ww = _as_int(lf.get("w"), "成品宽")
            hh = _as_int(lf.get("h"), "成品高")
            if rotated:
                if not prod["rotatable"]:
                    raise PlanError(f"成品「{prod['name']}」不允许旋转")
                want = (prod["width"], prod["length"])
            else:
                want = (prod["length"], prod["width"])
            if (ww, hh) != want:
                raise PlanError(f"成品「{prod['name']}」排样尺寸不匹配")
            if x < 0 or y < 0 or x + ww > W or y + hh > H:
                raise PlanError(f"成品「{prod['name']}」越界")
            leaf_rects.append((x, y, ww, hh, pi))
            produced[pi] += 1

        # ---- 成品两两不得重叠（轴对齐矩形相交，与纸张尺寸无关）----
        for ai in range(len(leaf_rects)):
            ax, ay, aw, ah, _ = leaf_rects[ai]
            for bi in range(ai + 1, len(leaf_rects)):
                bx, by, bw, bh, _ = leaf_rects[bi]
                if ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah:
                    raise PlanError("成品相互重叠")

        # ---- 刀缝：一条边恰为刀缝宽、另一条边为进刀贯穿长度；
        #      在界内、不被成品侵入（与成品矩形不得相交）----
        if k > 0 and cuts and len(gaps) != len(cuts):
            raise PlanError("刀缝数量与裁切刀数不一致")
        for g in gaps:
            gx = _as_int(g.get("x"), "刀缝x")
            gy = _as_int(g.get("y"), "刀缝y")
            gw = _as_int(g.get("w"), "刀缝宽")
            gh = _as_int(g.get("h"), "刀缝高")
            # 纵刀缝隙宽=k、横刀缝隙高=k（k=0 时无刀缝矩形）
            if not ((gw == k and gh >= 1) or (gh == k and gw >= 1)):
                raise PlanError("刀缝宽度与输入不一致")
            if min(gw, gh) < 1 or (gw != k and gh != k):
                raise PlanError("刀缝尺寸非法")
            if gx < 0 or gy < 0 or gx + gw > W or gy + gh > H:
                raise PlanError("刀缝越界")
            for lx, ly, lw, lh, _pi in leaf_rects:
                if gx < lx + lw and lx < gx + gw and gy < ly + lh and ly < gy + gh:
                    raise PlanError("成品侵入刀缝")

        # ---- 裁切步骤：顺序执行、贯穿当前余料、尺寸自洽 ----
        sheet_index = _as_int(sh.get("sheet_index"), "母纸序号")
        root = f"第{sheet_index}张母纸"
        # 区域名 -> (x, y, w, h)，x/y 为其在母纸内的绝对坐标
        regions = {root: (0, 0, W, H)}
        internal = set()  # 被后续刀切过的区域（内部节点）
        last_seq = 0
        for c in cuts:
            seq = _as_int(c.get("seq"), "刀序号")
            if seq != last_seq + 1:
                raise PlanError("裁切步骤序号不连续")
            last_seq = seq
            region = c.get("region")
            if region not in regions:
                raise PlanError(f"裁切区域不存在或尚未切出：{region}")
            if region in internal:
                raise PlanError(f"余料被重复裁切：{region}")
            internal.add(region)
            rx, ry, rw, rh = regions[region]
            vertical = str(c.get("direction", "")).startswith("纵向")
            horizontal = str(c.get("direction", "")).startswith("横向")
            if vertical == horizontal:
                raise PlanError("裁切方向无效")
            m = _POS_RE.search(str(c.get("at_label", "")))
            if not m:
                raise PlanError("缺少刀口位置")
            pos = int(m.group(1))
            if pos <= 0:
                raise PlanError("刀口位置非法")
            if vertical:
                if not (1 <= pos <= rw - k - 1):
                    raise PlanError("纵刀位置无法在扣除刀缝后切出两块余料")
                if _as_int(c.get("length"), "刀口长") != rh:
                    raise PlanError("纵刀未贯穿当前余料高度")
                a_rect = (rx, ry, pos, rh)
                b_rect = (rx + pos + k, ry, rw - k - pos, rh)
                a_size, b_size = (pos, rh), (rw - k - pos, rh)
            else:
                if not (1 <= pos <= rh - k - 1):
                    raise PlanError("横刀位置无法在扣除刀缝后切出两块余料")
                if _as_int(c.get("length"), "刀口长") != rw:
                    raise PlanError("横刀未贯穿当前余料宽度")
                a_rect = (rx, ry, rw, pos)
                b_rect = (rx, ry + pos + k, rw, rh - k - pos)
                a_size, b_size = (rw, pos), (rw, rh - k - pos)
            for label, key, size in (("一刀后", "size_a", a_size),
                                     ("二刀后", "size_b", b_size)):
                sm = re.findall(r"(\d+)×(\d+)mm", str(c.get(key, "")))
                if not sm:
                    raise PlanError(f"{label}余料尺寸缺失")
                if tuple(map(int, sm[0])) != size:
                    raise PlanError(f"{label}余料尺寸与刀口/刀缝不自洽")
            ea, eb = c.get("edge_a"), c.get("edge_b")
            if not isinstance(ea, str) or not isinstance(eb, str):
                raise PlanError("刀后余料命名缺失")
            regions[f"{region} / {ea}"] = a_rect
            regions[f"{region} / {eb}"] = b_rect

        # 整棵 guillotine 切分树：区域集合累加为 1 + 2·刀数；其中被再次
        # 下刀的“内部区域”数等于刀数（除根外每个内部区域都由一刀产生），
        # 故末端区域（成品叶子 + 废料块）数 = (1+2c) - c = c + 1。
        if len(regions) != 2 * len(cuts) + 1:
            raise PlanError("裁切区域剖分数量不自洽")
        terminal_rects = [rect for name, rect in regions.items()
                          if name not in internal]
        if len(terminal_rects) != len(cuts) + 1:
            raise PlanError("末端余料数与刀数不满足 guillotine 关系")

        # 每个成品叶子的矩形必须恰好等于某个末端区域（其余末端为废料）。
        # 这同时排除“成品漂在区域外 / 叶子与切分树不对应”等篡改。
        used_terminals = set()
        for lf in leaves:
            rect = (lf["x"], lf["y"], lf["w"], lf["h"])
            try:
                ti = terminal_rects.index(rect)
            except ValueError:
                raise PlanError(
                    f"成品矩形 {rect} 不对应任何裁切末端区域（可能越界或与刀路不符）")
            if ti in used_terminals:
                raise PlanError("两个成品映射到同一末端区域")
            used_terminals.add(ti)
        if len(leaves) > len(terminal_rects):
            raise PlanError("成品件数超过末端区域数，排样不自洽")

    if produced != demand:
        raise PlanError(
            f"实际成品数 {produced} 与需求份数 {demand} 不一致")


def actual_counts(result: dict, inputs: dict) -> list:
    """已 verify 通过后提取各类实际数量。"""
    counts = {}
    for sh in result["sheets"]:
        for lf in sh["leaves"]:
            counts[lf["product"]] = counts.get(lf["product"], 0) + 1
    return [counts.get(i, 0) for i in range(len(inputs["products"]))]

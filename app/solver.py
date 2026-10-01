"""贯通式（guillotine）矩形纸张裁切排样求解器（支持母纸瑕疵避让）。

规则：
- 每一刀沿直线贯穿当前矩形余料，把它切成两块；
- 刀缝（kerf）宽度在切分时扣除，两块余料之间留出刀缝；
- 成品矩形不得旋转超出 rotatable 许可，不得重叠或越界；
- 母纸可带若干矩形“瑕疵区”：成品（叶子）矩形不得与瑕疵区有面积重叠
  （边相切允许）；刀路、刀缝与废料均可穿过瑕疵区；
- 叶子节点为一块尺寸完全相同的成品，或纯废料。

搜索引擎（精确目标填充 + 成败双重记忆）：
- fill(w,h,ox,oy,t)：把“恰好”数量向量 t 的成品放入原点为 (ox,oy) 的
  w×h 余料，成功返回切分树，失败返回 None。成败只依赖
  (w,h,ox,oy,t)（无瑕疵母纸退化为 (w,h,t)），与调用方无关，因此成功树
  与失败结论都可跨父节点安全记忆（同瑕疵集合内）；
  树节点自带外框尺寸：
    ("leaf",  w,h, i, rotated) / ("waste", w,h)
    ("cut",   w,h, orient, pos, tree_a, tree_b)
- 切分：枚举刀口（归一化位置）与目标拆分 t = ta + tb（ta 可为 0，
  对应一侧整块留废），两半各自递归 fill；
- 母纸层：在物理母纸编号中选择/跳过具体纸张（编号递增，消除排列冗余），
  枚举当前剩余需求 caps 的子向量 t 作为一张母纸的精确任务，
  要求含编号最小的待生产类（消除张序冗余），按面积利用率降序尝试；
- 每次进入填充状态 / 尝试拆分 / 枚举母纸任务消耗一个状态预算：
    预算内全部穷尽仍无方案 => infeasible（已穷尽，可断言无解）；
    预算耗尽 => budget_exhausted（不得声称无解，可加大预算重试）；
    找到方案立即返回 => found。

刀口位置归一化：
- 无瑕疵时只取成品在进刀方向上的边长 d 及镜像 size-kerf-d；
- 有瑕疵时额外纳入瑕疵边缘相对当前余料原点的坐标 e（及镜像）：
  成品不得压瑕疵而刀路可以穿过，因此余料剥离需要对齐瑕疵边缘；
  guillotine 正常模式（normal patterns）定理在带瑕疵情形下的标准扩展为
  “刀口可取成品边缘或瑕疵边缘”，完备性由 test_defects.py 的全枚举朴素
  判定器做随机交叉验证。
"""
from __future__ import annotations

import bisect
import sys
from dataclasses import dataclass, field
from typing import Optional

sys.setrecursionlimit(200_000)

STATUS_FOUND = "found"
STATUS_INFEASIBLE = "infeasible"
STATUS_BUDGET = "budget_exhausted"


class BudgetExceeded(Exception):
    """搜索状态数预算耗尽。"""


@dataclass(frozen=True)
class Product:
    name: str
    length: int          # 原始长 mm
    width: int           # 原始宽 mm
    qty: int
    rotatable: bool


@dataclass(frozen=True)
class Params:
    sheet_w: int
    sheet_h: int
    kerf: int
    max_sheets: int
    state_limit: int
    products: tuple = field(default_factory=tuple)
    # 瑕疵登记：((sheet_no, ((x,y,w,h), ...)), ...)，按纸号升序、
    # 同纸瑕疵矩形按 (x,y,w,h) 升序（validation 层已规范化）。
    defects: tuple = field(default_factory=tuple)


def rects_overlap(ax: int, ay: int, aw: int, ah: int,
                  bx: int, by: int, bw: int, bh: int) -> bool:
    """两轴对齐矩形是否有正面积相交（边相切不算）。"""
    return ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah


class _FillEngine:
    """一个瑕疵子集上的精确填充搜索引擎。

    regions 为与“当前搜索所在余料”相交的绝对坐标瑕疵矩形；空元组表示
    不含瑕疵的干净余料（此时填充与绝对原点无关，记忆键退化为 (w,h,t)，
    可被任意纸张的任意干净子区域共享）。
    """

    def __init__(self, solver: "Solver", regions: tuple,
                 sheet_regions: tuple = None):
        self.sv = solver
        # 绝对坐标瑕疵矩形元组 (x,y,w,h)；空元组表示干净余料
        self.regions = tuple(regions)
        self.has_defects = bool(self.regions)
        # 本引擎所属母纸的完整瑕疵集合（供子余料路由取交集）；
        # 自身即由交集产生时必须显式带上原集合。
        self._sheet_regions = (sheet_regions if sheet_regions is not None
                               else self.regions)
        self._ok: dict = {}      # key -> tree（成功记忆）
        self._fail: set = set()  # key（失败记忆）
        self._pos_cache: dict = {}

    # ---------- 瑕疵碰撞 ----------
    def _hits_defect(self, x: int, y: int, w: int, h: int) -> bool:
        for dx, dy, dw, dh in self.regions:
            if rects_overlap(x, y, w, h, dx, dy, dw, dh):
                return True
        return False

    # ---------- 归一化刀口位置 ----------
    def _positions(self, size: int, axis: str, w: int, h: int,
                   ox: int, oy: int) -> list:
        """竖直刀口(axis='V')沿宽度方向取位；水平刀口(axis='H')沿高度方向。

        size 为进刀方向余料长度，w/h 为余料宽高，(ox,oy) 为其在母纸内
        的绝对原点。本引擎只含与当前余料相交的瑕疵，但仍需按刀口线段
        是否扫到具体瑕疵逐条过滤。
        """
        ckey = (axis, size, w, h, ox, oy) if self.has_defects else (axis, size)
        cached = self._pos_cache.get(ckey)
        if cached is not None:
            return cached
        sv = self.sv
        k = sv.k
        out = set()
        if size > k + 1:
            # 成品边缘（含镜像）
            if axis == "V":
                dims = {pw for opts in sv.opts for pw, _ph, _r in opts}
            else:
                dims = {ph for opts in sv.opts for _pw, ph, _r in opts}
            for d in dims:
                if 1 <= d <= size - k - 1:
                    out.add(d)
                    q = size - k - d
                    if q >= 1:
                        out.add(q)
            # 瑕疵边缘（含镜像）：仅纳入刀口线段实际扫到的瑕疵
            if self.has_defects:
                for dx, dy, dw, dh in self.regions:
                    if axis == "V":
                        # 竖线 x=e 需与瑕疵的 y 区间在当前余料内有交集
                        if not (dy < oy + h and dy + dh > oy):
                            continue
                        edges = (dx, dx + dw)
                    else:
                        if not (dx < ox + w and dx + dw > ox):
                            continue
                        edges = (dy, dy + dh)
                    for edge in edges:
                        e = edge - (ox if axis == "V" else oy)
                        if 1 <= e <= size - k - 1:
                            out.add(e)
                            q = size - k - e
                            if q >= 1:
                                out.add(q)
        result = sorted(out)
        self._pos_cache[ckey] = result
        return result

    # ---------- 精确目标填充 ----------
    def fill(self, w: int, h: int, ox: int, oy: int, t: tuple):
        """把恰好 t 份成品放入绝对原点 (ox,oy) 的 w×h 余料；tree/None。"""
        sv = self.sv
        if not any(t):
            return ("waste", w, h)
        key = (w, h, t) if not self.has_defects else (w, h, ox, oy, t)
        if key in self._ok:
            return self._ok[key]
        if key in self._fail:
            return None
        sv._tick()
        n = sv.n
        k = sv.k

        # 面积必要条件
        need_area = sum(t[i] * sv.area[i] for i in range(n))
        if need_area > w * h:
            self._fail.add(key)
            return None

        # 瑕疵必要条件：需求中每一类至少要能几何放入一件（廉价预检，
        # 与 guillotine 无关；仅判不可能，不参与构造，故不损完备性）。
        if self.has_defects:
            for i, cnt in enumerate(t):
                if cnt and not sv._item_placeable(
                        self._sheet_regions, (ox, oy, w, h), i):
                    self._fail.add(key)
                    return None

        # 单件叶子：尺寸恰好相等且不压瑕疵则直接成品；否则仍可能通过
        # 切分把该件放在某一子块中（其余为废料），故不匹配时继续枚举切分。
        if sum(t) == 1:
            i = next(j for j, c in enumerate(t) if c == 1)
            prod = sv.p.products[i]
            for pw, ph, _rot in sv.opts[i]:
                if (pw, ph) == (w, h):
                    if self.has_defects and self._hits_defect(ox, oy, w, h):
                        break
                    tree = ("leaf", w, h, i,
                            sv._is_rotated(prod, pw, ph))
                    self._ok[key] = tree
                    return tree

        # 切分枚举：刀口 + 目标拆分。大尺寸刀口优先；拆分按两侧面积
        # 利用率降序（|a块需求面积 - a块面积| + |b块需求面积 - b块面积|）。
        cuts = []
        for pos in self._positions(w, "V", w, h, ox, oy):
            wb = w - k - pos
            if wb >= 1:
                cuts.append(("V", pos, pos, h, wb, h))
        for pos in self._positions(h, "H", w, h, ox, oy):
            hb = h - k - pos
            if hb >= 1:
                cuts.append(("H", pos, w, pos, w, hb))
        cuts.sort(key=lambda c: -(c[2] * c[3]))

        splits = sv._splits(t)

        def split_score(ta, area_a, area_b):
            sa = sum(ta[i] * sv.area[i] for i in range(n))
            da = abs(sa - area_a)
            db = abs(need_area - sa - area_b)
            return da + db

        for orient, pos, wa, ha, wb, hb in cuts:
            area_a, area_b = wa * ha, wb * hb
            ordered = sorted(splits,
                             key=lambda ta: split_score(ta, area_a, area_b))
            for ta in ordered:
                # 单侧面积预检
                sa = sum(ta[i] * sv.area[i] for i in range(n))
                if sa > area_a or need_area - sa > area_b:
                    continue
                sv._tick()
                tb = tuple(t[i] - ta[i] for i in range(n))
                if orient == "V":
                    a_rect = (ox, oy, wa, ha)
                    b_rect = (ox + pos + k, oy, wb, hb)
                else:
                    a_rect = (ox, oy, wa, ha)
                    b_rect = (ox, oy + pos + k, wb, hb)
                # 子区域按其实际相交的瑕疵子集路由：干净子区域共用
                # 全局无瑕疵引擎的记忆，显著压缩状态空间。
                tree_a = sv.fill_region(self._sheet_regions, a_rect, ta)
                if tree_a is None:
                    continue
                tree_b = sv.fill_region(self._sheet_regions, b_rect, tb)
                if tree_b is None:
                    continue
                tree = ("cut", w, h, orient, pos, tree_a, tree_b)
                self._ok[key] = tree
                return tree

        self._fail.add(key)
        return None


class Solver:
    def __init__(self, params: Params):
        self.p = params
        self.n = len(params.products)
        self.k = params.kerf
        # 每类成品允许的摆放方向（去重）：(w, h, rotated)
        self.opts = []
        for prod in params.products:
            seen = {(prod.length, prod.width, False)}
            if prod.rotatable and prod.length != prod.width:
                seen.add((prod.width, prod.length, True))
            self.opts.append(sorted(seen))
        self.area = [prod.length * prod.width for prod in params.products]
        self.states = 0
        self._split_cache: dict = {}
        # 物理母纸瑕疵：纸号 -> 瑕疵矩形元组
        self.defect_map = {s: rects for s, rects in params.defects}
        self._defective_nos = sorted(self.defect_map)
        # 引擎按“当前余料实际相交的瑕疵子集”缓存：键为该子集的绝对
        # 坐标矩形元组；空元组键即全局共享的无瑕疵引擎（记忆键与原点
        # 无关，任意纸张的干净子区域都可复用）。
        self._engines: dict = {(): _FillEngine(self, ())}
        self._place_cache: dict = {}

    # ---------- 预算 ----------
    def _tick(self) -> None:
        self.states += 1
        if self.states > self.p.state_limit:
            raise BudgetExceeded()

    # ---------- 按瑕疵交集子集路由填充 ----------
    def fill_region(self, sheet_regions: tuple, rect: tuple, t: tuple):
        """把 t 份成品填入 rect=(ox,oy,w,h)；只考虑与该矩形正面积相交
        的瑕疵（其外的瑕疵对本区域填充无影响）。"""
        ox, oy, w, h = rect
        if any(t) and sheet_regions:
            subset = tuple(
                r for r in sheet_regions
                if rects_overlap(ox, oy, w, h, r[0], r[1], r[2], r[3]))
        else:
            subset = ()
        eng = self._engines.get(subset)
        if eng is None:
            eng = _FillEngine(self, subset, sheet_regions=subset)
            self._engines[subset] = eng
        return eng.fill(w, h, ox, oy, t)

    def _first_intact_sheet(self, lo: int) -> Optional[int]:
        """编号 >= lo 的第一张完好母纸号（不存在返回 None）。"""
        i = bisect.bisect_left(self._defective_nos, lo)
        s = lo
        while i < len(self._defective_nos) and self._defective_nos[i] == s:
            s += 1
            i += 1
        return s if s <= self.p.max_sheets else None

    def _sheet_choices(self, lo: int) -> list:
        """本层可选用的物理母纸：编号 >= lo 的带瑕疵纸各一个分支，
        外加“任选一张完好纸”的单个分支（完好纸彼此等价，取最小编号）。
        返回 [(sheet_no, regions), ...]，完好纸分支排在最前——多数作坊
        场景只需跳过个别带瑕疵母纸，先试完好纸能极快找到方案；
        分支集合不变，故完备性不受影响。"""
        i = bisect.bisect_left(self._defective_nos, lo)
        defective = [(s, self.defect_map[s]) for s in self._defective_nos[i:]]
        intact = self._first_intact_sheet(lo)
        if intact is not None:
            return [(intact, ())] + defective
        return defective

    # ---------- 单件几何可放置性（避让瑕疵的必要条件，忽略 guillotine）----------
    def _item_placeable(self, regions: tuple, box: tuple, i: int) -> bool:
        """第 i 类成品是否存在某允许方向，能在 box=(x,y,w,h) 内找到一个
        不与 regions 中任一瑕疵正面积相交的摆位（仅作必要条件预检：
        误判“可放”无妨，漏判“不可放”才会破坏完备性）。

        平移固定矩形避让轴对齐障碍：若存在可行摆位，则可行域（锚点域
        扣除障碍膨胀矩形）中必有一点取自“域边界/障碍角点”，故只需检验
        下述锚点候选的笛卡尔积。
        """
        key = (regions, box, i)
        cached = self._place_cache.get(key)
        if cached is not None:
            return cached
        bx, by, bw, bh = box
        for pw, ph, _rot in self.opts[i]:
            if pw > bw or ph > bh:
                continue
            xs = {bx, bx + bw - pw}
            ys = {by, by + bh - ph}
            for dx, dy, dw, dh in regions:
                if dx + dw <= bx or dx >= bx + bw or \
                        dy + dh <= by or dy >= by + bh:
                    continue
                ax = dx + dw
                if bx <= ax <= bx + bw - pw:
                    xs.add(ax)
                ay = dy + dh
                if by <= ay <= by + bh - ph:
                    ys.add(ay)
            for x0 in xs:
                for y0 in ys:
                    if not any(rects_overlap(x0, y0, pw, ph,
                                             dx, dy, dw, dh)
                               for dx, dy, dw, dh in regions):
                        self._place_cache[key] = True
                        return True
        self._place_cache[key] = False
        return False

    @staticmethod
    def _is_rotated(prod, pw: int, ph: int) -> bool:
        return (prod.rotatable and prod.length != prod.width
                and (pw, ph) == (prod.width, prod.length))

    # ---------- 目标向量拆分 ----------
    def _splits(self, t: tuple) -> list:
        """枚举 (ta, tb) 使 ta + tb = t，ta 各分量 0..t_i（tb 由差得到）。

        只产出 ta（tb = t - ta）；按 ta 的面积相对“较小侧”的利用率排序，
        让两侧都尽量装满的拆分优先尝试。
        """
        cached = self._split_cache.get(t)
        if cached is not None:
            return cached
        results = [()]
        for cnt in t:
            nxt = []
            for partial in results:
                for a in range(cnt + 1):
                    nxt.append(partial + (a,))
            results = nxt
        tas = [tuple(x) for x in results]
        self._split_cache[t] = tas
        return tas

    # ---------- 母纸任务子向量枚举 ----------
    def _sheet_targets(self, caps: tuple, must: int) -> list:
        """枚举一张母纸可承担的任务 t：0 ≤ t ≤ caps、t[must] ≥ 1、
        面积不超过母纸。按需求面积降序（高利用率优先）。"""
        W, H = self.p.sheet_w, self.p.sheet_h
        max_area = W * H
        n = self.n
        partials = [()]
        for i, c in enumerate(caps):
            nxt = []
            hi = min(c, max_area // self.area[i])
            for partial in partials:
                for a in range(hi + 1):
                    nxt.append(partial + (a,))
            partials = nxt
        out = []
        for raw in partials:
            t = tuple(raw)
            if t[must] < 1:
                continue
            if sum(t[i] * self.area[i] for i in range(n)) > max_area:
                continue
            out.append(t)
        out.sort(key=lambda t: (
            -sum(t[i] * self.area[i] for i in range(n)),
            -sum(t), t))
        return out

    # ---------- 全局：多张母纸（可选择/跳过具体纸号）----------
    def solve(self) -> dict:
        demand = tuple(prod.qty for prod in self.p.products)
        W, H = self.p.sheet_w, self.p.sheet_h

        # 确定性必要条件预检（不耗搜索预算，结论确定）
        for i, prod in enumerate(self.p.products):
            if not any(pw <= W and ph <= H
                       for pw, ph, _rot in self.opts[i]):
                return self._result(STATUS_INFEASIBLE, [],
                                    note=f"成品「{prod.name}」{prod.length}×{prod.width}mm "
                                         f"在旋转限制内无法放入母纸 {W}×{H}mm")
        total_area = sum(demand[i] * self.area[i] for i in range(self.n))
        if total_area > self.p.max_sheets * W * H:
            return self._result(
                STATUS_INFEASIBLE, [],
                note=f"成品总面积 {total_area}mm² 超过 {self.p.max_sheets} 张母纸总面积 "
                     f"{self.p.max_sheets * W * H}mm²（尚未计刀缝损耗）")

        fail_memo: set = set()

        def dfs(caps: tuple, next_no: int):
            """用编号 >= next_no 的物理母纸完成 caps；成功返回
            [(sheet_no, t, tree), ...]。"""
            if not any(caps):
                return []
            if next_no > self.p.max_sheets:
                return None
            fkey = (caps, next_no)
            if fkey in fail_memo:
                return None
            self._tick()
            must = next(j for j, c in enumerate(caps) if c > 0)
            targets = self._sheet_targets(caps, must)
            for sheet_no, regions in self._sheet_choices(next_no):
                for t in targets:
                    self._tick()
                    tree = self.fill_region(regions, (0, 0, W, H), t)
                    if tree is None:
                        continue
                    rem = tuple(caps[i] - t[i] for i in range(self.n))
                    sub = dfs(rem, sheet_no + 1)
                    if sub is not None:
                        return [(sheet_no, t, tree)] + sub
            fail_memo.add(fkey)
            return None

        try:
            plans = dfs(demand, 1)
        except BudgetExceeded:
            return self._result(STATUS_BUDGET, [])

        if plans is None:
            return self._result(STATUS_INFEASIBLE, [])
        return self._build_found(plans)

    # ---------- 结果构造 ----------
    def _result(self, status: str, sheets: list, note: str = "") -> dict:
        return {
            "status": status,
            "states_used": self.states,
            "sheets_used": len(sheets),
            "sheets": sheets,
            "note": note,
        }

    def _build_found(self, plans: list) -> dict:
        sheets = []
        produced = [0] * self.n
        for ordinal, (sheet_no, _t, tree) in enumerate(plans, start=1):
            layout = self._flatten(ordinal, sheet_no, tree)
            sheets.append(layout)
            for leaf in layout["leaves"]:
                produced[leaf["product"]] += 1
        counts = [
            {
                "name": self.p.products[i].name,
                "length": self.p.products[i].length,
                "width": self.p.products[i].width,
                "rotatable": self.p.products[i].rotatable,
                "demand": self.p.products[i].qty,
                "actual": produced[i],
            }
            for i in range(self.n)
        ]
        res = self._result(STATUS_FOUND, sheets)
        res["counts"] = counts
        return res

    # ---------- 展平：坐标、刀缝、按执行顺序的贯通刀步骤 ----------
    def _flatten(self, sheet_index: int, sheet_no: int, tree) -> dict:
        W, H = self.p.sheet_w, self.p.sheet_h
        leaves = []
        cuts_raw = []
        gaps = []

        def rec(node, x: int, y: int, path: list):
            kind = node[0]
            w, h = node[1], node[2]
            if kind == "leaf":
                leaves.append({
                    "product": node[3],
                    "rotated": node[4],
                    "x": x, "y": y, "w": w, "h": h,
                })
                return
            if kind == "waste":
                return
            _c, _w, _h, orient, pos, a_node, b_node = node
            wa, ha = a_node[1], a_node[2]
            wb, hb = b_node[1], b_node[2]
            region = self._region_name(sheet_index, path)
            if orient == "V":
                edge_a, edge_b = "左片", "右片"
                cuts_raw.append({
                    "sheet": sheet_index,
                    "region": region,
                    "direction": "纵向（垂直刀，自上而下贯穿当前余料）",
                    "at_label": f"距该余料左边缘 {pos}mm",
                    "length": ha,
                    "size_a": f"{wa}×{ha}mm",
                    "size_b": f"{wb}×{hb}mm",
                    "edge_a": edge_a,
                    "edge_b": edge_b,
                })
                if self.k > 0:
                    gaps.append({"x": x + pos, "y": y, "w": self.k, "h": ha})
                rec(a_node, x, y, path + [edge_a])
                rec(b_node, x + pos + self.k, y, path + [edge_b])
            else:
                edge_a, edge_b = "上片", "下片"
                cuts_raw.append({
                    "sheet": sheet_index,
                    "region": region,
                    "direction": "横向（水平刀，自左而右贯穿当前余料）",
                    "at_label": f"距该余料上边缘 {pos}mm",
                    "length": wa,
                    "size_a": f"{wa}×{ha}mm",
                    "size_b": f"{wb}×{hb}mm",
                    "edge_a": edge_a,
                    "edge_b": edge_b,
                })
                if self.k > 0:
                    gaps.append({"x": x, "y": y + pos, "w": wa, "h": self.k})
                rec(a_node, x, y, path + [edge_a])
                rec(b_node, x, y + pos + self.k, path + [edge_b])

        rec(tree, 0, 0, [])
        cuts = []
        for seq, c in enumerate(cuts_raw, start=1):
            c2 = dict(c)
            c2["seq"] = seq
            cuts.append(c2)
        defects_echo = [
            {"x": x, "y": y, "w": w, "h": h}
            for (x, y, w, h) in self.defect_map.get(sheet_no, ())
        ]
        return {
            "sheet_index": sheet_index,
            "sheet_no": sheet_no,
            "width": W,
            "height": H,
            "defects": defects_echo,
            "leaves": leaves,
            "gaps": gaps,
            "cuts": cuts,
        }

    @staticmethod
    def _region_name(sheet_index: int, path: list) -> str:
        if not path:
            return f"第{sheet_index}张母纸"
        return f"第{sheet_index}张母纸 / " + " / ".join(path)


def run(params: Params) -> dict:
    return Solver(params).solve()

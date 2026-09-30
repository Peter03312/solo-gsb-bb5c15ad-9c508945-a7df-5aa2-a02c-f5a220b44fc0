"""贯通式（guillotine）矩形纸张裁切排样求解器。

规则：
- 每一刀沿直线贯穿当前矩形余料，把它切成两块；
- 刀缝（kerf）宽度在切分时扣除，两块余料之间留出刀缝；
- 成品矩形不得旋转超出 rotatable 许可，不得重叠或越界；
- 叶子节点为一块尺寸完全相同的成品，或纯废料。

搜索引擎（精确目标填充 + 成败双重记忆）：
- fill_exact(w,h,t)：把“恰好”数量向量 t 的成品放入 w×h 余料，
  成功返回切分树，失败返回 None。成败只依赖 (w,h,t)，与调用方无关，
  因此成功树与失败结论都可跨父节点安全记忆（无 caps 上下文污染）。
  树节点自带外框尺寸：
    ("leaf",  w,h, i, rotated) / ("waste", w,h)
    ("cut",   w,h, orient, pos, tree_a, tree_b)
- 切分：枚举刀口（归一化位置）与目标拆分 t = ta + tb（ta 可为 0，
  对应一侧整块留废），两半各自递归 fill_exact；
- 母纸层：枚举当前剩余需求 caps 的子向量 t 作为一张母纸的精确任务，
  要求含编号最小的待生产类（消除张序冗余），按面积利用率降序尝试；
- 每次进入填充状态 / 尝试拆分 / 枚举母纸任务消耗一个状态预算：
    预算内全部穷尽仍无方案 => infeasible（已穷尽，可断言无解）；
    预算耗尽 => budget_exhausted（不得声称无解，可加大预算重试）；
    找到方案立即返回 => found。

刀口位置归一化：只取成品在进刀方向上的边长 d 及镜像 size-kerf-d。
guillotine 正常模式（normal patterns）定理保证：若存在可行贯通刀排样，
则第一刀可平移到与某成品边缘对齐而不损失可行性，递归成立，故枚举完备。
"""
from __future__ import annotations

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
        self._ok: dict = {}     # (w,h,t) -> tree（成功记忆）
        self._fail: set = set()  # (w,h,t)（失败记忆）
        self._pos_cache: dict = {}
        self._split_cache: dict = {}

    # ---------- 预算 ----------
    def _tick(self) -> None:
        self.states += 1
        if self.states > self.p.state_limit:
            raise BudgetExceeded()

    # ---------- 归一化刀口位置 ----------
    def _positions(self, size: int, dims: set, cache_key) -> list:
        cached = self._pos_cache.get(cache_key)
        if cached is not None:
            return cached
        k = self.k
        out = set()
        if size > k + 1:
            for d in dims:
                if 1 <= d <= size - k - 1:
                    out.add(d)
                    q = size - k - d
                    if q >= 1:
                        out.add(q)
        result = sorted(out)
        self._pos_cache[cache_key] = result
        return result

    def _v_positions(self, w: int) -> list:
        return self._positions(
            w, {pw for opts in self.opts for pw, _ph, _r in opts}, ("v", w))

    def _h_positions(self, h: int) -> list:
        return self._positions(
            h, {ph for opts in self.opts for _pw, ph, _r in opts}, ("h", h))

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

    # ---------- 精确目标填充 ----------
    def fill_exact(self, w: int, h: int, t: tuple):
        """把恰好 t 份成品放入 w×h；返回 tree 或 None。"""
        if not any(t):
            return ("waste", w, h)
        key = (w, h, t)
        if key in self._ok:
            return self._ok[key]
        if key in self._fail:
            return None
        self._tick()
        n = self.n
        k = self.k

        # 面积必要条件
        need_area = sum(t[i] * self.area[i] for i in range(n))
        if need_area > w * h:
            self._fail.add(key)
            return None

        # 单件叶子：尺寸恰好相等则直接成品；否则仍可能通过切分把该件
        # 放在某一子块中（其余为废料），故不匹配时继续枚举切分。
        if sum(t) == 1:
            i = next(j for j, c in enumerate(t) if c == 1)
            prod = self.p.products[i]
            for pw, ph, _rot in self.opts[i]:
                if (pw, ph) == (w, h):
                    tree = ("leaf", w, h, i, self._is_rotated(prod, pw, ph))
                    self._ok[key] = tree
                    return tree

        # 切分枚举：刀口 + 目标拆分。大尺寸刀口优先；拆分按两侧面积
        # 利用率降序（|a块需求面积 - a块面积| + |b块需求面积 - b块面积|）。
        cuts = []
        for pos in self._v_positions(w):
            wb = w - k - pos
            if wb >= 1:
                cuts.append(("V", pos, pos, h, wb, h))
        for pos in self._h_positions(h):
            hb = h - k - pos
            if hb >= 1:
                cuts.append(("H", pos, w, pos, w, hb))
        cuts.sort(key=lambda c: -(c[2] * c[3]))

        splits = self._splits(t)

        def split_score(ta, area_a, area_b):
            sa = sum(ta[i] * self.area[i] for i in range(n))
            da = abs(sa - area_a)
            db = abs(need_area - sa - area_b)
            return da + db

        for orient, pos, wa, ha, wb, hb in cuts:
            area_a, area_b = wa * ha, wb * hb
            ordered = sorted(splits, key=lambda ta: split_score(ta, area_a, area_b))
            for ta in ordered:
                # 单侧面积预检
                sa = sum(ta[i] * self.area[i] for i in range(n))
                if sa > area_a or need_area - sa > area_b:
                    continue
                self._tick()
                tb = tuple(t[i] - ta[i] for i in range(n))
                tree_a = self.fill_exact(wa, ha, ta)
                if tree_a is None:
                    continue
                tree_b = self.fill_exact(wb, hb, tb)
                if tree_b is None:
                    continue
                tree = ("cut", w, h, orient, pos, tree_a, tree_b)
                self._ok[key] = tree
                return tree

        self._fail.add(key)
        return None

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

    # ---------- 全局：多张母纸 ----------
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

        def dfs(caps: tuple, used: int):
            if not any(caps):
                return []
            remaining = self.p.max_sheets - used
            if remaining <= 0:
                return None
            fkey = (caps, remaining)
            if fkey in fail_memo:
                return None
            self._tick()
            must = next(j for j, c in enumerate(caps) if c > 0)
            for t in self._sheet_targets(caps, must):
                self._tick()
                tree = self.fill_exact(W, H, t)
                if tree is None:
                    continue
                rem = tuple(caps[i] - t[i] for i in range(self.n))
                sub = dfs(rem, used + 1)
                if sub is not None:
                    return [(t, tree)] + sub
            fail_memo.add(fkey)
            return None

        try:
            plans = dfs(demand, 0)
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
        for idx, (_t, tree) in enumerate(plans, start=1):
            layout = self._flatten(idx, tree)
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
    def _flatten(self, sheet_index: int, tree) -> dict:
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
        return {
            "sheet_index": sheet_index,
            "width": W,
            "height": H,
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

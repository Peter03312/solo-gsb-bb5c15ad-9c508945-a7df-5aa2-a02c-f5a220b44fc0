"""瑕疵避让专项测试：典型场景 + 与独立朴素 guillotine 判定器随机交叉验证。

朴素判定器枚举每一个整数刀口位置（不做“成品边/瑕疵边”归一化），
用于核对求解器在带瑕疵母纸上的 found/infeasible 结论。
"""
import random
import sys
from functools import lru_cache
sys.path.insert(0, "/workspace/app")

from solver import Solver, Params, Product, rects_overlap
from test_solver import validate_layout
from verify import verify, PlanError


def validate_defects(result, params: Params, defect_map: dict):
    """在 test_solver 的通用校验之外，追加：
    纸号合法唯一、回显瑕疵一致、成品不压瑕疵。"""
    used = set()
    for ordinal, sheet in enumerate(result["sheets"], start=1):
        no = sheet.get("sheet_no", ordinal)
        assert 1 <= no <= params.max_sheets
        assert no not in used, "纸号重复"
        used.add(no)
        expect = sorted(defect_map.get(no, ()))
        got = sorted((r["x"], r["y"], r["w"], r["h"])
                     for r in sheet.get("defects", []))
        assert got == expect, f"纸号 {no} 瑕疵回显不一致: {got} != {expect}"
        for lf in sheet["leaves"]:
            for dx, dy, dw, dh in defect_map.get(no, ()):
                assert not rects_overlap(lf["x"], lf["y"], lf["w"], lf["h"],
                                         dx, dy, dw, dh), \
                    f"成品压瑕疵: 纸号{no} leaf={lf} defect={dx,dy,dw,dh}"


def make_naive(W, H, items, counts, max_sheets, kerf, defects_by_no):
    """全枚举整数刀口的朴素 guillotine + 瑕疵避让判定。
    defects_by_no: {sheet_no: ((x,y,w,h),...)}"""
    def subvectors(caps):
        res = [()]
        for c in caps:
            res = [p + (x,) for p in res for x in range(c + 1)]
        return res

    def hits(regions, x, y, w, h):
        for dx, dy, dw, dh in regions:
            if rects_overlap(x, y, w, h, dx, dy, dw, dh):
                return True
        return False

    def fill_factory(regions):
        @lru_cache(maxsize=None)
        def fill(x, y, w, h, need):
            if not any(need):
                return True
            if sum(need) == 1:
                i = next(j for j, c in enumerate(need) if c == 1)
                iw, ih, rot = items[i]
                dirs = {(iw, ih)}
                if rot and iw != ih:
                    dirs.add((ih, iw))
                if (w, h) in dirs and not hits(regions, x, y, w, h):
                    return True
                # 尺寸不等 / 压瑕疵：仍可切分把该件放进子块
            # 纵刀
            for pos in range(1, w - kerf):
                rw = w - kerf - pos
                if rw < 1:
                    continue
                for a in subvectors(need):
                    b = tuple(need[j] - a[j] for j in range(len(need)))
                    if fill(x, y, pos, h, a) and \
                            fill(x + pos + kerf, y, rw, h, b):
                        return True
            # 横刀
            for pos in range(1, h - kerf):
                rh = h - kerf - pos
                if rh < 1:
                    continue
                for a in subvectors(need):
                    b = tuple(need[j] - a[j] for j in range(len(need)))
                    if fill(x, y, w, pos, a) and \
                            fill(x, y + pos + kerf, w, rh, b):
                        return True
            return False
        return fill

    fills = {no: fill_factory(tuple(rects))
             for no, rects in defects_by_no.items()}
    intact_fill = fill_factory(())

    def use_sheets(need, lo):
        if not any(need):
            return True
        if lo > max_sheets:
            return False
        i = next(j for j, c in enumerate(need) if c > 0)
        # 带瑕疵纸 + 第一张完好纸（完好纸彼此等价）
        choices = [no for no in defects_by_no if no >= lo]
        first_intact = lo
        ds = sorted(defects_by_no)
        k = 0
        while k < len(ds) and ds[k] == first_intact:
            first_intact += 1
            k += 1
        if first_intact <= max_sheets:
            choices.append(first_intact)
        for no in sorted(choices):
            f = fills[no] if no in fills else intact_fill
            for t in subvectors(need):
                if t[i] < 1:
                    continue
                if sum(t[j] * items[j][0] * items[j][1]
                       for j in range(len(need))) > W * H:
                    continue
                if f(0, 0, W, H, t):
                    rest = tuple(need[j] - t[j] for j in range(len(need)))
                    if use_sheets(rest, no + 1):
                        return True
        return False

    return use_sheets(tuple(counts), 1)


# ---------------- 固定场景 ----------------
def case(name, params, expect, defects_map):
    res = Solver(params).solve()
    assert res["status"] == expect, \
        f"[{name}] 期望 {expect}，实际 {res['status']} ({res.get('note')})"
    if res["status"] == "found":
        validate_layout(res, params)
        validate_defects(res, params, defects_map)
        verify(res, _inputs_from(params, defects_map))
        print(f"[OK] {name}: 用纸号 "
              f"{[s['sheet_no'] for s in res['sheets']]}，"
              f"{res['states_used']} 状态")
    else:
        print(f"[OK] {name}: {res['status']} states={res['states_used']}")
    return res


def _inputs_from(params: Params, defects_map: dict) -> dict:
    return {
        "sheet_w": params.sheet_w, "sheet_h": params.sheet_h,
        "kerf": params.kerf, "max_sheets": params.max_sheets,
        "products": [
            {"name": p.name, "length": p.length, "width": p.width,
             "qty": p.qty, "rotatable": p.rotatable}
            for p in params.products],
        "defects": [
            {"sheet_no": no,
             "regions": [{"x": x, "y": y, "w": w, "h": h}
                         for (x, y, w, h) in rects]}
            for no, rects in sorted(defects_map.items())],
    }


dm = {1: ((0, 0, 10, 10),)}
case("一象限瑕疵需两张",
     Params(20, 20, 0, 3, 1_000_000,
            (Product("A", 10, 10, 4, False),),
            tuple((s, tuple(sorted(rs))) for s, rs in sorted(dm.items()))),
     "found", dm)

dm = {1: ((0, 0, 20, 20),)}
case("满纸瑕疵单张无解",
     Params(20, 20, 0, 1, 1_000_000,
            (Product("A", 10, 10, 1, False),),
            tuple((s, tuple(sorted(rs))) for s, rs in sorted(dm.items()))),
     "infeasible", dm)

dm = {1: ((0, 0, 20, 20),)}
case("跳过满瑕疵纸使用2号",
     Params(20, 20, 0, 2, 1_000_000,
            (Product("A", 10, 10, 4, False),),
            tuple((s, tuple(sorted(rs))) for s, rs in sorted(dm.items()))),
     "found", dm)

# 重叠瑕疵区允许登记
dm = {2: ((2, 2, 6, 6), (4, 4, 6, 6))}
case("同纸重叠瑕疵区可登记并避让",
     Params(20, 20, 0, 3, 1_000_000,
            (Product("A", 9, 9, 4, True),),
            tuple((s, tuple(sorted(rs))) for s, rs in sorted(dm.items()))),
     "found", dm)

# 边相切不算重叠：瑕疵 (10,0,10,20) 与叶子 [0,10] 相切
dm = {1: ((10, 0, 10, 20),)}
case("瑕疵边相切可接受",
     Params(20, 20, 0, 1, 1_000_000,
            (Product("A", 10, 20, 1, False),),
            tuple((s, tuple(sorted(rs))) for s, rs in sorted(dm.items()))),
     "found", dm)

# 带刀缝 + 瑕疵
dm = {1: ((9, 0, 2, 20),)}
case("刀缝与瑕疵共面仍可切",
     Params(20, 20, 2, 1, 1_000_000,
            (Product("A", 9, 20, 2, False),),
            tuple((s, tuple(sorted(rs))) for s, rs in sorted(dm.items()))),
     "found", dm)

# ---------------- 篡改必须被 verify 拒绝 ----------------
# 5 件 10x10、上限 2 张（单张容量 4）=> 必须同时用纸号 1、2；
# 瑕疵在 1 号右下角，这样篡改（把 1 号纸上的叶子挪到瑕疵象限）必然违规。
inputs = {
    "sheet_w": 20, "sheet_h": 20, "kerf": 0, "max_sheets": 2,
    "products": [{"name": "A", "length": 10, "width": 10,
                  "qty": 5, "rotatable": False}],
    "defects": [{"sheet_no": 1, "regions": [
        {"x": 10, "y": 10, "w": 10, "h": 10}]}],
}
res = Solver(Params(20, 20, 0, 2, 1_000_000,
                    (Product("A", 10, 10, 5, False),),
                    ((1, ((10, 10, 10, 10),)),))).solve()
assert res["status"] == "found"
assert sorted(s["sheet_no"] for s in res["sheets"]) == [1, 2]
verify(res, inputs)  # 原始方案通过


def _sheet1(result):
    return next(s for s in result["sheets"] if s["sheet_no"] == 1)


# 把 1 号纸上的成品挪到瑕疵象限
s1 = _sheet1(res)
tampered_sheets = [
    dict(s, leaves=[dict(lf, x=10, y=10) for lf in s["leaves"]])
    if s["sheet_no"] == 1 else s for s in res["sheets"]]
tampered = {**res, "sheets": tampered_sheets}
try:
    verify(tampered, inputs)
    raise AssertionError("压瑕疵方案必须被拒绝")
except PlanError as e:
    print(f"[OK] 压瑕疵篡改被拒：{e}")

# 回显瑕疵与登记不一致（把 1 号纸回显清空）
tampered2 = {**res, "sheets": [
    dict(s, defects=[]) if s["sheet_no"] == 1 else s
    for s in res["sheets"]]}
try:
    verify(tampered2, inputs)
    raise AssertionError("瑕疵回显不一致必须被拒绝")
except PlanError as e:
    print(f"[OK] 瑕疵回显不一致被拒：{e}")

# 纸号越界
tampered3 = {**res, "sheets": [dict(s, sheet_no=3)
                               for s in res["sheets"]]}
try:
    verify(tampered3, inputs)
    raise AssertionError("越界纸号必须被拒绝")
except PlanError as e:
    print(f"[OK] 越界纸号被拒：{e}")

# 纸号重复（两页都改成纸号 2）
tampered4 = {**res, "sheets": [dict(s, sheet_no=2)
                               for s in res["sheets"]]}
try:
    verify(tampered4, inputs)
    raise AssertionError("重复纸号必须被拒绝")
except PlanError as e:
    print(f"[OK] 重复纸号被拒：{e}")

# ---------------- 输入校验 ----------------
from validation import validate_inputs, ValidationError

base = {
    "sheet_w": 100, "sheet_h": 80, "kerf": 0, "max_sheets": 3,
    "state_limit": 1000,
    "products": [{"name": "A", "length": 20, "width": 20,
                  "qty": 1, "rotatable": True}],
}


def reject(desc, payload, field=None):
    try:
        validate_inputs(payload)
        raise AssertionError(f"应拒绝：{desc}")
    except ValidationError as e:
        print(f"[OK] 拒绝 {desc}：{e.message}"
              + (f"（字段 {e.field}）" if e.field else ""))


reject("纸号越界", {**base, "defects": [
    {"sheet_no": 4, "regions": [{"x": 0, "y": 0, "w": 1, "h": 1}]}]})
reject("纸号非正", {**base, "defects": [
    {"sheet_no": 0, "regions": [{"x": 0, "y": 0, "w": 1, "h": 1}]}]})
reject("重复纸号", {**base, "defects": [
    {"sheet_no": 2, "regions": [{"x": 0, "y": 0, "w": 1, "h": 1}]},
    {"sheet_no": 2, "regions": [{"x": 5, "y": 5, "w": 1, "h": 1}]}]})
reject("空瑕疵清单", {**base, "defects": [{"sheet_no": 1, "regions": []}]})
reject("零宽瑕疵", {**base, "defects": [
    {"sheet_no": 1, "regions": [{"x": 0, "y": 0, "w": 0, "h": 1}]}]})
reject("负坐标", {**base, "defects": [
    {"sheet_no": 1, "regions": [{"x": -1, "y": 0, "w": 1, "h": 1}]}]})
reject("瑕疵越界x", {**base, "defects": [
    {"sheet_no": 1, "regions": [{"x": 99, "y": 0, "w": 2, "h": 1}]}]})
reject("瑕疵越界y", {**base, "defects": [
    {"sheet_no": 1, "regions": [{"x": 0, "y": 80, "w": 1, "h": 1}]}]})
reject("小数宽", {**base, "defects": [
    {"sheet_no": 1, "regions": [{"x": 0, "y": 0, "w": 1.5, "h": 1}]}]})

clean = validate_inputs({**base, "defects": [
    {"sheet_no": 2, "regions": [{"x": 3, "y": 4, "w": 5, "h": 6},
                                 {"x": 1, "y": 1, "w": 2, "h": 2}]},
    {"sheet_no": 1, "regions": [{"x": 0, "y": 0, "w": 1, "h": 1}]},
]})
assert [e["sheet_no"] for e in clean["defects"]] == [1, 2]
assert clean["defects"][1]["regions"][0] == {"x": 1, "y": 1, "w": 2, "h": 2}
print("[OK] 合法瑕疵登记规范化（按纸号/坐标排序）")

clean0 = validate_inputs({**base})
assert clean0["defects"] == []
print("[OK] 省略 defects 视为全部完好")

# ---------------- 随机交叉验证 ----------------
# 朴素判定器枚举全部整数刀口，开销随网格指数增长；带瑕疵时每个余料原点
# 都是新状态，故这里只用小网格（12..18mm、1..2 张、份数 1..2）做穷举级
# 交叉验证，单题朴素侧墙钟超过 8 秒则跳过（防止个别组合拖垮整套测试）。
import time

random.seed(20261001)
mismatch = checked = budget = skipped = 0
trial = 0
target = 60
while checked + skipped + budget < 200 and checked < target:
    trial += 1
    W = random.choice([12, 14, 16, 18])
    H = random.choice([12, 14, 16, 18])
    kerf = random.choice([0, 0, 1, 2])
    ntypes = random.randint(1, 2)
    items, counts = [], []
    for _ in range(ntypes):
        items.append((random.randint(4, W), random.randint(4, H),
                      random.random() < 0.5))
        counts.append(random.randint(1, 2))
    max_sheets = random.randint(1, 2)

    # 随机登记 0..max_sheets 张纸的瑕疵（每张 1~2 个小矩形）
    n_def_sheets = random.randint(0, max_sheets)
    defect_nos = random.sample(range(1, max_sheets + 1), n_def_sheets)
    defects_by_no = {}
    for no in defect_nos:
        nreg = random.randint(1, 2)
        rects = []
        for _ in range(nreg):
            dw = random.randint(1, max(2, W // 2))
            dh = random.randint(1, max(2, H // 2))
            dx = random.randint(0, W - dw)
            dy = random.randint(0, H - dh)
            rects.append((dx, dy, dw, dh))
        defects_by_no[no] = tuple(rects)

    t0 = time.time()
    expect = make_naive(W, H, items, counts, max_sheets, kerf, defects_by_no)
    if time.time() - t0 > 8:
        skipped += 1
        continue
    prods = tuple(Product(f"P{j}", items[j][0], items[j][1], counts[j],
                          items[j][2]) for j in range(ntypes))
    params = Params(
        W, H, kerf, max_sheets, 5_000_000, prods,
        tuple((s, rs) for s, rs in sorted(defects_by_no.items())))
    res = Solver(params).solve()
    if res["status"] == "budget_exhausted":
        budget += 1
        continue
    checked += 1
    got = res["status"] == "found"
    if got:
        validate_layout(res, params)
        validate_defects(res, params, defects_by_no)
        verify(res, _inputs_from(params, defects_by_no))
    if got != expect:
        mismatch += 1
        print(f"[MISMATCH] trial={trial} {W}x{H} k={kerf} sheets={max_sheets} "
              f"items={items} counts={counts} defects={defects_by_no} "
              f"naive={expect} solver={res['status']} "
              f"states={res['states_used']}")
        if mismatch >= 5:
            break

print(f"\n瑕疵交叉验证：有效对照 {checked} 例（朴素侧过慢跳过 {skipped} 例、"
      f"求解器预算耗尽 {budget} 例），不一致 {mismatch} 例")
assert mismatch == 0
print("瑕疵专项测试全部通过 ✔")

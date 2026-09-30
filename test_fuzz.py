"""模糊交叉验证：独立朴素 guillotine 判定 vs Solver。

朴素判定器用完全不同的实现风格（递归 + 区域拆分，无位置归一化缓存），
用于交叉核对 found/infeasible；小尺寸（网格 <= 2400）零刀缝场景运行。
"""
import random
import sys
sys.path.insert(0, "/workspace/app")
from solver import Solver, Params, Product, STATUS_FOUND, STATUS_INFEASIBLE
from test_solver import validate_layout


def naive_feasible2(W, H, items, counts, max_sheets):
    from functools import lru_cache

    def subvectors(caps):
        res = [()]
        for c in caps:
            res = [p + (x,) for p in res for x in range(c + 1)]
        return res

    @lru_cache(maxsize=None)
    def fill(w, h, need):
        if not any(need):
            return True
        # 叶子：整块余料恰好一件成品
        if sum(need) == 1:
            i = next(j for j, c in enumerate(need) if c == 1)
            iw, ih, rot = items[i]
            dirs = {(iw, ih)}
            if rot and iw != ih:
                dirs.add((ih, iw))
            if (w, h) in dirs:
                return True
            # 尺寸不等：仍可切分把该件放进子块（其余为废料），继续向下
        for pos in range(1, w):
            for a in subvectors(need):
                b = tuple(need[j] - a[j] for j in range(len(need)))
                if fill(pos, h, a) and fill(w - pos, h, b):
                    return True
        for pos in range(1, h):
            for a in subvectors(need):
                b = tuple(need[j] - a[j] for j in range(len(need)))
                if fill(w, pos, a) and fill(w, h - pos, b):
                    return True
        return False

    @lru_cache(maxsize=None)
    def use_sheets(need, used):
        if not any(need):
            return True
        if used >= max_sheets:
            return False
        i = next(j for j, c in enumerate(need) if c > 0)
        for t in subvectors(need):
            if t[i] < 1:
                continue
            if sum(t[j] * items[j][0] * items[j][1]
                   for j in range(len(need))) > W * H:
                continue
            if fill(W, H, t):
                rest = tuple(need[j] - t[j] for j in range(len(need)))
                if use_sheets(rest, used + 1):
                    return True
        return False

    return use_sheets(tuple(counts), 0)


random.seed(20260930)
mismatch = 0
checked = 0
for trial in range(60):
    W = random.choice([20, 24, 30, 36, 40])
    H = random.choice([20, 24, 30, 36, 40])
    ntypes = random.randint(1, 2)
    items = []
    counts = []
    for _ in range(ntypes):
        iw = random.randint(6, W)
        ih = random.randint(6, H)
        rot = random.random() < 0.5
        items.append((iw, ih, rot))
        counts.append(random.randint(1, 4))
    max_sheets = random.randint(1, 6)
    # 朴素判定（仅小网格）
    if W * H * max_sheets > 60_000_000:
        continue
    expect = naive_feasible2(W, H, items, counts, max_sheets)
    prods = tuple(Product(f"P{j}", items[j][0], items[j][1], counts[j],
                          items[j][2]) for j in range(ntypes))
    params = Params(W, H, 0, max_sheets, 2_000_000, prods)
    res = Solver(params).solve()
    got_found = res["status"] == STATUS_FOUND
    got_inf = res["status"] == STATUS_INFEASIBLE
    checked += 1
    if got_found:
        validate_layout(res, params)
    if got_found != expect:
        mismatch += 1
        print(f"[MISMATCH] {W}x{H} sheets={max_sheets} items={items} "
              f"counts={counts} naive={expect} solver={res['status']} "
              f"states={res['states_used']}")
    if res["status"] == "budget_exhausted":
        print(f"[note] 预算耗尽（小用例，可接受）trial={trial}")

print(f"\n交叉验证 {checked} 例，不一致 {mismatch} 例")
assert mismatch == 0
print("模糊测试通过 ✔")

"""带刀缝的随机交叉验证（独立朴素 guillotine 判定器，切分扣除 kerf）。"""
import random
import sys
from functools import lru_cache
sys.path.insert(0, "/workspace")
sys.path.insert(0, "/workspace/app")
from solver import Solver, Params, Product, STATUS_FOUND, STATUS_INFEASIBLE
from test_solver import validate_layout


def make_naive(W, H, items, counts, max_sheets, kerf):
    def subvectors(caps):
        res = [()]
        for c in caps:
            res = [p + (x,) for p in res for x in range(c + 1)]
        return res

    @lru_cache(maxsize=None)
    def fill(w, h, need):
        if not any(need):
            return True
        if sum(need) == 1:
            i = next(j for j, c in enumerate(need) if c == 1)
            iw, ih, rot = items[i]
            dirs = {(iw, ih)}
            if rot and iw != ih:
                dirs.add((ih, iw))
            if (w, h) in dirs:
                return True
        for pos in range(1, w - kerf):
            rw = w - kerf - pos
            if rw < 1:
                continue
            for a in subvectors(need):
                b = tuple(need[j] - a[j] for j in range(len(need)))
                if fill(pos, h, a) and fill(rw, h, b):
                    return True
        for pos in range(1, h - kerf):
            rh = h - kerf - pos
            if rh < 1:
                continue
            for a in subvectors(need):
                b = tuple(need[j] - a[j] for j in range(len(need)))
                if fill(w, pos, a) and fill(w, rh, b):
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
            if fill(W, H, t):
                rest = tuple(need[j] - t[j] for j in range(len(need)))
                if use_sheets(rest, used + 1):
                    return True
        return False

    return use_sheets(tuple(counts), 0)


random.seed(99)
mismatch = checked = 0
for trial in range(40):
    W = random.choice([18, 22, 26, 30])
    H = random.choice([18, 22, 26, 30])
    kerf = random.choice([1, 2, 3])
    ntypes = random.randint(1, 2)
    items, counts = [], []
    for _ in range(ntypes):
        items.append((random.randint(5, W), random.randint(5, H),
                      random.random() < 0.5))
        counts.append(random.randint(1, 3))
    max_sheets = random.randint(1, 4)
    expect = make_naive(W, H, items, counts, max_sheets, kerf)
    prods = tuple(Product(f"P{j}", items[j][0], items[j][1], counts[j],
                          items[j][2]) for j in range(ntypes))
    params = Params(W, H, kerf, max_sheets, 2_000_000, prods)
    res = Solver(params).solve()
    got_found = res["status"] == STATUS_FOUND
    checked += 1
    if got_found:
        validate_layout(res, params)
    if got_found != expect:
        mismatch += 1
        print(f"[MISMATCH] {W}x{H} k={kerf} sheets={max_sheets} "
              f"items={items} counts={counts} naive={expect} "
              f"solver={res['status']} states={res['states_used']}")
        if mismatch >= 5:
            break

print(f"\n刀缝交叉验证 {checked} 例，不一致 {mismatch} 例")
assert mismatch == 0
print("刀缝模糊测试通过 ✔")

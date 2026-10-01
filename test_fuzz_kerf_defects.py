"""刀缝 + 瑕疵联合的随机交叉验证（独立朴素判定器 vs Solver）。"""
import random
import signal
import sys
from functools import lru_cache
sys.path.insert(0, "/workspace")
sys.path.insert(0, "/workspace/app")
from solver import Solver, Params, Product, STATUS_FOUND, STATUS_BUDGET
from test_solver import validate_layout


class _Timeout(Exception):
    pass
signal.signal(signal.SIGALRM, lambda *a: (_ for _ in ()).throw(_Timeout()))


def make_naive(W, H, kerf, items, counts, max_sheets, sheet_defects):
    def subvectors(caps):
        res = [()]
        for c in caps:
            res = [p + (x,) for p in res for x in range(c + 1)]
        return res

    def clip(defs, x0, y0, w, h):
        out = []
        for dx, dy, dw, dh in defs:
            cx0, cy0 = max(dx, x0), max(dy, y0)
            cx1, cy1 = min(dx + dw, x0 + w), min(dy + dh, y0 + h)
            if cx1 > cx0 and cy1 > cy0:
                out.append((cx0 - x0, cy0 - y0, cx1 - cx0, cy1 - cy0))
        return tuple(sorted(out))

    @lru_cache(maxsize=None)
    def fill(w, h, need, defs):
        if not any(need):
            return True
        if sum(need[j] * items[j][0] * items[j][1]
               for j in range(len(need))) > w * h:
            return False
        if not defs and sum(need) == 1:
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
            da, db = clip(defs, 0, 0, pos, h), clip(defs, pos + kerf, 0, rw, h)
            for a in subvectors(need):
                b = tuple(need[j] - a[j] for j in range(len(need)))
                if fill(pos, h, a, da) and fill(rw, h, b, db):
                    return True
        for pos in range(1, h - kerf):
            rh = h - kerf - pos
            if rh < 1:
                continue
            da, db = clip(defs, 0, 0, w, pos), clip(defs, 0, pos + kerf, w, rh)
            for a in subvectors(need):
                b = tuple(need[j] - a[j] for j in range(len(need)))
                if fill(w, pos, a, da) and fill(w, rh, b, db):
                    return True
        return False

    def use_sheets(need, s):
        if not any(need):
            return True
        if s > max_sheets:
            return False
        i = next(j for j, c in enumerate(need) if c > 0)
        for t in subvectors(need):
            if t[i] < 1:
                continue
            if fill(W, H, t, tuple(sorted(sheet_defects.get(s, ())))):
                rest = tuple(need[j] - t[j] for j in range(len(need)))
                if use_sheets(rest, s + 1):
                    return True
        if s in sheet_defects and use_sheets(need, s + 1):
            return True
        return False

    return use_sheets(tuple(counts), 1)


random.seed(777)
mismatch = checked = skipped = 0
for trial in range(30):
    W = random.choice([12, 14, 16, 18])
    H = random.choice([12, 14, 16, 18])
    kerf = random.choice([1, 2])
    items = [(random.randint(4, W), random.randint(4, H),
              random.random() < 0.5)
             for _ in range(random.randint(1, 2))]
    counts = [random.randint(1, 3) for _ in items]
    max_sheets = random.randint(1, 3)
    sheet_defects = {}
    for no in range(1, max_sheets + 1):
        if random.random() < 0.5:
            dw = random.randint(1, max(1, W // 2))
            dh = random.randint(1, max(1, H // 2))
            sheet_defects[no] = [(random.randint(0, W - dw),
                                  random.randint(0, H - dh), dw, dh)]
    prods = tuple(Product(f"P{j}", items[j][0], items[j][1],
                          counts[j], items[j][2])
                  for j in range(len(items)))
    defects = tuple((no, tuple(regs)) for no, regs in sheet_defects.items())
    params = Params(W, H, kerf, max_sheets, 5_000_000, prods, defects)
    res = Solver(params).solve()
    if res["status"] == STATUS_BUDGET or W * H * max_sheets > 5_500:
        skipped += 1
        continue
    signal.setitimer(signal.ITIMER_REAL, 8.0)
    try:
        expect = make_naive(W, H, kerf, items, counts, max_sheets, sheet_defects)
    except _Timeout:
        skipped += 1
        continue
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
    got = res["status"] == STATUS_FOUND
    checked += 1
    if got:
        validate_layout(res, params)
    if got != expect:
        mismatch += 1
        print(f"[MISMATCH] {W}x{H} k={kerf} s={max_sheets} items={items} "
              f"counts={counts} def={sheet_defects} naive={expect} "
              f"solver={res['status']}")

print(f"\n刀缝+瑕疵交叉验证 {checked} 例（跳过 {skipped}），不一致 {mismatch}")
assert mismatch == 0
print("刀缝+瑕疵模糊测试通过 ✔")

"""带瑕疵的随机交叉验证（独立朴素 guillotine 判定器 vs Solver）。

朴素判定器用完全不同的实现风格：刀口逐毫米全枚举（不做 normal-patterns
归一化）、逐张纸枚举“使用/跳过”、瑕疵随切分裁剪；用于交叉核对新求解器
在瑕疵场景下的 found/infeasible 是否完备。小尺寸零刀缝场景运行。
"""
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


def _alarm(_sig, _frm):
    raise _Timeout()


signal.signal(signal.SIGALRM, _alarm)


def make_naive(W, H, items, counts, max_sheets, sheet_defects):
    """sheet_defects: {纸号1基: [(x,y,w,h), ...]}；未登记视为完好。"""

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
        # 叶子：整块恰好一件成品且无任何正面积瑕疵
        if not defs and sum(need) == 1:
            i = next(j for j, c in enumerate(need) if c == 1)
            iw, ih, rot = items[i]
            dirs = {(iw, ih)}
            if rot and iw != ih:
                dirs.add((ih, iw))
            if (w, h) in dirs:
                return True
        # 纵刀：逐毫米枚举（扣除 0 刀缝）
        for pos in range(1, w):
            da, db = clip(defs, 0, 0, pos, h), clip(defs, pos, 0, w - pos, h)
            for a in subvectors(need):
                b = tuple(need[j] - a[j] for j in range(len(need)))
                if fill(pos, h, a, da) and fill(w - pos, h, b, db):
                    return True
        # 横刀
        for pos in range(1, h):
            da, db = clip(defs, 0, 0, w, pos), clip(defs, 0, pos, w, h - pos)
            for a in subvectors(need):
                b = tuple(need[j] - a[j] for j in range(len(need)))
                if fill(w, pos, a, da) and fill(w, h - pos, b, db):
                    return True
        return False

    def use_sheets(need, s):
        if not any(need):
            return True
        if s > max_sheets:
            return False
        i = next(j for j, c in enumerate(need) if c > 0)
        root_defs = tuple(sorted(sheet_defects.get(s, ())))
        # 使用本纸：枚举含最小编号待生产类的任务
        for t in subvectors(need):
            if t[i] < 1:
                continue
            if sum(t[j] * items[j][0] * items[j][1]
                   for j in range(len(need))) > W * H:
                continue
            if fill(W, H, t, root_defs):
                rest = tuple(need[j] - t[j] for j in range(len(need)))
                if use_sheets(rest, s + 1):
                    return True
        # 只有带瑕疵纸允许跳过（与求解器同策略；跳过完好纸无完备性损失）
        if s in sheet_defects and use_sheets(need, s + 1):
            return True
        return False

    return use_sheets(tuple(counts), 1)


random.seed(20261001)
mismatch = checked = budgeted = 0
N_TRIALS = 40
for trial in range(N_TRIALS):
    W = random.choice([12, 14, 16, 18, 20])
    H = random.choice([12, 14, 16, 18, 20])
    ntypes = random.randint(1, 2)
    items, counts = [], []
    for _ in range(ntypes):
        items.append((random.randint(4, W), random.randint(4, H),
                      random.random() < 0.5))
        counts.append(random.randint(1, 3))
    max_sheets = random.randint(1, 3)
    # 随机瑕疵：只给部分纸登记，每纸 1~2 个正面积矩形（允许重叠）
    sheet_defects = {}
    for no in range(1, max_sheets + 1):
        if random.random() < 0.5:
            regs = []
            for _ in range(random.randint(1, 2)):
                dw = random.randint(1, max(1, W // 2))
                dh = random.randint(1, max(1, H // 2))
                regs.append((random.randint(0, W - dw),
                             random.randint(0, H - dh), dw, dh))
            sheet_defects[no] = regs
    prods = tuple(Product(f"P{j}", items[j][0], items[j][1], counts[j],
                          items[j][2]) for j in range(ntypes))
    defects = tuple((no, tuple(regs)) for no, regs in sheet_defects.items())
    params = Params(W, H, 0, max_sheets, 5_000_000, prods, defects)
    res = Solver(params).solve()
    if res["status"] == STATUS_BUDGET:
        budgeted += 1
        continue
    # 朴素判定器为纯参考实现，限制其搜索规模/单例耗时以防指数爆炸
    if W * H * max_sheets > 6_000:
        continue
    signal.setitimer(signal.ITIMER_REAL, 8.0)
    try:
        expect = make_naive(W, H, items, counts, max_sheets, sheet_defects)
    except _Timeout:
        budgeted += 1
        continue
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
    got_found = res["status"] == STATUS_FOUND
    checked += 1
    if got_found:
        validate_layout(res, params)
    if got_found != expect:
        mismatch += 1
        print(f"[MISMATCH] {W}x{H} sheets={max_sheets} items={items} "
              f"counts={counts} defects={sheet_defects} naive={expect} "
              f"solver={res['status']} states={res['states_used']}")
    print(f"[{trial + 1}/{N_TRIALS}] {'found' if got_found else 'infeasible'} "
          f"{'=' if got_found == expect else '!'} "
          f"{W}x{H} s={max_sheets} def={sheet_defects}")

print(f"\n瑕疵交叉验证 {checked} 例（预算耗尽跳过 {budgeted} 例），"
      f"不一致 {mismatch} 例")
assert mismatch == 0
print("带瑕疵模糊测试通过 ✔")

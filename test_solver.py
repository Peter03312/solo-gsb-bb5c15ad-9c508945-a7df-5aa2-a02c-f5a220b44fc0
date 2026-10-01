"""求解器自检：手工校验排样几何合法性 + 若干典型场景。"""
import sys
sys.path.insert(0, "/workspace/app")

from solver import (Solver, Params, Product,
                    STATUS_FOUND, STATUS_INFEASIBLE, STATUS_BUDGET)


def validate_layout(result, params: Params):
    """独立校验：不重叠、不越界、刀缝正确、数量与份数匹配、步骤可执行。"""
    W, H = params.sheet_w, params.sheet_h
    k = params.kerf
    produced = [0] * len(params.products)
    used_nos = []
    for sheet in result["sheets"]:
        assert sheet["width"] == W and sheet["height"] == H
        sheet_no = sheet.get("sheet_no", sheet["sheet_index"])
        assert 1 <= sheet_no <= params.max_sheets
        assert sheet_no not in used_nos, "用纸号重复"
        used_nos.append(sheet_no)
        leaves = sheet["leaves"]
        # 越界 + 瑕疵避让
        sheet_defs = [(d["x"], d["y"], d["w"], d["h"])
                      for d in sheet.get("defects", [])]
        expect_defs = sorted(dict(params.defects).get(sheet_no, ()))
        assert sorted(sheet_defs) == expect_defs, "排样页瑕疵清单与输入不符"
        for lf in leaves:
            assert lf["x"] >= 0 and lf["y"] >= 0
            assert lf["x"] + lf["w"] <= W and lf["y"] + lf["h"] <= H
            prod = params.products[lf["product"]]
            if lf["rotated"]:
                assert prod.rotatable
                assert (lf["w"], lf["h"]) == (prod.width, prod.length)
            else:
                assert (lf["w"], lf["h"]) == (prod.length, prod.width)
            for dx, dy, dw, dh in sheet_defs:
                # 正面积重叠即非法（边界相切允许）
                assert not (lf["x"] < dx + dw and dx < lf["x"] + lf["w"]
                            and lf["y"] < dy + dh and dy < lf["y"] + lf["h"]), \
                    f"成品与 {sheet_no} 号纸瑕疵区重叠"
            produced[lf["product"]] += 1
        # 重叠（整数格子面积法，刀缝位置不应被任何成品占用）
        grid = bytearray(W * H)
        for lf in leaves:
            for yy in range(lf["y"], lf["y"] + lf["h"]):
                base = yy * W + lf["x"]
                row = grid[base:base + lf["w"]]
                assert not any(row), "成品重叠"
                for xx in range(lf["w"]):
                    grid[base + xx] = 1
        # 刀缝矩形确实落在两成品/边界之间且不被占用（仅检查不被成品占用）
        for g in sheet["gaps"]:
            if g["w"] == 0 or g["h"] == 0:
                continue
            assert g["x"] >= 0 and g["y"] >= 0
            assert g["x"] + g["w"] <= W and g["y"] + g["h"] <= H
            for yy in range(g["y"], min(g["y"] + g["h"], H)):
                for xx in range(g["x"], min(g["x"] + g["w"], W)):
                    assert grid[yy * W + xx] == 0, "成品侵入刀缝"
        # 裁切步骤：按顺序模拟，每刀必须贯穿一个当前存在的余料区域
        import re as _re
        sheet_no = sheet.get("sheet_no", sheet["sheet_index"])
        regions = {f"第{sheet_no}号母纸": (W, H)}
        for c in sheet["cuts"]:
            assert c["region"] in regions, f"未知裁切区域 {c['region']}"
            rw, rh = regions[c["region"]]
            pos = int(_re.search(r"(\d+)mm", c["at_label"]).group(1))
            if c["direction"].startswith("纵向"):
                assert c["length"] == rh, "纵刀未贯穿余料高度"
                a, b = (pos, rh), (rw - k - pos, rh)
            else:
                assert c["length"] == rw, "横刀未贯穿余料宽度"
                a, b = (rw, pos), (rw, rh - k - pos)
            aw, ah = map(int, c["size_a"].replace("mm", "").split("×"))
            bw, bh = map(int, c["size_b"].replace("mm", "").split("×"))
            assert (aw, ah) == a and (bw, bh) == b, "刀后余料尺寸不一致"
            assert min(a) >= 1 and min(b) >= 1, "刀后出现非正尺寸余料"
            regions[c["region"] + " / " + c["edge_a"]] = a
            regions[c["region"] + " / " + c["edge_b"]] = b
        # 刀序唯一且连续
        seqs = [c["seq"] for c in sheet["cuts"]]
        assert seqs == list(range(1, len(seqs) + 1)), "刀序不连续"
    for i, prod in enumerate(params.products):
        assert produced[i] == prod.qty, f"{prod.name}: {produced[i]} != {prod.qty}"


def case(name, params, expect_status, check_validate=True):
    res = Solver(params).solve()
    status = res["status"]
    assert status == expect_status, f"[{name}] 期望 {expect_status}, 实际 {status} ({res.get('note')})"
    if status == "found" and check_validate:
        validate_layout(res, params)
        print(f"[OK] {name}: {res['sheets_used']} 张母纸, {res['states_used']} 状态")
    elif status != "found":
        print(f"[OK] {name}: {status} ({res.get('note','')}) states={res['states_used']}")
    return res


if __name__ == "__main__":
    # 1. 基本：100x100 母纸，放 50x50 四张，刀缝 0
    case("基本四宫格",
         Params(100, 100, 0, 1, 1_000_000, (
             Product("A", 50, 50, 4, True),
         )), "found")

    # 2. 刀缝 2：100x100 放 50x50 两张一张内放不下 4（(50*2+2)>100），需要 4 张？
    #    实际 2x2 需要 50+2+50=102 > 100，一排放 1 个，一张放 1 个 => 需 4 张
    case("刀缝导致一张一件",
         Params(100, 100, 2, 4, 1_000_000, (
             Product("A", 50, 50, 4, True),
         )), "found")
    # 上限 3 张 => 无解
    r = case("刀缝+张数上限无解",
         Params(100, 100, 2, 3, 1_000_000, (
             Product("A", 50, 50, 4, True),
         )), "infeasible")

    # 3. 旋转限制：母纸 100x60，成品 110x30 不可旋转 => 110>100 放不进
    case("几何放不进",
         Params(100, 60, 0, 2, 1_000_000, (
             Product("B", 110, 30, 1, False),
         )), "infeasible")

    # 4. 不可旋转但方向正确能放下：母纸 100x60，70x30 横放两件
    case("不可旋转横放",
         Params(100, 60, 0, 1, 1_000_000, (
             Product("B", 70, 30, 2, False),
         )), "found")

    # 5. 旋转有意义：母纸 60x100（竖母纸），70x30 不可旋转放不进，可旋转可放
    case("必须旋转",
         Params(60, 100, 0, 1, 1_000_000, (
             Product("B", 70, 30, 2, True),
         )), "found")
    case("禁止旋转则无解",
         Params(60, 100, 0, 1, 1_000_000, (
             Product("B", 70, 30, 2, False),
         )), "infeasible")

    # 6. 多种成品混排（总面积 76600mm² < 90000mm²，且需满足贯通刀约束）
    case("多品种混排",
         Params(200, 150, 3, 3, 20_000_000, (
             Product("名片", 90, 54, 6, True),
             Product("明信片", 148, 100, 1, True),
             Product("书签", 150, 40, 2, False),
         )), "found")

    # 7. 预算耗尽（极小预算，预算状态必须不能声称无解）
    res = Solver(Params(200, 200, 2, 5, 5, (
        Product("X", 37, 53, 30, True),
        Product("Y", 61, 29, 20, True),
    ))).solve()
    assert res["status"] == STATUS_BUDGET, res["status"]
    print(f"[OK] 预算耗尽: states={res['states_used']}，note 不声称无解")
    # 加大预算后可以找到
    case("加大预算后找到",
         Params(200, 200, 2, 30, 50_000_000, (
             Product("X", 37, 53, 30, True),
             Product("Y", 61, 29, 20, True),
         )), "found")

    # 8. 贯通性：纵刀长度=余料高度，横刀长度=余料宽度；且 4 叶 => 3 刀
    res = Solver(Params(100, 100, 0, 1, 1_000_000, (
        Product("A", 50, 50, 4, True),))).solve()
    sh = res["sheets"][0]
    assert len(sh["cuts"]) == len(sh["leaves"]) - 1 == 3
    for c in sh["cuts"]:
        sa_w, sa_h = map(int, c["size_a"].replace("mm", "").split("×"))
        if c["direction"].startswith("纵向"):
            assert c["length"] == sa_h == 100 or c["length"] == sa_h
        else:
            assert c["length"] == sa_w
    print("[OK] 每刀贯穿当前余料，且叶子数 = 刀数 + 1（四宫格 3 刀）")

    # 9. 张数上限边界：刚好够
    case("张数刚好够",
         Params(100, 100, 0, 4, 1_000_000, (
             Product("A", 50, 50, 16, True),
         )), "found")
    case("张数差一张",
         Params(100, 100, 0, 3, 1_000_000, (
             Product("A", 50, 50, 16, True),
         )), "infeasible")
    print("\n全部测试通过 ✔")

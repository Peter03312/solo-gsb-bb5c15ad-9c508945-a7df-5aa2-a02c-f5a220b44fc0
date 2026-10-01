"""输入校验：所有尺寸为正整数毫米、刀缝非负整数、份数/张数/搜索上限正整数。

任何非法输入都会抛出 ValidationError，由调用方在事务外/提交前拦截，
保证非法数据绝不落库。
"""
from __future__ import annotations

# 防止误用导致资源耗尽的合理上限（毫米 / 个数 / 状态数）
MAX_DIM = 10_000
MAX_KERF = 1_000
MAX_QTY = 100_000
MAX_SHEETS = 10_000
MAX_STATE_LIMIT = 100_000_000
MAX_PRODUCT_TYPES = 12
MAX_NAME_LEN = 40
MAX_DEFECT_REGIONS_PER_SHEET = 100
MAX_DEFECT_REGIONS_TOTAL = 2_000


class ValidationError(Exception):
    def __init__(self, message: str, field: str = ""):
        super().__init__(message)
        self.message = message
        self.field = field


def _coerce_int(value, name: str, field: str, integer_only: bool) -> int:
    """把 JSON 输入转成整数。布尔与非整数浮点一律拒绝；
    整数字符串（允许首尾空白）接受。"""
    if isinstance(value, bool):
        raise ValidationError(f"{name}必须为整数", field)
    if isinstance(value, float):
        if not integer_only or not value.is_integer():
            raise ValidationError(f"{name}必须为整数", field)
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        s = value.strip()
        if not s:
            raise ValidationError(f"{name}必须为整数", field)
        try:
            return int(s)
        except ValueError:
            raise ValidationError(f"{name}必须为整数", field)
    raise ValidationError(f"{name}必须为整数", field)


def _pos_int(value, name: str, field: str, maximum: int) -> int:
    iv = _coerce_int(value, name, field, integer_only=True)
    if iv <= 0:
        raise ValidationError(f"{name}必须为正整数（≥1）", field)
    if iv > maximum:
        raise ValidationError(f"{name}不得超过 {maximum}", field)
    return iv


def _nonneg_int(value, name: str, field: str, maximum: int) -> int:
    iv = _coerce_int(value, name, field, integer_only=True)
    if iv < 0:
        raise ValidationError(f"{name}必须为非负整数（≥0）", field)
    if iv > maximum:
        raise ValidationError(f"{name}不得超过 {maximum}", field)
    return iv


def validate_defect_regions(regions, sheet_w: int, sheet_h: int,
                            field: str = "regions") -> list:
    """校验某一纸号的矩形瑕疵区清单，返回规范化列表。

    每区给出左上角坐标 x/y 与宽高 w/h（毫米整数）：
    - 宽高必须为正整数；坐标非负；矩形整体落在母纸内（可贴边）；
    - 允许同纸多个瑕疵区互相重叠；
    - 清单至少包含一个瑕疵区（空清单无登记意义，未登记即视为完好）。
    """
    if not isinstance(regions, list) or not regions:
        raise ValidationError(
            "瑕疵区清单必须为非空数组（未登记的纸号视为完好，无需提交空清单）",
            field)
    if len(regions) > MAX_DEFECT_REGIONS_PER_SHEET:
        raise ValidationError(
            f"单张母纸瑕疵区最多 {MAX_DEFECT_REGIONS_PER_SHEET} 个", field)
    out = []
    for i, r in enumerate(regions):
        fp = f"{field}[{i}]"
        if not isinstance(r, dict):
            raise ValidationError("瑕疵区格式错误", fp)
        x = _nonneg_int(r.get("x"), "瑕疵区左上角x", f"{fp}.x", sheet_w)
        y = _nonneg_int(r.get("y"), "瑕疵区左上角y", f"{fp}.y", sheet_h)
        w = _pos_int(r.get("w"), "瑕疵区宽度", f"{fp}.w", sheet_w)
        h = _pos_int(r.get("h"), "瑕疵区高度", f"{fp}.h", sheet_h)
        if x + w > sheet_w or y + h > sheet_h:
            raise ValidationError(
                f"瑕疵区 ({x},{y},{w}×{h}mm) 越界：必须整体落在母纸 "
                f"{sheet_w}×{sheet_h}mm 内", fp)
        out.append({"x": x, "y": y, "w": w, "h": h})
    return out


def validate_defects(raw, max_sheets: int, sheet_w: int, sheet_h: int) -> dict:
    """校验整份瑕疵登记（纸号 1..max_sheets，各登记一次）。

    接受两种形态：
    - 对象：{"1": [区...], "3": [区...]}（键可为整数或数字字符串）；
    - 数组：[{"sheet_no": 1, "regions": [...]}, ...]（纸号不得重复）。
    返回规范化对象 {纸号int: [规范化瑕疵区]}（键升序）。
    """
    if raw is None:
        return {}
    entries = []
    if isinstance(raw, dict):
        for key, regions in raw.items():
            no = _coerce_int(key, "纸号", "sheet_no", integer_only=True)
            entries.append((no, regions))
    elif isinstance(raw, list):
        for idx, item in enumerate(raw):
            if not isinstance(item, dict):
                raise ValidationError("瑕疵登记项格式错误", f"defects[{idx}]")
            no = _pos_int(item.get("sheet_no"), "纸号",
                          f"defects[{idx}].sheet_no", max_sheets)
            entries.append((no, item.get("regions")))
    else:
        raise ValidationError(
            "瑕疵登记必须为对象 {纸号: [瑕疵区...]} 或数组 "
            "[{sheet_no, regions}]", "defects")

    result = {}
    for no, regions in entries:
        if not (1 <= no <= max_sheets):
            raise ValidationError(
                f"纸号必须在 1 至张数上限 {max_sheets} 之间", "sheet_no")
        if no in result:
            raise ValidationError(f"纸号 {no} 的瑕疵区重复登记", "sheet_no")
        result[no] = validate_defect_regions(
            regions, sheet_w, sheet_h,
            f"defects[{no}]" if isinstance(raw, dict) else "regions")
    if sum(len(v) for v in result.values()) > MAX_DEFECT_REGIONS_TOTAL:
        raise ValidationError(
            f"瑕疵区总数最多 {MAX_DEFECT_REGIONS_TOTAL} 个", "defects")
    return {no: result[no] for no in sorted(result)}


def validate_inputs(payload: dict) -> dict:
    """校验并返回规范化后的输入字典；非法时抛 ValidationError。"""
    if not isinstance(payload, dict):
        raise ValidationError("请求体必须为 JSON 对象")

    sheet_w = _pos_int(payload.get("sheet_w"), "母纸长度", "sheet_w", MAX_DIM)
    sheet_h = _pos_int(payload.get("sheet_h"), "母纸宽度", "sheet_h", MAX_DIM)
    kerf = _nonneg_int(payload.get("kerf"), "刀缝宽度", "kerf", MAX_KERF)
    max_sheets = _pos_int(payload.get("max_sheets"), "张数上限",
                          "max_sheets", MAX_SHEETS)
    state_limit = _pos_int(payload.get("state_limit"), "搜索状态数上限",
                           "state_limit", MAX_STATE_LIMIT)

    if kerf >= sheet_w or kerf >= sheet_h:
        raise ValidationError(
            f"刀缝宽度 {kerf}mm 必须小于母纸长宽 "
            f"（{sheet_w}×{sheet_h}mm）", "kerf")

    products_raw = payload.get("products")
    if not isinstance(products_raw, list) or not products_raw:
        raise ValidationError("至少需要一种成品", "products")
    if len(products_raw) > MAX_PRODUCT_TYPES:
        raise ValidationError(
            f"成品种类最多 {MAX_PRODUCT_TYPES} 种", "products")

    products = []
    names = set()
    for idx, item in enumerate(products_raw):
        fieldpfx = f"products[{idx}]"
        if not isinstance(item, dict):
            raise ValidationError("成品项格式错误", fieldpfx)
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValidationError("成品名称不能为空", f"{fieldpfx}.name")
        name = name.strip()
        if len(name) > MAX_NAME_LEN:
            raise ValidationError(f"成品名称不得超过 {MAX_NAME_LEN} 个字符",
                                  f"{fieldpfx}.name")
        if name in names:
            raise ValidationError(f"成品名称「{name}」重复",
                                  f"{fieldpfx}.name")
        names.add(name)

        length = _pos_int(item.get("length"), f"成品「{name}」长度",
                          f"{fieldpfx}.length", MAX_DIM)
        width = _pos_int(item.get("width"), f"成品「{name}」宽度",
                         f"{fieldpfx}.width", MAX_DIM)
        qty = _pos_int(item.get("qty"), f"成品「{name}」份数",
                       f"{fieldpfx}.qty", MAX_QTY)
        rotatable = item.get("rotatable")
        if not isinstance(rotatable, bool):
            raise ValidationError(f"成品「{name}」的能否旋转必须为是/否",
                                  f"{fieldpfx}.rotatable")

        products.append({
            "name": name,
            "length": length,
            "width": width,
            "qty": qty,
            "rotatable": rotatable,
        })

    # 瑕疵登记（可选；缺省视为全部完好）。尺寸非正、越界、重复纸号在此拒绝。
    defects = validate_defects(
        payload.get("defects"), max_sheets, sheet_w, sheet_h)

    return {
        "sheet_w": sheet_w,
        "sheet_h": sheet_h,
        "kerf": kerf,
        "max_sheets": max_sheets,
        "state_limit": state_limit,
        "products": products,
        "defects": defects,
    }

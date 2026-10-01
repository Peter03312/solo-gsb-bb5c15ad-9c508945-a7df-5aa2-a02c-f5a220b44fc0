"""WSGI 应用：仅依赖 Python 标准库。

路由：
- GET  /                     浏览器界面（单页）
- GET  /static/app.js
- GET  /static/style.css
- GET  /api/state            当前输入（含版本）+ 方案状态
- POST /api/inputs           校验并替换输入；非法输入 400 且不落库；
                             保存成功后旧方案自动失效
- POST /api/defects          按纸号登记矩形瑕疵区清单（每纸号一次）；
                             尺寸非正/越界 400、重复纸号 409；成功后
                             版本 +1、旧方案失效
- DELETE /api/defects        删除瑕疵登记（?sheet_no=N 单删，缺省清空）；
                             未登记纸号 404；确有删除则旧方案失效
- POST /api/solve            用当前版本输入试算（不落库），返回
                             found / infeasible / budget_exhausted
                             （成品自动避让各纸瑕疵区，刀路/废料可经过）
- POST /api/solution         保存试算方案（服务端独立几何+瑕疵核验 +
                             版本核对）
- DELETE /api/solution       手动废弃当前方案
- GET  /api/cutting-sheet    导出有效方案的裁切单 HTML（标明输入版本、
                             实际用纸号与瑕疵区；失效方案一律拒绝）
"""
from __future__ import annotations

import json
import os
import threading
from urllib.parse import parse_qs

from solver import Solver, Params, Product
from validation import (validate_inputs, validate_defect_regions,
                        ValidationError)
from verify import verify, actual_counts, PlanError
import cutting_sheet as cs
from database import (Database, StaleVersionError, DuplicateDefectError,
                      MissingDefectError)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")


class App:
    def __init__(self, db: Database):
        self.db = db

    # ---------- WSGI 入口 ----------
    def __call__(self, environ, start_response):
        method = environ.get("REQUEST_METHOD", "GET")
        path = environ.get("PATH_INFO", "/")
        try:
            if method == "GET" and path == "/":
                return self._serve_file("index.html", "text/html; charset=utf-8",
                                        start_response)
            if method == "GET" and path == "/static/app.js":
                return self._serve_file("app.js", "application/javascript; charset=utf-8",
                                        start_response)
            if method == "GET" and path == "/static/style.css":
                return self._serve_file("style.css", "text/css; charset=utf-8",
                                        start_response)
            if method == "GET" and path == "/api/state":
                return self._json(200, self._state(), start_response)
            if method == "POST" and path == "/api/inputs":
                return self._update_inputs(environ, start_response)
            if method == "POST" and path == "/api/defects":
                return self._register_defects(environ, start_response)
            if method == "DELETE" and path == "/api/defects":
                return self._delete_defects(environ, start_response)
            if method == "POST" and path == "/api/solve":
                return self._solve(environ, start_response)
            if method == "POST" and path == "/api/solution":
                return self._save_solution(environ, start_response)
            if method == "DELETE" and path == "/api/solution":
                self.db.invalidate_solution()
                return self._json(200, {"ok": True, **self._state()},
                                  start_response)
            if method == "GET" and path == "/api/cutting-sheet":
                return self._export(start_response)
            return self._json(404, {"error": "未找到该路径"}, start_response)
        except Exception as exc:  # noqa: BLE001
            return self._json(500, {"error": f"服务器内部错误：{exc}"},
                              start_response)

    # ---------- 静态文件 ----------
    def _serve_file(self, name: str, ctype: str, start_response):
        fpath = os.path.join(STATIC_DIR, name)
        if not os.path.abspath(fpath).startswith(
                os.path.abspath(STATIC_DIR) + os.sep) or not os.path.exists(fpath):
            return self._json(404, {"error": "file not found"}, start_response)
        with open(fpath, "rb") as fh:
            body = fh.read()
        start_response("200 OK", [
            ("Content-Type", ctype),
            ("Content-Length", str(len(body))),
        ])
        return [body]

    # ---------- JSON 辅助 ----------
    @staticmethod
    def _json(code: int, obj: dict, start_response):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        start_response(f"{code} " + {
            200: "OK", 400: "BAD REQUEST", 404: "NOT FOUND",
            409: "CONFLICT", 500: "INTERNAL SERVER ERROR",
        }.get(code, "OK"), [
            ("Content-Type", "application/json; charset=utf-8"),
            ("Content-Length", str(len(body))),
        ])
        return [body]

    def _read_json(self, environ) -> dict:
        try:
            length = int(environ.get("CONTENT_LENGTH") or 0)
        except ValueError:
            length = 0
        # 限制请求体 5MB，防止异常大负载
        length = min(length, 5 * 1024 * 1024)
        raw = environ["wsgi.input"].read(length) if length else b""
        if not raw:
            return {}
        return json.loads(raw.decode("utf-8"))

    # ---------- 状态聚合 ----------
    def _state(self) -> dict:
        inputs = self.db.get_inputs()
        sol = self.db.get_solution()
        version = inputs["input_version"]
        # 有效性必须以版本匹配为前提（双保险）
        valid = bool(sol["valid"]) and sol["input_version"] == version \
            and sol["status"] == "found"
        out_sol = {
            "valid": valid,
            "input_version": sol["input_version"],
            "status": sol["status"] if valid or sol["status"] == "found"
                      else sol["status"],
            "note": sol["note"],
            "states_used": sol["states_used"],
            "sheets_used": sol["sheets_used"],
            "saved_at": sol["saved_at"],
        }
        if valid and "payload" in sol:
            out_sol["payload"] = sol["payload"]
        return {
            "inputs": {k: v for k, v in inputs.items()},
            "solution": out_sol,
        }

    # ---------- 更新输入 ----------
    def _update_inputs(self, environ, start_response):
        try:
            payload = self._read_json(environ)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return self._json(400, {"error": "请求体不是合法 JSON"},
                              start_response)
        try:
            clean = validate_inputs(payload)
        except ValidationError as exc:
            return self._json(400, {"error": exc.message, "field": exc.field},
                              start_response)
        # 仅在校验通过后写库；写库即令旧方案失效
        updated = self.db.replace_inputs(clean)
        return self._json(200, {"ok": True, **self._state()}, start_response)

    # ---------- 瑕疵登记 ----------
    def _register_defects(self, environ, start_response):
        """按纸号登记矩形瑕疵区清单（每纸号仅可登记一次）。

        body：{"sheet_no": 2, "regions": [{"x":..,"y":..,"w":..,"h":..}]}
        尺寸非正 / 越界 / 重复纸号一律 400（或 409）且不保存；
        登记成功后输入版本 +1、旧方案立即失效。
        """
        try:
            body = self._read_json(environ)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return self._json(400, {"error": "请求体不是合法 JSON"},
                              start_response)
        if not isinstance(body, dict):
            return self._json(400, {"error": "请求体必须为 JSON 对象"},
                              start_response)
        inputs_row = self.db.get_inputs()
        max_sheets = inputs_row["max_sheets"]
        if isinstance(body.get("sheet_no"), bool) or \
                not isinstance(body.get("sheet_no"), int):
            return self._json(400, {"error": "纸号必须为 1 至张数上限之间的"
                                             "正整数", "field": "sheet_no"},
                              start_response)
        sheet_no = body["sheet_no"]
        if not (1 <= sheet_no <= max_sheets):
            return self._json(400, {
                "error": f"纸号必须在 1 至张数上限 {max_sheets} 之间",
                "field": "sheet_no"}, start_response)
        try:
            regions = validate_defect_regions(
                body.get("regions"), inputs_row["sheet_w"],
                inputs_row["sheet_h"])
        except ValidationError as exc:
            return self._json(400, {"error": exc.message, "field": exc.field},
                              start_response)
        try:
            self.db.register_defects(sheet_no, regions)
        except DuplicateDefectError as exc:
            return self._json(409, {"error": str(exc),
                                    "field": "sheet_no",
                                    **self._state()}, start_response)
        return self._json(200, {"ok": True, **self._state()}, start_response)

    def _delete_defects(self, environ, start_response):
        """删除瑕疵登记：DELETE /api/defects 清空；
        ?sheet_no=N 删除指定纸号（未登记 404）。删除同样令旧方案失效。"""
        qs = parse_qs(environ.get("QUERY_STRING", ""))
        sheet_no = None
        if qs.get("sheet_no"):
            raw = qs["sheet_no"][0]
            try:
                sheet_no = int(raw)
            except ValueError:
                return self._json(400, {"error": "纸号必须为整数",
                                        "field": "sheet_no"}, start_response)
            max_sheets = self.db.get_inputs()["max_sheets"]
            if not (1 <= sheet_no <= max_sheets):
                return self._json(400, {
                    "error": f"纸号必须在 1 至张数上限 {max_sheets} 之间",
                    "field": "sheet_no"}, start_response)
        try:
            self.db.delete_defects(sheet_no)
        except MissingDefectError as exc:
            return self._json(404, {"error": str(exc),
                                    "field": "sheet_no"}, start_response)
        return self._json(200, {"ok": True, **self._state()}, start_response)

    # ---------- 试算 ----------
    def _solve(self, environ, start_response):
        try:
            body = self._read_json(environ)
        except (json.JSONDecodeError, UnicodeDecodeError):
            body = {}
        state = self._state()
        inputs_row = state["inputs"]
        client_version = body.get("input_version")
        if client_version is not None and client_version != \
                inputs_row["input_version"]:
            return self._json(409, {
                "error": "输入已被修改，请使用最新版本参数重试",
                **state,
            }, start_response)

        # state_limit：可选覆盖；缺省用已保存输入中的上限。
        # 仅接受正整数（拒绝小数/布尔/非数字）。
        raw_limit = body.get("state_limit", inputs_row["state_limit"])
        if isinstance(raw_limit, bool) or not isinstance(raw_limit, int) \
                or raw_limit <= 0:
            return self._json(400, {"error": "搜索状态数上限必须为正整数",
                                    "field": "state_limit"}, start_response)
        state_limit = raw_limit
        if state_limit > 100_000_000:
            return self._json(400, {"error": "搜索状态数上限不得超过 100000000",
                                    "field": "state_limit"}, start_response)

        params = self._to_params(inputs_row, state_limit)
        result = Solver(params).solve()

        # found 时服务端先做一次独立几何核验，绝不返回结构非法的方案
        if result["status"] == "found":
            try:
                verify(result, inputs_row)
            except PlanError as exc:
                return self._json(500, {
                    "error": f"求解结果未通过内部核验：{exc}"}, start_response)
        return self._json(200, {
            "ok": True,
            "input_version": inputs_row["input_version"],
            "result": self._public_result(result, inputs_row),
        }, start_response)

    @staticmethod
    def _to_params(inputs_row: dict, state_limit: int) -> Params:
        products = tuple(
            Product(p["name"], p["length"], p["width"], p["qty"],
                    bool(p["rotatable"]))
            for p in inputs_row["products"])
        defects = tuple(
            (no, tuple((r["x"], r["y"], r["w"], r["h"]) for r in regions))
            for no, regions in sorted(
                (inputs_row.get("defects") or {}).items()))
        return Params(
            sheet_w=inputs_row["sheet_w"],
            sheet_h=inputs_row["sheet_h"],
            kerf=inputs_row["kerf"],
            max_sheets=inputs_row["max_sheets"],
            state_limit=state_limit,
            products=products,
            defects=defects)

    @staticmethod
    def _public_result(result: dict, inputs_row: dict) -> dict:
        out = {
            "status": result["status"],
            "states_used": result["states_used"],
            "sheets_used": result["sheets_used"],
            "note": result.get("note", ""),
            "state_limit": inputs_row["state_limit"],
        }
        if result["status"] == "found":
            out["sheets"] = result["sheets"]
            out["counts"] = result["counts"]
            out["sheet_numbers_used"] = result.get("sheet_numbers_used", [])
        return out

    # ---------- 保存方案 ----------
    def _save_solution(self, environ, start_response):
        try:
            body = self._read_json(environ)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return self._json(400, {"error": "请求体不是合法 JSON"},
                              start_response)
        inputs_row = self.db.get_inputs()
        version = inputs_row["input_version"]
        if body.get("input_version") != version:
            return self._json(409, {
                "error": "输入版本已变化，该试算结果已过期，无法保存；"
                         "请基于最新输入重新搜索。",
                **self._state(),
            }, start_response)
        result = body.get("result")
        if not isinstance(result, dict):
            return self._json(400, {"error": "缺少方案数据 result"},
                              start_response)
        try:
            verify(result, inputs_row)
        except PlanError as exc:
            return self._json(400, {"error": f"方案核验失败：{exc}"},
                              start_response)
        try:
            self.db.save_solution(version, result)
        except StaleVersionError as exc:
            return self._json(409, {"error": str(exc), **self._state()},
                              start_response)
        return self._json(200, {"ok": True, **self._state()}, start_response)

    # ---------- 导出裁切单 ----------
    def _export(self, start_response):
        state = self._state()
        inputs_row = state["inputs"]
        sol = state["solution"]
        if not sol.get("valid"):
            msg = ("当前没有与最新输入匹配的有效方案：方案已失效或尚未保存，"
                   "拒绝导出裁切单。请先搜索并保存方案。")
            body = msg.encode("utf-8")
            start_response("409 CONFLICT", [
                ("Content-Type", "text/plain; charset=utf-8"),
                ("Content-Length", str(len(body))),
            ])
            return [body]
        result = sol["payload"]
        # 导出前再次核验（防御性）
        verify(result, inputs_row)
        html = cs.render_cutting_sheet(inputs_row, sol, result)
        data = html.encode("utf-8")
        fname = f"cutting-sheet-v{inputs_row['input_version']}.html"
        start_response("200 OK", [
            ("Content-Type", "text/html; charset=utf-8"),
            ("Content-Disposition",
             f'inline; filename="{fname}"'),
            ("Content-Length", str(len(data))),
        ])
        return [data]

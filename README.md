# 矩形纸张裁切排样 Web 应用

面向小型印刷作坊的矩形纸张裁切（排样）工具。给定**母纸尺寸、刀缝宽度、
张数上限、搜索状态数上限**以及各类**成品的长宽、份数、能否旋转**，
还可按**纸号逐张登记母纸局部瑕疵区（污点）清单**，自动给出：

- **逐张实际用纸的排样图**（SVG，成品上色、刀缝红色、瑕疵区棕色斜纹、
  旋转件标 ↻；可自动跳过无法使用的脏纸）；
- **按执行顺序排列的贯通式裁切步骤**（每刀贯穿当前余料，注明刀向、刀口
  位置、刀长及刀后两块余料尺寸）；
- 每类成品的**需求份数 / 实际成品数**，以及**实际使用的纸号**；
- 可**导出/打印裁切单**（自包含 HTML，标明所依据的输入版本、每张用纸的
  纸号与其瑕疵区）。

后端为 **Python 3 + SQLite**，前端为原生 HTML/CSS/JS。
**仅使用 Python 标准库，零第三方依赖。**

---

## 一、快速启动（Docker Compose 一键启动）

前置：已安装 Docker 与 Docker Compose（`docker compose version` 可用）。

```bash
docker compose up -d --build
```

启动后访问：

- **地址：** <http://localhost:8000>
- **端口：** 容器内固定 `8000`，宿主机默认映射 `8000`。

如需改用宿主机其他端口：

```bash
PORT=9000 docker compose up -d --build
# 访问 http://localhost:9000
```

停止与查看日志：

```bash
docker compose down          # 停止（数据保留在卷 cutting-data 中）
docker compose logs -f       # 跟踪日志
```

首次启动会自动初始化 SQLite 数据库（建表并写入一份可直接试算的示例输入），
无需手工执行任何初始化命令。

### 不用 Compose（仅 Docker）

```bash
docker build -t cutting-planner .
docker run -d --name cutting-planner -p 8000:8000 \
  -v cutting-data:/data cutting-planner
```

---

## 二、本地直接运行（不用 Docker）

要求 Python ≥ 3.10（开发使用 3.11），无需安装任何依赖：

```bash
python3 app/server.py
# 访问 http://localhost:8000
```

---

## 三、配置（环境变量）

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `HOST` | `0.0.0.0` | 监听地址 |
| `PORT` | `8000` | 容器/服务监听端口 |
| `DB_PATH` | `/data/cutting.db`（容器）或绝对路径 | SQLite 数据库文件路径，所在目录会自动创建 |

Compose 中通过 `PORT`（宿主机侧）调整映射端口；数据库保存在具名卷
`cutting-data`（挂载到容器 `/data`）中，容器重建后数据仍在。

### 输入取值约束（服务端强校验，非法输入不落库）

- 母纸长、宽；成品长、宽：**正整数毫米**（1–10000）；
- 刀缝宽度：**非负整数毫米**（0–1000，且必须小于母纸长宽）；
- 成品份数、张数上限、搜索状态数上限：**正整数**；
- 成品名称非空、不重复；成品最多 12 种；
- 瑕疵区：左上角坐标为**非负整数**、宽高为**正整数**（毫米），
  必须整体落在母纸内（允许贴边）；按纸号（1～张数上限）登记，
  每纸号仅可登记一次，同纸可有相互重叠的多个瑕疵区。

任何非法输入（含小数、负数、0、非数字、瑕疵尺寸非正/越界、重复纸号）
都会被拒绝并返回 400 与字段级错误提示，**不会写入数据库**。

---

## 四、使用流程与关键规则

1. 在页面填写母纸/刀缝/张数上限/搜索状态数上限和成品清单，点击
   **“保存输入并搜索”**。
2. 每次保存输入都会生成一个**单调递增的输入版本号 vN**，并使任何旧方案
   **立即失效**。
3. 搜索结果有三种，界面与 API 明确区分：
   - **找到可行方案（found）**：满足全部份数的排样（找到即可，不追求最优）。
     检查无误后点 **“保存此方案”**，随后可 **“导出裁切单”**。
   - **已穷尽，确认无解（infeasible）**：在当前输入与旋转限制下已穷尽搜索，
     不存在可行贯通刀切法（含面积必然超限等确定性预检）。
   - **搜索预算耗尽（budget_exhausted）**：在给定状态数内**未穷尽**搜索，
     **不能据此声称无解**；可**增大“搜索状态数上限”后重试**。
4. **导出裁切单**会标明所依据的输入版本；仅当方案有效（已保存且版本与
   当前输入一致）时才允许导出，否则返回 409 并拒绝导出。
5. “仅按当前输入试算”不会保存输入，只用于探索；试算方案需先“保存此方案”
   才能导出裁切单。

### 裁切模型（满足题目约束）

- 每一刀都是沿直线**贯穿当前矩形余料**的贯通刀（guillotine cut），一刀
  把该余料分成两块；
- 切分时**扣除刀缝宽度**（两块余料之间留出等宽刀缝，刀缝不是成品）；
- 成品**不得重叠、不得越界**；成品叶子与其所在末端余料尺寸完全相等；
- 严格遵守**旋转限制**（不允许旋转的成品只能按原长宽摆放）；
- **瑕疵避让**：母纸可按纸号登记矩形瑕疵区（污点）。
  - **成品矩形不得与任何瑕疵区发生正面积重叠**（仅边界相切允许）；
  - **刀路、刀缝与废料块可以穿过瑕疵区**——瑕疵最终只会被切进废料；
  - 求解时可**选择使用或跳过具体纸号**（带瑕疵纸若无法利用会被自动
    跳过），结果与裁切单给出**实际使用的纸号**；
  - **未登记瑕疵的纸号视为完好**。
- 搜索刀口位置按“成品边长 / 镜像边长”及“**瑕疵边缘 / 其镜像**”
  归一化枚举，依据 guillotine normal-patterns 定理的扩展保证：**若存在
  满足瑕疵避让的可行贯通刀排样，就不会因位置归一化而漏掉**（在状态
  预算内）。

服务端在**保存与导出**前还会对方案做一次独立于求解器的几何核验
（越界、重叠、旋转、刀缝侵入、**成品-瑕疵正面积重叠**、排样页瑕疵清单
与当前输入一致、刀路贯通与区域自洽、实际数量=需求、用纸号合法不重复），
任何被篡改或不自洽的方案都会被拒绝。

---

## 五、HTTP API（供脚本/集成调用）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/state` | 当前输入（含版本、`defects`）+ 方案状态（有效则内含方案） |
| POST | `/api/inputs` | 校验并替换输入（body 可带 `defects`）；成功后版本 +1、旧方案失效；非法 400 |
| POST | `/api/defects` | 按纸号登记一次瑕疵区清单；尺寸非正/越界 400、重复纸号 409；成功后版本 +1 |
| DELETE | `/api/defects` | 删除瑕疵登记：`?sheet_no=N` 删单纸（未登记 404），缺省清空；成功后旧方案失效 |
| POST | `/api/solve` | 按当前版本输入试算，body 可覆盖 `state_limit`；返回 `found/infeasible/budget_exhausted` |
| POST | `/api/solution` | 保存试算方案（服务端几何+瑕疵独立核验 + 版本核对） |
| DELETE | `/api/solution` | 废弃当前方案 |
| GET | `/api/cutting-sheet` | 导出有效方案裁切单 HTML（含实际纸号与各纸瑕疵区）；失效/无方案返回 409 |

### 瑕疵区数据格式

坐标单位均为**整数毫米**，原点为母纸左上角，x 向右、y 向下。
`regions` 是至少含一个矩形的数组（同一纸可有相互重叠的多个区）：

```json
{ "sheet_no": 2,
  "regions": [
    {"x": 100, "y": 50, "w": 30, "h": 20},
    {"x": 105, "y": 55, "w": 40, "h": 10}
  ] }
```

- 登记接口：`POST /api/defects`，每个纸号**只能登记一次**（重复返回 409，
  需先 `DELETE /api/defects?sheet_no=N` 再登记）；
- 也可在 `POST /api/inputs` 一次性随输入提交 `defects`，接受两种形态：
  - 对象：`{"1": [区...], "3": [区...]}`（注意 JSON 对象重复键会被
    解析器折叠，如需对同纸分批追加请改用专用登记接口）；
  - 数组：`[{"sheet_no":1,"regions":[...]}, ...]`（重复纸号必被拒）。

`POST /api/solve` 请求示例：

```json
{ "input_version": 3, "state_limit": 1000000 }
```

返回（节选）：

```json
{ "input_version": 3,
  "result": { "status": "found", "sheets_used": 2,
              "sheet_numbers_used": [1, 3],
              "states_used": 134053, "sheets": [ ... ], "counts": [ ... ] } }
```

`status` 为 `infeasible` 表示已穷尽确认无解；为 `budget_exhausted`
表示预算耗尽、**不是**无解，请加大 `state_limit` 重试。
每个 `sheets[i]` 含 `sheet_no`（实际物理纸号）、`defects`（该纸登记的
瑕疵区）、`leaves`（成品叶子坐标）、`gaps`（刀缝）与 `cuts`（裁切步骤）。

### 命令行调用示例（curl）

```bash
# 1) 保存基础输入（也可在此一并提交 defects）
curl -s -X POST localhost:8000/api/inputs -H 'Content-Type: application/json' -d '{
  "sheet_w": 450, "sheet_h": 320, "kerf": 3, "max_sheets": 6,
  "state_limit": 200000,
  "products": [
    {"name":"卡片A","length":120,"width":80,"qty":8,"rotatable":true},
    {"name":"卡片B","length":200,"width":60,"qty":4,"rotatable":false}
  ]}'

# 2) 给 2 号纸登记两处重叠瑕疵（未登记的纸视为完好）
curl -s -X POST localhost:8000/api/defects -H 'Content-Type: application/json' -d '{
  "sheet_no": 2,
  "regions": [{"x":100,"y":50,"w":30,"h":20},{"x":105,"y":55,"w":40,"h":10}]}'

# 3) 试算（求解器自动避让瑕疵、必要时跳过脏纸）
curl -s -X POST localhost:8000/api/solve -H 'Content-Type: application/json' \
  -d '{"input_version": 2}'

# 4) 保存找到的方案（服务端独立复核瑕疵避让与几何约束）
curl -s -X POST localhost:8000/api/solution -H 'Content-Type: application/json' \
  -d '{"input_version": 2, "result": <上一步 result>}'

# 5) 删除 2 号纸登记（旧方案随即失效）；清空全部用不带参数的同路径 DELETE
curl -s -X DELETE 'localhost:8000/api/defects?sheet_no=2'

# 6) 导出/打印裁切单（HTML，含实际纸号与瑕疵区标示）
curl -s localhost:8000/api/cutting-sheet -o cutting-sheet.html
```

---

## 六、项目结构

```
.
├── Dockerfile                 # 零第三方依赖的镜像
├── docker-compose.yml         # 一键启动（含数据卷与健康检查）
├── requirements.txt           # 说明文件（无需安装第三方包）
├── README.md
└── app/
    ├── server.py              # 标准库线程 HTTP 服务器入口（读环境变量、初始化DB）
    ├── app.py                 # WSGI 路由 / API
    ├── validation.py          # 输入强校验（非法不落库）
    ├── solver.py              # 贯通刀排样求解器（精确目标填充 + 状态预算）
    ├── verify.py              # 服务端独立方案几何核验
    ├── cutting_sheet.py       # 裁切单 HTML/SVG 导出
    ├── database.py            # SQLite 持久化（输入版本 + 方案失效）
    └── static/                # 浏览器界面
        ├── index.html
        ├── style.css
        └── app.js
```

---

## 七、自带测试（开发用，不打进镜像）

仓库根目录提供求解器与交叉验证脚本（构建镜像时被 `.dockerignore` 排除）：

```bash
python3 test_solver.py             # 典型用例 + 独立几何/刀路/瑕疵合法性校验
python3 test_fuzz.py               # 60 组随机用例：与独立朴素 guillotine 判定器交叉验证
python3 test_fuzz_kerf.py          # 40 组带刀缝随机用例交叉验证
python3 test_fuzz_defects.py       # 40 组带瑕疵随机用例交叉验证（朴素器全位置枚举）
python3 test_fuzz_kerf_defects.py  # 30 组刀缝+瑕疵联合随机用例交叉验证
```

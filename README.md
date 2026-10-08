# 油井压裂评价微服务 · Oil Fracturing Evaluator

> 以 **Draw.io 流程图作为评价模板**的规则评价微服务：接收标准 JSON，按声明式规则做**三态求值**，把结果以「节点配色 + 参数值回写」渲染回原流程图并输出 HTML / 结构化 JSON。**流程结构与规则逻辑解耦、原始 `.drawio` 只读、阈值/样式/算子全部配置驱动。**

- **核心链路**：`JSON 输入 → 三态规则求值 → 节点状态计算 → 结果样式回写 → HTML / JSON 输出`
- **分层职责**：*Draw.io 决定「流程怎么走」，规则配置决定「什么时候成立」，参数配置决定「阈值是多少」，Renderer 决定「如何展示」。*
- **权威依据**：`油井压裂评价微服务需求文档（整合版）.md`（本仓库唯一需求基准）；`后续开发流程.md` 为其执行配套。

---

## 目录
- [核心特性](#核心特性)
- [技术栈](#技术栈)
- [快速开始](#快速开始)
- [API 参考](#api-参考)
- [参数结构（输入契约）](#参数结构输入契约)
- [求值语义与结果配色](#求值语义与结果配色)
- [演示指南](#演示指南)
- [模板与配置](#模板与配置)
- [目录结构](#目录结构)
- [测试与质量门禁](#测试与质量门禁)
- [开发与路线图](#开发与路线图)
- [约定与边界](#约定与边界)

---

## 核心特性

- **三态（Kleene）求值**：`TRUE / FALSE / UNKNOWN`；`AND/OR` 短路；自底向上 `P → R → C → 根` 拓扑聚合。缺失数据记 `UNKNOWN`，与 `FALSE` 严格区分。
- **业务码为唯一主键**：全链路以节点 `value` 前缀业务码（`P001`/`R06`/`C001`…）为锚点，`mxCell.id` 仅作运行时定位，**禁止**作引用锚点。
- **配置驱动、零硬编码**：比较算子、聚合关系、阈值、结果色、值回写格式均来自 `templates/*/` 下的 YAML。改数值/改样式只改配置，无需改代码（有回归测试保障）。
- **原始模板只读**：评价结果只写入内存模型与输出，**绝不覆盖** `.drawio` 源文件。
- **内容协商输出**：默认返回内嵌 draw.io `graphViewer` 的 **HTML**；`?result=json`（或 `Accept: application/json`）返回结构化三态 **JSON**。
- **黄金端到端回归 + CI 门禁**：以业务码为主键的稳定快照锁定行为；`ruff` → `mypy` → `pytest` 在 push/PR 上强制执行。

---

## 技术栈

| 项 | 选型 | 说明 |
|---|---|---|
| 语言/运行时 | Python ≥ 3.11 | `from __future__ import annotations` 全量类型注解 |
| Web 框架 | Flask ≥ 3.0 | 应用工厂 `create_app` |
| XML 解析 | `defusedxml` | **强制**，禁 DTD / 外部实体（需求 V30） |
| 配置解析 | `PyYAML` | 加载模板 5 份 YAML |
| 输入校验 | `pydantic` | 字段 / 类型 / 单位 / 范围 |
| 测试 | `pytest` + `pytest-cov` | 单元 / 集成 / 黄金回归 |
| 质量 | `ruff` + `mypy` | lint + 类型检查（CI 执行） |

依赖清单：运行依赖见 [`requirements.txt`](requirements.txt)，开发/测试依赖见 [`requirements-dev.txt`](requirements-dev.txt)。

---

## 快速开始

```bash
# 1) 安装依赖
python -m venv .venv && source .venv/bin/activate   # Windows: py -3.11 -m venv .venv; .venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt

# 2) 启动服务（默认 :5000，debug）
python app.py                # 或： flask --app app run

# 3) 发起一次评价（HTML）
curl -X POST http://127.0.0.1:5000/api/v1/evaluate \
  -H "Content-Type: application/json" \
  --data @templates/oil_fracturing_v1/sample_request.json \
  -o out/demo.html

#    结构化三态 JSON
curl -X POST "http://127.0.0.1:5000/api/v1/evaluate?result=json" \
  -H "Content-Type: application/json" \
  --data @templates/oil_fracturing_v1/sample_request.json
```

> Windows PowerShell 下调用示例：
> ```powershell
> Invoke-WebRequest -Method Post http://127.0.0.1:5000/api/v1/evaluate `
>   -ContentType 'application/json' `
>   -InFile templates\oil_fracturing_v1\sample_request.json `
>   -OutFile out\demo.html
> ```
> 输出的 HTML 通过 CDN 加载 draw.io `viewer-static.min.js` 渲染图形，**查看流程图需联网**。

---

## API 参考

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/health` | 健康检查 → `{"status":"ok"}` |
| `GET` | `/api/v1/templates` | 已加载模板清单（`id`/`version`/`rules_version`/`nodes`），便于排障与验收 |
| `POST` | `/api/v1/templates/inspect` | 检视任意 .drawio（JSON `path`，只读无需注册；另有 CLI `python -m core.inspector`） |
| `POST` | `/api/v1/templates/<id>/flow` | 热部署（F3）：multipart `file` 原位替换 flow.drawio，先验后写、落盘持久，旧图备份 `flow.drawio.bak`；失败磁盘不动 |
| `POST` | `/api/v1/evaluate` | 评价主入口。默认 `text/html`；`?result=json` 或 `Accept: application/json` 返回结构化 JSON |

> **开发态 CLI 工具**（脱离服务运行）：
> - `python -m core.inspector <file.drawio> [--json]` —— 任意图只读体检（码清单/重复码/悬空边）
> - `python -m core.scaffolder <file.drawio> --id <新模板id> [--name 中文名] [--out-root templates]` —— 生成新模板五件套**草稿**：业务码分组、R/C children（按图连线拓扑自动探测方向推导）、聚合算子按新规定档（R=AND/C=OR）、阈值符号表自洽；P 比较式的字段名/运算符/阈值真值落 `TODO(human)` 注释待人工确认，补完后按五件套流程注册；拒绝覆盖已存在目录

**JSON 响应（`?result=json`）**

```jsonc
{
  "template_id": "oil_fracturing_v1",
  "well_id": "W001",
  "rules_version": "2026.05",
  "root_state": "FALSE",                 // 最终结论（根节点 C001）
  "node_states": { "P001": "TRUE", "P003": "FALSE", "R06": "TRUE", "C001": "FALSE", "...": "..." },
  "colors":      { "P001": "#00B050", "C001": "#FF0000", "...": "..." },  // 仅 P/C 着色，R 不列入
  "validation":  { "ok": true, "issues": [] }
}
```

**错误码**

| HTTP | `error` | 触发条件 |
|---|---|---|
| `400` | `invalid_request` | 请求缺少 `template` 字段 |
| `404` | `template_not_found` | `template` 不在已加载注册表 |
| `422` | `template_unavailable` | 模板加载/结构校验失败（`ConfigError`） |

> 鲁棒性：非法/缺失的**输入值**不会导致 5xx——相应节点判 `UNKNOWN`（黄底、不回写脏值），并在 `validation.issues` 记录 `location/kind/reason`。

---

## 参数结构（输入契约）

请求体以**业务码**传值（`node_values`）：每个节点值为 **标量或数组**，数组按 `rules.yaml` 去重后的**操作数顺序位置映射**（也兼容按操作数名的映射）。比较右值一律为阈值占位符（可经 `thresholds` 临时覆盖，缺省用 `parameters.yaml` 占位默认值）；`basis` 通道已随 R2 改版退役。节选自 [`sample_request.json`](templates/oil_fracturing_v1/sample_request.json)：

```jsonc
{
  "template": "oil_fracturing_v1",   // 必填
  "well_id": "W001",                 // 可选
  "node_values": {                   // 必填（字段必须提供，可空对象）
    "P001": [5, 12.3],        // 数组按操作数顺序位置映射：[reservoir_layers, converted_thickness]
    "P016": [110, false],     // [casing_inner_diameter, casing_damage]，布尔按位置给出
    "P006": 20,               // 单操作数节点直接给标量
    "...": {}
  },
  "thresholds": { "x3": 3, "x4": 5, "x12": 10, "x13": 70 }           // 覆盖 parameters.yaml 占位
}
```

### 顶层字段（入参契约，2026-10-05 拍板）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `template` | string | ✅ | 模板 ID，须命中已加载注册表（否则 `404`） |
| `node_values` | object | ✅ | 按 **P 业务码** 传本井实测/派生值：键=节点码，值=**标量 / 数组（按操作数顺序）/ 兼容 name 映射**；字段内部的物理量可缺省 |
| `well_id` | string | ⬜ | 井号，仅用于标题与结果追溯 |
| `thresholds` | object | ⬜ | 覆盖 `parameters.yaml` 的阈值占位默认值（`x1`…`x_p012_block` 符号） |

> 除 `template` 与 `node_values` 外均为可选；`node_values` **字段必须提供**（可传空对象 `{}` → 全节点 `UNKNOWN`，仍正常返 200），但字段内缺省的具体量 → 对应判据 `UNKNOWN`（不报错）。数值为 `int/float`；布尔用 `true/false`；类型/单位/范围由 `parameters.yaml` 定义、`InputValidator` 校验，非法值被丢弃并记入 `validation.issues`。~~`basis`~~ 已随 R2 改版退役（右值全面阈值化，服务不感知聚合口径）。

### `node_values` —— P 节点判据（操作数名 → 值）

| P 码 | 判据含义 | 操作数（传值键，单位） | 比较 | 右值：阈值符号 | 节点内合并 |
|---|---|---|---|---|---|
| `P001` | 可压层数 且 折算厚度 | `reservoir_layers`(层,int 0~100) · `converted_thickness`(m) | `>` | `x3` · `x4` | AND |
| `P002` | 全井连通厚度 | `total_connected_thickness`(m) | `>` | `x1` | AND |
| `P003` | 周围油井日产油（右值已塌缩） | `neighbor_avg_oil_daily`(t) | `>` | `x_p003` | AND |
| `P005` | 含水率够低 | `water_cut`(% 0~100) | `<` | `x13` / `x_p005_region` / `x_p005_net` | OR |
| `P006` | 初期−目前日产油之差 | `oil_diff`(t) | `>` | `x12` | AND |
| `P007` | 产液强度低 | `liquid_intensity`(t/d.m) | `<` | `x8` / `x_p007_region` / `x_p007_net` | OR |
| `P008` | 日产液低 | `liquid_daily`(t) | `<` | `x6` / `x_p008_region` / `x_p008_net` | OR |
| `P009` | 日产油低 | `oil_daily`(t) | `<` | `x10` / `x_p009_region` / `x_p009_net` | OR |
| `P010` | 地层压力（vs 区块均值，已塌缩） | `formation_pressure`(MPa) | `>` | `x_p010_block` | AND |
| `P011` | 流压（1005 修订版仅留此分支） | `flow_pressure`(MPa) | `<` | `x_p011_flow_block` | AND |
| `P012` | 动液面大于区块均值（1005 新增） | `inflow_performance`(m) | `>` | `x_p012_block` | AND |
| `P013` | 连通开井数 | `connected_open_wells`(口,int) | `>` | `x20` | AND |
| `P015` | 配注完成率 | `injection_completion_rate`(%) | `>` | `x22` | AND |
| `P016` | 套管通径 且 无严重损坏 | `casing_inner_diameter`(mm) · `casing_damage`(bool) | `>` / `==` | `casing_min` / 字面量 `false` | AND |

> 右值全部为阈值符号（定义于 `parameters.yaml`，请求 `thresholds` 可覆盖）；后缀 `region`/`net`/`block` = 全区/井网/区块口径由**外部算好后塌缩的单值**，服务不感知、不统计。布尔字面量比较（P016）不参与阈值替换。完整语义见 `rules.yaml` 各 P 节点 `operands`。

### `node_values` 传值形态（标量 / 数组）

数组的**位序 = 上表“操作数”列的去重顺序**；单操作数节点可直接给标量。

| P 码 | 位置含义 → 传值示例 |
|---|---|
| `P001` | `[reservoir_layers, converted_thickness]` → `[5, 12.3]` |
| `P016` | `[casing_inner_diameter, casing_damage]` → `[110, false]` |
| 其余全部 P 码 | 单操作数节点直接给标量，如 `"P012": 700`、`"P011": 10` |

> 数组偏短 → 尾部记 `missing`；偏长 → 记 `type` 并忽略多余位；亦接受 `{"P001": {"reservoir_layers": 5, "converted_thickness": 12.3}}` 的按名映射（向后兼容）。多分支比较（P005/P007/P008/P009 的 OR）共用同一个实测值，无需重传。

### `thresholds` —— 阈值符号（默认均为占位值，与代码解耦）

| 符号 | 默认 | 单位 | 含义 | 符号 | 默认 | 单位 | 含义 |
|---|---|---|---|---|---|---|---|
| `x1` | 5 | m | 连通厚度 | `x10` | 3 | t | 日产油 |
| `x3` | 3 | 层 | 可压层数 | `x12` | 10 | t | 日产油之差 |
| `x4` | 5 | m | 折算厚度 | `x13` | 70 | % | 含水率 |
| `x6` | 10 | t | 日产液 | `x20` | 2 | 口 | 连通开井数 |
| `x8` | 2 | t/d.m | 产液强度 | `x22` | 80 | % | 配注完成率 |
| `casing_min` | 105 | mm | 套管通径下限 | `x_p003` | 9 | t | P003 邻井均值×系数（已塌缩） |
| `x_p005_region` | 47 | % | P005 全区基准 | `x_p005_net` | 45 | % | P005 井网基准 |
| `x_p007_region` | 1.6 | t/d.m | P007 全区基准 | `x_p007_net` | 1.5 | t/d.m | P007 井网基准 |
| `x_p008_region` | 12.5 | t | P008 全区基准 | `x_p008_net` | 12 | t | P008 井网基准 |
| `x_p009_region` | 3 | t | P009 全区基准 | `x_p009_net` | 2.5 | t | P009 井网基准 |
| `x_p010_block` | 16 | MPa | P010 区块平均地层压 | `x_p011_flow_block` | 12 | MPa | P011 区块平均流压 |
| `x_p012_block` | 600 | m | P012 区块平均动液面 | — | — | — | — |

> 均为占位默认（待业务核实，B 组）；上线真实值确定后**只改 `parameters.yaml`**（或在请求 `thresholds` 中临时覆盖），求值逻辑不受影响。旧机制符号（系数 `x7`/`x9`/`x11`、`x_p005_y`、`x_neighbor_factor`、`basis` 通道）均已随 R2 改版退役。

仓库附带多份边界示例，便于自测与演示：

| 文件 | 场景 | 预期 |
|---|---|---|
| `sample_request_true.json` | 全部达标（14 个 P 均判真） | 根结论 `C001=TRUE`、P/C 全绿、符号换数值 |
| `sample_request.json` | 完整黄金样例（`P003` 邻井产油不足判假） | 根 `C001=FALSE`、三色分明、阈值符号替换 |
| `sample_request_missing.json` | 参数缺失（部分填） | 省略项 → `UNKNOWN` |
| `sample_request_invalid.json` | 越界 / 非数值 / 非法布尔 | 记 `issues`、节点 `UNKNOWN`、不抛错 |
| `sample_request_minimal.json` | 空 `node_values` 对象 | 全 `UNKNOWN`、`validation.ok=true` |

---

## 求值语义与结果配色

- **节点种类**：`P`=输入/谓词 · `R`=关系/中间规则 · `C`=结论（根节点为 `C001`，代表最终评价）。
- **三态结果样式**（2026-10-08 通道调整）：`TRUE` 绿 `#00B050` · `FALSE` 红 `#FF0000` · `UNKNOWN` 黄 `#FFFF00`——**以“边线变色 + 加粗”表达**（`style.yaml: result_style.channel: stroke`）；节点背景色保留模板原业务含义，不再承载结果。旧 `fill` 通道（换底色）仍被渲染器支持，向后兼容。
- **样式施加范围**：结果样式**仅施加于 `P`/`C` 节点**；关系节点 `R` 保持模板原样。该行为由 `style.yaml: result_style.color_kinds: [P, C]` 配置驱动（改回 `[P, R, C]` 或留空即全覆盖，无需改代码）。
- **参数值回写**：把传入的实测值以全角 `（值）` 插到 `P` 节点标签比较运算符**之前**（如 `P009 本井日产油（2） < 3 t`）；布尔回写 `（是）/（否）`；`UNKNOWN` 不插入；幂等（同值不重复）。
- **不改版式**：输出 HTML 保持原图位置、尺寸、文字、连线、箭头与未涉及样式。

---

## 演示指南

> 三种由浅入深的演示方式。输出 HTML 内嵌 draw.io `graphViewer`，**查看流程图需联网**。

### 方式 A · 接口调用 + 浏览器看结果图

```bash
python app.py                                   # 监听 :5000
mkdir -p out
curl -X POST http://127.0.0.1:5000/api/v1/evaluate \
  -H "Content-Type: application/json" \
  --data @templates/oil_fracturing_v1/sample_request.json -o out/demo.html

python -m http.server 8137 --directory out       # 另开终端
# 浏览器打开 http://127.0.0.1:8137/demo.html
```

### 方式 B · 结构化三态（无需联网）

```bash
curl -X POST "http://127.0.0.1:5000/api/v1/evaluate?result=json" \
  -H "Content-Type: application/json" \
  --data @templates/oil_fracturing_v1/sample_request.json
```

重点字段：`root_state`（最终结论）、`node_states`（全部 37 业务码三态）、`colors`（仅 P/C 的实际着色）、`validation.issues`。

### 方式 C · 四场景对比（覆盖缺失 / 非法）

| 场景 | 数据文件 | 根结论 | UNKNOWN 数 | issues |
|---|---|---|---|---|
| 完整 | `sample_request.json` | `FALSE` | 0 | 0 |
| 缺失 | `sample_request_missing.json` | `UNKNOWN` | 30 | 2（missing） |
| 非法 | `sample_request_invalid.json` | `UNKNOWN` | 36 | 3（range/type） |
| 极简 | `sample_request_minimal.json` | `UNKNOWN` | 37 | 0 |

```bash
for f in sample_request sample_request_missing sample_request_invalid sample_request_minimal; do
  echo "== $f =="
  curl -s -X POST "http://127.0.0.1:5000/api/v1/evaluate?result=json" \
    -H "Content-Type: application/json" \
    --data @templates/oil_fracturing_v1/$f.json | python -c "import sys,json;d=json.load(sys.stdin);print('root=',d['root_state'],'UNKNOWN=',sum(v=='UNKNOWN' for v in d['node_states'].values()),'issues=',len(d['validation']['issues']))"
done
```

### 演示「配置解耦」—— 零代码改动翻转结果

只改配置、重启服务即可看到输出变化（`tests/golden/test_config_decoupling.py` 同口径验证）：

- **改结果色**：`style.yaml → result_style.TRUE.strokeColor`（如 `#123456`）→ P/C 的 TRUE 节点边线变色，三态与背景色不变。
- **改着色范围**：`style.yaml → result_style.color_kinds` 由 `[P, C]` 改 `[P, R, C]` → 关系节点 R 也开始着色。
- **改判据算子**：`rules.yaml → P006.operands[0].op` 由 `>` 改 `<` → `P006` 结果翻转，并沿 `R11 → C019 → …` 向上传播。
- **改阈值**：`parameters.yaml → thresholds.x12.value` 由 `10` 改 `1`（或请求 `thresholds.x12=1`）→ 相关节点成立性变化。

> 全部演示均**不写回** `flow.drawio` 原始模板。

---

## 模板与配置

一个模板 = `templates/<template_id>/` 下一个自包含目录：

| 文件 | 作用 |
|---|---|
| `template.yaml` | 元数据：模板 ID/版本、文件清单、`.drawio` 渲染约定、业务码识别正则 |
| `flow.drawio` | 流程图模板（**只读源**，明文 `mxGraphModel`） |
| `rules.yaml` | 声明式规则：P 节点算子/操作数，R/C 聚合算子（`AND`/`OR`）与子节点，根结论 |
| `parameters.yaml` | 参数语义字典（量/单位/范围）与阈值占位默认值 |
| `style.yaml` | 结果样式（三态 → `strokeColor` + 加粗，`channel` 可在 stroke/fill 切换）、`color_kinds`、值回写样式、HTML 输出参数 |

**新增一个模板**：复制 `templates/oil_fracturing_v1/` 目录、改 5 份配置与 `flow.drawio` 即可被 `TemplateManager` 自动发现加载，无需改动服务代码。

---

## 目录结构

```text
app.py                     # Flask 工厂 create_app + 路由 / 内容协商 + _run_pipeline 编排
core/                      # 业务内核（不依赖 Flask）
  drawio_parser.py         #   .drawio → TemplateModel（顶点/边/业务码映射）
  expr.py                  #   参数上下文构建 + 表达式/比较求值
  models.py                #   共享运行时类型（TriState / Node / Edge / TemplateModel / EvaluationResult）
  renderer.py              #   着色 + 值回写标注 + HTML/JSON 组装（配置驱动）
  rule_engine.py           #   三态 Kleene 逻辑 + 自底向上 P→R→C→根 聚合
  template_manager.py      #   模板加载 / 解析缓存（原始 .drawio 只读）
  template_validator.py    #   模板结构校验（Phase 2：环检 / 码-规则一致性 / XXE 等）
  validator.py             #   入参校验（InputValidator）
config_loader/             # 配置加载子系统（loader / models / exceptions）
templates/oil_fracturing_v1/   # 模板目录：5 份配置 + flow.drawio + sample_request*.json
tests/                     # conftest.py + unit/ + golden/（黄金回归 & 配置解耦）
.github/workflows/ci.yml   # CI 质量门禁
```

---

## 测试与质量门禁

```bash
python -m pytest -p no:cacheprovider                 # 全量（含覆盖率，对 core/config_loader/app）
python -m pytest tests/unit/test_app.py -q           # 单文件
python -m pytest tests/golden -p no:cacheprovider --update-golden   # 改动求值/着色/回写后重生成黄金快照

ruff check .                                         # lint（line-length=100；E,F,I,UP,B,ANN）
mypy core config_loader app.py                       # 类型检查
```

- **黄金快照** `tests/golden/*.sample.json` 以业务码为主键、`sort_keys` 稳定输出，与随机 `mxCell.id` 无关。
- **配置解耦回归**：临时副本改 `style/rules/parameters` → 断言输出按预期翻转，且原始模板文件保持不动。
- **CI**（[`.github/workflows/ci.yml`](.github/workflows/ci.yml)）：`push`/`PR` 上执行 `ruff check` → `mypy`（当前 `continue-on-error`）→ `pytest`。

---

## 开发与路线图

| 阶段 | 内容 | 状态 |
|---|---|---|
| **Phase 1** | 端到端 MVP：解析 → 三态求值 → 结果色 + 值回写 → HTML/JSON → `POST /api/v1/evaluate` + 黄金回归 + CI | ✅ 完成 |
| **Phase 2** | 模板结构校验落地（需求第 7 章 V01–V31：环检 V12、码-规则一致性 V20/V21、children 引用有效 V22、阈值已定义 V23、XXE/压缩 V30/V31、业务码唯一 V01 等） | 🔜 计划中 |
| **Phase 3** | 多模板并存、热部署与缓存失效、版本管理与结果追溯、SVG 输出 | 🔜 计划中 |

---

## 约定与边界

- **服务只消费不计算数据**：派生量与跨井/区域聚合（`avg_*`、`block_avg_*` 等）一律由外部传入，本服务不做公式派生或统计聚合。
- **阈值为占位默认值**：`parameters.yaml` 中的阈值与业务口径待确认后更新，与代码完全解耦（改数值只改本文件）。
- **原始模板只读**：任何评价结果都不写回 `.drawio` 源文件；输出仅存在于内存模型与生成的 HTML/JSON。
- **本地开发环境**：Windows PowerShell 语句分隔使用 `;`（不支持 `&&`）。

---

*本项目随附完整需求与设计文档（见仓库根目录 `*.md`）。如需引用具体行为条款，请对照 `油井压裂评价微服务需求文档（整合版）.md` 对应章节。*

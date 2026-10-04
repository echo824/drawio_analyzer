# Phase 1 — 核心链路 MVP · Issue 清单

> 目标（源自《后续开发流程.md》Phase 1）：单模板 `oil_fracturing_v1` 端到端跑通
> **输入 → 三态求值 → `fillColor` 回写 → HTML 输出**，无任何硬编码阈值。
> 每个 Issue 均可独立提交、独立评审；标签中的 `ISSUE-1.x` 与 `core/*.py` 里的 `TODO(Phase 1/ISSUE-1.x)` 对应。
> DoD 通用要求：实现 + 单测通过 + `ruff`/`mypy` 干净 + 黄金样例 `sample_request.json` 回归通过。

## 依赖关系（建议实现顺序）

```text
1.1 parse ─┐
           ├─→ 1.4 P 求值 ─→ 1.5 R/C 聚合 ─→ 1.8 API 串联 ─→ 1.9 黄金端到端
1.2 阈值解析 ┘                                     ↑
1.3 输入校验 ──────────────────────────────────────┘
1.6 fillColor 回写 ─┐
1.7 值回写标注 ─────┴─→ 1.8
```

---

## Epic A · 解析与数据

### ISSUE-1.1 · Draw.io 解析器（fill `core.drawio_parser`）
- **任务**：用 `defusedxml` 解析 `flow.drawio`；提取 nodes(id/type/text/position/size/style)/edges(source/target/style)；按 `node_code_pattern` 从 `value` 前缀提取并归一业务码，构建 `code_map`（业务码→当前 mxCell.id）；产出内部 `TemplateModel`。
- **需求**：§3、§10.3、§3.4（大小写归一 p003→P003）。
- **涉及文件**：`core/drawio_parser.py`（`DrawioParser.parse`）、`core/models.py`。
- **验收**：
  - 解析真实模板得到节点数与 config 注册表一致（≈37 业务码节点）；
  - `extract_business_code("p003 …") == "P003"`；无 `value` 前缀节点 → `UNCLASSIFIED`；
  - 富文本/HTML 实体在 text 中正确解码且不丢失；
  - 含 DTD/外部实体的样例被拒绝（V30）。
- **DoD**：单测覆盖上述；不改动原始文件。

### ISSUE-1.2 · 阈值与基准解析（fill `config_loader` 求值侧支持）
- **任务**：合并"默认阈值(`parameters.yaml`)+请求覆盖(`thresholds`)"；解析 `basis`；将 `expr`（如 `@avg_water_cut - x_p005_y`、`x9 * @avg_liquid`）中的 `@引用`/符号名解析为数值上下文。
- **需求**：§5.1、§5.3、§11.4（默认值 + 请求覆盖）。
- **涉及文件**：新增 `core/expr.py` 或 `config_loader` 内工具。
- **验收**：`x_p005_y` 默认 8；请求传 `{"x13":60}` 时覆盖生效；缺失基准不报错（交由 1.4 置 UNKNOWN）。

### ISSUE-1.3 · 输入 Schema 校验（fill `core.validator`）
- **任务**：按 `quantities` 对 `node_values` 校验类型/单位/范围；缺项或非法 → 该操作数置"缺失哨兵"（供引擎判 UNKNOWN），不抛错；`required` 量缺失按策略处理。
- **需求**：§5.5、§2.1。
- **涉及文件**：`core/validator.py`（`InputValidator.validate_node_values`）。
- **验收**：越界/错类型被标记；合法样例原样通过；`casing_damage` 布尔按 bool 校验；输出映射保持"按节点 ID + 操作数 name"结构。

---

## Epic B · 规则求值（三态）

### ISSUE-1.4 · P 节点求值（fill `core.rule_engine` 叶层）
- **任务**：拓扑排序（检出环）；对每个 P，按 `operands` 逐项比较（左值来自校验后的 `node_values`，右值用 1.2 解析）；节点内按 `logic`(AND/OR) 合并；任一子条件缺输入 → UNKNOWN，短路优先。
- **需求**：§4.2、§4.4（Kleene）、§4.5。
- **涉及文件**：`core/rule_engine.py`。
- **验收（真值表用例）**：
  - P005 `water_cut< x13 OR water_cut < avg_water_cut - x_p005_y` 覆盖 T/F/U；
  - 缺 `formation_pressure` → P010=UNKNOWN；
  - P016 `casing_damage==false` 布尔比较正确。

### ISSUE-1.5 · R/C 聚合（fill `core.rule_engine` 聚合层）
- **任务**：按每节点 `aggregate` 用真值表自底向上合并 `children`；支持 AND/OR（WEIGHTED/VOTE 可留骨架）；产出 `EvaluationResult{code→TriState}` 与根 `C001`。
- **需求**：§4.3、§4.4、§4.5。
- **涉及文件**：`core/rule_engine.py`、`core/models.py`。
- **验收**：
  - R11=OR（任一低即低效）、C020=OR、R03/R02-2=AND 行为符合 `rules.yaml`；
  - 子含 UNKNOWN 且无短路 → UNKNOWN；
  - 黄金样例全链路三态输出与预期快照一致。

---

## Epic C · 渲染回写

### ISSUE-1.6 · fillColor 结果着色（fill `core.renderer`）
- **任务**：仅对参与求值节点替换 `fillColor`（绿/红/黄，取自 `style.yaml result_style` 字符串键）；其余 style 键、富文本、连线原样保留；UNCLASSIFIED/边/说明透传；不改原始 `.drawio`。
- **需求**：§6.1–6.3。
- **涉及文件**：`core/renderer.py`。
- **验收**：TRUE/FALSE/UNKNOWN → `#00B050`/`#FF0000`/`#FFFF00`；未求值节点保持 `#FFFFCC/#CCFFFF/#FFCCFF`；`strokeColor`/字号不变。

### ISSUE-1.7 · 参数值回写标注（fill `core.renderer`）
- **任务**：按 `value_annotation`，对**输入节点(P)**在各运算符前插全角 `（值）`；命名槽位定位；布尔 `（是）/（否）`；UNKNOWN 不插；幂等（已有则跳过）；仅追加不改原文。
- **需求**：§6.6（9 条规则）。
- **涉及文件**：`core/renderer.py`。
- **验收**：
  - `P006 → …之差（20） > x12 t`；
  - `P001 → 可压储层层数（5） > x3个 且 折算厚度（12） > x4m`；
  - 重复渲染不叠加括号；`casing_damage:false → （否）`；UNKNOWN 无括号且黄底。

### ISSUE-1.8 · HTML 输出组装（fill `core.renderer`）
- **任务**：内存模型 → 输出 XML → 内嵌 draw.io viewer 的 HTML；预留 SVG。
- **需求**：§6.5、§8.2、§9.1。
- **验收**：输出 HTML 布局/连线/字体与原图一致；结构化 JSON 可一并返回。

---

## Epic D · 接口串联

### ISSUE-1.9 · `POST /api/v1/evaluate` 打通（替换 501 占位）
- **任务**：按 `template` 取注册表模板 → 1.1 解析 → 1.3 校验 → 1.4/1.5 求值 → 1.6/1.7/1.8 渲染；返回 HTML（`Accept` 或参数控制），可选 `result=json` 返回三态结构。
- **需求**：§9。
- **涉及文件**：`app.py`（`evaluate`）、`core/template_manager.py`（取模型缓存）。
- **验收**：黄金样例 200 + 正确 HTML/JSON；缺 `template`→400；未知模板→404；无硬编码阈值。

### ISSUE-1.10 · Phase 1 黄金端到端回归
- **任务**：固化 `tests/golden/` 快照（各节点三态、配色、值注释）；纳入 CI 门禁。
- **验收**：`pytest` 全绿；改动 `rules.yaml` 算子/`parameters.yaml` 阈值/`style.yaml` 色值时，快照按预期变化（验证"配置解耦"）。

---

## 提交规范（每个 Issue 一个 PR）

- 标题：`feat(core): ISSUE-1.4 P 节点三态求值`。
- 描述引用：`Refs 需求 §4.4`、`Refs 后续开发流程 Phase 1`。
- 必附：单元测试 + 必要的负样例；触及求值/解析/渲染核心逻辑必须有测试。
- 门禁：`ruff` + `mypy` + `pytest`（含黄金冒烟）通过。

## 完成定义（Phase 1 出口 = MVP 可演示）

给定 `sample_request.json`：输出 HTML 与原图布局一致、结果色正确、值注释正确；结构化 JSON 列出每个业务码三态与最终 `C001`；全程无硬编码阈值/算子/颜色；原始 `.drawio` 未被写改。

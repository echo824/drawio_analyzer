"""输入 Schema 校验（需求 §5.5、§2.1）。ISSUE-1.3 落地。

职责：按 `parameters.yaml` 的 quantities 语义字典，把 `node_values` 归一为
`{节点ID: {操作数name: 值 | None}}`，并逐项校验类型/范围。节点值支持三种形式：
  - **标量**：该节点仅 1 个操作数时（如 `"P002": 30`）；
  - **数组（推荐契约）**：按 `rules.yaml` 去重后的操作数顺序**位置映射**
    （如 `"P011": [10, 500]` → flow_pressure/inflow_performance）；
  - **映射**：按操作数 name 取值（兼容旧格式）。
合法（含可安全强制的类型）→ 归一值；越界 / 类型不符 / 缺失 / 未知操作数 →
置哨兵 None 并记录 issue（**不抛错**），交由求值层判 UNKNOWN，与 FALSE 严格区分。
输出结构保持"按节点 ID + 操作数 name"，与值回写（§6.6）天然对齐。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

IssueKind = Literal["missing", "type", "range", "required", "unknown_operand", "unknown_node"]


class ValidationError(Exception):
    """仅在 strict 模式下、且存在 required 缺失时抛出。"""


@dataclass(slots=True)
class ValidationIssue:
    location: str        # 形如 "P001.reservoir_layers"
    kind: IssueKind
    reason: str

    def __str__(self) -> str:
        return f"[{self.kind}] {self.location}: {self.reason}"


@dataclass(slots=True)
class NodeValuesResult:
    """node_values 归一 + 校验结果。"""

    values: dict[str, dict[str, Any]] = field(default_factory=dict)
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.issues

    def to_dict(self) -> dict[str, Any]:
        return {
            "values": self.values,
            "issues": [{"location": i.location, "kind": i.kind, "reason": i.reason} for i in self.issues],
        }


def _to_number(raw: Any) -> int | float | None:
    """把数值/数值字符串规范为 int|float；无法解析返回 None。布尔不视为数值。"""
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float):
        return raw
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return None
        try:
            fval = float(text)
        except ValueError:
            return None
        return int(fval) if fval.is_integer() else fval
    return None


class InputValidator:
    """基于 quantities 语义字典校验 node_values/basis/thresholds。"""

    def __init__(
        self,
        nodes: dict[str, Any],
        quantities: dict[str, Any],
        thresholds: dict[str, Any] | None = None,
        *,
        strict: bool = False,
    ) -> None:
        self.nodes = nodes
        self.quantities = quantities
        self.thresholds = thresholds or {}
        self.strict = strict

    @classmethod
    def from_config(cls, cfg: Any, *, strict: bool = False) -> InputValidator:
        return cls(cfg.nodes, cfg.quantities, cfg.thresholds, strict=strict)

    # ── 操作数 → 期望类型（缺 schema 时视为自由数值）───────
    def _operand_type(self, name: str) -> str | None:
        spec = self.quantities.get(name)
        return spec.get("type") if isinstance(spec, dict) else None

    def _operand_spec(self, name: str) -> dict[str, Any] | None:
        spec = self.quantities.get(name)
        return spec if isinstance(spec, dict) else None

    def coerce_value(self, name: str, raw: Any) -> tuple[Any, ValidationIssue | None]:
        """按操作数类型/范围强制单值；失败返回 (None, issue)。"""
        spec = self._operand_spec(name)
        expected = spec.get("type") if spec else None
        loc_name = name

        if expected == "boolean":
            coerced = self._coerce_bool(raw)
            if coerced is None:
                return None, ValidationIssue(loc_name, "type", f"期望 boolean，实得 {raw!r}")
            return coerced, None

        # 数值族（number/integer/未声明）
        num = _to_number(raw)
        if num is None:
            label = expected or "number"
            return None, ValidationIssue(loc_name, "type", f"期望 {label}，实得非数值 {raw!r}")

        if expected == "integer" and not float(num).is_integer():
            return None, ValidationIssue(loc_name, "type", f"期望 integer，实得 {raw!r} 非整数")

        rng = spec.get("range") if spec else None
        if isinstance(rng, (list, tuple)) and len(rng) == 2:
            lo, hi = rng
            if (lo is not None and num < lo) or (hi is not None and num > hi):
                return None, ValidationIssue(loc_name, "range", f"{num} 越界（允许 {lo}~{hi}）")

        return int(num) if expected == "integer" else num, None

    @staticmethod
    def _coerce_bool(raw: Any) -> bool | None:
        if isinstance(raw, bool):
            return raw
        if raw in (0, 1):
            return bool(raw)
        if isinstance(raw, str):
            low = raw.strip().lower()
            if low in ("true", "yes", "y", "1"):
                return True
            if low in ("false", "no", "n", "0"):
                return False
        return None

    # ── 主入口：node_values ───────────────────────────────
    def validate_node_values(self, node_values: dict[str, Any]) -> NodeValuesResult:
        result = NodeValuesResult()
        for code, raw in (node_values or {}).items():
            node = self.nodes.get(code)
            operand_names = self._operand_names(node)
            if operand_names is None:  # 非 predicate 或未知节点
                result.issues.append(ValidationIssue(code, "unknown_node", "非判据(P)节点或规则未定义"))
                continue
            result.values[code] = self._validate_one_node(code, raw, operand_names, result.issues)
        self._maybe_raise(result)
        return result

    @staticmethod
    def _operand_names(node: Any) -> list[str] | None:
        """返回该 P 节点去重后的操作数 name 列表；非 predicate 返回 None。"""
        if node is None or getattr(node, "type", None) != "predicate":
            return None
        names: list[str] = []
        for operand in node.operands or []:
            name = operand.get("name")
            if name and name not in names:
                names.append(name)
        return names

    def _validate_one_node(
        self,
        code: str,
        raw: Any,
        operand_names: list[str],
        issues: list[ValidationIssue],
    ) -> dict[str, Any]:
        """把某节点的输入（标量 / 数组 / 映射）归一到 {operand_name: value|None}。

        归一优先级：
          - 映射(dict)：按操作数 name 取值（兼容旧格式）；
          - 数组(list/tuple)：按 operand_names（已去重、按声明顺序）**位置映射**；
          - 标量：仅当该节点只有 1 个操作数时允许。
        数组偏短 → 尾部操作数记缺失；偏长 → 记 type issue 并忽略多余位。"""
        provided: dict[str, Any] = {}

        if isinstance(raw, dict):
            provided = dict(raw)
            for key in provided:
                if key not in operand_names:
                    issues.append(ValidationIssue(f"{code}.{key}", "unknown_operand", "不在 rules 操作数中"))
        elif isinstance(raw, (list, tuple)):
            if len(raw) > len(operand_names):
                issues.append(
                    ValidationIssue(code, "type", f"数组长度 {len(raw)} 超过操作数个数 {len(operand_names)}")
                )
            for i, value in enumerate(raw):
                if i < len(operand_names):
                    provided[operand_names[i]] = value
        elif len(operand_names) == 1:  # 标量仅允许单操作数节点（§5.1）
            provided = {operand_names[0]: raw}
        else:
            issues.append(ValidationIssue(code, "type", "多操作数节点应提供数组或按 name 的映射"))

        normalized: dict[str, Any] = {}
        for name in operand_names:
            location = f"{code}.{name}"
            if name not in provided:
                normalized[name] = None
                spec = self._operand_spec(name)
                if spec and spec.get("required"):
                    issues.append(ValidationIssue(location, "required", "必填量缺失"))
                else:
                    issues.append(ValidationIssue(location, "missing", "未提供，置 UNKNOWN"))
                continue
            coerced, issue = self.coerce_value(name, provided[name])
            normalized[name] = coerced
            if issue:
                issues.append(ValidationIssue(location, issue.kind, issue.reason))
        return normalized

    # ── 附加：basis / thresholds 的轻量数值校验 ───────────
    def validate_basis(self, basis: dict[str, Any] | None) -> tuple[dict[str, Any], list[ValidationIssue]]:
        return self._validate_numeric_map(basis or {}, scope="basis")

    def validate_thresholds(self, thresholds: dict[str, Any] | None) -> tuple[dict[str, Any], list[ValidationIssue]]:
        return self._validate_numeric_map(thresholds or {}, scope="thresholds")

    def _validate_numeric_map(
        self, mapping: dict[str, Any], *, scope: str
    ) -> tuple[dict[str, Any], list[ValidationIssue]]:
        out: dict[str, Any] = {}
        issues: list[ValidationIssue] = []
        for key, raw in mapping.items():
            num = _to_number(raw)
            out[key] = num
            if num is None:
                issues.append(ValidationIssue(f"{scope}.{key}", "type", f"期望数值，实得 {raw!r}"))
        return out, issues

    def _maybe_raise(self, result: NodeValuesResult) -> None:
        if self.strict:
            missing_required = [i for i in result.issues if i.kind == "required"]
            if missing_required:
                raise ValidationError("; ".join(str(i) for i in missing_required))


__all__ = [
    "InputValidator",
    "ValidationError",
    "ValidationIssue",
    "NodeValuesResult",
]

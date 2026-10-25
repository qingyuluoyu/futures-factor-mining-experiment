import ast
from dataclasses import dataclass
import hashlib
import math

import numpy as np
import pandas as pd

METHOD_VERSION = "stock-expression-v1"
FUNCTIONS = {"abs": 1, "log": 1, "rank": 1, "zscore": 1, "clip": 3, "lag": 2, "rolling_mean": 2, "rolling_std": 2}


class ExpressionError(ValueError):
    pass


@dataclass(frozen=True)
class CompiledFactor:
    expression: str
    version: str
    fields: tuple[str, ...]
    lookback: int


def _number(node):
    sign = 1
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        sign = -1 if isinstance(node.op, ast.USub) else 1
        node = node.operand
    if not isinstance(node, ast.Constant) or type(node.value) not in (int, float):
        raise ExpressionError("参数必须为有限数值常量")
    value = sign * node.value
    if abs(value) > 1e12 or not math.isfinite(value):
        raise ExpressionError("数值常量超出范围")
    return value


def compile_expression(text: str, *, allowed_fields: set[str]) -> CompiledFactor:
    if not isinstance(text, str) or not 1 <= len(text) <= 4096:
        raise ExpressionError("表达式长度须为1至4096")
    try:
        tree = ast.parse(text.strip(), mode="eval")
    except (SyntaxError, ValueError, RecursionError):
        raise ExpressionError("表达式语法无效") from None
    if sum(1 for _ in ast.walk(tree)) > 128:
        raise ExpressionError("表达式节点超过128")
    fields = set()

    def validate(node, depth=0):
        if depth > 16:
            raise ExpressionError("表达式嵌套超过16层")
        if isinstance(node, ast.Name) and node.id in allowed_fields:
            fields.add(node.id)
            return 0
        if isinstance(node, ast.Constant):
            _number(node)
            return 0
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            return validate(node.operand, depth + 1)
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)):
            return max(validate(node.left, depth + 1), validate(node.right, depth + 1))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in FUNCTIONS:
            name = node.func.id
            if node.keywords or len(node.args) != FUNCTIONS[name]:
                raise ExpressionError("函数参数数量或关键字无效")
            history = validate(node.args[0], depth + 1)
            if name in ("lag", "rolling_mean", "rolling_std"):
                window = _number(node.args[1])
                if type(window) is not int or not (1 if name == "lag" else 2) <= window <= 244:
                    raise ExpressionError("历史窗口必须为允许范围内的正整数")
                return history + (window if name == "lag" else window - 1)
            if name == "clip" and _number(node.args[1]) > _number(node.args[2]):
                raise ExpressionError("裁剪下界不得大于上界")
            if name == "clip":
                _number(node.args[1]); _number(node.args[2])
            return history
        raise ExpressionError("表达式包含未知字段或禁止的语法")

    history = validate(tree.body)
    if not fields or len(fields) > 16 or history > 244:
        raise ExpressionError("必须引用1至16个允许字段，合计历史窗口不得超过244")
    canonical = ast.unparse(tree.body)
    version = hashlib.sha256((METHOD_VERSION + ":" + ast.dump(tree, include_attributes=False)).encode()).hexdigest()
    return CompiledFactor(canonical, version, tuple(sorted(fields)), history)


def evaluate_expression(compiled: CompiledFactor, panels: dict[str, pd.DataFrame], *, max_cells: int = 2_000_000) -> pd.DataFrame:
    if not isinstance(compiled, CompiledFactor) or compile_expression(compiled.expression, allowed_fields=set(compiled.fields)) != compiled:
        raise ExpressionError("表达式版本或依赖校验失败")
    if type(max_cells) is not int or not 0 < max_cells <= 2_000_000:
        raise ExpressionError("面板预算无效")
    frames = {}
    reference = None
    for field in compiled.fields:
        frame = panels.get(field)
        if not isinstance(frame, pd.DataFrame) or frame.empty or frame.size * len(compiled.fields) > max_cells:
            raise ExpressionError("字段缺失或面板超过预算")
        if not isinstance(frame.index, pd.DatetimeIndex) or frame.index.hasnans or not frame.index.is_unique or not frame.index.is_monotonic_increasing or not frame.columns.is_unique:
            raise ExpressionError("字段日期或证券轴无效")
        if reference is not None and (not reference.index.equals(frame.index) or not reference.columns.equals(frame.columns)):
            raise ExpressionError("字段日期和证券轴不一致")
        reference = frame
        if any(isinstance(value, (bool, np.bool_)) for value in frame.to_numpy().flat):
            raise ExpressionError("布尔值不能作为金融数值")
        try:
            frames[field] = frame.astype(float).replace([np.inf, -np.inf], np.nan)
        except (ValueError, TypeError):
            raise ExpressionError("字段必须为可计算的数值面板") from None

    def calculate(node):
        if isinstance(node, ast.Name):
            return frames[node.id]
        if isinstance(node, ast.Constant):
            return _number(node)
        if isinstance(node, ast.UnaryOp):
            value = calculate(node.operand)
            return -value if isinstance(node.op, ast.USub) else value
        if isinstance(node, ast.BinOp):
            left, right = calculate(node.left), calculate(node.right)
            if isinstance(node.op, ast.Add): return left + right
            if isinstance(node.op, ast.Sub): return left - right
            if isinstance(node.op, ast.Mult): return left * right
            if isinstance(right, pd.DataFrame): right = right.where(right != 0)
            elif right == 0: return left * np.nan
            return left / right
        name, value = node.func.id, calculate(node.args[0])
        if not isinstance(value, pd.DataFrame):
            raise ExpressionError("函数首个参数必须为字段面板")
        if name == "abs": return value.abs()
        if name == "log": return np.log(value.where(value > 0))
        if name == "rank": return value.rank(axis=1, pct=True, method="average")
        if name == "zscore": return value.sub(value.mean(axis=1), axis=0).div(value.std(axis=1, ddof=1).replace(0, np.nan), axis=0)
        if name == "clip": return value.clip(_number(node.args[1]), _number(node.args[2]))
        window = _number(node.args[1])
        if name == "lag": return value.shift(window)
        rolling = value.rolling(window, min_periods=window)
        return rolling.mean() if name == "rolling_mean" else rolling.std(ddof=1)

    with np.errstate(all="ignore"):
        result = calculate(ast.parse(compiled.expression, mode="eval").body)
    if not isinstance(result, pd.DataFrame):
        raise ExpressionError("表达式未产生因子面板")
    return result.replace([np.inf, -np.inf], np.nan)

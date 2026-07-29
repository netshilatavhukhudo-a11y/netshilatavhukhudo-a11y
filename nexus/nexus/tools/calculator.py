"""Evaluate an arithmetic expression. The 8th-tool proof: one file, one entry
in enabled_tools, and a tier — no core change. Under 20 lines of real code,
and it does not use eval(), so it cannot execute arbitrary Python."""

from __future__ import annotations

import ast
import operator

from nexus.tools.base import ToolError, tool

_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
        ast.Div: operator.truediv, ast.Pow: operator.pow, ast.Mod: operator.mod,
        ast.USub: operator.neg, ast.UAdd: operator.pos, ast.FloorDiv: operator.floordiv}


def _eval(node: ast.AST) -> float:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval(node.left), _eval(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval(node.operand))
    raise ToolError(f"unsupported expression element: {ast.dump(node)}")


@tool("calculator", "Evaluate an arithmetic expression, e.g. '2 * (3 + 4) ** 2'.",
      {"type": "object", "properties": {"expression": {"type": "string"}},
       "required": ["expression"]}, tier_key="calculator")
def calculator(inp: dict) -> str:
    expr = (inp.get("expression") or "").strip()
    if not expr:
        raise ToolError("calculator requires an 'expression'")
    try:
        return str(_eval(ast.parse(expr, mode="eval").body))
    except ToolError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ToolError(f"could not evaluate {expr!r}: {exc}") from exc

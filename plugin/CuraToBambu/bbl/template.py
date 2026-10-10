# Copyright (c) 2026. Released under the terms of the LGPLv3 or higher.
"""A small renderer for BambuStudio / PrusaSlicer G-code macro templates.

Supports what Bambu Lab's machine templates use:
  * legacy placeholders      [name]          (vectors -> element of the current extruder)
  * expressions              {expr}          (arithmetic, comparisons, ||, &&, !, indexing, strings)
  * conditionals             {if c} .. {elsif c} .. {else} .. {endif}

Pure Python (no Cura imports) so it can be unit-tested outside Cura.
"""
import math
import re
from typing import Any, Dict, List, Tuple, Union

__all__ = ["render", "TemplateError", "format_value"]


class TemplateError(Exception):
    pass


_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_LEGACY = re.compile(r"\[([A-Za-z_][A-Za-z0-9_]*)\]")
_SAFE_FUNCS = {"min": min, "max": max, "int": int, "abs": abs, "round": round, "floor": math.floor,
               "ceil": math.ceil, "sqrt": math.sqrt}


def format_value(value: Any) -> str:
    """Format a value the way PrusaSlicer/BambuStudio's placeholder parser does."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if math.isfinite(value) and abs(value - round(value)) < 1e-9:
            return str(int(round(value)))
        text = ("%.6f" % value).rstrip("0").rstrip(".")
        return "0" if text in ("-0", "") else text
    if isinstance(value, (list, tuple)):
        return ",".join(format_value(v) for v in value)
    return str(value)


def _translate(expr: str) -> str:
    """Translate a Bambu macro expression into an equivalent Python expression."""
    out: List[str] = []
    i, n = 0, len(expr)
    while i < n:
        c = expr[i]
        if c in "\"'":
            j = i + 1
            while j < n and expr[j] != c:
                j += 2 if expr[j] == "\\" else 1
            out.append(expr[i:j + 1])
            i = j + 1
            continue
        two = expr[i:i + 2]
        if two == "||":
            out.append(" or ")
            i += 2
        elif two == "&&":
            out.append(" and ")
            i += 2
        elif two in ("==", "!=", "<=", ">="):
            out.append(two)
            i += 2
        elif c == "!":
            out.append(" not ")
            i += 1
        elif c == "=":  # Bambu allows a single '=' as comparison
            out.append("==")
            i += 1
        elif c.isalpha() or c == "_":
            m = _IDENT.match(expr, i)
            word = m.group(0)
            out.append({"true": "True", "false": "False", "and": " and ", "or": " or ",
                        "not": " not "}.get(word, word))
            i = m.end()
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _eval(expr: str, variables: Dict[str, Any]) -> Any:
    py = _translate(expr.strip())
    try:
        return eval(compile(py, "<bbl-template>", "eval"), {"__builtins__": {}}, {**_SAFE_FUNCS, **variables})
    except NameError as e:
        raise TemplateError("Unknown variable in template expression {%s}: %s" % (expr, e)) from e
    except Exception as e:  # noqa: BLE001 - surface any template problem with context
        raise TemplateError("Could not evaluate template expression {%s}: %s" % (expr, e)) from e


# ---------------------------------------------------------------------------------------------
# Parsing into a tiny tree:  ("text", str) | ("expr", str) | ("if", [(cond, children)], else_children)
Node = Union[Tuple[str, str], Tuple[str, list, list]]


def _tokenize(template: str) -> List[Tuple[str, str]]:
    tokens: List[Tuple[str, str]] = []
    pos = 0
    while True:
        start = template.find("{", pos)
        if start < 0:
            tokens.append(("text", template[pos:]))
            break
        end = template.find("}", start)
        if end < 0:
            raise TemplateError("Unclosed '{' in template near: %r" % template[start:start + 40])
        tokens.append(("text", template[pos:start]))
        body = template[start + 1:end].strip()
        word = body.split(None, 1)[0] if body else ""
        if word in ("if", "elsif"):
            tokens.append((word, body[len(word):].strip()))
        elif word in ("else", "endif") and body == word:
            tokens.append((word, ""))
        else:
            tokens.append(("expr", body))
        pos = end + 1
    return tokens


def _parse(tokens: List[Tuple[str, str]], i: int = 0, stop=()) -> Tuple[list, int]:
    nodes: list = []
    while i < len(tokens):
        kind, value = tokens[i]
        if kind in stop:
            return nodes, i
        if kind in ("text", "expr"):
            nodes.append((kind, value))
            i += 1
        elif kind == "if":
            branches, else_nodes = [], []
            cond = value
            i += 1
            while True:
                body, i = _parse(tokens, i, stop=("elsif", "else", "endif"))
                if i >= len(tokens):
                    raise TemplateError("Missing {endif}")
                branches.append((cond, body))
                kind2, value2 = tokens[i]
                i += 1
                if kind2 == "elsif":
                    cond = value2
                    continue
                if kind2 == "else":
                    else_nodes, i = _parse(tokens, i, stop=("endif",))
                    if i >= len(tokens):
                        raise TemplateError("Missing {endif}")
                    i += 1
                break
            nodes.append(("if", branches, else_nodes))
        else:
            raise TemplateError("Unexpected {%s}" % kind)
    return nodes, i


def _render_text(text: str, variables: Dict[str, Any], extruder: int) -> str:
    def legacy(m):
        name = m.group(1)
        if name not in variables:
            return m.group(0)  # not a placeholder, leave as-is (e.g. comments with brackets)
        v = variables[name]
        if isinstance(v, (list, tuple)):
            v = v[extruder] if extruder < len(v) else v[0]
        return format_value(v)
    return _LEGACY.sub(legacy, text)


def _render_nodes(nodes: list, variables: Dict[str, Any], extruder: int, out: List[str]) -> None:
    for node in nodes:
        if node[0] == "text":
            out.append(_render_text(node[1], variables, extruder))
        elif node[0] == "expr":
            out.append(format_value(_eval(node[1], variables)))
        else:
            _, branches, else_nodes = node
            for cond, body in branches:
                if _eval(cond, variables):
                    _render_nodes(body, variables, extruder, out)
                    break
            else:
                _render_nodes(else_nodes, variables, extruder, out)


def render(template: str, variables: Dict[str, Any], current_extruder: int = 0) -> str:
    """Render a Bambu G-code template with the given variables."""
    nodes, _ = _parse(_tokenize(template))
    out: List[str] = []
    _render_nodes(nodes, variables, current_extruder, out)
    return "".join(out)

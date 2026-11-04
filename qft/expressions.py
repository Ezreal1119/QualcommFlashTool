"""Restricted integer arithmetic used by Qualcomm XML. Never calls eval."""

import ast
import re


class ValidationError(ValueError):
    pass


def evaluate(expression: str, sectors: int | None = None, crc=None) -> int:
    text = re.sub(r"(?<=\d)\.(?!\d)", "", str(expression).strip())
    if len(text) > 256:
        raise ValidationError("Expression is too long")
    try:
        root = ast.parse(text, mode="eval").body
    except (SyntaxError, ValueError) as exc:
        raise ValidationError(f"Invalid expression: {expression}") from exc

    def visit(node, depth=0):
        if depth > 16:
            raise ValidationError("Expression is too complex")
        if isinstance(node, ast.Constant) and type(node.value) is int:
            result = node.value
        elif isinstance(node, ast.Name) and node.id == "NUM_DISK_SECTORS":
            result = sectors if sectors is not None else 1_000_000
        elif isinstance(node, ast.UnaryOp) and isinstance(
            node.op, (ast.UAdd, ast.USub)
        ):
            result = visit(node.operand, depth + 1) * (
                -1 if isinstance(node.op, ast.USub) else 1
            )
        elif isinstance(node, ast.BinOp) and isinstance(
            node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv)
        ):
            a, b = visit(node.left, depth + 1), visit(node.right, depth + 1)
            if isinstance(node.op, ast.Add):
                result = a + b
            elif isinstance(node.op, ast.Sub):
                result = a - b
            elif isinstance(node.op, ast.Mult):
                result = a * b
            else:
                if b == 0 or a % b:
                    raise ValidationError("Sector arithmetic must produce an integer")
                result = a // b
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "CRC32"
            and len(node.args) == 2
            and not node.keywords
        ):
            a, b = (visit(n, depth + 1) for n in node.args)
            if a < 0 or b < 0 or b > 64 * 1024 * 1024:
                raise ValidationError("Invalid CRC32 range")
            result = crc(a, b) if crc else 0
        else:
            raise ValidationError(f"Unsupported expression: {expression}")
        if abs(result) > 2**64:
            raise ValidationError("Expression exceeds supported integer range")
        return result

    return visit(root)

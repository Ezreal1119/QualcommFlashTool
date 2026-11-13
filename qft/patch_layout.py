"""Prove that host-file GPT patches have equivalent ordered DISK operations.

Host patches are an alternative file-preparation target, not additional USB
writes. We leave original firmware untouched and require an exact disk mirror.
"""

import ast
import re
from pathlib import Path
from .expressions import ValidationError, evaluate


def tree(text):
    evaluate(text)  # Apply the same whitelist and complexity/size bounds.
    return ast.parse(
        re.sub(r"(?<=\d)\.(?!\d)", "", str(text).strip()), mode="eval"
    ).body


def linear(text):
    """Canonical a*NUM_DISK_SECTORS+b, without sampling or expression execution."""

    def visit(n):
        if isinstance(n, ast.Constant) and type(n.value) is int:
            return 0, n.value
        if isinstance(n, ast.Name) and n.id == "NUM_DISK_SECTORS":
            return 1, 0
        if isinstance(n, ast.UnaryOp):
            a, b = visit(n.operand)
            if isinstance(n.op, ast.USub):
                return -a, -b
            if isinstance(n.op, ast.UAdd):
                return a, b
        if isinstance(n, ast.BinOp):
            a, b = visit(n.left)
            c, d = visit(n.right)
            if isinstance(n.op, ast.Add):
                return a + c, b + d
            if isinstance(n.op, ast.Sub):
                return a - c, b - d
            if isinstance(n.op, ast.Mult) and not a * c:
                return a * d + b * c, b * d
            if (
                isinstance(n.op, (ast.Div, ast.FloorDiv))
                and not c
                and d
                and not a % d
                and not b % d
            ):
                return a // d, b // d
        raise ValidationError(
            "Cannot prove equivalence of a non-linear GPT patch expression"
        )

    return visit(tree(text))


def value_key(text, base=(0, 0), file_sectors=None, file_offset=0):
    node = tree(text)
    if isinstance(node, ast.Call):
        first = linear(ast.unparse(node.args[0]))
        length = linear(ast.unparse(node.args[1]))
        if file_sectors is not None:
            if (
                first[0]
                or length[0]
                or first[1] < file_offset
                or length[1] < 0
                or first[1] + (length[1] + file_sectors[1] - 1) // file_sectors[1]
                > file_offset + file_sectors[0]
            ):
                raise ValidationError(
                    "Host-file CRC32 reads outside its programmed range"
                )
        return "CRC32", (first[0] + base[0], first[1] + base[1] - file_offset), length
    return "integer", linear(text)


def validate_file_mirrors(programs, disk, host):
    """Require matching data, addresses, CRC sources and per-file ordering."""
    if not host:
        return  # Device-only patches do not need symbolic mirror normalization.
    previous = {}
    disk_keys = [
        (
            p.lun,
            p.sector_size,
            linear(p.start),
            p.byte_offset,
            p.size,
            value_key(p.value),
        )
        for p in disk
    ]
    for patch in host:
        mappings = [
            p
            for p in programs
            if p.path == patch.target
            and p.lun == patch.lun
            and p.sector_size == patch.sector_size
        ]
        if len(mappings) != 1:
            raise ValidationError(
                f"Host patch target has no unique program mapping: {Path(patch.target).name}"
            )
        op = mappings[0]
        relative = linear(patch.start)
        base = linear(op.start)
        offset = op.file_offset // op.sector_size
        if relative[0] or not offset <= relative[1] < offset + op.sectors:
            raise ValidationError("Host patch lies outside its programmed image range")
        start = (relative[0] + base[0], relative[1] + base[1] - offset)
        key = (
            patch.lun,
            patch.sector_size,
            start,
            patch.byte_offset,
            patch.size,
            value_key(patch.value, base, (op.sectors, op.sector_size), offset),
        )
        begin = previous.get(patch.target, -1) + 1
        match = next(
            (i for i in range(begin, len(disk_keys)) if disk_keys[i] == key), None
        )
        if match is None:
            raise ValidationError(
                f"Host patch has no equivalent ordered DISK patch: {Path(patch.target).name} ({patch.description})"
            )
        previous[patch.target] = match

from dataclasses import replace
from pathlib import Path
import struct
import pytest
from qft.expressions import evaluate, ValidationError
from qft.package import make_plan, recheck, validate_ranges, discover
from qft.images import image_info, image_range


@pytest.mark.parametrize(
    "text,result",
    [
        ("NUM_DISK_SECTORS-1.", 127),
        ("(4+2)*512/2", 1536),
        ("0x10", 16),
        ("CRC32(1,92)", 0),
    ],
)
def test_safe_arithmetic(text, result):
    assert evaluate(text, 128) == result


@pytest.mark.parametrize(
    "text",
    [
        "__import__('os').system('echo bad')",
        "2**100",
        "a.b",
        "[1][0]",
        "1/0",
        "1.5",
        "1/2",
        "NUM_DISK_SECTORS.__class__",
    ],
)
def test_expression_rejection(text):
    with pytest.raises(ValidationError):
        evaluate(text, 128)


def test_unicode_package_and_fingerprint(plan):
    assert "固件 with spaces" in plan.folder
    assert plan.programs[0].path.endswith("boot.img")
    recheck(plan)
    Path(plan.programs[0].path).write_bytes(b"C" * 512)
    with pytest.raises(ValidationError, match="changed"):
        recheck(plan)


def test_missing_image_is_not_skipped(package):
    (package / "boot.img").unlink()
    with pytest.raises(FileNotFoundError):
        make_plan(
            package,
            package / "prog_firehose.mbn",
            "emmc",
            [package / "rawprogram0.xml"],
            [package / "patch0.xml"],
        )


def test_missing_pair_is_not_filtered(package):
    (package / "rawprogram1.xml").write_text((package / "rawprogram0.xml").read_text())
    with pytest.raises(ValidationError, match="corresponding"):
        make_plan(
            package,
            package / "prog_firehose.mbn",
            "emmc",
            [package / "rawprogram0.xml", package / "rawprogram1.xml"],
            [package / "patch0.xml"],
        )


def test_ambiguous_variants(package):
    (package / "rawprogram_unsparse0.xml").write_text(
        (package / "rawprogram0.xml").read_text()
    )
    assert len(discover(package)["raws"]) == 2
    with pytest.raises(ValidationError, match="Ambiguous"):
        make_plan(
            package,
            package / "prog_firehose.mbn",
            "emmc",
            [package / "rawprogram0.xml", package / "rawprogram_unsparse0.xml"],
            [package / "patch0.xml", package / "patch0.xml"],
        )


def test_external_loader_keeps_full_path(package, tmp_path):
    other = tmp_path / "external"
    other.mkdir()
    (other / "prog_firehose.mbn").write_bytes(b"different loader")
    p = make_plan(
        package,
        other / "prog_firehose.mbn",
        "emmc",
        [package / "rawprogram0.xml"],
        [package / "patch0.xml"],
    )
    assert p.loader == str(other / "prog_firehose.mbn")


def test_malformed_xml(package):
    (package / "rawprogram0.xml").write_text("<data><program")
    with pytest.raises(Exception):
        make_plan(
            package,
            package / "prog_firehose.mbn",
            "emmc",
            [package / "rawprogram0.xml"],
            [package / "patch0.xml"],
        )


def test_external_entity_rejected(package):
    (package / "rawprogram0.xml").write_text(
        '<!DOCTYPE data [<!ENTITY x SYSTEM "file:///etc/passwd">]><data>&x;</data>'
    )
    with pytest.raises(Exception):
        make_plan(
            package,
            package / "prog_firehose.mbn",
            "emmc",
            [package / "rawprogram0.xml"],
            [package / "patch0.xml"],
        )


def test_capacity_and_overlap(plan):
    assert validate_ranges(plan, {0: 128})[0] == []
    assert validate_ranges(plan, {0: 10})[0]
    assert validate_ranges(plan, {})[0]
    duplicate = replace(plan, programs=plan.programs * 2)
    assert validate_ranges(duplicate, {0: 128})[1]


def test_file_offset_and_padding(package):
    (package / "boot.img").write_bytes(b"A" * 512 + b"B" * 511)
    p = package / "rawprogram0.xml"
    p.write_text(
        p.read_text().replace('file_sector_offset="0"', 'file_sector_offset="1"')
    )
    plan = make_plan(
        package, package / "prog_firehose.mbn", "emmc", [p], [package / "patch0.xml"]
    )
    assert b"".join(image_range(plan.programs[0].path, 512, 512)) == b"B" * 511 + b"\0"


def sparse_file(path, kind=0xCAC1, payload=b"A" * 512, total_size=None):
    block = 512
    expected = 512 if kind == 0xCAC1 else 4 if kind == 0xCAC2 else 0
    path.write_bytes(
        struct.pack("<I4H4I", 0xED26FF3A, 1, 0, 28, 12, block, 1, 1, 0)
        + struct.pack("<2H2I", kind, 0, 1, total_size or 12 + expected)
        + payload
    )


@pytest.mark.parametrize(
    "kind,payload,expected",
    [
        (0xCAC1, b"A" * 512, b"A" * 512),
        (0xCAC2, b"1234", b"1234" * 128),
        (0xCAC3, b"", bytes(512)),
    ],
)
def test_sparse_chunks(tmp_path, kind, payload, expected):
    path = tmp_path / "sparse.img"
    sparse_file(path, kind, payload)
    assert image_info(path) == (512, True)
    assert b"".join(image_range(path, 0, 512)) == expected


def test_sparse_corruption(tmp_path):
    path = tmp_path / "bad.img"
    sparse_file(path, payload=b"x")
    with pytest.raises(ValidationError):
        image_info(path)


def test_out_of_range_file_offset(package):
    p = package / "rawprogram0.xml"
    p.write_text(
        p.read_text().replace('file_sector_offset="0"', 'file_sector_offset="4"')
    )
    with pytest.raises(ValidationError):
        make_plan(
            package,
            package / "prog_firehose.mbn",
            "emmc",
            [p],
            [package / "patch0.xml"],
        )


def test_dynamic_length_cannot_be_silently_substituted(package):
    p = package / "rawprogram0.xml"
    p.write_text(
        p.read_text().replace(
            'num_partition_sectors="1"', 'num_partition_sectors="NUM_DISK_SECTORS"'
        )
    )
    with pytest.raises(ValidationError, match="Dynamic"):
        make_plan(
            package,
            package / "prog_firehose.mbn",
            "emmc",
            [p],
            [package / "patch0.xml"],
        )

"""Conservative, offline Qualcomm ELF-v6 programmer authentication.

Self-signed certificates establish integrity only. Device trust is established
separately by comparing the complete root-certificate digest reported by Sahara.
No filenames, CPU aliases, truncated hashes or trial uploads establish a match.
"""

from __future__ import annotations

import hashlib
import struct
from dataclasses import asdict, dataclass
from pathlib import Path

from cryptography import x509
from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from .expressions import ValidationError

MAX_SIZE = 32 * 1024**2


class NotProgrammer(ValidationError):
    """A signed image explicitly declares a different software identity."""


@dataclass(frozen=True)
class LoaderInfo:
    path: str
    sha256: str
    bits: int
    hw_id: int
    oem_id: int
    model_id: int
    flags: int
    soc_vers: tuple[int, ...]
    serials: tuple[int, ...]
    root_sha256: str
    root_sha384: str
    restrictions: tuple[str, ...]


@dataclass(frozen=True)
class DeviceIdentity:
    hwid: int | None
    pkhash: str
    serial: int | None
    # Only populated when supplied by an authenticated device source. Sahara v2
    # does not report SoC versions; a CPU-name lookup is not an equivalent source.
    soc_vers: tuple[int, ...] = ()


def _slice(data, offset, size):
    if offset < 0 or size < 0 or offset + size > len(data):
        raise ValidationError("Loader contains an out-of-bounds range")
    return data[offset : offset + size]


def _certificates(data):
    certs, encoded = [], []
    offset = 0
    while offset < len(data) and data[offset] == 0x30:
        if len(certs) == 4 or offset + 2 > len(data):
            raise ValidationError("Invalid loader certificate chain")
        length = data[offset + 1]
        header = 2
        if length & 0x80:
            count = length & 0x7F
            if not 1 <= count <= 3:
                raise ValidationError("Invalid DER certificate length")
            length = int.from_bytes(_slice(data, offset + 2, count), "big")
            header += count
        raw = _slice(data, offset, header + length)
        certs.append(x509.load_der_x509_certificate(raw))
        encoded.append(raw)
        offset += len(raw)
    if not 2 <= len(certs) <= 4 or any(x != 0xFF for x in data[offset:]):
        raise ValidationError("Invalid loader certificate chain or padding")
    for i, cert in enumerate(certs):
        issuer = certs[min(i + 1, len(certs) - 1)]
        key = issuer.public_key()
        if cert.issuer != issuer.subject or not isinstance(key, rsa.RSAPublicKey):
            raise ValidationError("Invalid loader certificate issuer")
        if key.key_size < 2048:
            raise ValidationError("Loader certificate key is too small")
        if cert.signature_hash_algorithm.name not in ("sha256", "sha384", "sha512"):
            raise ValidationError("Unsupported loader certificate signature")
        key.verify(
            cert.signature,
            cert.tbs_certificate_bytes,
            cert.signature_algorithm_parameters,
            cert.signature_hash_algorithm,
        )
    # Some Qualcomm attestation leaves contain CA:FALSE/pathlen:0, which is not
    # RFC 5280 compliant. Do not parse leaf extensions as a web-PKI certificate.
    # Issuers must still be certificate authorities with sufficient path depth.
    for i, issuer in enumerate(certs[1:]):
        constraints = issuer.extensions.get_extension_for_class(
            x509.BasicConstraints
        ).value
        if not constraints.ca or (
            constraints.path_length is not None and constraints.path_length < i
        ):
            raise ValidationError("Loader certificate issuer is not a valid CA")
    return certs[0], encoded[-1]


def _inspect(data, path):
    if len(data) < 64 or data[:4] != b"\x7fELF" or data[5:7] != b"\x01\x01":
        raise ValidationError(
            "Unsupported loader: expected a little-endian ARM ELF programmer"
        )
    bits = {1: 32, 2: 64}.get(data[4])
    if bits == 64:
        hdr = struct.unpack_from("<HHIQQQIHHHHHH", data, 16)
        ph_fmt, header_size = "<IIQQQQQQ", 64
    elif bits == 32:
        hdr = struct.unpack_from("<HHIIIIIHHHHHH", data, 16)
        ph_fmt, header_size = "<IIIIIIII", 52
    else:
        raise ValidationError("Unsupported ELF class")
    kind, machine, version, entry, phoff, _, _, ehsize, phsize, count, *_ = hdr
    if (
        kind != 2
        or machine != (183 if bits == 64 else 40)
        or version != 1
        or ehsize != header_size
        or phsize != struct.calcsize(ph_fmt)
        or not 2 <= count <= 128
        or phoff < header_size
    ):
        raise ValidationError("Unsupported ELF programmer header")
    _slice(data, phoff, count * phsize)
    segments = []
    for index in range(count):
        values = struct.unpack_from(ph_fmt, data, phoff + index * phsize)
        if bits == 64:
            typ, flags, offset, virtual, _, size, mem, _ = values
        else:
            typ, offset, virtual, _, size, mem, flags, _ = values
        _slice(data, offset, size)
        if typ not in (0, 1) or flags & 0x00100000:
            raise ValidationError("Unsupported ELF segment type or paged loader")
        if typ == 1 and size > mem:
            raise ValidationError("Invalid ELF load segment")
        segments.append((typ, flags, offset, virtual, size))
    if not any(t == 1 and f & 1 and v <= entry < v + s for t, f, _, v, s in segments):
        raise ValidationError("ELF entry point is outside executable image data")
    hash_segments = [s for s in segments if (s[1] >> 24) & 7 == 2]
    if len(hash_segments) != 1:
        raise ValidationError("Expected exactly one Qualcomm signing segment")
    _, _, offset, _, size = hash_segments[0]
    signed = _slice(data, offset, size)
    if len(signed) < 48:
        raise ValidationError("Truncated Qualcomm signing header")
    (
        image,
        ver,
        qsig,
        qcert,
        image_size,
        code_size,
        _,
        sig_size,
        _,
        cert_size,
        qmeta,
        meta_size,
    ) = struct.unpack_from("<12I", signed)
    if image != 5 or ver != 6 or qsig or qcert or qmeta or meta_size != 120:
        raise ValidationError(
            "Unsupported signing layout: require single-OEM MBN v6, metadata 0.0"
        )
    if (
        image_size != code_size + sig_size + cert_size
        or len(signed) != 48 + meta_size + image_size
    ):
        raise ValidationError("Inconsistent Qualcomm signing lengths")
    major, minor, sw, hw, oem, model, app, flags = struct.unpack_from("<8I", signed, 48)
    if (major, minor) != (0, 0) or oem > 0xFFFF or model > 0xFFFF:
        raise ValidationError("Unsupported Qualcomm signing metadata")
    socs = tuple(x for x in struct.unpack_from("<12I", signed, 80) if x)
    serials = tuple(x for x in struct.unpack_from("<8I", signed, 128) if x)
    root_index, rollback = struct.unpack_from("<2I", signed, 160)
    restrictions = []
    # Only the SoC/HWID selector and explicit serial allow-list are implemented.
    # Do not silently treat OEM independence, debug, ROT or rollback as wildcards.
    if flags & ~0x2 or app or root_index or rollback:
        restrictions.append(
            "unsupported signing flags, app ID, root index or rollback constraint"
        )
    end = 48 + meta_size + code_size
    table = _slice(signed, 48 + meta_size, code_size)
    signature = _slice(signed, end, sig_size)
    leaf, root = _certificates(_slice(signed, end + sig_size, cert_size))
    key = leaf.public_key()
    if not isinstance(key, rsa.RSAPublicKey) or key.key_size < 2048:
        raise ValidationError("Unsupported programmer signing key")
    valid = False
    for algorithm in (hashes.SHA256(), hashes.SHA384()):
        for scheme in (
            padding.PSS(mgf=padding.MGF1(algorithm), salt_length=algorithm.digest_size),
            padding.PKCS1v15(),
        ):
            try:
                key.verify(signature, signed[:end], scheme, algorithm)
                valid = True
            except InvalidSignature:
                pass
    if not valid:
        raise ValidationError("Loader image signature is invalid")
    width = code_size // count
    if code_size != width * count or width not in (32, 48):
        raise ValidationError("Unsupported ELF hash table layout")
    algorithm = "sha256" if width == 32 else "sha384"
    covered_headers = False
    for index, (_, segment_flags, start, _, length) in enumerate(segments):
        expected = table[index * width : (index + 1) * width]
        if not length or (segment_flags >> 24) & 7 == 2:
            actual = bytes(width)
        else:
            actual = hashlib.new(algorithm, data[start : start + length]).digest()
            if start == 0 and length >= phoff + count * phsize:
                covered_headers = True
        if actual != expected:
            raise ValidationError(f"Loader segment {index} hash is invalid")
    if not covered_headers:
        raise ValidationError("Loader signature does not cover the ELF headers")
    if sw != 3:
        raise NotProgrammer(
            f"Signed image SW_ID={sw:#x} is not a device programmer (0x3)"
        )
    return LoaderInfo(
        str(path),
        hashlib.sha256(data).hexdigest(),
        bits,
        hw,
        oem,
        model,
        flags,
        socs,
        serials,
        hashlib.sha256(root).hexdigest(),
        hashlib.sha384(root).hexdigest(),
        tuple(restrictions),
    )


def inspect_loader(path):
    """Bounded read; return verified metadata, or explain why it is unusable."""
    path = Path(path).resolve(strict=True)
    if path.stat().st_size > MAX_SIZE:
        raise ValidationError("Loader exceeds the 32 MiB inspection limit")
    with path.open("rb") as source:
        data = source.read(MAX_SIZE + 1)
    if len(data) > MAX_SIZE:
        raise ValidationError("Loader exceeds the 32 MiB inspection limit")
    try:
        return _inspect(data, path)
    except ValidationError:
        raise
    except (
        ValueError,
        struct.error,
        InvalidSignature,
        UnsupportedAlgorithm,
        x509.ExtensionNotFound,
        x509.DuplicateExtension,
    ) as exc:
        raise ValidationError(
            f"Invalid loader signature or structure: {type(exc).__name__}"
        ) from exc


# Narrow evidence from docs/hardware/DT50-2026-11-12.md. This is a single tested
# programmer AND chip, not a general SDM662/MSM-ID -> SoC-version conversion.
_DT50_EVIDENCE = (
    "147b7cf50f27d4feced8563aec47483cf84557da5db7bf1581942cbca073e908",
    0x0014D0E100000000,
    "52ce9c0eba3d6712cb2594d1d6993067ef148b3bb5ac068937bbfe8661176bb06c185f733459b7b2262692031b349a90",
    0x688BC49B,
)


def match_loader(info, device):
    """Return (match|conflict|unknown, evidence). No upload is performed."""
    if device.hwid is None or not 0 <= device.hwid < 2**64 or device.serial is None:
        return "unknown", "Sahara did not provide HWID and chip serial"
    pkhash = device.pkhash.lower().removeprefix("0x")
    expected = {64: info.root_sha256, 96: info.root_sha384}.get(len(pkhash))
    if not expected or not any(x != "0" for x in pkhash):
        return "unknown", "Missing or unsupported full device root hash"
    if pkhash != expected:
        return "conflict", "Full device root hash differs from the loader certificate"
    if info.restrictions:
        return "unknown", "; ".join(info.restrictions)
    if (
        device.hwid >> 16
    ) & 0xFFFF != info.oem_id or device.hwid & 0xFFFF != info.model_id:
        return "conflict", "OEM_ID / MODEL_ID differs"
    if info.serials and device.serial not in info.serials:
        return "conflict", "Chip serial is outside the signed allow-list"
    if info.flags & 2:
        if not info.soc_vers:
            return "unknown", "Signed SoC version list is empty"
        if device.soc_vers:
            if not set(device.soc_vers).intersection(info.soc_vers):
                return "conflict", "Device SoC version differs"
        elif (info.sha256, device.hwid, pkhash, device.serial) == _DT50_EVIDENCE:
            return (
                "match",
                "Full root hash, OEM/model and exact previously tested DT50 chip/programmer",
            )
        else:
            return (
                "unknown",
                "Loader requires a SoC version; Sahara only reported an MSM HWID (no trusted conversion)",
            )
    elif device.hwid >> 32 != info.hw_id:
        return "conflict", "MSM hardware ID differs"
    return (
        "match",
        "Signature, segment hashes, full root hash and signed hardware constraints match",
    )


def select_loader(plan, device, emit=lambda *args: None):
    paths = plan.loader_candidates or (plan.loader,)
    matches, report, unknown = {}, [], False
    for path in paths:
        try:
            info = inspect_loader(path)
            stamp = next((f for f in plan.files if f.path == info.path), None)
            if stamp is None or stamp.sha256 != info.sha256:
                raise ValidationError(
                    "Loader changed after checks; check the package again"
                )
            status, reason = match_loader(info, device)
            if status == "match":
                matches.setdefault(info.sha256, info)
            unknown |= status == "unknown"
            report.append({**asdict(info), "status": status, "reason": reason})
        except (ValidationError, OSError) as exc:
            unknown = True
            report.append({"path": path, "status": "unknown", "reason": str(exc)})
    selected = (
        next(iter(matches.values())) if len(matches) == 1 and not unknown else None
    )
    emit(
        "loader_selection",
        {
            "device": asdict(device),
            "candidates": report,
            "selected": selected.path if selected else "",
        },
    )
    if selected is None:
        details = "; ".join(
            f"{Path(r['path']).name}: {r['status']} ({r['reason']})" for r in report
        )
        raise ValidationError(
            "Cannot confirm one compatible loader. No loader was uploaded. " + details
        )
    emit(
        "log",
        {"text": f"Loader verified: {selected.path}\nSHA-256: {selected.sha256}\n"},
    )
    return selected

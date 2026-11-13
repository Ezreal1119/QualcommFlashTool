"""Synthetic signed Qualcomm programmers; no proprietary firmware in tests."""

import hashlib
import struct
from datetime import datetime, timedelta, timezone
from functools import lru_cache

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import NameOID


@lru_cache
def keys():
    root = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    leaf = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.now(timezone.utc)
    root_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "QFT test root")])
    leaf_name = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, "QFT test programmer")]
    )

    def cert(subject, issuer, key, ca, number):
        return (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(key.public_key())
            .serial_number(number)
            .not_valid_before(now - timedelta(days=1))
            .not_valid_after(now + timedelta(days=1))
            .add_extension(
                x509.BasicConstraints(ca=ca, path_length=None), critical=True
            )
            .sign(root, hashes.SHA256())
            .public_bytes(serialization.Encoding.DER)
        )

    return leaf, cert(leaf_name, root_name, leaf, False, 2) + cert(
        root_name, root_name, root, True, 1
    )


@lru_cache
def build_loader(
    hw=0x12345678,
    oem=0,
    model=0,
    flags=0,
    soc=0,
    serial=0,
    sw=3,
    text=b"programmer",
    bits=64,
    hash_width=48,
):
    key, chain = keys()
    count = 3
    header_size, phsize = (64, 56) if bits == 64 else (52, 32)
    headers_size = header_size + count * phsize
    hash_offset, code_offset = 4096, 8192
    cert_size, sig_size, meta_size = len(chain), 256, 120
    code_size = count * hash_width
    sign_size = 48 + meta_size + code_size + sig_size + cert_size
    ident = b"\x7fELF" + bytes([2 if bits == 64 else 1, 1, 1]) + bytes(9)
    hdrfmt = "<HHIQQQIHHHHHH" if bits == 64 else "<HHIIIIIHHHHHH"
    header = ident + struct.pack(
        hdrfmt,
        2,
        183 if bits == 64 else 40,
        1,
        0x1000,
        header_size,
        0,
        0,
        header_size,
        phsize,
        count,
        0,
        0,
        0,
    )
    segments = [
        (0, 0x07000000, 0, 0, headers_size, 0),
        (0, 0x02200000, hash_offset, 0, sign_size, sign_size),
        (1, 5, code_offset, 0x1000, len(text), len(text)),
    ]
    for typ, flg, off, va, size, mem in segments:
        header += (
            struct.pack("<IIQQQQQQ", typ, flg, off, va, va, size, mem, 1)
            if bits == 64
            else struct.pack("<IIIIIIII", typ, off, va, va, size, mem, flg, 1)
        )
    alg = "sha384" if hash_width == 48 else "sha256"
    table = (
        hashlib.new(alg, header).digest()
        + bytes(hash_width)
        + hashlib.new(alg, text).digest()
    )
    meta = struct.pack("<8I", 0, 0, sw, hw, oem, model, 0, flags)
    meta += struct.pack("<12I", soc, *([0] * 11))
    meta += struct.pack("<8I", serial, *([0] * 7)) + bytes(8)
    signed = (
        struct.pack(
            "<12I",
            5,
            6,
            0,
            0,
            code_size + sig_size + cert_size,
            code_size,
            0xFFFFFFFF,
            sig_size,
            0xFFFFFFFF,
            cert_size,
            0,
            meta_size,
        )
        + meta
        + table
    )
    signature = key.sign(
        signed,
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=32),
        hashes.SHA256(),
    )
    data = bytearray(code_offset + len(text))
    data[: len(header)] = header
    data[hash_offset : hash_offset + sign_size] = signed + signature + chain
    data[code_offset:] = text
    return bytes(data)

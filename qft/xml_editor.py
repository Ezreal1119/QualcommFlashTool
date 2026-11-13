"""Explicit XML edits with conflict detection, backups and atomic replacement."""

import hashlib
import os
import tempfile
import uuid
from pathlib import Path
from defusedxml import ElementTree as ET
from .expressions import ValidationError

MAX_XML = 2 * 1024**2


def read_xml(path):
    path = Path(path).resolve(strict=True)
    if path.suffix.lower() != ".xml" or path.stat().st_size > MAX_XML:
        raise ValidationError("Choose an XML file no larger than 2 MB.")
    raw = path.read_bytes()
    return raw.decode("utf-8-sig"), hashlib.sha256(raw).hexdigest()


def save_xml(path, text, expected_hash):
    path = Path(path).resolve(strict=True)
    data = text.encode("utf-8")
    if len(data) > MAX_XML:
        raise ValidationError("XML exceeds the 2 MB editor limit.")
    parsed = ET.fromstring(data)
    if parsed.tag != "data":
        raise ValidationError("Firmware XML must have a <data> root.")
    original = path.read_bytes()
    if hashlib.sha256(original).hexdigest() != expected_hash:
        raise ValidationError(
            "File changed outside the editor. Reopen it before saving."
        )
    backup = path.with_name(path.name + ".qft-backup-" + uuid.uuid4().hex[:8])
    # Backup is created before the original is replaced, never overwritten.
    with backup.open("xb") as f:
        f.write(original)
    fd, temporary = tempfile.mkstemp(prefix=".qft-edit-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(temporary, path.stat().st_mode & 0o777)
        os.replace(temporary, path)
    finally:
        if Path(temporary).exists():
            Path(temporary).unlink()
    return str(backup)

from pathlib import Path
from importlib import metadata
import shutil

root = Path(__file__).resolve().parent.parent
out = root / "resources/python-licenses"
out.mkdir(exist_ok=True)
for dist in metadata.distributions():
    name = dist.metadata.get("Name", "unknown")
    for f in dist.files or []:
        if any(
            s in str(f).lower() for s in ("license", "copying", "notice", "copyright")
        ):
            src = dist.locate_file(f)
            if src.is_file():
                dst = out / name / str(f)
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, dst)

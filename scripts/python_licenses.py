"""Print declared licenses for the distributions named on stdin, as JSON.

Used by scripts/check-licenses.mjs; reads metadata only, installs nothing.
"""

import json
import re
import sys
from importlib.metadata import PackageNotFoundError, metadata


def normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def license_of(meta) -> str:  # type: ignore[no-untyped-def]
    expression = meta.get("License-Expression")
    if expression:
        return str(expression)
    classifiers = [c.split("::")[-1].strip() for c in meta.get_all("Classifier") or [] if c.startswith("License ::")]
    if classifiers:
        return " OR ".join(classifiers)
    raw = (meta.get("License") or "").strip()
    return raw.splitlines()[0][:80] if raw else ""


rows = []
for name in sorted({normalize(n) for n in sys.stdin.read().split() if n}):
    try:
        rows.append({"name": name, "license": license_of(metadata(name))})
    except PackageNotFoundError:
        rows.append({"name": name, "license": ""})
print(json.dumps(rows))

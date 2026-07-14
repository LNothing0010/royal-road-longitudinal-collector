from __future__ import annotations

import base64
import hashlib
import io
import shutil
import tarfile
from pathlib import Path

PAYLOAD_SHA256 = "fdcb0f4ffd28a322f91c40b6609b1505910691929ad0e7897ffe03bb0e2e2609"


def main() -> None:
    parts_dir = Path(".installer")
    parts = sorted(parts_dir.glob("part*.txt"))
    if not parts:
        raise SystemExit("installer payload parts are missing")

    encoded = "".join(part.read_text(encoding="ascii").strip() for part in parts)
    archive = base64.b64decode(encoded.encode("ascii"), validate=True)
    actual = hashlib.sha256(archive).hexdigest()
    if actual != PAYLOAD_SHA256:
        raise SystemExit(f"installer checksum mismatch: {actual} != {PAYLOAD_SHA256}")

    root = Path.cwd().resolve()
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as bundle:
        members = bundle.getmembers()
        for member in members:
            target = (root / member.name).resolve()
            if root not in target.parents and target != root:
                raise SystemExit(f"unsafe archive path: {member.name}")
            if not member.isfile():
                raise SystemExit(f"unexpected non-file archive entry: {member.name}")

        for member in members:
            source = bundle.extractfile(member)
            if source is None:
                raise SystemExit(f"unable to read archive member: {member.name}")
            target = root / member.name
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(target.name + ".tmp-install")
            temporary.write_bytes(source.read())
            temporary.replace(target)

    Path("collector-package.zip").unlink(missing_ok=True)
    shutil.rmtree(parts_dir, ignore_errors=True)
    Path(__file__).unlink(missing_ok=True)
    print(f"materialized {len(members)} checksum-verified source files")


if __name__ == "__main__":
    main()

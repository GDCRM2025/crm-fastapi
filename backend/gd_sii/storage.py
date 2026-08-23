from __future__ import annotations

import os
import tempfile
from pathlib import Path


class PrivateXMLStorage:
    def __init__(self, root: str | None = None) -> None:
        self.root = Path(root or os.getenv("SII_PRIVATE_XML_DIR", "/var/lib/greendiamond/private/dte"))

    def put(self, legal_entity_id: int, sha256: str, data: bytes) -> str:
        target_dir = self.root / str(legal_entity_id)
        target_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(target_dir, 0o700)
        target = target_dir / f"{sha256}.xml"
        if target.exists():
            return str(target.relative_to(self.root))
        fd, temp_name = tempfile.mkstemp(prefix=".dte-", dir=target_dir)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temp_name, 0o600)
            os.replace(temp_name, target)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
        return str(target.relative_to(self.root))

    def get(self, storage_key: str) -> bytes:
        target = (self.root / storage_key).resolve()
        if self.root.resolve() not in target.parents:
            raise ValueError("Storage key invalida")
        return target.read_bytes()

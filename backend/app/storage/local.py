"""Local-disk media storage behind a tiny interface, so S3/GCS can replace it later."""
from __future__ import annotations

import os
import shutil
import tempfile
import uuid
from pathlib import Path


class LocalFileStorage:
    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()

    def path_for(self, relative_path: str) -> Path:
        """Resolve a stored path, refusing anything that escapes the storage root."""
        candidate = (self.root / relative_path).resolve()
        if self.root != candidate and self.root not in candidate.parents:
            raise ValueError("Path escapes storage root.")
        return candidate

    def save(self, data: bytes, *, subdir: str, extension: str) -> str:
        """Write bytes atomically; return the path relative to the storage root."""
        relative = f"{subdir}/{uuid.uuid4().hex}{extension}"
        target = self.path_for(relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=target.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
            os.replace(tmp_name, target)
        except BaseException:
            Path(tmp_name).unlink(missing_ok=True)
            raise
        return relative

    def delete(self, relative_path: str | None) -> None:
        if relative_path:
            self.path_for(relative_path).unlink(missing_ok=True)

    def delete_tree(self, subdir: str) -> None:
        shutil.rmtree(self.path_for(subdir), ignore_errors=True)

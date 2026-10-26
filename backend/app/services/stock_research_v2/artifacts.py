from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile


class ArtifactError(ValueError):
    pass


# A study panel is the largest bounded payload the store carries: one field's
# (date x instrument) float64 grid, so the ceiling is set per payload, not per store.
MAX_BINARY_BYTES = 32 * 1024 * 1024


@dataclass(frozen=True)
class StoredArtifact:
    sha256: str
    byte_size: int
    relative_path: str


class ArtifactStore:
    def __init__(self, root: Path, *, max_bytes: int = 8 * 1024 * 1024):
        if type(max_bytes) is not int or not 0 < max_bytes <= 64 * 1024 * 1024:
            raise ArtifactError("产物大小预算无效")
        self.root = Path(root).absolute()
        if self.root.resolve() != self.root:
            raise ArtifactError("产物根不能通过链接指向其他路径")
        self.root.mkdir(parents=True, exist_ok=True)
        self.max_bytes = max_bytes

    def _path(self, digest: str, suffix: str = ".json"):
        if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
            raise ArtifactError("产物指纹无效")
        target = self.root / (digest + suffix)
        if target.is_symlink() or target.resolve().parent != self.root:
            raise ArtifactError("产物路径越界或为链接")
        return target

    def read_json(self, digest: str):
        target = self._path(digest)
        try:
            with target.open("rb") as source:
                data = source.read(self.max_bytes + 1)
            if len(data) > self.max_bytes or hashlib.sha256(data).hexdigest() != digest:
                raise ArtifactError("产物过大或指纹不一致")
            payload = json.loads(data)
            canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf8")
            if canonical != data:
                raise ArtifactError("产物不是规范化JSON或含重复字段")
            return payload
        except (OSError, UnicodeError, ValueError, OverflowError, RecursionError):
            raise ArtifactError("产物不存在或无法校验") from None

    def put_json(self, payload) -> StoredArtifact:
        try:
            parts, size = [], 0
            for part in json.JSONEncoder(sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")).iterencode(payload):
                encoded = part.encode("utf8"); size += len(encoded)
                if size > self.max_bytes: raise ArtifactError("产物超过大小预算")
                parts.append(encoded)
            data = b"".join(parts)
            if json.loads(data) != payload:
                raise ArtifactError("JSON键或类型不能无损保存")
        except (ValueError, TypeError, OverflowError, RecursionError):
            raise ArtifactError("产物不是有界有限的JSON数据") from None
        digest = hashlib.sha256(data).hexdigest()
        target = self._path(digest)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.root, suffix=".tmp", delete=False) as output:
                temporary = Path(output.name)
                output.write(data); output.flush(); os.fsync(output.fileno())
            try:
                os.link(temporary, target)
            except FileExistsError:
                pass
            self.read_json(digest)
        except OSError:
            raise ArtifactError("产物写入失败，未返回可用引用") from None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return StoredArtifact(digest, len(data), target.name)

    def read_binary(self, digest: str, *, limit: int = MAX_BINARY_BYTES):
        """Read one content-addressed binary payload, byte-exact.

        ``read_json`` canonicalises and forbids NaN, so it cannot carry a research
        panel: a study grid is mostly NaN where a disclosure is missing, and a
        float64 that round-trips through decimal text is no longer the value the
        source recorded.  Here the digest *is* the whole contract — any payload whose
        bytes hash to it is returned unchanged, and nothing is interpreted.
        """
        if type(limit) is not int or not 0 < limit <= MAX_BINARY_BYTES:
            raise ArtifactError("二进制产物大小预算无效")
        target = self._path(digest, ".bin")
        try:
            with target.open("rb") as source:
                data = source.read(limit + 1)
            if len(data) > limit or hashlib.sha256(data).hexdigest() != digest:
                raise ArtifactError("二进制产物过大或指纹不一致")
            return data
        except ArtifactError:
            raise
        except (OSError, ValueError, OverflowError):
            raise ArtifactError("二进制产物不存在或无法校验") from None

    def put_binary(self, payload: bytes, *, limit: int = MAX_BINARY_BYTES) -> StoredArtifact:
        """Store one bounded binary payload under its own content digest."""
        if not isinstance(payload, (bytes, bytearray)) or type(limit) is not int or not 0 < limit <= MAX_BINARY_BYTES:
            raise ArtifactError("二进制产物无效")
        payload = bytes(payload)
        if not 0 < len(payload) <= limit:
            raise ArtifactError("二进制产物为空或超过大小预算")
        digest = hashlib.sha256(payload).hexdigest()
        target = self._path(digest, ".bin")
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.root, suffix=".tmp", delete=False) as output:
                temporary = Path(output.name)
                output.write(payload); output.flush(); os.fsync(output.fileno())
            try:
                os.link(temporary, target)
            except FileExistsError:
                pass
            self.read_binary(digest, limit=limit)
        except OSError:
            raise ArtifactError("二进制产物写入失败，未返回可用引用") from None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return StoredArtifact(digest, len(payload), target.name)

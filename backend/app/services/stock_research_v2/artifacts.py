from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile


class ArtifactError(ValueError):
    pass


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

    def _path(self, digest: str):
        if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
            raise ArtifactError("产物指纹无效")
        target = self.root / (digest + ".json")
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

"""AES-256-GCM 加密解密工具 —— API Key 安全存储"""

import base64
import os
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from dotenv import load_dotenv

# 加载 .env 到 os.environ，确保 os.getenv 能读到 AES_ENCRYPTION_KEY
_ENV_FILE = Path(__file__).resolve().parents[3] / ".env"
load_dotenv(_ENV_FILE)

# 模块级缓存，避免每次调用重新生成密钥
_cached_key: bytes | None = None


def _get_key() -> bytes:
    """从环境变量读取 AES-256 密钥（解码后必须 32 字节）

    缺失或非法时直接抛错，禁止进程内临时生成：
    临时密钥不落盘、多 worker 不一致，会导致 API Key 解密失败。
    """
    global _cached_key
    if _cached_key is not None:
        return _cached_key

    key_b64 = os.getenv("AES_ENCRYPTION_KEY", "").strip()
    if not key_b64:
        raise RuntimeError(
            "缺少 AES_ENCRYPTION_KEY：请在 .env 中配置 base64 编码的 32 字节密钥，"
            "可用 python -c \"import os,base64; print(base64.b64encode(os.urandom(32)).decode())\" 生成"
        )

    try:
        key = base64.b64decode(key_b64)
    except Exception as exc:
        raise RuntimeError("AES_ENCRYPTION_KEY 不是合法 base64") from exc

    if len(key) != 32:
        raise RuntimeError(
            f"AES_ENCRYPTION_KEY 解码后长度必须为 32 字节，当前为 {len(key)}"
        )

    _cached_key = key
    return _cached_key


def encrypt(plaintext: str) -> str:
    """使用 AES-256-GCM 加密字符串，返回 base64 编码的 nonce+密文"""
    key = _get_key()
    aesgcm = AESGCM(key)
    nonce = os.urandom(12)
    ciphertext = aesgcm.encrypt(nonce, plaintext.encode(), None)
    return base64.b64encode(nonce + ciphertext).decode()


def decrypt(encrypted: str) -> str:
    """解密 AES-256-GCM 加密的字符串"""
    key = _get_key()
    aesgcm = AESGCM(key)
    # 兼容被去掉 padding 的 base64 字符串
    padded = encrypted + "=" * (-len(encrypted) % 4)
    raw = base64.b64decode(padded)
    nonce = raw[:12]
    ciphertext = raw[12:]
    return aesgcm.decrypt(nonce, ciphertext, None).decode()


def mask_key(api_key: str) -> str:
    """脱敏显示 API Key：仅显示前 4 位和后 4 位"""
    if len(api_key) <= 12:
        return "****"
    return f"{api_key[:4]}****{api_key[-4:]}"

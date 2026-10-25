"""AI 看盘主播云端 TTS —— 微软 Edge 神经网络自然音色（edge-tts）

浏览器 Web Speech 仅 Edge 能用到"晓晓/云希"等自然音色；
这里由后端统一代理合成 mp3,任何浏览器都能用,失败由前端回退本地语音。
"""

from __future__ import annotations

import asyncio
import logging

import edge_tts

logger = logging.getLogger("qihuo.ai_anchor")

# 云端自然音色白名单（前端下拉与此一致,GET /api/ai-anchor/voices 下发）
CLOUD_VOICES: list[dict[str, str]] = [
    {"id": "zh-CN-YunxiNeural", "name": "云希", "desc": "男 · 阳光沉稳"},
    {"id": "zh-CN-XiaoxiaoNeural", "name": "晓晓", "desc": "女 · 温暖亲切"},
    {"id": "zh-CN-YunjianNeural", "name": "云健", "desc": "男 · 浑厚磁性"},
    {"id": "zh-CN-XiaoyiNeural", "name": "晓伊", "desc": "女 · 甜美活泼"},
    {"id": "zh-CN-YunyangNeural", "name": "云扬", "desc": "男 · 新闻播报"},
    {"id": "zh-CN-XiaochenNeural", "name": "晓辰", "desc": "女 · 沉稳干练"},
]

# 单次合成超时（秒）：网络不通时快速失败让前端回退
_SYNTH_TIMEOUT_S = 20.0
# 合成文本上限（字符）
_MAX_TEXT = 500


def cloud_voice_ids() -> set[str]:
    """白名单音色 ID 集合。"""
    return {item["id"] for item in CLOUD_VOICES}


def normalize_rate(rate: float) -> str:
    """语速 0.5-1.5 → edge-tts 百分比串（1.0 → +0%）。"""
    safe = min(1.5, max(0.5, float(rate)))
    percent = round((safe - 1.0) * 100)
    return f"{percent:+d}%"


async def synthesize(text: str, voice: str, rate: float) -> bytes:
    """合成 mp3 音频字节;voice 不在白名单抛 ValueError,超时/网络异常抛 RuntimeError。"""
    clean = (text or "").strip()[:_MAX_TEXT]
    if not clean:
        raise ValueError("合成文本为空")
    if voice not in cloud_voice_ids():
        raise ValueError(f"不支持的音色: {voice}")

    async def _run() -> bytes:
        comm = edge_tts.Communicate(clean, voice, rate=normalize_rate(rate))
        data = bytearray()
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                data.extend(chunk["data"])
        return bytes(data)

    try:
        return await asyncio.wait_for(_run(), timeout=_SYNTH_TIMEOUT_S)
    except asyncio.TimeoutError as exc:
        raise RuntimeError(f"云端TTS合成超时(>{_SYNTH_TIMEOUT_S:.0f}s)") from exc
    except ValueError:
        raise
    except Exception as exc:
        logger.warning("云端TTS合成失败 voice=%s err=%s", voice, exc)
        raise RuntimeError(f"云端TTS合成失败: {exc}") from exc

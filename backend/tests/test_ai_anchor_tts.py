"""AI 看盘主播云端 TTS 端点测试 —— 鉴权/白名单/长度/成功合成(mock 免网络)"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch
from uuid import uuid4

from httpx import ASGITransport, AsyncClient

from app.models.user import User


def _make_user() -> User:
    return User(
        id=uuid4(),
        username="tts-tester",
        hashed_password="x",
        role="user",
        status="active",
    )


def _get_test_app(user: User | None):
    from app.core.deps import AuthContext, get_auth_context
    from app.main import app

    if user is not None:

        async def _mock_ctx():
            return AuthContext(user=user, trading_mode="live")

        app.dependency_overrides[get_auth_context] = _mock_ctx
    return app


def _clear_overrides() -> None:
    from app.main import app

    app.dependency_overrides.clear()


def _body(**overrides: object) -> dict:
    payload: dict = {
        "text": "螺纹钢看多，建议做单。",
        "voice": "zh-CN-YunxiNeural",
        "rate": 1.0,
    }
    payload.update(overrides)
    return payload


async def test_tts_requires_auth():
    """未带 token → 401"""
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/ai-anchor/tts", json=_body())
        assert resp.status_code == 401


async def test_tts_invalid_voice_rejected():
    """音色不在白名单 → 400"""
    user = _make_user()
    app = _get_test_app(user)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/ai-anchor/tts", json=_body(voice="zh-CN-EvilVoice")
            )
            assert resp.status_code == 400
    finally:
        _clear_overrides()


async def test_tts_text_too_long_rejected():
    """超长文本 → 422（schema 上限 500）"""
    user = _make_user()
    app = _get_test_app(user)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/ai-anchor/tts", json=_body(text="长" * 501)
            )
            assert resp.status_code == 422
    finally:
        _clear_overrides()


async def test_tts_happy_path_returns_mp3():
    """正常合成：mock synthesize 返回字节 → audio/mpeg 响应,透传语速"""
    user = _make_user()
    app = _get_test_app(user)
    try:
        with patch(
            "app.api.ai_anchor.synthesize",
            new_callable=AsyncMock,
            return_value=b"FAKE_MP3_BYTES",
        ) as mock_synth:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/ai-anchor/tts", json=_body(rate=1.25)
                )
                assert resp.status_code == 200
                assert resp.headers["content-type"].startswith("audio/mpeg")
                assert resp.content == b"FAKE_MP3_BYTES"
        mock_synth.assert_awaited_once()
        args = mock_synth.await_args.args
        assert args[1] == "zh-CN-YunxiNeural"
        assert abs(args[2] - 1.25) < 1e-9
    finally:
        _clear_overrides()


async def test_tts_synthesis_failure_502():
    """云端合成失败（网络/超时）→ 502,前端据此回退本地语音"""
    user = _make_user()
    app = _get_test_app(user)
    try:
        with patch(
            "app.api.ai_anchor.synthesize",
            new_callable=AsyncMock,
            side_effect=RuntimeError("云端TTS合成失败: 网络不可达"),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post("/api/ai-anchor/tts", json=_body())
                assert resp.status_code == 502
                assert "云端TTS" in resp.json()["detail"]
    finally:
        _clear_overrides()


async def test_voices_list_no_auth_required_but_scoped():
    """voices 列表需登录;返回云端音色白名单"""
    user = _make_user()
    app = _get_test_app(user)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/ai-anchor/voices")
            assert resp.status_code == 200
            voices = resp.json()["voices"]
            ids = [v["id"] for v in voices]
            assert "zh-CN-YunxiNeural" in ids
            assert "zh-CN-XiaoxiaoNeural" in ids
    finally:
        _clear_overrides()


# ---------- 播报文案长度上限回归 ----------

def test_commentary_cap_allows_long_multitf_text() -> None:
    """多周期播报常 600-900 字：400 上限会拦腰截断（回归：曾截在"整理，M"）"""
    from app.services.ai_anchor.prompts import _MAX_COMMENTARY, parse_anchor_output

    assert _MAX_COMMENTARY >= 2000

    def _parse(text: str) -> str:
        payload = (
            '{"direction":"neutral","action":"wait",'
            f'"commentary":"{text}","key_levels":[]}}'
        )
        out = parse_anchor_output(payload)
        assert out["parse_ok"] is True
        return out["commentary"]

    # 928 字（> 旧上限 400，< 新上限 2000）：完整保留不截断
    mid_text = "多周期共振分析。" + "价格在2900附近窄幅整理，MACD动能衰减。" * 40
    assert len(mid_text) > 600
    assert _parse(mid_text) == mid_text

    # 超过 2000 上限：夹到上限（防 LLM 输出异常超长）
    huge_text = "涨。" * 3000
    assert len(huge_text) > _MAX_COMMENTARY
    assert _parse(huge_text) == huge_text[:_MAX_COMMENTARY]

"""Provider 工厂 —— 根据 api_type 返回对应实现"""

from app.services.ai.providers.base import AIProviderBase
from app.services.ai.providers.claude_provider import ClaudeProvider
from app.services.ai.providers.openai_provider import OpenAIProvider


def create_provider(
    api_type: str,
    base_url: str,
    api_key: str,
    model_id: str,
    thinking_enabled: bool | None = None,
) -> AIProviderBase:
    """根据 api_type 创建对应的 Provider 实例"""
    if api_type == "openai":
        return OpenAIProvider(base_url, api_key, model_id)
    if api_type == "claude":
        return ClaudeProvider(
            base_url, api_key, model_id, thinking_enabled=thinking_enabled
        )
    if api_type == "jev":
        # TypeSafe Jev 是 System One 决策模型（POST /v1/systemone，返回
        # 结构化判断+概率），没有对话补全端点；渠道连通性测试走
        # api.ai_provider.test_provider，不经过本工厂。
        raise ValueError("Jev 为 System One 决策模型，不支持对话调用，请换用对话模型")
    raise ValueError(f"不支持的接口规范: {api_type}")

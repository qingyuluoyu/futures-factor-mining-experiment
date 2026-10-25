"""AI Provider 抽象基类 —— 统一聊天接口协议"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, AsyncIterator


@dataclass
class StreamEvent:
    """统一流式事件

    type: message | thinking | tool_call | tool_result | confirmation_request
          | react_round | subagent_plan | subagent_update | error | done
    extra: 扩展载荷（子代理 plan/update 等），由 SSE 层展开
    """
    type: str
    content: str = ""
    tool_name: str = ""
    tool_args: str = ""
    tool_call_id: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolDef:
    """工具定义"""
    name: str
    description: str
    parameters: dict
    requires_confirmation: bool = False


class AIProviderBase(ABC):
    """AI 渠道抽象基类，子类需实现 OpenAI 和 Claude 两种规范"""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model_id: str,
        thinking_enabled: bool | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model_id = model_id
        # 渠道级思考开关：None=跟随模型自动，True=强制开，False=强制关（仅 Claude 规范实现）
        self.thinking_enabled = thinking_enabled

    @abstractmethod
    async def chat_stream(
        self,
        messages: list[dict],
        tools: list[ToolDef] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """流式聊天，返回统一事件迭代器"""
        ...

    @abstractmethod
    async def list_models(self) -> list[str]:
        """获取渠道可用模型列表"""
        ...

    @abstractmethod
    def build_request(
        self,
        messages: list[dict],
        tools: list[ToolDef] | None = None,
    ) -> dict:
        """构造请求体"""
        ...

    @abstractmethod
    async def parse_stream(self, response) -> AsyncIterator[StreamEvent]:
        """解析流式响应"""
        ...

"""Agent 工具层测试（只读工具集）。

覆盖：
  - 请求级上下文（current_user_id_var / thinking_callback_var）的读写
  - get_user_info_tools：合法 token 解析 / 非法 token 兜底
  - what_time_is_now：时间格式

知识库检索工具 search_rag 的测试见 tests/agent/test_agent_rag_integration.py。
"""
import re

from app.agent.agent_tools import (
    get_current_user_id_from_context,
    get_thinking_callback_from_context,
    get_user_info_tools,
    set_current_user_id,
    set_thinking_callback,
    what_time_is_now,
)
from app.utils.auth_utils import generate_token


# ---------------------------------------------------------------------------
# 请求级上下文
# ---------------------------------------------------------------------------
def test_user_id_context_roundtrip():
    set_current_user_id("u-ctx-1")
    assert get_current_user_id_from_context() == "u-ctx-1"


def test_thinking_callback_context_roundtrip():
    async def cb(*args, **kwargs):
        return None

    set_thinking_callback(cb)
    assert get_thinking_callback_from_context() is cb


# ---------------------------------------------------------------------------
# get_user_info_tools
# ---------------------------------------------------------------------------
async def test_get_user_info_tools_with_valid_token():
    token, _ = generate_token("u-1", "张三", "zhangsan@example.com")

    out = await get_user_info_tools.ainvoke({"token": token})

    assert "u-1" in out
    assert "张三" in out


async def test_get_user_info_tools_with_invalid_token():
    out = await get_user_info_tools.ainvoke({"token": "not-a-jwt"})

    assert "无法解析" in out


# ---------------------------------------------------------------------------
# what_time_is_now
# ---------------------------------------------------------------------------
async def test_what_time_is_now_format():
    out = await what_time_is_now.ainvoke({})

    assert re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", out)

"""Agent 工具与请求级上下文。

工具集为只读：时间、当前用户信息。知识库检索由 `agent_rag_tool.search_rag` 提供。
`current_user_id_var` / `thinking_callback_var` 由编排层在每次请求开始时注入，
供工具与中间件读取，避免把用户身份塞进模型上下文。
"""
import datetime
from collections.abc import Callable
from contextvars import ContextVar

from langchain_core.tools import tool

from app.utils.auth_utils import decode_django_jwt

current_user_id_var: ContextVar[str] = ContextVar('current_user_id', default=None)
thinking_callback_var: ContextVar[Callable | None] = ContextVar('thinking_callback', default=None)


def set_current_user_id(user_id: str):
    """设置当前用户ID到上下文"""
    current_user_id_var.set(user_id)


def get_current_user_id_from_context() -> str:
    """从上下文获取当前用户ID"""
    return current_user_id_var.get()


def set_thinking_callback(callback):
    """设置思考过程回调到上下文"""
    thinking_callback_var.set(callback)


def get_thinking_callback_from_context():
    """从上下文获取思考过程回调"""
    return thinking_callback_var.get()


@tool(description="当用户明确问自己的ID和用户名时，从JWT中获取当前用户ID和用户名，参数为完整的JWT token字符串")
async def get_user_info_tools(token: str) -> str:
    """获取用户信息工具"""
    payload = decode_django_jwt(token)
    if payload:
        user_id = payload.get("user_id", "未知")
        # 兼容两种 claim 命名：本应用 generate_token 签发的是 username，
        # 部分外部/旧 token 使用 user_name。
        user_name = payload.get("user_name") or payload.get("username") or "未知"
        return f"用户信息：\n- 用户ID: {user_id}\n- 用户名: {user_name}"
    else:
        return "无法解析JWT token，无法获取用户信息"


@tool(description="用于获取当前年月日时分的工具")
async def what_time_is_now() -> str:
    """获取当前年月日时分的工具"""
    return f"当前时间是：{datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}"

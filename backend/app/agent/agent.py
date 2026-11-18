"""生产 Agent：LangGraph create_agent 编译图 + 流式事件转发。

设计要点：
- 每次请求新建编译图，避免全局状态污染；
- 工具集为只读（知识库检索、时间、当前用户），因此不需要 HITL 审批与 checkpoint；
- 多轮上下文由 session_manager 取历史后显式拼进 messages，不依赖 checkpointer。
"""
import asyncio
import inspect
import json
import time
from collections.abc import AsyncGenerator

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.tools import BaseTool

from app.agent.agent_rag_tool import init_rag_guard, search_rag
from app.agent.agent_tools import (
    get_thinking_callback_from_context,
    get_user_info_tools,
    set_current_user_id,
    set_thinking_callback,
    what_time_is_now,
)
from app.core.logger_handler import logger
from app.core.settings import settings
from app.services import session_manager as sm
from app.utils.prompt_loader import load_prompt


class AgentFactory:
    """
    生产 Agent 工厂类
    支持：
    - 每次调用创建全新的 LangChain 1.0+ create_agent 编译图实例
    - 动态注入工具、提示词、模型配置、中间件
    - 支持异步流式调用（astream_events v2）
    """

    def __init__(
            self,
            model: str = "qwen3-max",
            api_key: str | None = None,
            default_tools: list[BaseTool] | None = None,
            default_middleware: list | None = None,
            default_system_prompt: str | None = None,
    ):
        """
        初始化工厂配置（仅配置，不创建实例）
        :param model: 默认模型名称
        :param api_key: 默认 API Key（不传则从env读取）
        :param default_tools: 默认工具列表
        :param default_system_prompt: 默认系统提示词
        """
        self.model = model
        self.api_key = api_key or settings.CHAT_API_KEY or None
        self.default_tools = default_tools or self._get_default_tools()
        self.default_middleware = default_middleware or self._get_default_middleware()
        self.default_system_prompt = default_system_prompt or self._get_default_system_prompt()

    @staticmethod
    def _get_default_tools() -> list[BaseTool]:
        """获取默认工具列表（全部只读）"""
        return [
            what_time_is_now,
            get_user_info_tools,
            search_rag,
        ]

    def _get_default_middleware(self) -> list:
        """获取默认中间件列表（日志/重试/工具调用上限）"""
        try:
            from app.agent.agent_middleware import get_middleware

            return get_middleware()
        except ImportError:
            logger.warning("Agent middleware unavailable; continuing without middleware.", exc_info=True)
            return []

    @staticmethod
    def _get_default_system_prompt() -> str:
        """获取默认系统提示词"""
        return load_prompt('main_prompt')

    def _create_chat_model(self, custom_model: str | None = None):
        """内部方法：创建聊天模型实例（统一 OpenAI 兼容协议）"""
        from app.utils.factory import create_chat_openai

        model = custom_model or settings.OPENAI_MODEL_NAME or "gpt-4o-mini"
        logger.info(f"🤖 Agent使用OpenAI兼容模型: {model}")
        return create_chat_openai(
            model=model,
            api_key=settings.OPENAI_API_KEY or None,
            base_url=settings.OPENAI_BASE_URL or None,
            streaming=True,
            top_p=0.7,
        )

    def create_agent(
            self,
            custom_tools: list[BaseTool] | None = None,
            custom_model: str | None = None,
            custom_system_prompt: str | None = None,
            **kwargs
    ):
        """
        核心工厂方法：创建 LangChain 1.0+ create_agent 编译图实例。
        每次调用都会生成新的实例，彻底避免全局状态污染。

        :param custom_tools: 自定义工具列表（覆盖默认）
        :param custom_model: 自定义模型（覆盖默认）
        :param custom_system_prompt: 自定义系统提示词（覆盖默认）
        :param kwargs: 其他 create_agent 参数（debug/name 等）
        :return: 全新的 CompiledStateGraph 实例
        """
        chat_model = self._create_chat_model(custom_model)
        tools = custom_tools or self.default_tools
        system_prompt = custom_system_prompt or self.default_system_prompt

        # 注：create_agent 不提供 AgentExecutor 时代的 max_iterations/handle_parsing_errors，
        # 错误兜底由编排层 try/except 负责（见 get_agent_response / get_agent_stream_response）。
        return create_agent(
            chat_model,
            tools,
            system_prompt=system_prompt,
            middleware=self.default_middleware,
            **kwargs
        )


# 同一会话并发互斥（进程内；键为 user_id:session_id），避免同一会话被并发写入。
_session_locks: dict[str, asyncio.Lock] = {}


def _get_session_lock(user_id: str, session_id: str) -> asyncio.Lock:
    """同一会话的互斥锁（进程内；键为 user_id:session_id）。"""
    key = f"{user_id}:{session_id}"
    lock = _session_locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _session_locks[key] = lock
    return lock


# 初始化全局工厂配置
agent_factory = AgentFactory()


def get_agent():
    """
    获取 create_agent 编译图实例（LangGraph）
    :return: CompiledStateGraph 实例
    """
    return agent_factory.create_agent()


def _build_chat_history(history: list[tuple] | None) -> list[BaseMessage]:
    """将 [(user_msg, assistant_msg), ...] 历史转换为 Human/AI 消息对。"""
    chat_history: list[BaseMessage] = []
    if history:
        for user_msg, assistant_msg in history:
            chat_history.append(HumanMessage(content=user_msg))
            chat_history.append(AIMessage(content=assistant_msg))
    return chat_history


def _collect_steps(messages: list[BaseMessage]) -> list[dict]:
    """从消息状态中提取工具调用步骤（AIMessage.tool_calls ↔ ToolMessage）。"""
    steps: list[dict] = []
    for msg in messages:
        if not isinstance(msg, AIMessage):
            continue
        for tool_call in getattr(msg, "tool_calls", []) or []:
            tool_output = None
            for other in messages:
                if isinstance(other, ToolMessage) and other.tool_call_id == tool_call.get("id"):
                    tool_output = other.content
                    break
            steps.append({
                "thought": None,
                "tool": tool_call.get("name"),
                "tool_input": tool_call.get("args"),
                "tool_output": tool_output,
            })
    return steps


async def get_agent_response(
        query: str,
        history: list[tuple] | None = None,
        user_id: str | None = None,
        custom_tools: list[BaseTool] | None = None,
        **kwargs
):
    """
    获取 Agent 响应（非流式，用于评测/调试）

    :param query: 用户查询
    :param history: 会话历史 [(user_msg, assistant_msg), ...]
    :param user_id: 用户ID
    :param custom_tools: 自定义工具（可选）
    :param kwargs: 其他工厂参数
    """
    if user_id:
        set_current_user_id(user_id)

    try:
        agent = agent_factory.create_agent(custom_tools=custom_tools, **kwargs)
        chat_history = _build_chat_history(history)
        state = await agent.ainvoke(
            {"messages": [*chat_history, HumanMessage(content=query)]},
        )

        messages = state.get("messages", [])
        final = next((m for m in reversed(messages) if isinstance(m, AIMessage)), None)
        response = (final.content or "") if final is not None else ""

        return {
            "response": response if response else "抱歉，我无法理解您的请求。",
            "steps": _collect_steps(messages),
        }

    except Exception as e:
        logger.error(f"Agent 执行错误: {str(e)}", exc_info=True)
        return {
            "response": f"抱歉，处理您的请求时出现了错误: {str(e)}",
            "steps": []
        }


def _message_text(output) -> str:
    """从模型消息/终态输出提取文本（str/list content 形态）。"""
    content = getattr(output, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(item.get("text", "") for item in content if isinstance(item, dict))
    return ""


async def _emit_agent_events(agent, inputs, thinking_queue, full_response) -> str | None:
    """执行 astream_events 并转发 thinking/response 事件；返回错误文本或 None。"""
    async def emit_thinking(stage, content, details):
        callback = get_thinking_callback_from_context()
        if callback is None:
            return
        result = callback({"type": "thinking", "stage": stage,
                           "content": content, "details": details})
        if inspect.isawaitable(result):
            await result

    async def emit_response(content: str):
        if not content:
            return
        full_response.append(content)
        await thinking_queue.put({"type": "response", "content": content})

    try:
        t_first = time.perf_counter()
        first_token_ms: float | None = None
        async for event in agent.astream_events(inputs, version="v2"):
            event_type = event.get("event")
            event_data = event.get("data") or {}
            node = (event.get("metadata") or {}).get("langgraph_node")
            # 只转发主模型节点（langgraph node = model）的模型输出：
            # 工具内部（如 search_rag 的规划器/可答性评估）也会调用模型，
            # 其 token（常是内部 JSON）若一并转发会污染回答正文。
            if (event_type in ("on_chat_model_stream", "on_chat_model_end")
                    and node is not None and node != "model"):
                continue
            if event_type == "on_chat_model_stream":
                chunk = event_data.get("chunk")
                if getattr(chunk, "tool_call_chunks", None):
                    continue
                content = getattr(chunk, "content", None)
                if content is None and isinstance(chunk, dict):
                    content = chunk.get("content")
                if isinstance(content, str):
                    if first_token_ms is None and content:
                        first_token_ms = (time.perf_counter() - t_first) * 1000
                        logger.info(f"【Agent耗时】首token first_token={first_token_ms:.0f}ms")
                    await emit_response(content)
                elif isinstance(content, list):
                    text = "".join(item.get("text", "")
                                   for item in content if isinstance(item, dict))
                    if first_token_ms is None and text:
                        first_token_ms = (time.perf_counter() - t_first) * 1000
                        logger.info(f"【Agent耗时】首token first_token={first_token_ms:.0f}ms")
                    await emit_response(text)
            elif event_type == "on_tool_start":
                tool = event.get("name", "unknown_tool")
                await emit_thinking("tool_start", f"正在调用 {tool}",
                                    {"tool": tool, "tool_input": event_data.get("input")})
            elif event_type == "on_tool_end":
                tool = event.get("name", "unknown_tool")
                tool_output = event_data.get("output")
                if isinstance(tool_output, BaseMessage):
                    tool_output = tool_output.content
                await emit_thinking("tool_end", f"{tool} 执行完成",
                                    {"tool": tool, "tool_output": tool_output})
            elif event_type == "on_chat_model_end":
                # 非流式/假模型不产生 on_chat_model_stream，这里兜底收割最终文本；
                # 真实流式模型已逐块累积（full_response 非空）则跳过，避免重复。
                if not full_response:
                    await emit_response(_message_text(event_data.get("output")))
        return None
    except Exception as e:
        logger.error(f"Agent 执行错误: {e}", exc_info=True)
        return str(e)


async def get_agent_stream_response(
        query: str,
        session_id: str,
        user_id: str,
        custom_tools: list[BaseTool] | None = None,
        rag_context: str = "",
        rag_searched_queries: list[str] | None = None,
        **kwargs
) -> AsyncGenerator[str, None]:
    """获取 Agent 流式响应。

    时序：空 response 帧（前端建气泡）→ 思考/内容帧 → done。
    完成后把 (query, response) 落库，供刷新页面恢复历史。
    """
    async with _get_session_lock(user_id, session_id):
        thinking_queue = asyncio.Queue()
        agent_result_holder = {"error": None}
        agent_done = asyncio.Event()

        async def thinking_callback(data: dict):
            logger.info(f"【思考过程】{data.get('stage', 'unknown')}: {data.get('content', '')}")
            await thinking_queue.put(data)

        async def run_agent():
            try:
                set_current_user_id(user_id)
                set_thinking_callback(thinking_callback)
                init_rag_guard(rag_searched_queries)

                history = await sm.session_manager.get_history(session_id, user_id)
                chat_history = _build_chat_history(history)
                system_prompt = (
                    load_prompt("rag_context_prompt").replace("{context}", rag_context)
                    if rag_context else agent_factory.default_system_prompt
                )
                t_create = time.perf_counter()
                agent = agent_factory.create_agent(
                    custom_tools=custom_tools, custom_system_prompt=system_prompt, **kwargs
                )
                create_ms = (time.perf_counter() - t_create) * 1000
                logger.info(f"【Agent耗时】准备阶段 history条数={len(history)} create_agent={create_ms:.0f}ms rag_context_chars={len(rag_context)}")

                full_response = []
                inputs = {"messages": [*chat_history, HumanMessage(content=query)]}
                error = await _emit_agent_events(agent, inputs, thinking_queue, full_response)

                if error:
                    agent_result_holder["error"] = error
                    return

                response = "".join(full_response) if full_response else "抱歉，我无法理解您的请求。"
                await sm.session_manager.add_message(session_id, user_id, query, response)
            except Exception as e:
                logger.error(f"【Agent流式响应】Agent执行失败: {e}", exc_info=True)
                agent_result_holder["error"] = str(e)
            finally:
                agent_done.set()

        agent_task = asyncio.create_task(run_agent())
        try:
            yield f"data: {json.dumps({'type': 'response', 'content': '', 'session_id': session_id}, ensure_ascii=False)}\n\n"
            while not agent_done.is_set():
                try:
                    event = await asyncio.wait_for(thinking_queue.get(), timeout=0.1)
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                    thinking_queue.task_done()
                except TimeoutError:
                    continue
            while not thinking_queue.empty():
                try:
                    event = thinking_queue.get_nowait()
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                    thinking_queue.task_done()
                except asyncio.QueueEmpty:
                    break
            await agent_task

            if agent_result_holder.get("error"):
                content = agent_result_holder["error"]
                yield f"data: {json.dumps({'type': 'error', 'content': content, 'session_id': session_id}, ensure_ascii=False)}\n\n"
                yield f"data: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"
                return

            yield f"data: {json.dumps({'type': 'done', 'session_id': session_id}, ensure_ascii=False)}\n\n"
        except Exception as e:
            logger.error(f"【Agent流式响应】处理请求失败: {e}", exc_info=True)
            agent_task.cancel()
            try:
                await agent_task
            except asyncio.CancelledError:
                pass
            error_message = f"错误: {str(e)}"
            yield f"data: {json.dumps({'type': 'error', 'content': error_message, 'session_id': session_id}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"

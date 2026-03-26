"""
BOB Agent — connects to planner_agent MCP server.
Graph is IDENTICAL to your original.
The planner tools appear alongside your existing tools.
"""

from langgraph.graph import StateGraph, START, END
from typing import TypedDict, Annotated
from langchain_core.messages import BaseMessage, HumanMessage
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition
from langchain_community.tools import DuckDuckGoSearchRun
from langchain_core.tools import tool, BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient
from dotenv import load_dotenv
import aiosqlite, requests, asyncio, threading, traceback
from langchain_groq import ChatGroq
from langchain_mcp_adapters.callbacks import Callbacks

load_dotenv()

_ASYNC_LOOP = asyncio.new_event_loop()
_ASYNC_THREAD = threading.Thread(target=_ASYNC_LOOP.run_forever, daemon=True)
_ASYNC_THREAD.start()
def _submit_async(coro): return asyncio.run_coroutine_threadsafe(coro, _ASYNC_LOOP)
def run_async(coro): return _submit_async(coro).result()
def submit_async_task(coro): return _submit_async(coro)
notification_queue = asyncio.Queue()

groq_api_key = ""
llm = ChatGroq(groq_api_key=groq_api_key, model_name="openai/gpt-oss-120b")

search_tool = DuckDuckGoSearchRun(region="us-en")

async def handle_notification(event):
    await notification_queue.put(event)

class MyCallbacks(Callbacks):
    async def on_notification(self, notification, context):
        print("Notification:", notification)
        await handle_notification(notification)

@tool
def get_stock_price(symbol: str) -> dict:
    """Fetch latest stock price for a given symbol."""
    url = f"https://www.alphavantage.co/query?function=GLOBAL_QUOTE&symbol={symbol}&apikey=C9PE94QUEW9VWGFM"
    return requests.get(url).json()

MCP_SERVERS = {
    "loan_queries": {
        "transport": "stdio",
        "command": "python",
        "args": [r"C:\Users\rayid\Desktop\QABrain\mcp_hackathon\mcp_server.py"],
    },
    "planner_agent": {
        "transport": "stdio",
        "command": "python",
        "args": [r"C:\Users\rayid\Desktop\QABrain\mcp_hackathon\planner_mcp_server.py"],
    },
}

def load_mcp_tools_safe() -> list[BaseTool]:
    try:
        client = MultiServerMCPClient(MCP_SERVERS, callbacks=MyCallbacks())
        all_tools = run_async(client.get_tools())
        print(f"[MCP] All servers OK - {len(all_tools)} tools:")
        for t in all_tools: print(f"  - {t.name}")
        return all_tools
    except Exception as e:
        print(f"[MCP] Combined load failed: {e}\n[MCP] Loading individually...")
    all_tools = []
    for name, config in MCP_SERVERS.items():
        try:
            c = MultiServerMCPClient({name: config}, callbacks=MyCallbacks())
            tools = run_async(c.get_tools())
            print(f"[MCP] {name}: OK - {len(tools)} tools")
            all_tools.extend(tools)
        except Exception as e:
            print(f"[MCP] {name}: FAILED - {e}")
            traceback.print_exc()
    return all_tools

mcp_tools = load_mcp_tools_safe()
tools = [search_tool, get_stock_price, *mcp_tools]
llm_with_tools = llm.bind_tools(tools) if tools else llm

class ChatState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]

async def chat_node(state: ChatState):
    messages = state["messages"]
    response = await llm_with_tools.ainvoke(messages)
    return {"messages": [response]}

tool_node = ToolNode(tools) if tools else None

async def _init_checkpointer():
    conn = await aiosqlite.connect(database="chatbot.db")
    return AsyncSqliteSaver(conn)
checkpointer = run_async(_init_checkpointer())

graph = StateGraph(ChatState)
graph.add_node("chat_node", chat_node)
graph.add_edge(START, "chat_node")
if tool_node:
    graph.add_node("tools", tool_node)
    graph.add_conditional_edges("chat_node", tools_condition)
    graph.add_edge("tools", "chat_node")
else:
    graph.add_edge("chat_node", END)

chatbot = graph.compile(checkpointer=checkpointer)

async def _alist_threads():
    all_threads = set()
    async for checkpoint in checkpointer.alist(None):
        all_threads.add(checkpoint.config["configurable"]["thread_id"])
    return list(all_threads)

def retrieve_all_threads():
    return run_async(_alist_threads())

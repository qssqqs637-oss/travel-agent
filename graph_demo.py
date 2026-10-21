
import os
from typing import TypedDict, Annotated
from dotenv import load_dotenv
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition
from langchain_core.messages import HumanMessage, AIMessage
from langchain_core.tools import tool
from langchain_deepseek import ChatDeepSeek
load_dotenv(override=True)

model = ChatDeepSeek(
      model="deepseek-v4-pro",
      api_key=os.getenv("DEEPSEEK_API_KEY"),
      base_url=os.getenv("DEEPSEEK_API_URL"),
      temperature=0.7,
)
@tool
def get_weather(city: str) -> str:
      """获取指定城市的天气信息。"""
      return f"{city}天气晴朗"
model_with_tools=model.bind_tools([get_weather])
class State(TypedDict):
    messages: Annotated[list, add_messages]

def agent_node(state):
    reply=model_with_tools.invoke(state["messages"])
    return {"messages":[reply]}
def planner(state):
    return {"messages": [AIMessage("【规划】我是调度者，我先想清楚怎么做")]}

tool_node=ToolNode([get_weather])
def executor(state):
    reply = model.invoke(state["messages"])
    return {"messages": [reply]}


def synthesizer(state):
    return {"messages": [AIMessage("【汇总】我是汇总者，结果整理好了")]}
graph=StateGraph(State)
graph.add_node("planner", planner)
graph.add_node("executor", executor)
graph.add_node("synthesizer", synthesizer)
graph.add_edge(START, "planner")
graph.add_edge("planner", "executor")
graph.add_edge("executor", "synthesizer")
graph.add_edge("synthesizer", END)
app = graph.compile()
result = app.invoke({"messages": [HumanMessage("帮我规划一次旅行")]})

for m in result["messages"]:
    print(m.content)
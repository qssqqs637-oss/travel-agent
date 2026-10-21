import os
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Dict, Any
from langchain.tools import tool
from langchain_openai import ChatOpenAI
from langchain.agents import create_agent
from  langchain_deepseek import ChatDeepSeek
from pydantic import Field,BaseModel
from langgraph.checkpoint.memory import InMemorySaver
from langchain_core.prompts import ChatPromptTemplate
load_dotenv(override=True)
DEEPSEEK_API_KEY=os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_API_URL=os.getenv("DEEPSEEK_API_URL")
model=ChatDeepSeek(
    model="deepseek-v4-pro",
    api_key=DEEPSEEK_API_KEY,
    base_url=DEEPSEEK_API_URL,
    temperature=0.7,
)
@tool(description="获取指定城市的天气信息")
def get_weather(city:str)->str:
    return f"{city}天气晴朗"
agent=create_agent(
    model=model,
    tools=[get_weather],
    system_prompt="你是一个乐于助人的助手，可以使用工具来帮助用户解决问题。"
)
result=agent.invoke({"messages":[{"role":"user","content":"北京天气怎么样"}]})
for msg in result["messages"]:
      print(f"[{msg.type}] {msg.content}")
      print("-" * 40)
# print(result["messages"][-1].content)




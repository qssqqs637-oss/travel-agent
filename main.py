from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from pydantic import BaseModel
from langchain_deepseek import ChatDeepSeek
from langchain_core.messages import HumanMessage
import os
from graph.workflow import app as graph_app
load_dotenv()
app=FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
model = ChatDeepSeek(
    model="deepseek-v4-pro",
    api_key=os.getenv("DEEPSEEK_API_KEY"),
    base_url=os.getenv("DEEPSEEK_API_URL"),
    temperature=0.7,
)
class ChatRequest(BaseModel):
    message:str
@app.get("/")
def root():
    return {"message":"Hello Agent"}
@app.post("/chat")
def chat(request: ChatRequest):
    config={
        "configurable":{
            "thread_id":"user_1"
        }
    }
    result = graph_app.invoke({
        "messages": [
            HumanMessage(content=request.message)
        ]
    },
        config=config
    )

    return {
         "reply": (
        result.get("results")
        or result.get("normal_reply")
        or "你好！有什么可以帮你的？"
    ),
        "debug_messages": [
            {
                "type": msg.type,
                "content": msg.content,
                "tool_calls": getattr(msg, "tool_calls", [])
            }
            for msg in result["messages"]
        ]
    }
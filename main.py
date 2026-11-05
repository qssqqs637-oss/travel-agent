import json
import logging
import os

from dotenv import load_dotenv

# 必须在导入 graph.workflow 之前加载 .env
load_dotenv()

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field

from graph.workflow import app as graph_app, memory, regenerate_day

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("travel.api")

app = FastAPI(
    title="AI 旅行规划助手",
    version="0.3.0",
    root_path=os.getenv("ROOT_PATH", ""),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    session_id: str = Field(
        min_length=8,
        max_length=64,
        pattern=r"^[A-Za-z0-9_-]+$",
    )


class ResetRequest(BaseModel):
    session_id: str = Field(
        min_length=8,
        max_length=64,
        pattern=r"^[A-Za-z0-9_-]+$",
    )


class DayEditRequest(BaseModel):
    session_id: str = Field(
        min_length=8,
        max_length=64,
        pattern=r"^[A-Za-z0-9_-]+$",
    )
    day_index: int = Field(ge=0)
    instruction: str = Field(default="", max_length=500)
    itinerary: dict | None = None


@app.get("/")
def root():
    return {"message": "Hello Agent"}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/chat")
def chat(request: ChatRequest):
    config = {"configurable": {"thread_id": request.session_id}}
    logger.info(
        "chat session=%s chars=%d",
        request.session_id,
        len(request.message),
    )

    try:
        result = graph_app.invoke(
            {"messages": [HumanMessage(content=request.message)]},
            config=config,
        )
    except Exception:
        logger.exception("chat failed session=%s", request.session_id)
        raise HTTPException(
            status_code=500,
            detail="服务暂时不可用，请稍后重试",
        )

    reply = (
        result.get("results")
        or result.get("normal_reply")
        or "抱歉，模型这次没有返回内容，请再试一次。"
    )

    return {
        "reply": reply,
        "session_id": request.session_id,
        "itinerary": result.get("itinerary") or {},
    }


def sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


NODE_STATUS = {
    "planner": "正在理解你的需求…",
    "executor": "正在查询旅行信息…",
    "synthesizer": "正在撰写攻略…",
    "structurer": "正在整理行程卡片…",
    "normal_chat": "正在思考…",
}


@app.post("/chat/stream")
def chat_stream(request: ChatRequest):
    config = {"configurable": {"thread_id": request.session_id}}
    logger.info(
        "chat stream session=%s chars=%d",
        request.session_id,
        len(request.message),
    )

    def event_generator():
        parts = []
        current_node = None
        done_sent = False

        try:
            for chunk, metadata in graph_app.stream(
                {"messages": [HumanMessage(content=request.message)]},
                config=config,
                stream_mode="messages",
            ):
                node = metadata.get("langgraph_node")

                if node in NODE_STATUS and node != current_node:
                    current_node = node
                    yield sse({"type": "status", "text": NODE_STATUS[node]})

                # 只把最终回复节点的内容发给浏览器，
                # planner / executor 的中间输出不展示。
                if node not in ("synthesizer", "normal_chat"):
                    continue

                content = getattr(chunk, "content", "")
                if isinstance(content, list):
                    content = "".join(
                        part.get("text", "")
                        for part in content
                        if isinstance(part, dict)
                    )
                if not content:
                    continue

                parts.append(content)
                yield sse({"type": "token", "text": content})

            state = graph_app.get_state(config)
            values = state.values if state else {}
            reply = values.get("results") or values.get("normal_reply") or ""
            streamed = "".join(parts)
            itinerary = values.get("itinerary") or {}

            # ask_user 之类的节点不经过 LLM，没有 token 流，
            # 这里把最终结果一次性补发。
            if not streamed and reply:
                yield sse({"type": "token", "text": reply})

            if not streamed and not reply:
                reply = "抱歉，模型这次没有返回内容，请再试一次。"
                yield sse({"type": "token", "text": reply})

            done_sent = True
            yield sse({
                "type": "done",
                "reply": streamed or reply,
                "session_id": request.session_id,
                "itinerary": itinerary,
            })
        except Exception:
            logger.exception(
                "chat stream failed session=%s", request.session_id
            )
            if not done_sent:
                yield sse({
                    "type": "error",
                    "message": "服务暂时不可用，请稍后重试",
                })

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/reset")
def reset(request: ResetRequest):
    # 删掉这个会话的全部 checkpoint，下一轮就是全新 State
    memory.delete_thread(request.session_id)
    logger.info("reset session=%s", request.session_id)
    return {"ok": True, "session_id": request.session_id}


@app.post("/itinerary/day")
def edit_itinerary_day(request: DayEditRequest):
    config = {"configurable": {"thread_id": request.session_id}}
    snapshot = graph_app.get_state(config)
    values = snapshot.values if snapshot else {}

    # 优先用前端传上来的行程；刷新后前端也能从 sessionStorage 恢复
    itinerary = request.itinerary or values.get("itinerary") or {}
    day_plans = list(itinerary.get("day_plans") or [])

    if not 0 <= request.day_index < len(day_plans):
        raise HTTPException(status_code=400, detail="day_index 超出范围")

    current = day_plans[request.day_index]
    attractions = values.get("attractions") or ""

    if not attractions:
        # 刷新后 state 里的景点可能没了，用行程里已有的景点兜底
        titles = [
            item.get("title")
            for day in day_plans
            for item in (day.get("items") or [])
            if item.get("title")
        ]
        attractions = "、".join(titles)

    day = regenerate_day(
        city=values.get("city") or itinerary.get("city") or "",
        date=current.get("date", ""),
        weekday=current.get("weekday", ""),
        weather=current.get("weather", ""),
        attractions=attractions,
        current_day=current,
        instruction=request.instruction,
    )

    if day is None:
        raise HTTPException(status_code=502, detail="这一天重排失败，请重试")

    day_plans[request.day_index] = day
    updated = dict(itinerary)
    updated["day_plans"] = day_plans

    try:
        graph_app.update_state(config, {"itinerary": updated})
    except Exception:
        logger.exception("update itinerary state failed")

    logger.info(
        "edit day session=%s index=%d instruction=%s",
        request.session_id,
        request.day_index,
        request.instruction,
    )

    return {
        "itinerary": updated,
        "day_index": request.day_index,
        "day": day,
    }
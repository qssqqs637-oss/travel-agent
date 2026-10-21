import os
import json
from dotenv import load_dotenv
from langgraph.graph import StateGraph, START, END
from langchain_deepseek import ChatDeepSeek
from langchain_core.tools import tool
from langchain_core.messages import SystemMessage, HumanMessage
from .state import State
from langgraph.prebuilt import ToolNode, tools_condition
from langgraph.checkpoint.memory import MemorySaver
from pydantic import BaseModel, Field
import requests
load_dotenv()

class TravelInfo(BaseModel):
    city: str = Field(default="")
    start_date: str = Field(default="")
    days: int = Field(default=0)
    is_travel_request: bool = Field(default=False)
    travel_intent: str = Field(default="new")

model = ChatDeepSeek(
    model="deepseek-v4-pro",
    api_key=os.getenv("DEEPSEEK_API_KEY"),
    base_url=os.getenv("DEEPSEEK_API_URL"),
    temperature=0.7,
)


# =========================
# 1. 天气工具
# =========================
def weather_code_to_text(code: int):
    weather_map = {
        0: "晴天",
        1: "基本晴朗",
        2: "局部多云",
        3: "阴天",
        45: "雾",
        48: "雾",
        51: "小毛毛雨",
        53: "中等毛毛雨",
        55: "较强毛毛雨",
        61: "小雨",
        63: "中雨",
        65: "大雨",
        71: "小雪",
        73: "中雪",
        75: "大雪",
        80: "小阵雨",
        81: "中阵雨",
        82: "强阵雨",
        95: "雷雨",
        96: "雷雨伴小冰雹",
        99: "雷雨伴大冰雹",
    }

    return weather_map.get(code, "未知天气")
@tool
def get_weather(city: str, start_date: str, days: int):
    """查询指定城市从指定日期开始的旅行天气"""

    # =========================
    # 1. 城市名称 -> 经纬度
    # =========================

    geo_url = "https://geocoding-api.open-meteo.com/v1/search"

    geo_response = requests.get(
        geo_url,
        params={
            "name": city,
            "count": 1,
            "language": "zh",
            "format": "json"
        }
    )

    geo_data = geo_response.json()

    if "results" not in geo_data:
        return f"找不到城市：{city}"

    latitude = geo_data["results"][0]["latitude"]
    longitude = geo_data["results"][0]["longitude"]

    # =========================
    # 计算结束日期
    # =========================

    from datetime import datetime, timedelta

    start = datetime.strptime(start_date, "%Y-%m-%d").date()

    end = start + timedelta(days=days - 1)

    end_date = end.strftime("%Y-%m-%d")

    # =========================
    # 查询天气
    # =========================

    weather_url = "https://api.open-meteo.com/v1/forecast"

    weather_response = requests.get(
        weather_url,
        params={
            "latitude": latitude,
            "longitude": longitude,

            "start_date": start_date,
            "end_date": end_date,

            "daily": "temperature_2m_max,temperature_2m_min,weather_code",

            "timezone": "Asia/Shanghai"
        }
    )

    weather_data = weather_response.json()

    # =========================
    # 检查 API 是否返回错误
    # =========================

    if "daily" not in weather_data:
        return f"天气查询失败：{weather_data}"

    daily = weather_data["daily"]

    # =========================
    # 整理天气结果
    # =========================

    result = (
        f"城市：{city}\n"
        f"旅行开始日期：{start_date}\n"
        f"旅行结束日期：{end_date}\n"
        f"旅行天数：{days}天\n\n"
        f"旅行期间天气：\n"
    )

    for i in range(len(daily["time"])):

        date = daily["time"][i]

        max_temp = daily["temperature_2m_max"][i]
        min_temp = daily["temperature_2m_min"][i]

        weather_code = daily["weather_code"][i]

        weather_text = weather_code_to_text(weather_code)

        result += (
            f"{date}："
            f"{weather_text}，"
            f"{min_temp}°C ~ {max_temp}°C\n"
        )

    return result


 # 景点工具
@tool
def search_attractions(city: str):
    """
    查询指定城市的热门旅游景点。
    """
    # 暂时先用固定数据测试 Tool Calling
    attractions = {
        "北京": [
            "故宫",
            "天安门广场",
            "颐和园",
            "天坛",
            "八达岭长城"
        ],
        "上海": [
            "外滩",
            "东方明珠",
            "豫园",
            "上海博物馆",
            "迪士尼乐园"
        ],
        "杭州": [
            "西湖",
            "灵隐寺",
            "西溪湿地",
            "雷峰塔",
            "宋城"
        ]
    }

    result = attractions.get(city)

    if not result:
        return f"暂时没有找到 {city} 的景点信息。"

    return f"{city}热门旅游景点：\n" + "\n".join(
        f"- {item}" for item in result
    )

# =========================
# 2. 给模型绑定工具
# =========================

model_with_tools = model.bind_tools([get_weather,search_attractions])

tool_node = ToolNode([get_weather,search_attractions])


# =========================
# 3. Planner
# =========================

def planner(state: State):

    print("===== Planner 收到的 State =====")
    print("city =", state.get("city"))
    print("start_date =", state.get("start_date"))
    print("days =", state.get("days"))

    old_city = state.get("city") or ""
    old_start_date = state.get("start_date") or ""
    old_days = state.get("days") or 0

    response = model.invoke([
        SystemMessage(
            content=(
                "你是一个旅行需求信息提取器。\n"
                "请从用户最新消息中提取旅行信息。\n\n"

                f"之前已经知道的信息：\n"
                f"城市：{old_city or '未指定'}\n"
                f"开始日期：{old_start_date or '未指定'}\n"
                f"旅行天数：{old_days or '未指定'}\n\n"
                "如果用户只是打招呼、闲聊，或者与旅行无关，"
                "请将 is_travel_request 设置为 false。\n"
                "如果用户正在提出旅行需求，或者修改、补充之前的旅行需求，"
                "请将 is_travel_request 设置为 true。\n"
                "如果用户是在创建新的旅行计划，"
                "请将 travel_intent 设置为 new。\n"
                "如果用户是在修改、调整之前已经存在的旅行计划，"
                "请将 travel_intent 设置为 update。\n"
                "如果用户只是普通聊天，"
                "请将 travel_intent 设置为 chat。\n"
                "如果用户最新消息没有提供某个信息，"
                "请返回空字符串或 0，不要编造。\n"

                "开始日期请转换成 YYYY-MM-DD 格式。\n"
                "如果用户使用“9月15号”“9月20日”这样的中文日期，"
                "请正确转换成对应年份的 YYYY-MM-DD。\n"
                "当前年份是 2026 年。\n\n"

                "必须严格返回 JSON，不要输出其他内容。\n"
                "格式如下：\n"
                '{"city":"","start_date":"","days":0,"is_travel_request":false,"travel_intent":"chat"}'
            )
        ),
        HumanMessage(
            content=state["messages"][-1].content
        )
    ])

    response_data=json.loads(response.content)
    response = TravelInfo(**response_data)

    print("===== Planner =====")
    print(response)

    city = old_city
    start_date = old_start_date
    days = old_days

    if response.city:
        city = response.city

    if response.start_date:
        start_date = response.start_date

    if response.days > 0:
        days = response.days

    return {
        "city": city,
        "start_date": start_date,
        "days": days,
        "plan": str(response),
        "is_travel_request": response.is_travel_request,
        "travel_intent": response.travel_intent,
        "results": ""
    }
# =========================
# 检查旅行需求是否完整
# =========================
def check_requirements(state:State):
    if(not state["is_travel_request"]):
        print("不是旅行请求，跳过需求检查")
        return {"need_input":False}

    if( not state["city"]
        or not state["start_date"]
        or state["start_date"]=="未指定"
        or state["days"]<=0 ):
        print("===== Check Requirements =====")
        print("旅行需求不完整，需要用户补充信息")
        return {
            "need_input":True
        }
    print("===== Check Requirements =====")
    print("旅行需求完整")
    return {
        "need_input": False
    }
# =========================
# 普通聊天节点
# =========================
def normal_chat(state: State):
    print("===== Normal Chat =====")

    response = model.invoke(
        state["messages"]
    )

    print("===== Normal Chat 回复 =====")
    print(response.content)

    return {
        "normal_reply": response.content
    }
# =========================
#  请求用户补充信息
# =========================
def ask_user(state:State):
    missing=[]
    if not state["city"]:
        missing.append("城市")
    if not state["start_date"] or state["start_date"]=="未指定":
        missing.append("开始日期")
    if  state["days"]<=0:
        missing.append("旅行天数")
    message="为了帮您制定旅行计划，还需要提供：\n"
    for item in missing:
        message+=f"- {item}\n"
    return {
        "results":message
    }
# =========================
#  条件路由
# =========================
def requirements_router(state: State):
    print("===== Requirements Router =====")

    if not state["is_travel_request"]:
        print("→ normal_chat")
        return "normal_chat"

    if state["need_input"]:
        print("→ ask_user")
        return "ask_user"

    if state["travel_intent"] == "update":
        print("→ update_travel")
        return "update_travel"
    print("→ executor")
    return "executor"


# =========================
#  保存工具调用结果
# =========================
def save_tool_results(state: State):

    weather = ""
    attractions = ""

    for msg in state["messages"]:

        if msg.type == "tool":

            if msg.name == "get_weather":
                weather = msg.content

            elif msg.name == "search_attractions":
                attractions = msg.content

    print("===== Save Tool Results =====")
    print("天气：")
    print(weather)

    print("景点：")
    print(attractions)

    return {
        "weather": weather,
        "attractions": attractions
    }

def update_travel(state: State):
    print("===== Update Travel =====")
    print("正在更新旅行计划")
    print("城市：", state["city"])
    print("开始日期：", state["start_date"])
    print("旅行天数：", state["days"])

    return {}

# =========================
#  Executor
# =========================
def executor_router(state: State):

    last_message = state["messages"][-1]

    print("===== Executor Router =====")

    if getattr(last_message, "tool_calls", []):
        print("→ tools")
        return "tools"

    print("→ synthesizer")
    return "synthesizer"
def executor(state: State):

    messages = [
        SystemMessage(
            content=(
                "你是一个旅行执行助手。\n"
                "请根据旅行计划执行旅行相关的信息查询任务。\n\n"

                "【必须完成的任务】\n"
                "1. 必须调用 get_weather 工具查询旅行期间的天气。\n"
                "2. 必须调用 search_attractions 工具查询目的地的热门旅游景点。\n\n"

                "【天气工具】\n"
                "天气工具所需要的参数已经保存在 State 中。\n"
                "不要自己猜测或修改这些参数。\n"
                f"城市：{state['city']}\n"
                f"旅行开始日期：{state['start_date']}\n"
                f"旅行天数：{state['days']}\n\n"

                "调用 get_weather 时，必须使用上面的城市、开始日期和旅行天数。\n"
                "不要自己编造天气信息。\n\n"

                "【景点工具】\n"
                "调用 search_attractions 时，必须使用 State 中的城市。\n"
                f"景点查询城市：{state['city']}\n"
                "不要自己编造景点查询结果。\n\n"

                "请先完成必要的信息查询，再根据工具返回的结果进行整理。"
            )
        ),
        HumanMessage(
            content=state["plan"]
        )
    ]

    # 把之前的 AI 工具调用和 ToolMessage
    # 一起交给模型
    messages.extend(state["messages"])

    response = model_with_tools.invoke(messages)

    print("===== Executor =====")
    print(response)
    print("===== Executor 返回的 tool_calls =====")
    print(response.tool_calls)

    return {
        "messages": [response],
        "results": ""
    }


# =========================
# Synthesizer
# =========================

def synthesizer(state: State):
    print("================================")
    print("===== Synthesizer 开始执行 =====")
    print("================================")
    response = model.invoke([
        SystemMessage(
            content=(
                "你是一个旅行攻略编辑。\n"
                "请根据旅行计划、天气信息、景点查询结果和执行结果，"
                "整理成清晰、简洁、实用的最终旅行攻略。\n\n"

                "【非常重要的规则】\n"
                "1. 景点必须优先使用景点工具查询结果中提供的景点。\n"
                "2. 不要自行编造、添加景点查询结果中没有出现的景点。\n"
                "3. 可以根据旅行天数合理安排这些景点的游览顺序。\n"
                "4. 必须参考天气安排每天的行程。\n"
                "5. 晴天优先安排适合户外的景点。\n"
                "6. 高温天气避免安排长时间暴晒的活动。\n"
                "7. 阴天可以安排户外景点，但注意体感温度。\n"
                "8. 不要编造天气信息。\n"
                "9. 如果查询到的景点数量不足以安排完整行程，"
                "可以安排自由活动，但不要自行创造新的景点。\n"
            )
        ),
        HumanMessage(
                 content=(
                f"【旅行计划】\n"
                f"{state['plan']}\n\n"

                f"【天气信息】\n"
                f"{state['weather']}\n\n"

                f"【景点查询结果】\n"
                f"{state['attractions']}\n\n"
                f"【执行结果】\n"
                f"{state['results']}"
            )
        )
    ])
    print("===== Synthesizer 生成结果 =====")
    print(response.content)
    return {
        "city": state["city"],
        "start_date": state["start_date"],
        "days": state["days"],
        "results": response.content
    }


# =========================
# 6. 创建 Graph
# =========================

graph = StateGraph(State)

graph.add_node("planner", planner)
graph.add_node("check_requirements", check_requirements)
graph.add_node("ask_user", ask_user)
graph.add_node("executor", executor)
graph.add_node("synthesizer", synthesizer)
graph.add_node("tools", tool_node)
graph.add_node("save_tool_results", save_tool_results)
graph.add_node("normal_chat",normal_chat)
graph.add_node("update_travel", update_travel)
# =========================
# 7. 连接 Graph
# =========================

graph.add_edge(START, "planner")

graph.add_edge("planner", "check_requirements")
graph.add_conditional_edges(
    "check_requirements",
    requirements_router,
    {
        "executor":"executor",
        "ask_user":"ask_user",
        "normal_chat": "normal_chat",
        "update_travel": "update_travel"
    }
)
graph.add_edge("ask_user", END)
graph.add_edge("update_travel", "executor")
graph.add_conditional_edges(
    "executor",
    executor_router,
    {
        "tools": "tools",
        "synthesizer": "synthesizer"
    }
)


graph.add_edge("tools", "save_tool_results")
graph.add_edge("save_tool_results", "executor")
graph.add_edge("synthesizer", END)
graph.add_edge("normal_chat",END)
# =========================
# 8. 编译
# =========================
memory=MemorySaver()

app = graph.compile(checkpointer=memory)
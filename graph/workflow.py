import os
import json
from dotenv import load_dotenv
from langgraph.graph import StateGraph, START, END
from langchain_deepseek import ChatDeepSeek
from langchain_core.tools import tool
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
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
    model="deepseek-flash",
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
                              "请判断用户最新消息属于哪一类：\n"
                "1. 新的旅行计划（travel_intent=new）：提出一个与之前无关的新目的地/新行程。\n"
                "2. 修改已有计划（travel_intent=update）：在之前计划的基础上，"
                "改城市、日期、天数或行程安排。\n"
                "3. 提问或闲聊（travel_intent=chat）：询问、评价、确认之前已经生成的行程细节，"
                "比如“第一天会不会太赶”“那边天气怎么样”“需要带什么”，或者只是打招呼。\n\n"
                
                "属于第 1、2 类时，is_travel_request=true；"
                "属于第 3 类时，is_travel_request=false。\n"
                "仅仅提到旅行、行程、景点，并不代表用户要新建或修改计划，"
                "关键要看用户是在“提问”还是在“提出/修改需求”。\n"  "如果用户只是打招呼、闲聊，或者与旅行无关，"
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

    if response.travel_intent == "new":
        # 全新计划：不继承上一轮的信息
        city, start_date, days = "", "", 0
    else:
        # 补充/修改：以旧值为底，新值覆盖
        city, start_date, days = old_city, old_start_date, old_days

    if response.city:
        city = response.city

    if response.start_date:
        start_date = response.start_date

    if response.days > 0:
        days = response.days

        # 关键：plan 必须用“合并后”的值，否则 synthesizer 拿到的是空字段
    plan_info = TravelInfo(
        city=city,
        start_date=start_date,
        days=days,
        is_travel_request=response.is_travel_request,
        travel_intent=response.travel_intent,
    )

    return {
        "city": city,
        "start_date": start_date,
        "days": days,
        "plan": str(plan_info),
        "is_travel_request": response.is_travel_request,
        "travel_intent": response.travel_intent,
        "results": "",
        # 每轮重新开始：清空上一轮的工具结果和轮数。
        # 否则模型这轮没调工具时，synthesizer 会拿到上一个城市的天气。
        "weather": "",
        "attractions": "",
        "tool_rounds": 0,
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

    messages = [
        SystemMessage(
            content=(
                "你是旅行助手。用户是在追问、评价或确认之前已经生成的行程时，"
                "请结合对话历史直接回答，不要重新生成整篇攻略，"
                "也不要重复罗列全部日期安排。"
            )
        )
    ]
    messages.extend(state["messages"])

    response = model.invoke(messages)

    return {
        "normal_reply": response.content,
        # 关键：把模型自己的回复写回 messages，
        # 下一轮 normal_chat / planner 才看得到“我上一轮说了什么”
        "messages": [response],
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
        "results": message,
        # 追问也是助手说过的话，写回 messages 才能让下一轮有上下文
        "messages": [AIMessage(content=message)],
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

def _tool_messages_this_turn(messages):
    """只返回最后一条用户消息之后的 ToolMessage。

    不能扫描全部历史：历史里还留着上一轮北京的工具结果，
    这轮只查了上海景点时，会把北京的天气当成上海的。
    """
    start = 0
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].type == "human":
            start = i + 1
            break
    return messages[start:]

# =========================
#  保存工具调用结果
# =========================
def save_tool_results(state: State):
    weather = ""
    attractions = ""

    for msg in _tool_messages_this_turn(state["messages"]):
        if msg.type != "tool":
            continue

        if msg.name == "get_weather":
            weather = msg.content

        elif msg.name == "search_attractions":
            attractions = msg.content

    rounds = state.get("tool_rounds", 0) + 1

    print(f"===== Save Tool Results (round {rounds}) =====")
    print("天气：")
    print(weather)
    print("景点：")
    print(attractions)

    return {
        "weather": weather,
        "attractions": attractions,
        "tool_rounds": rounds,
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
# 一轮对话里最多执行几轮工具调用；超过就不再给模型工具，逼它总结
MAX_TOOL_ROUNDS = 3


def executor(state: State):
    rounds = state.get("tool_rounds", 0)
    use_tools = rounds < MAX_TOOL_ROUNDS

    system_content = (
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

        # 计划放进 system，而不是再塞一条 HumanMessage，
        # 避免出现“两条连续用户消息”干扰模型
        f"【本轮旅行计划】\n{state['plan']}\n\n"

        "请先完成必要的信息查询，再根据工具返回的结果进行整理。"
    )

    if rounds > 0:
        # 第二轮起，已经拿到部分结果时，鼓励直接总结，减少无意义的重复调用
        system_content += (
            "\n\n【注意】本轮之前已经调用过工具。"
            "如果信息已经足够，请不要再调用工具，直接整理结果。"
        )

    if not use_tools:
        system_content += (
            f"\n\n【重要】本轮已经达到 {MAX_TOOL_ROUNDS} 轮工具调用上限，"
            "工具已被禁用，请直接根据已有的工具结果完成总结，不要声称还要查询。"
        )

    # 只保留 system + 完整对话历史（plan 已经在 system 里）
    messages = [SystemMessage(content=system_content)]
    messages.extend(state["messages"])

    llm = model_with_tools if use_tools else model

    if not use_tools:
        print(f"===== Executor: 已达工具轮数上限 {MAX_TOOL_ROUNDS}，强制总结 =====")

    response = llm.invoke(messages)

    print("===== Executor =====")
    print(response)
    print("===== Executor 返回的 tool_calls =====")
    print(response.tool_calls)

    return {
        "messages": [response],
        "results": "",
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
        "results": response.content,
        # 最终攻略写回 messages：用户的下一句追问才能引用它
        "messages": [response],
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
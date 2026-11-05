import os
import json
import sqlite3
import requests
import time
from datetime import datetime, timedelta
from dotenv import load_dotenv
from langgraph.graph import StateGraph, START, END
from langchain_core.tools import tool
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from .state import State
from .llm import build_model
from .utils import (
    format_driving,
    merge_travel_info,
    normalize_budget,
    normalize_temps,
    recent_messages,
    today_cn,
)
from langgraph.prebuilt import ToolNode
from langgraph.checkpoint.sqlite import SqliteSaver
from pydantic import BaseModel, Field
load_dotenv()

GUIDE_MAX_TOKENS = int(os.getenv("GUIDE_MAX_TOKENS", "1200"))
AMAP_API_KEY = os.getenv("AMAP_API_KEY", "").strip()
HISTORY_TURNS = int(os.getenv("HISTORY_TURNS", "8"))
POI_CACHE: dict[tuple[str, str], str] = {}

print(
    "[amap] enabled"
    if AMAP_API_KEY
    else "[amap] disabled, using built-in attractions"
)


class TravelInfo(BaseModel):
    city: str = Field(default="")
    start_date: str = Field(default="")
    days: int = Field(default=0)
    is_travel_request: bool = Field(default=False)
    travel_intent: str = Field(default="new")
    keep_previous: bool = Field(default=False)


class PlanItem(BaseModel):
    time: str = Field(default="")
    title: str = Field(default="")
    description: str = Field(default="")
    travel_from_previous: str = Field(default="")


class DayPlan(BaseModel):
    date: str = Field(default="")
    weekday: str = Field(default="")
    weather: str = Field(default="")
    theme: str = Field(default="")
    items: list[PlanItem] = Field(default_factory=list)
    notes: str = Field(default="")


class BudgetItem(BaseModel):
    category: str = Field(default="")
    amount: str = Field(default="")
    note: str = Field(default="")


class Budget(BaseModel):
    currency: str = Field(default="CNY")
    per_person: str = Field(default="")
    total: str = Field(default="")
    items: list[BudgetItem] = Field(default_factory=list)
    note: str = Field(default="")


class Itinerary(BaseModel):
    city: str = Field(default="")
    start_date: str = Field(default="")
    days: int = Field(default=0)
    summary: str = Field(default="")
    day_plans: list[DayPlan] = Field(default_factory=list)
    tips: list[str] = Field(default_factory=list)
    budget: Budget | None = None

model = build_model()
guide_model = model.bind(max_tokens=GUIDE_MAX_TOKENS)
# 百炼部分模型不支持 response_format 的结构化输出，默认走函数调用
STRUCTURED_METHOD = os.getenv("STRUCTURED_METHOD", "function_calling")
# 让模型直接产出 TravelInfo，不再自己解析 JSON 文本
structured_planner = model.with_structured_output(
    TravelInfo, include_raw=True, method=STRUCTURED_METHOD
)
# 把 Markdown 攻略整理成结构化行程
structured_itinerary = model.with_structured_output(
    Itinerary, include_raw=True, method=STRUCTURED_METHOD
)
# 单日重排：只生成一个 DayPlan
structured_day = model.with_structured_output(
    DayPlan, include_raw=True, method=STRUCTURED_METHOD
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

    try:
        geo_response = requests.get(
            geo_url,
            params={
                "name": city,
                "count": 1,
                "language": "zh",
                "format": "json"
            },
            timeout=10,
        )
        geo_response.raise_for_status()
        geo_data = geo_response.json()
    except requests.RequestException as exc:
        return f"天气查询失败：{exc}"

    if "results" not in geo_data:
        return f"找不到城市：{city}"

    latitude = geo_data["results"][0]["latitude"]
    longitude = geo_data["results"][0]["longitude"]

    # =========================
    # 计算结束日期
    # =========================

    try:
        start = datetime.strptime(start_date, "%Y-%m-%d").date()
    except ValueError:
        return f"日期格式不正确：{start_date}"

    end = start + timedelta(days=days - 1)
    end_date = end.strftime("%Y-%m-%d")

    today = today_cn()
    if (start - today).days < 0 or (end - today).days > 15:
        return (
            f"{city}的天气预报只支持未来 15 天，"
            f"{start_date} 至 {end_date} 暂时查不到。"
        )

    # =========================
    # 查询天气
    # =========================

    weather_url = "https://api.open-meteo.com/v1/forecast"

    try:
        weather_response = requests.get(
            weather_url,
            params={
                "latitude": latitude,
                "longitude": longitude,
                "start_date": start_date,
                "end_date": end_date,
                "daily": "temperature_2m_max,temperature_2m_min,weather_code",
                "timezone": "Asia/Shanghai",
            },
            timeout=10,
        )
        weather_response.raise_for_status()
        weather_data = weather_response.json()
    except requests.RequestException as exc:
        return f"天气查询失败：{exc}"

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

        day = daily["time"][i]

        max_temp = daily["temperature_2m_max"][i]
        min_temp = daily["temperature_2m_min"][i]

        weather_code = daily["weather_code"][i]

        weather_text = weather_code_to_text(weather_code)

        result += (
            f"{day}："
            f"{weather_text}，"
            f"{min_temp}°C ~ {max_temp}°C\n"
        )

    return result


# 景点工具
_FALLBACK_ATTRACTIONS = {
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


def _fallback_attractions(city: str) -> str:
    items = _FALLBACK_ATTRACTIONS.get(city)
    if not items:
        return f"暂时没有找到 {city} 的景点信息。"
    return f"{city}热门旅游景点：\n" + "\n".join(
        f"- {item}" for item in items
    )


@tool
def search_attractions(city: str):
    """查询指定城市的热门旅游景点。"""
    if not AMAP_API_KEY:
        return _fallback_attractions(city)

    url = "https://restapi.amap.com/v3/place/text"

    try:
        response = requests.get(
            url,
            params={
                "key": AMAP_API_KEY,
                "city": city,
                "citylimit": "true",
                "types": "110000|140100",  # 风景名胜 + 博物馆
                "offset": 10,
                "page": 1,
                "extensions": "all",
            },
            timeout=10,
        )
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError) as exc:
        print("高德景点查询失败:", exc)
        return _fallback_attractions(city)

    if str(data.get("status")) != "1":
        print("高德景点查询返回错误:", data.get("info"))
        return _fallback_attractions(city)

    pois = data.get("pois") or []
    if not isinstance(pois, list) or not pois:
        return _fallback_attractions(city)

    lines = [f"{city}热门旅游景点："]
    seen = set()

    for poi in pois:
        name = (poi.get("name") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)

        location = poi.get("location")
        if location:
            POI_CACHE[(city, name)] = location

        address = poi.get("address") or ""
        if isinstance(address, list):
            address = "".join(address)

        biz_ext = poi.get("biz_ext") or {}
        rating = ""
        if isinstance(biz_ext, dict):
            rating = biz_ext.get("rating") or ""

        detail = name
        if address:
            detail += f"（{address}）"
        if rating:
            detail += f" 评分 {rating}"

        lines.append(f"- {detail}")

        if len(lines) > 8:
            break

    if len(lines) == 1:
        return _fallback_attractions(city)

    return "\n".join(lines)


def _lookup_location(title, city):
    """按景点名找坐标：先查缓存，再查高德。"""
    if not title:
        return None

    cached = POI_CACHE.get((city, title))
    if cached:
        return cached

    for (cached_city, name), location in POI_CACHE.items():
        if cached_city != city:
            continue
        if title in name or name in title:
            return location

    if not AMAP_API_KEY:
        return None

    try:
        response = requests.get(
            "https://restapi.amap.com/v3/place/text",
            params={
                "key": AMAP_API_KEY,
                "keywords": title,
                "city": city,
                "citylimit": "true",
                "offset": 1,
                "page": 1,
                "extensions": "base",
            },
            timeout=10,
        )
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError) as exc:
        print("景点坐标查询失败:", title, exc)
        return None

    if str(data.get("status")) != "1":
        return None

    pois = data.get("pois") or []
    if not isinstance(pois, list) or not pois:
        return None

    location = pois[0].get("location")
    if location:
        POI_CACHE[(city, title)] = location
    return location


def _driving_info(origin, destination):
    """返回 '车程 25 分钟 / 8.2 公里'，失败返回空字符串。"""
    if not AMAP_API_KEY:
        return ""

    try:
        response = requests.get(
            "https://restapi.amap.com/v3/direction/driving",
            params={
                "key": AMAP_API_KEY,
                "origin": origin,
                "destination": destination,
                "extensions": "base",
            },
            timeout=10,
        )
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError) as exc:
        print("路径规划失败:", exc)
        return ""

    if str(data.get("status")) != "1":
        return ""

    paths = (data.get("route") or {}).get("paths") or []
    if not paths:
        return ""

    path = paths[0]
    return format_driving(path.get("distance"), path.get("duration"))


_GENERIC_TITLE_WORDS = (
    "自由活动",
    "自由安排",
    "休息",
    "返程",
    "用餐",
    "入住",
    "退房",
)


def _is_generic_title(title):
    return any(word in title for word in _GENERIC_TITLE_WORDS)


# =========================
# 2. 给模型绑定工具
# =========================

model_with_tools = model.bind_tools([get_weather,search_attractions])

tool_node = ToolNode([get_weather,search_attractions])


# =========================
# 3. Planner
# =========================

def planner(state: State):
    today = today_cn()
    print("===== Planner 收到的 State =====")
    print("city =", state.get("city"))
    print("start_date =", state.get("start_date"))
    print("days =", state.get("days"))

    old_city = state.get("city") or ""
    old_start_date = state.get("start_date") or ""
    old_days = state.get("days") or 0

    raw_result = structured_planner.invoke([
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
                "关键要看用户是在“提问”还是在“提出/修改需求”。\n"
                "如果用户只是报了一个新城市（例如“上海”），没有提到日期或天数，"
                "也没有说“还是之前的时间”，请按新计划处理，travel_intent=new，"
                "keep_previous=false，不要默认沿用上一轮的时间。\n"
                "只有当用户明确说“还是这三天”“日期不变”“同样的时间”等，"
                "才把 keep_previous 设置为 true。\n"
                "如果用户最新消息没有提供某个信息，"
                "请返回空字符串或 0，不要编造。\n"

                "开始日期请转换成 YYYY-MM-DD 格式。\n"
                "如果用户使用“9月15号”“9月20日”这样的中文日期，"
                "请正确转换成对应年份的 YYYY-MM-DD。\n"
                f"当前日期是 {today.isoformat()}（{today.year} 年）。\n"
                "请根据当前日期推断用户说的“9月15号”具体是哪一年，输出 YYYY-MM-DD。\n"
                "如果用户说的日期已经过去，也请原样返回该日期，"
                "不要自行顺延到明年，系统会统一提示用户。\n\n"
            )
        ),
        HumanMessage(
            content=state["messages"][-1].content
        )
    ])
    response = raw_result.get("parsed")

    if isinstance(response, dict):
        response = TravelInfo(**response)

    if response is None:
        # 模型没按 schema 输出时不崩，降级成普通聊天
        print("结构化输出解析失败:", raw_result.get("parsing_error"))
        response = TravelInfo(is_travel_request=False, travel_intent="chat")

    print("===== Planner =====")
    print(response)

    city, start_date, days, date_error = merge_travel_info(
        old_city,
        old_start_date,
        old_days,
        response,
        today,
    )

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
        "date_error": date_error,
        "itinerary": {},
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
    messages.extend(recent_messages(state["messages"], HISTORY_TURNS))

    response = model.invoke(messages)

    content = response.content
    if isinstance(content, list):
        content = "".join(
            part.get("text", "")
            for part in content
            if isinstance(part, dict)
        )

    if not content:
        print("normal_chat 返回空内容")
        print("content =", repr(content))
        print("additional_kwargs =", response.additional_kwargs)
        print("response_metadata =", response.response_metadata)
        print("usage_metadata =", getattr(response, "usage_metadata", None))
        content = "抱歉，这次模型没有返回内容，请再问一次。"

    return {
        "normal_reply": content,
        # 关键：把模型自己的回复写回 messages，
        # 下一轮 normal_chat / planner 才看得到“我上一轮说了什么”
        "messages": [AIMessage(content=content)],
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

    prefix = ""
    if state.get("date_error"):
        prefix = state["date_error"] + "\n\n"

    message = prefix + "为了帮您制定旅行计划，还需要提供：\n"
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
def after_tools_router(state: State):
    # 天气和景点都拿到了，直接去写攻略，不必再绕一次 executor
    if state.get("weather") and state.get("attractions"):
        return "synthesizer"
    return "executor"


def ensure_travel_data(state: State):
    """兜底：模型这轮没调工具时，直接用 State 里的参数补查。"""
    weather = state.get("weather") or ""
    attractions = state.get("attractions") or ""

    if not weather and state.get("city") and state.get("start_date") and state.get("days"):
        print("===== Ensure Travel Data: 补查天气 =====")
        try:
            weather = get_weather.invoke({
                "city": state["city"],
                "start_date": state["start_date"],
                "days": state["days"],
            })
        except Exception as exc:
            weather = f"天气查询失败：{exc}"

    if not attractions and state.get("city"):
        print("===== Ensure Travel Data: 补查景点 =====")
        try:
            attractions = search_attractions.invoke({"city": state["city"]})
        except Exception as exc:
            attractions = f"景点查询失败：{exc}"

    return {
        "weather": weather,
        "attractions": attractions,
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
    messages.extend(recent_messages(state["messages"], HISTORY_TURNS))

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
    t0 = time.perf_counter()
    response = guide_model.invoke([
        SystemMessage(
            content=(
                "你是一个旅行攻略编辑。\n"
                "请根据旅行计划、天气信息、景点查询结果，"
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
                "10. 总字数控制在 600 字以内。\n"
                "11. 按天列点，每天最多 3 个安排，每个安排一句话。\n"
                "12. 不要重复粘贴天气原始数据，只在必要时提一句温度。\n"
                "13. 不要写开场白和总结语，直接给行程。\n"
                "14. 如果天气信息为空或显示查询失败，明确说明天气暂缺，不要编造。\n"
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
            )
        )
    ])
    print(
        f"[timing] synthesizer: {time.perf_counter() - t0:.1f}s  "
        f"usage={response.usage_metadata}"
    )
    print("===== Synthesizer 生成结果 =====")
    print(response.content)
    return {
        "results": response.content,
        # 最终攻略写回 messages：用户的下一句追问才能引用它
        "messages": [response],
    }


# =========================
# Structurer：把攻略转成结构化行程
# =========================

def regenerate_day(city, date, weekday, weather, attractions, current_day, instruction=""):
    """只重写行程中的某一天，返回 DayPlan 的 dict。"""
    raw = structured_day.invoke([
        SystemMessage(
            content=(
                "你是行程编辑助手。请只重写指定这一天的安排。\n"
                "要求：\n"
                "1. 日期、星期、天气保持不变，不要修改。\n"
                "2. 只能使用给定景点列表里的景点，不要新增。\n"
                "3. items 按上午/下午/晚上排列，time 只写“上午/下午/晚上”。\n"
                "4. 用户的要求：" + (instruction or "保持原样，只做轻微优化") + "\n"
                "5. 不要输出与这一天无关的内容。\n"
            )
        ),
        HumanMessage(
            content=(
                f"【城市】{city}\n"
                f"【日期】{date}（{weekday}）\n"
                f"【天气】{weather}\n"
                f"【可用景点】\n{attractions}\n\n"
                f"【当前这一天】\n"
                f"{json.dumps(current_day, ensure_ascii=False, indent=2)}"
            )
        ),
    ])

    day = raw.get("parsed")
    if day is None:
        print("单日重排解析失败:", raw.get("parsing_error"))
        return None

    if not isinstance(day, dict):
        day = day.model_dump()

    # 日期/星期/天气以当前卡片为准，避免模型改写
    day["date"] = date
    day["weekday"] = weekday
    day["weather"] = weather

    for item in day.get("items", []):
        item["travel_from_previous"] = ""

    return normalize_temps(day)


def structurer(state: State):
    print("===== Structurer =====")
    raw = structured_itinerary.invoke([
        SystemMessage(
            content=(
                "你是一个行程结构化助手。请把旅行攻略整理成结构化行程。\n"
                "要求：\n"
                "1. 每一天对应一个 DayPlan，date 用 YYYY-MM-DD。\n"
                "2. items 按上午/下午/晚上排列，time 只写“上午/下午/晚上”。\n"
                "3. weather 只写当天的天气和温度，不要编造。\n"
                "4. tips 是通用提醒，最多 5 条。\n"
                "5. 不要添加攻略里没有的景点。\n"
                "6. 如果攻略明确说天气暂缺，weather 写“天气暂缺”。\n"
                "7. weather 和 summary 里的温度统一写成“最低°C ~ 最高°C”，不要漏掉 ~。\n"
                "8. budget 是参考预算，只给区间，不要精确金额。\n"
                "9. budget.per_person 和 budget.total 只写数字区间，例如“1200~1800”，不要带“元”。\n"
                "10. budget.items 至少包含交通、门票、餐饮、住宿四类，amount 也只写区间。\n"
                "11. budget.note 固定写“参考价，按 2 人估算，以实际为准”。\n"
                "12. budget.items 的 amount 是人均金额；per_person 是四项之和，total 是 per_person 的 2 倍。\n"
            )
        ),
        HumanMessage(
            content=(
                f"【旅行计划】\n{state['plan']}\n\n"
                f"【天气信息】\n{state['weather']}\n\n"
                f"【景点查询结果】\n{state['attractions']}\n\n"
                f"【攻略正文】\n{state['results']}"
            )
        ),
    ])

    itinerary = raw.get("parsed")

    if itinerary is None:
        print("结构化行程解析失败:", raw.get("parsing_error"))
        return {"itinerary": {}}

    data = itinerary if isinstance(itinerary, dict) else itinerary.model_dump()

    if data.get("budget"):
        data["budget"] = normalize_budget(data["budget"])

    # 车程由 route_enricher 统一计算，先清掉模型可能编的值
    for day in data.get("day_plans", []):
        for item in day.get("items", []):
            item["travel_from_previous"] = ""

    return {"itinerary": normalize_temps(data)}


# =========================
# Route Enricher：计算景点之间的车程
# =========================

def route_enricher(state: State):
    itinerary = state.get("itinerary") or {}
    day_plans = itinerary.get("day_plans") or []

    if not AMAP_API_KEY or not day_plans:
        return {"itinerary": itinerary}

    city = itinerary.get("city") or state.get("city") or ""
    updated_days = []

    for day in day_plans:
        items = [dict(item) for item in (day.get("items") or [])]

        last_location = None

        for item in items:
            title = item.get("title", "")

            # “自由活动/返程/用餐”不是具体景点，不参与车程计算
            if _is_generic_title(title):
                item["travel_from_previous"] = ""
                continue

            location = _lookup_location(title, city)

            if last_location and location:
                item["travel_from_previous"] = _driving_info(
                    last_location, location
                )
            else:
                item["travel_from_previous"] = ""

            if location:
                last_location = location

        day = dict(day)
        day["items"] = items
        updated_days.append(day)

    updated = dict(itinerary)
    updated["day_plans"] = updated_days
    return {"itinerary": updated}


# =========================
# 6. 创建 Graph
# =========================

graph = StateGraph(State)

graph.add_node("planner", planner)
graph.add_node("check_requirements", check_requirements)
graph.add_node("ask_user", ask_user)
graph.add_node("executor", executor)
graph.add_node("synthesizer", synthesizer)
graph.add_node("structurer", structurer)
graph.add_node("route_enricher", route_enricher)
graph.add_node("ensure_travel_data", ensure_travel_data)
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
        "synthesizer": "ensure_travel_data"
    }
)


graph.add_edge("tools", "save_tool_results")
graph.add_conditional_edges(
    "save_tool_results",
    after_tools_router,
    {
        "synthesizer": "synthesizer",
        "executor": "executor",
    },
)
graph.add_edge("synthesizer", "structurer")
graph.add_edge("structurer", "route_enricher")
graph.add_edge("route_enricher", END)
graph.add_edge("ensure_travel_data", "synthesizer")
graph.add_edge("normal_chat",END)
# =========================
# 8. 编译
# =========================
DB_PATH = os.getenv("CHECKPOINT_DB", "checkpoints.sqlite")

conn = sqlite3.connect(DB_PATH, check_same_thread=False)
conn.execute("PRAGMA journal_mode=WAL;")

memory = SqliteSaver(conn)
memory.setup()

app = graph.compile(checkpointer=memory)
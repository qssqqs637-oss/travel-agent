"""与模型、数据库无关的纯工具函数，方便单元测试。"""

import re
from datetime import datetime, timedelta, timezone

# 中国没有夏令时，固定 UTC+8
CHINA_TZ = timezone(timedelta(hours=8))

_TEMP_RANGE_RE = re.compile(
    r"(-?\d+(?:\.\d+)?\s*°C)\s*~?\s*(-?\d+(?:\.\d+)?\s*°C)"
)


def today_cn():
    """固定按北京时间取“今天”，避免云服务器 UTC 时区把日期算错。"""
    return datetime.now(CHINA_TZ).date()


def normalize_temps(value):
    """把 '2.7°C13.5°C' / '2.7°C~13.5°C' 统一成 '2.7°C ~ 13.5°C'。

    支持递归处理 dict / list，用于清洗整个 itinerary。
    """
    if isinstance(value, str):
        return _TEMP_RANGE_RE.sub(r"\1 ~ \2", value)
    if isinstance(value, list):
        return [normalize_temps(item) for item in value]
    if isinstance(value, dict):
        return {key: normalize_temps(item) for key, item in value.items()}
    return value


def format_driving(distance, duration):
    """把高德返回的米/秒格式化成 '车程 25 分钟 / 8.2 公里'。"""
    try:
        distance = int(distance)
        duration = int(duration)
    except (TypeError, ValueError):
        return ""

    if distance <= 0 or duration <= 0:
        return ""

    minutes = max(1, round(duration / 60))
    if minutes < 60:
        time_text = f"{minutes} 分钟"
    else:
        hours, remainder = divmod(minutes, 60)
        if remainder:
            time_text = f"{hours} 小时 {remainder} 分钟"
        else:
            time_text = f"{hours} 小时"

    return f"车程 {time_text} / {distance / 1000:.1f} 公里"


def parse_amount_range(value):
    """把 '300~500' / '300-500' / '300' 解析成 (300, 500)。"""
    if value is None:
        return None

    text = str(value).replace(",", "")
    numbers = re.findall(r"\d+(?:\.\d+)?", text)

    if not numbers:
        return None

    if len(numbers) == 1:
        low = high = float(numbers[0])
    else:
        low, high = float(numbers[0]), float(numbers[1])

    if low > high:
        low, high = high, low

    return int(round(low)), int(round(high))


def normalize_budget(budget):
    """按 items 的人均区间重算 per_person / total，保证加总一致。"""
    if not isinstance(budget, dict):
        return budget

    items = budget.get("items") or []
    if not items:
        return budget

    ranges = [parse_amount_range(item.get("amount")) for item in items]
    if any(item is None for item in ranges):
        return budget

    low = sum(item[0] for item in ranges)
    high = sum(item[1] for item in ranges)

    budget["per_person"] = f"{low}~{high}"
    budget["total"] = f"{low * 2}~{high * 2}"
    return budget


def recent_messages(messages, turns=8):
    """只保留最近 turns 轮对话。

    从 HumanMessage 开始截断，避免窗口里出现没有对应 tool_call 的
    孤立 ToolMessage，否则 OpenAI 兼容接口会报错。
    """
    human_indices = [
        index for index, message in enumerate(messages)
        if message.type == "human"
    ]
    if len(human_indices) <= turns:
        return messages
    return messages[human_indices[-turns]:]


def merge_travel_info(
    old_city,
    old_start_date,
    old_days,
    response,
    today,
    date_error="",
):
    """合并旧行程与新抽取结果，返回 (city, start_date, days, date_error)。

    不修改 response，方便单测。
    """
    new_start_date = response.start_date
    new_date_invalid = False

    # 1. 先校验这次新抽取的日期
    if new_start_date:
        try:
            parsed_start = datetime.strptime(new_start_date, "%Y-%m-%d").date()
        except ValueError:
            parsed_start = None

        if parsed_start is None:
            date_error = (
                f"无法识别日期“{new_start_date}”，"
                "请用 YYYY-MM-DD 或“10月30日”这样的格式。"
            )
            new_start_date = ""
            new_date_invalid = True
        elif parsed_start < today:
            date_error = (
                f"{new_start_date} 已经过去了，"
                f"请提供今天（{today.isoformat()}）或之后的出发日期。"
            )
            new_start_date = ""
            new_date_invalid = True

    # 2. 决定是否继承旧值
    city_changed = bool(response.city and response.city != old_city)

    if response.travel_intent == "new" and not response.keep_previous:
        # 全新计划
        city, start_date, days = "", "", 0
    elif city_changed and not response.keep_previous:
        # 换城市，且没说“沿用旧日程”
        city, start_date, days = "", "", 0
        if not date_error:
            date_error = (
                f"已切换到{response.city}，"
                "请补充新的出发日期和旅行天数。"
            )
    else:
        # 同城修改，或明确 keep_previous
        city, start_date, days = old_city, old_start_date, old_days

    # 3. 用新值覆盖
    if response.city:
        city = response.city

    if new_start_date:
        start_date = new_start_date
    elif new_date_invalid:
        # 新日期无效/已过去时，不能拿旧日期顶替，否则会静默按旧日期出行
        start_date = ""

    if response.days > 30:
        date_error = (
            (date_error + "\n" if date_error else "")
            + "旅行天数请控制在 30 天以内。"
        )
        days = 0
    elif response.days > 0:
        days = response.days

    return city, start_date, days, date_error

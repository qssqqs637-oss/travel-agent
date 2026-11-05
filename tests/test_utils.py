from datetime import datetime, timedelta

from graph.utils import (
    CHINA_TZ,
    normalize_temps,
    recent_messages,
    today_cn,
)


class FakeMessage:
    def __init__(self, msg_type):
        self.type = msg_type


def test_china_tz_offset():
    assert CHINA_TZ.utcoffset(None) == timedelta(hours=8)


def test_today_cn_is_close_to_beijing_today():
    expected = datetime.now(CHINA_TZ).date()
    assert abs((today_cn() - expected).days) <= 1


def test_normalize_temps_missing_separator():
    assert normalize_temps("2.7°C13.5°C") == "2.7°C ~ 13.5°C"


def test_normalize_temps_normalizes_spacing():
    assert normalize_temps("2.7°C~13.5°C") == "2.7°C ~ 13.5°C"
    assert normalize_temps("2.7°C ~ 13.5°C") == "2.7°C ~ 13.5°C"


def test_normalize_temps_negative_range():
    assert normalize_temps("-3.3°C-1.2°C") == "-3.3°C ~ -1.2°C"


def test_normalize_temps_recursive():
    data = {
        "summary": "2.7°C13.5°C",
        "days": [{"weather": "7.0°C16.6°C", "notes": "正常"}],
    }

    result = normalize_temps(data)

    assert result["summary"] == "2.7°C ~ 13.5°C"
    assert result["days"][0]["weather"] == "7.0°C ~ 16.6°C"
    assert result["days"][0]["notes"] == "正常"


def test_normalize_temps_leaves_other_text():
    text = "14–20°C，适合户外"
    assert normalize_temps(text) == text


def test_recent_messages_returns_all_when_within_limit():
    messages = [FakeMessage("human"), FakeMessage("ai")]
    assert recent_messages(messages, turns=8) == messages


def test_recent_messages_trims_from_human_boundary():
    messages = [
        FakeMessage("human"),
        FakeMessage("ai"),
        FakeMessage("tool"),
        FakeMessage("human"),
        FakeMessage("ai"),
        FakeMessage("human"),
        FakeMessage("ai"),
    ]

    trimmed = recent_messages(messages, turns=2)

    assert [message.type for message in trimmed] == [
        "human",
        "ai",
        "human",
        "ai",
    ]


def test_recent_messages_never_starts_with_tool():
    messages = [
        FakeMessage("human"),
        FakeMessage("ai"),
        FakeMessage("tool"),
        FakeMessage("ai"),
        FakeMessage("human"),
        FakeMessage("ai"),
        FakeMessage("tool"),
    ]

    trimmed = recent_messages(messages, turns=1)

    assert trimmed[0].type == "human"
    assert trimmed[-1].type == "tool"


def test_recent_messages_without_humans_returns_all():
    messages = [FakeMessage("ai"), FakeMessage("tool")]
    assert recent_messages(messages, turns=1) == messages

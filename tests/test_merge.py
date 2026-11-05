from dataclasses import dataclass
from datetime import date

from graph.utils import merge_travel_info


TODAY = date(2026, 10, 1)


@dataclass
class FakeTravelInfo:
    city: str = ""
    start_date: str = ""
    days: int = 0
    travel_intent: str = "new"
    keep_previous: bool = False


def merge(old_city="", old_date="", old_days=0, **kwargs):
    return merge_travel_info(
        old_city,
        old_date,
        old_days,
        FakeTravelInfo(**kwargs),
        TODAY,
    )


def test_new_intent_overrides_old():
    city, start, days, err = merge(
        "北京",
        "2026-10-28",
        3,
        city="上海",
        start_date="2026-11-05",
        days=2,
        travel_intent="new",
    )
    assert (city, start, days, err) == ("上海", "2026-11-05", 2, "")


def test_new_intent_without_details_clears_old():
    city, start, days, err = merge(
        "北京",
        "2026-10-28",
        3,
        city="上海",
        travel_intent="new",
    )
    assert (city, start, days) == ("上海", "", 0)
    assert err == ""


def test_city_changed_does_not_inherit_dates():
    city, start, days, err = merge(
        "北京",
        "2026-10-28",
        3,
        city="上海",
        travel_intent="update",
    )
    assert (city, start, days) == ("上海", "", 0)
    assert "已切换到上海" in err


def test_city_changed_keep_previous_inherits():
    city, start, days, _ = merge(
        "北京",
        "2026-10-28",
        3,
        city="上海",
        travel_intent="update",
        keep_previous=True,
    )
    assert (city, start, days) == ("上海", "2026-10-28", 3)


def test_keep_previous_wins_over_new_intent():
    city, start, days, _ = merge(
        "北京",
        "2026-10-28",
        3,
        city="上海",
        travel_intent="new",
        keep_previous=True,
    )
    assert (city, start, days) == ("上海", "2026-10-28", 3)


def test_same_city_update_inherits_dates():
    city, start, days, _ = merge(
        "北京",
        "2026-10-28",
        3,
        city="北京",
        travel_intent="update",
    )
    assert (city, start, days) == ("北京", "2026-10-28", 3)


def test_same_city_new_date_keeps_old_days():
    city, start, days, _ = merge(
        "北京",
        "2026-10-28",
        3,
        start_date="2026-11-05",
        travel_intent="update",
    )
    assert (city, start, days) == ("北京", "2026-11-05", 3)


def test_past_date_is_cleared():
    city, start, days, err = merge(
        "",
        "",
        0,
        city="北京",
        start_date="2020-01-01",
        days=3,
    )
    assert city == "北京"
    assert start == ""
    assert days == 3
    assert "已经过去" in err


def test_invalid_date_is_cleared():
    _, start, _, err = merge(
        "",
        "",
        0,
        city="北京",
        start_date="10月30日",
        days=3,
    )
    assert start == ""
    assert "无法识别" in err


def test_too_many_days_is_cleared():
    _, _, days, err = merge(
        "北京",
        "2026-10-28",
        3,
        city="北京",
        days=31,
        travel_intent="update",
    )
    assert days == 0
    assert "30 天" in err


def test_missing_days_inherits_old():
    _, _, days, _ = merge(
        "北京",
        "2026-10-28",
        3,
        city="北京",
        days=0,
        travel_intent="update",
    )
    assert days == 3


def test_past_new_date_does_not_fall_back_to_old():
    city, start, days, err = merge(
        "成都",
        "2026-11-05",
        3,
        start_date="2026-09-01",
        travel_intent="update",
    )
    assert city == "成都"
    assert start == ""
    assert days == 3
    assert "已经过去" in err


def test_invalid_new_date_does_not_fall_back_to_old():
    city, start, days, err = merge(
        "成都",
        "2026-11-05",
        3,
        start_date="10月29日",
        travel_intent="update",
    )
    assert city == "成都"
    assert start == ""
    assert days == 3
    assert "无法识别" in err

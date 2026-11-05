from graph.utils import format_driving


def test_short_drive():
    assert format_driving(8200, 1500) == "车程 25 分钟 / 8.2 公里"


def test_under_one_minute_rounds_up():
    assert format_driving(500, 30) == "车程 1 分钟 / 0.5 公里"


def test_exact_hour():
    assert format_driving(30000, 3600) == "车程 1 小时 / 30.0 公里"


def test_hour_and_minutes():
    assert format_driving(50000, 5400) == "车程 1 小时 30 分钟 / 50.0 公里"


def test_string_values_from_amap():
    assert format_driving("8200", "1500") == "车程 25 分钟 / 8.2 公里"


def test_invalid_values_return_empty():
    assert format_driving(0, 100) == ""
    assert format_driving(100, 0) == ""
    assert format_driving(None, 100) == ""
    assert format_driving("abc", 100) == ""

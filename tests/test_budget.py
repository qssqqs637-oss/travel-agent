from graph.utils import normalize_budget, parse_amount_range


def test_parse_amount_range_with_tilde():
    assert parse_amount_range("300~500") == (300, 500)


def test_parse_amount_range_with_hyphen():
    assert parse_amount_range("300-500") == (300, 500)


def test_parse_amount_range_with_commas_and_unit():
    assert parse_amount_range("1,200~1,800 元") == (1200, 1800)


def test_parse_amount_single_value():
    assert parse_amount_range("300") == (300, 300)


def test_parse_amount_invalid():
    assert parse_amount_range(None) is None
    assert parse_amount_range("待定") is None


def test_normalize_budget_sums_items():
    budget = {
        "per_person": "1200~1800",
        "total": "2400~3600",
        "items": [
            {"category": "交通", "amount": "300~500"},
            {"category": "门票", "amount": "300~450"},
            {"category": "餐饮", "amount": "400~600"},
            {"category": "住宿", "amount": "600~1000"},
        ],
    }

    result = normalize_budget(budget)

    assert result["per_person"] == "1600~2550"
    assert result["total"] == "3200~5100"


def test_normalize_budget_keeps_original_if_item_invalid():
    budget = {
        "per_person": "100~200",
        "total": "200~400",
        "items": [{"category": "其他", "amount": "待定"}],
    }

    result = normalize_budget(budget)

    assert result["per_person"] == "100~200"
    assert result["total"] == "200~400"


def test_normalize_budget_handles_none():
    assert normalize_budget(None) is None


def test_normalize_budget_without_items():
    budget = {"per_person": "100~200"}
    assert normalize_budget(budget)["per_person"] == "100~200"

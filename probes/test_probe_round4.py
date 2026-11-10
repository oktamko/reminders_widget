import json

from probe_round4 import color_summary, order_summary


def test_color_summary_json_keeps_colour_keys_only():
    field = {"type": "STRING", "value": json.dumps(
        {"ckSymbolicColorName": "blue", "daHexString": "#1BADF8", "label": "zzprivate"})}
    out = color_summary(field)
    assert "STRING" in out and "json" in out
    assert "#1BADF8" in out and "blue" in out
    assert "label" in out             # key names are fine
    assert "zzprivate" not in out     # values of non-colour keys are not shown


def test_color_summary_plain_and_missing():
    assert "#FF0000" in color_summary({"type": "STRING", "value": "#FF0000"})
    assert color_summary(None) == "chýba"
    assert "NUMBER_INT64" in color_summary({"type": "NUMBER_INT64", "value": 3})


def test_color_summary_never_shows_long_non_colour_text():
    out = color_summary({"type": "STRING", "value": "Môj nákupný zoznam"})
    assert "nákupný" not in out


def test_order_summary_permutation():
    ids = ["Reminder/A", "Reminder/B"]
    out = order_summary({"type": "STRING", "value": json.dumps(["Reminder/B", "Reminder/A"])}, ids)
    assert out == {"type": "STRING", "json": True, "count": 2, "permutation": True, "missing": 0, "extra": 0}


def test_order_summary_bare_ids_and_mismatch():
    ids = ["Reminder/A", "Reminder/B", "Reminder/C"]
    out = order_summary({"type": "STRING", "value": json.dumps(["A", "B", "Z"])}, ids)
    assert out["json"] is True and out["count"] == 3
    assert out["permutation"] is False and out["missing"] == 1 and out["extra"] == 1


def test_order_summary_not_json():
    out = order_summary({"type": "STRING", "value": "not json"}, [])
    assert out["json"] is False and out["count"] is None

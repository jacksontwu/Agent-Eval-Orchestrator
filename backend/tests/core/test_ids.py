from app.core.ids import new_id, now_iso, sanitize_name


def test_new_id_has_prefix():
    value = new_id("run")
    assert value.startswith("run-")
    assert len(value) == len("run-") + 12


def test_now_iso_is_utc():
    assert now_iso().endswith("+00:00")


def test_sanitize_name_strips_unsafe():
    assert sanitize_name("a b/c!") == "a-b-c"
    assert sanitize_name("   ") == "unknown"

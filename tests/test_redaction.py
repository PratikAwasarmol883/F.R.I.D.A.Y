from friday.core.redaction import (
    REDACTED,
    contains_secret,
    hash_arguments,
    redact_text,
    redact_value,
)


def test_secret_shaped_strings_are_detected_and_masked() -> None:
    samples = [
        "fri_abcdefghij1234567890",
        "sk-ant-api03-abcdefghijklmnop",
        "tvly-abcdefgh12345678",
        "Authorization: Bearer abc.def",
        "a" * 40,
    ]
    for sample in samples:
        assert contains_secret(sample)
        assert sample not in redact_text(f"before {sample} after")


def test_ordinary_text_is_untouched() -> None:
    text = "latest python release notes and fastapi tutorial"
    assert not contains_secret(text)
    assert redact_text(text) == text


def test_redact_value_handles_nesting_and_named_fields() -> None:
    value = {
        "query": "hi fri_abcdefghij1234567890",
        "api_key": "plain",
        "items": [{"x": "tvly-abcdefgh1234"}],
        "n": 3,
    }
    result = redact_value(value, ["api_key"])
    assert result["api_key"] == REDACTED
    assert "fri_" not in str(result) and "tvly-" not in str(result)
    assert result["n"] == 3
    assert value["api_key"] == "plain"  # input is not mutated


def test_argument_hash_is_stable_and_order_independent() -> None:
    assert hash_arguments({"a": 1, "b": 2}) == hash_arguments({"b": 2, "a": 1})
    assert hash_arguments({"a": 1}) != hash_arguments({"a": 2})
    assert len(hash_arguments({})) == 64

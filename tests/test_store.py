from dowbench.runner.store import REDACTED, sanitize


def test_sanitize_drops_credential_fields_at_any_depth() -> None:
    body = {
        "model": "m",
        "headers": {"x-goog-api-key": "secret"},
        "config": {"http_options": {"api_key": "secret"}, "temperature": 0},
        "items": [{"Authorization": "Bearer secret", "text": "hi"}],
    }
    assert sanitize(body) == {"model": "m", "config": {"temperature": 0}, "items": [{"text": "hi"}]}


def test_sanitize_masks_key_shaped_values_inside_text() -> None:
    google = "AIza" + "x" * 35
    token = "AQ." + "y" * 40
    anthropic = "sk-ant-" + "z" * 20
    text = f"leaked {google} and {token} and {anthropic} here"
    cleaned = sanitize({"contents": [{"parts": [{"text": text}]}]})
    out = cleaned["contents"][0]["parts"][0]["text"]
    assert google not in out and token not in out and anthropic not in out
    assert out.count(REDACTED) == 3

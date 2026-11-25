from pathlib import Path

CADDYFILE = Path(__file__).parents[2] / "deploy" / "Caddyfile"


def _block(text: str, opening: str) -> str:
    start = text.index(opening) + len(opening)
    depth = 1
    for position in range(start, len(text)):
        if text[position] == "{":
            depth += 1
        elif text[position] == "}":
            depth -= 1
            if depth == 0:
                return text[start:position]
    raise AssertionError(f"unbalanced block after {opening!r}")


def test_plain_http_site_only_proxies_psp_webhooks() -> None:
    plain_http = _block(CADDYFILE.read_text(encoding="utf-8"), "http://:8080 {")
    webhook = _block(plain_http, "handle /api/webhooks/psp {")
    fallback = _block(plain_http.replace(webhook, ""), "handle {")

    assert "reverse_proxy app:8000" in webhook
    assert plain_http.count("reverse_proxy") == 1
    assert fallback.split() == ["respond", "404"]

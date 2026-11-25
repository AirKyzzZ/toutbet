import re
from html.parser import HTMLParser
from pathlib import Path

from fastapi.testclient import TestClient

STATIC = Path(__file__).parents[2] / "src" / "toutbet" / "static"
DANGEROUS_SINKS = {
    "innerHTML": r"\binnerHTML\b",
    "outerHTML": r"\bouterHTML\b",
    "insertAdjacentHTML": r"\binsertAdjacentHTML\b",
    "document.write": r"\bdocument\.write(ln)?\b",
    "eval": r"\beval\s*\(",
    "new Function": r"\bnew\s+Function\b",
    "string timer": r"\bset(Timeout|Interval)\s*\(\s*['\"`]",
    "localStorage": r"\blocalStorage\b",
    "document.cookie": r"\bdocument\.cookie\b",
    "style attribute": r"\.style\.|setAttribute\(\s*['\"]style",
    "html parser": r"\b(DOMParser|createContextualFragment)\b",
}


class InlineCodeFinder(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.problems: list[str] = []
        self._in_script = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        names = {name.lower(): value for name, value in attrs}
        for name in names:
            if name.startswith("on") or name == "style":
                self.problems.append(f"<{tag} {name}>")
        if tag == "style":
            self.problems.append("<style>")
        if tag == "script":
            source = names.get("src")
            if not source or not source.startswith("/") or source.startswith("//"):
                self.problems.append("<script> without same-origin src")
            self._in_script = True
        for name in ("href", "src", "action"):
            value = names.get(name) or ""
            if value.lower().startswith(("javascript:", "data:text/html", "http:", "https:")):
                self.problems.append(f"{name}={value}")

    def handle_endtag(self, tag: str) -> None:
        if tag == "script":
            self._in_script = False

    def handle_data(self, data: str) -> None:
        if self._in_script and data.strip():
            self.problems.append("inline script body")


def test_html_has_no_inline_script_or_style() -> None:
    pages = list(STATIC.rglob("*.html"))
    assert pages

    for page in pages:
        finder = InlineCodeFinder()
        finder.feed(page.read_text(encoding="utf-8"))
        assert finder.problems == [], page.name


def test_js_has_no_dangerous_sinks() -> None:
    scripts = list(STATIC.rglob("*.js"))
    assert len(scripts) >= 5

    for script in scripts:
        source = script.read_text(encoding="utf-8")
        for label, pattern in DANGEROUS_SINKS.items():
            assert not re.search(pattern, source), f"{label} in {script.name}"
        assert "http://" not in source
        assert "https://" not in source


def test_invitation_token_is_read_from_fragment_and_sent_in_body() -> None:
    main = (STATIC / "js" / "main.js").read_text(encoding="utf-8")

    assert "location.hash" in main
    assert "history.replaceState" in main
    assert '"/api/invitations/accept", { token }' in main
    assert "?token=" not in main


def test_static_files_are_served_with_safe_types(client: TestClient) -> None:
    manifest = client.get("/manifest.webmanifest")
    icon = client.get("/icon.svg")

    assert manifest.status_code == 200
    assert manifest.headers["content-type"].startswith("application/manifest+json")
    assert icon.headers["x-content-type-options"] == "nosniff"

from __future__ import annotations

import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path


class Validator(HTMLParser):
    void = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self) -> None:
        super().__init__()
        self.ids: list[str] = []
        self.stack: list[str] = []
        self.errors: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if values.get("id"):
            self.ids.append(values["id"] or "")
        if tag not in self.void:
            self.stack.append(tag)

    def handle_endtag(self, tag: str) -> None:
        if not self.stack or self.stack[-1] != tag:
            self.errors.append(f"Unerwartetes </{tag}> nach {self.stack[-1] if self.stack else 'nichts'}")
            return
        self.stack.pop()


def test_html_structure_and_unique_ids() -> None:
    parser = Validator()
    parser.feed(Path("storchcam/web/index.html").read_text(encoding="utf-8"))
    assert not parser.errors
    assert not parser.stack
    assert len(parser.ids) == len(set(parser.ids))
    required = {"weatherBlock", "streamStatus", "streamStatusText", "sys", "sysText", "boards", "sheet", "wizard", "pinDialog", "osk"}
    assert required <= set(parser.ids)


def test_javascript_syntax() -> None:
    node = shutil.which("node")
    if node:
        subprocess.run([node, "--check", "storchcam/web/app.js"], check=True)

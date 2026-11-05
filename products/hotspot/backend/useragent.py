"""A small User-Agent parser. It answers browser, OS and device class and nothing more."""

from __future__ import annotations

import re
from dataclasses import dataclass

_BOT = re.compile(r"bot|crawl|spider|slurp|headless|lighthouse|pingdom|monitor", re.IGNORECASE)
_VERSION = r"[\d.]+"

# Order matters: Edge and Opera embed "Chrome", Chrome embeds "Safari".
_BROWSERS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("Edge", re.compile(rf"(?:Edg|EdgiOS|EdgA)/({_VERSION})")),
    ("Opera", re.compile(rf"(?:OPR|Opera)/({_VERSION})")),
    ("Samsung Internet", re.compile(rf"SamsungBrowser/({_VERSION})")),
    ("Firefox", re.compile(rf"(?:Firefox|FxiOS)/({_VERSION})")),
    ("Chrome", re.compile(rf"(?:Chrome|CriOS)/({_VERSION})")),
    ("Safari", re.compile(rf"Version/({_VERSION}).*Safari")),
    ("Internet Explorer", re.compile(rf"(?:MSIE ({_VERSION})|Trident/.*rv:({_VERSION}))")),
)

_OSES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("iOS", re.compile(r"iPhone|iPad|iPod")),
    ("Android", re.compile(r"Android")),
    ("Windows", re.compile(r"Windows")),
    ("ChromeOS", re.compile(r"CrOS")),
    ("macOS", re.compile(r"Mac OS X|Macintosh")),
    ("Linux", re.compile(r"Linux|X11")),
)


@dataclass(frozen=True)
class ParsedUserAgent:
    browser: str | None
    browser_version: str | None
    os: str | None
    device_type: str | None


def parse_user_agent(user_agent: str | None) -> ParsedUserAgent:
    if not user_agent:
        return ParsedUserAgent(None, None, None, None)

    browser: str | None = None
    version: str | None = None
    for name, pattern in _BROWSERS:
        match = pattern.search(user_agent)
        if match:
            browser = name
            version = next((group for group in match.groups() if group), None)
            break

    os_name = next((name for name, pattern in _OSES if pattern.search(user_agent)), None)

    if _BOT.search(user_agent):
        device_type = "bot"
    elif re.search(r"iPad|Tablet|(?:Android(?!.*Mobile))", user_agent):
        device_type = "tablet"
    elif re.search(r"Mobi|iPhone|iPod|Android", user_agent):
        device_type = "mobile"
    else:
        device_type = "desktop"

    return ParsedUserAgent(browser=browser, browser_version=version, os=os_name, device_type=device_type)

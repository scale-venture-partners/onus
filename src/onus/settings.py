"""Configuration: defaults, onus.toml / pyproject [tool.onus], and flags.

Precedence, low to high: built-in defaults, the config file, command-line
flags. Codes and prefixes follow ruff.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path

from onus import rules
from onus.evidence import LOCAL_TOOLS

CONFIG_NAMES = ("onus.toml", ".onus.toml")


@dataclass
class Settings:
    select: tuple[str, ...] = ()
    ignore: tuple[str, ...] = ()
    extend_select: tuple[str, ...] = ()
    llm: bool = True
    model: str = "anthropic:claude-sonnet-4-6"
    judge_threshold: float = 0.7
    concurrency: int = 8
    transcript_exclude_tools: list[str] = field(default_factory=lambda: list(LOCAL_TOOLS))
    source: str = "defaults"

    def active_codes(self) -> set[str]:
        return rules.active(self.select, self.ignore, self.extend_select, self.llm)


def find_config(start: Path) -> Path | None:
    start = start.resolve()
    for directory in (start, *start.parents):
        for name in CONFIG_NAMES:
            if (directory / name).is_file():
                return directory / name
        pyproject = directory / "pyproject.toml"
        if pyproject.is_file() and "onus" in tomllib.loads(pyproject.read_text()).get("tool", {}):
            return pyproject
    return None


def from_mapping(data: dict, source: str = "config") -> Settings:
    known = {f.name for f in fields(Settings)} - {"source"}
    s = Settings(source=source)
    for key, value in data.items():
        name = key.replace("-", "_")
        if name not in known:
            raise ValueError(f"unknown setting {key!r}")
        if name in ("select", "ignore", "extend_select"):
            value = (value,) if isinstance(value, str) else tuple(value)
        setattr(s, name, value)
    return s


def load(config: Path | None = None, start: Path | None = None) -> Settings:
    path = config or find_config(start or Path.cwd())
    if path is None:
        return Settings()
    data = tomllib.loads(path.read_text())
    if path.name == "pyproject.toml":
        data = data.get("tool", {}).get("onus", {})
    return from_mapping(data, source=str(path))

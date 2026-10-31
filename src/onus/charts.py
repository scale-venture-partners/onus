"""Read a deck's native charts into blocks, one per category-value pair, so
tier 1 checks them with no model. A chart object carries its numbers exactly;
each is written the way PowerPoint prints it, from the number format the
series, its data labels or the value axis declare ("$2.4M", "88%", "1.4x").
A value with no format prints as a bare magnitude ("2400000"), and the block
says so, so that tier 1 matches it against evidence of any kind. A scatter or
bubble series contributes its y values only."""

from __future__ import annotations

import re
from datetime import date, timedelta

_SPLIT = re.compile(r";")
_LITERALS = re.compile(r'"[^"]*"|\\.|\[[^\]]*\]')
_EPOCH = date(1899, 12, 30)


def blocks(chart, label: str) -> list[tuple[str, str, bool]]:
    out = []
    if chart.has_title:
        out.append((label, chart.chart_title.text_frame.text, False))
    dated = bool(chart._chartSpace.xpath(".//c:dateAx"))
    for plot in chart.plots:
        for series in plot.series:
            fmt = _format(chart, plot, series)
            keys = _keys(plot, series, dated)
            for i, value in enumerate(series.values):
                if value is None:
                    continue
                head = ", ".join(part for part in (series.name, keys[i] if i < len(keys) else "") if part)
                out.append((label, f"{head}: {render(value, fmt)}" if head else render(value, fmt), _general(fmt)))
                custom = _custom_label(series, i)
                if custom:
                    out.append((label, custom, False))
    return out


def _keys(plot, series, dated: bool) -> list[str]:
    """The category labels as printed: text as is, a number through its format, a date from its serial."""
    element = series._element
    if not element.xpath("./c:cat/c:numRef"):
        return [" ".join(level for level in levels if level) for levels in plot.categories.flattened_labels]
    fmt = next(iter(element.xpath("./c:cat//c:formatCode/text()")), "General")
    letters = set(re.sub(r"[^dmy]", "", _LITERALS.sub("", fmt).lower()))
    values = [float(v) for v in element.xpath("./c:cat//c:pt/c:v/text()")]
    if not (dated or letters):
        return [render(v, fmt) for v in values]
    return [_date(v, letters) for v in values]


def _date(serial: float, letters: set[str]) -> str:
    day = _EPOCH + timedelta(days=int(serial))
    if "d" in letters:
        return f"{day.day} {day:%B %Y}"
    return f"{day:%B %Y}" if "m" in letters else f"{day:%Y}"


def _general(fmt: str | None) -> bool:
    return _SPLIT.split(fmt or "General")[0].strip().lower() == "general"


def _format(chart, plot, series) -> str:
    """The number format the values are printed with: the series' own data labels', else the plot's,
    else the series', else the value axis', else General."""
    own = series._element.xpath("./c:dLbls/c:numFmt[not(@sourceLinked='1')]/@formatCode")
    if own:
        return own[0]
    try:
        labels = plot.data_labels if plot.has_data_labels else None
    except AttributeError:  # python-pptx has no data-label support for scatter plots
        labels = None
    if labels is not None and not labels.number_format_is_linked:
        return labels.number_format
    element = series._element
    codes = element.xpath("./c:val//c:formatCode/text()") or element.xpath("./c:yVal//c:formatCode/text()")
    if codes and codes[0] != "General":
        return codes[0]
    try:
        ticks = chart.value_axis.tick_labels
    except ValueError:  # a pie has no value axis
        return "General"
    return "General" if ticks.number_format_is_linked else ticks.number_format


def _custom_label(series, i: int) -> str:
    try:
        label = series.points[i].data_label
    except (AttributeError, IndexError):
        return ""
    return label.text_frame.text if label.has_text_frame else ""


def render(value: float, fmt: str | None) -> str:
    """`value` as an Excel number format prints it: literals, currency, percent, decimals,
    thousands grouping and the trailing commas that scale to thousands and millions. A
    negative value is written with "minus", which the scanner reads and a dash is not."""
    sign = "minus " if value < 0 else ""
    if _general(fmt):
        return sign + _plain(abs(value))
    try:
        return sign + _formatted(abs(value), _SPLIT.split(fmt or "")[0])
    except ValueError:  # an unterminated quote or bracket in the format
        return sign + _plain(abs(value))


def _plain(value: float) -> str:
    value = float(value)
    return f"{value:.0f}" if value.is_integer() else f"{value:.15f}".rstrip("0").rstrip(".")


def _formatted(value: float, section: str) -> str:
    prefix: list[str] = []
    suffix: list[str] = []
    placeholders: list[str] = []
    scale, percent, grouping, i = 1.0, False, False, 0
    while i < len(section):
        c = section[i]
        side = suffix if placeholders else prefix
        if c == '"':
            j = section.index('"', i + 1)
            side.append(section[i + 1:j])
            i = j + 1
            continue
        if c == "\\":
            side.append(section[i + 1:i + 2])
            i += 2
            continue
        if c == "[":
            j = section.index("]", i)
            if section[i + 1] == "$":
                side.append(section[i + 2:j].split("-")[0])
            i = j + 1
            continue
        if c in "0#?.":
            placeholders.append(c)
        elif c == ",":
            if placeholders and section[i + 1:i + 2] in "0#?" and section[i + 1:i + 2]:
                grouping = True
            elif placeholders:
                scale *= 1000
        elif c == "%":
            percent = True
            side.append("%")
        elif c in "_*":
            i += 1
        elif c in "$€£¥" or c.isspace():
            side.append(c)
        i += 1
    mask = "".join(placeholders)
    decimals = len(mask.split(".")[1]) if "." in mask else 0
    number = value * (100 if percent else 1) / scale
    text = f"{number:,.{decimals}f}" if grouping else f"{number:.{decimals}f}"
    return "".join(prefix) + text + "".join(suffix)

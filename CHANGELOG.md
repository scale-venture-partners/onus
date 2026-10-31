# Changelog

## Unreleased

- A deck's native charts are read, with no model: one block per category and
  value, written as PowerPoint prints it from the chart's number format, plus
  the title and any custom data label, under "slide 3 (chart 1)". A value in
  "General" format is a bare magnitude, and tier 1 matches it against evidence
  of any kind at that magnitude (2400000 by $2.4M, 0.88 by 88%). A deck used
  as evidence contributes each chart as a passage of its own.

- A document's pictures are read. The model transcribes what a chart, table,
  KPI tile or screenshot prints, one statement per line, and the lines are
  checked like the slide's text, under the picture's location ("slide 3
  (image 1)"). Only what is printed is transcribed, never a value read off an
  axis. Every picture goes to the model, which says when there is nothing to
  check; only a vector format or an oversized file, which the API refuses, is
  skipped, with a note. `--no-images` leaves every picture unread.

- A deck's grouped shapes are read, including groups inside groups. A group
  has no text of its own, so a number in a grouped KPI tile was neither checked
  nor usable as evidence.

- A count in the 1950-2099 band is no longer forced to be a year: "2000 users"
  and "2048-bit keys" are counts, "in 2000" is still a year, and either way
  the number is matched as both readings (ONS001/ONS002).

- A range applies the symbol, scale or unit it states once to both of its
  ends: `$2-3M` is $2M and $3M, `18-24 months` two durations, `2-3%` two
  percentages, whichever dash writes it, as long as the dash is set tight: a
  spaced one joins a label to a value (`Headcount 12 - 15%`), and "grew 20% to
  1,200" is a change, not a range. `$2-3M` used to read as two dollars, with
  the 3M dropped.
  `US$2-3M` and `$2M-3M` read the same way. A spread is not a change, so
  `$2-3M` no longer derives "up 50%", while `from $2M to $3M` still does.

- A claim is checked at its own precision, not the looser of the two
  precisions: evidence reading "about 2 billion" no longer supports a claim of
  `$2.4B`, which the source never stated (ONS001). A fraction in the evidence
  keeps the rounding slack it was built with, so "two thirds" still supports
  both 66% and 67%.

## 0.1.0 - 2026-10-05

Initial alpha: deterministic number and date checks, model-judged claims with
verbatim quotes, document/transcript/directory evidence, ruff-style rule codes,
and a labelled eval. Default model: `anthropic:claude-sonnet-5-5`.

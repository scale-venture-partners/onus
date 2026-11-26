# Changelog

## Unreleased

- A statement's cell in the evidence supports a claim only if its row label
  or column header shares a word with the claim, a cell under a six-month
  column only a claim that names the half, and one under an annual column any
  claim but one about a quarter or a half; a change or ratio between two cells
  is held to the same.

- Under a unit note ("(In thousands)"), every number in a table row's cells
  is read as dollars at that scale, not only those with a `$` beside them, so
  `Total operating expenses | 43,503` supports "$43.5 million"; header dates
  and rows that count shares, staff or customers are not, but a row whose label
  names a period (`Cash at end of year`) or a cell like `1987` is. Accounting
  negatives keep their kind: `(36.4)%`, `$ | (0.38)` and `$(34.9)M`.

- A table in the document being checked carries the unit its caption or
  header names ("Headline Numbers ($M)") to its bare cells, and its caption
  and header rows to each row's claim, in Markdown, decks and Word.

- A number joined to a long digit run by a dash ("26-4087597") is no longer
  read as a count.

- A table cell in the evidence is also held to its column's year and quarter
  ("Q2 FY26" supports no claim about Q3 FY26 or FY25), takes a section row
  ("Operating income:") as the subject of a generic label ("Total"), and is
  aligned with its header by place when a cell is blank or "n/m". A unit note
  just above a blank line still heads its table, and stays with it when long
  evidence is split. A CSV file is read as a table, one row per line. A lone
  row of bare counts, a log line or a markdown rule ("|---|") is no statement
  row, and a rank column in a deck or Word table is no claim.

- A chart value in General format under a series name, value-axis title or
  chart title that names a currency and a scale ("Revenue ($B)", "in $
  millions", "USD M") is read as that money as well as a bare magnitude, so
  `96.2` is supported by `$96.2 billion` or `$96,221M`. A deck used as evidence
  states its chart values at that scale too, when all its series share one unit.

- A table under a unit note ("($ in millions)", "in thousands") states its
  dollar amounts at that scale as well, so `$4,210` in the evidence supports a
  claim of `$4.2B`. The unscaled reading stays, for a per-share column, and a
  blank line ends the table. A `$` in a cell of its own, as SEC filings print
  it, marks the amount in the next cell.

- A bare number that ends a sentence ("The count was 17.") is read as a count.
  A full stop after it hid it, so neither a document's nor the evidence's was
  checked. "1.2.3" is still no number.

- A chart value in General format is written with 15 significant digits, as
  PowerPoint prints it: 96.2 is "96.2", not "96.200000000000003", which held the
  evidence to 15-decimal precision and so never matched.

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

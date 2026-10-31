# onus

A claim checker. It finds the verifiable claims in a deck, memo or document
and checks each one against the evidence. Numbers and dates are checked
**deterministically**; everything else goes to a model that has to **quote**
its source. Findings use ruff-style rule codes.

> **Status: alpha (0.1).** Rule codes, config keys and the report format may change
> between releases. The deterministic tier is stable and tested; the model tiers
> depend on the model you point them at and will vary run to run.

```console
$ onus update.pptx --evidence facts.md
update.pptx:slide 1: ONS002 Q1 2025: no date like it in the evidence
    Q1 2025
update.pptx:slide 3: ONS001 95%: no number like it in the evidence
    The stations holding renewal above 95% are the same ones that fixed bikes fastest.
update.pptx:slide 3: ONS002 18 months: no date like it in the evidence
    Median repair backlog fell from 2.1x to 1.4x of weekly capacity over 18 months
update.pptx:slide 3: ONS002 2023: no date like it in the evidence
    The shift followed the 2023 flood closures.
update.pptx:slide 4: ONS101 The evidence says nothing about why riders keep coming back; it only
provides operational and financial facts. p=0.97
    Riders keep coming back because the stations sit beside daily commute routes.

8 findings across 17 claims (ONS002 ×6, ONS001 ×1, ONS101 ×1; numbers + model).
```

(Output trimmed to five of the eight findings.)

*The onus is on the claim.* It's the third of a set: [riff](https://github.com/scale-venture-partners/riff)
lints the writing, overset lints the layout, and onus checks whether what the document says is true to
its sources.

## Why

An agent built that deck from a prompt that stated a handful of facts. Every figure
above is one it invented: dates, periods, a retention threshold, a reason. The
prose linter passed every one of them, because each read well. What they
lacked was a source, and that's checkable.

## How it works

onus is a small test pipeline. The claims are the test cases, the evidence is
the fixture, and the checks are the assertions.

1. **Read** the document into blocks: text frames per slide, paragraphs, table
   cells, and a native chart's values (see [Charts](#charts)). A deck's pictures
   are transcribed by the model (see [Images](#images)).
2. **Extract claims** from two sources, merged:
   - a deterministic **scan** finds every quantity: money (`$2.4B`, `2.4
     billion`, `$2,400M` are all the same value), percentages, "two thirds",
     multiples, counts, years, quarters (`Q1 2025`), dates, durations, and
     ranges (`$2-3M` is $2M and $3M, each checked on its own);
   - a **pydantic-ai agent** returns typed claims, adding the ones with no
     number in them ("customers are deepening usage because…") and what each
     claim is about. Its spans must appear in the document **verbatim** or
     they're dropped, its numbers are re-parsed from the text rather than
     trusted, and any number it missed becomes a claim of its own.
3. **Tier 1: deterministic.** Each quantity is matched against every quantity
   in the evidence, at the precision **the claim** was written with (`$2.4B`
   matches `$2,412M`, which rounds to it; `42%` doesn't match `41%`, and a
   source that only said "about $2 billion" doesn't support `$2.4B`). It also
   matches the changes and ratios between quantities stated in one evidence
   sentence, so "1.4x, down from 2.1x" supports "down 33%". **A model can't
   overrule this tier.**
4. **Tier 2: judged.** A model sees a claim and the most relevant evidence
   passages, and answers supported, contradicted or unsupported. Its quote must
   appear in the evidence verbatim, or the verdict is discarded. It may judge
   claims with no number, and may clear a **small count** of listed things
   ("three theses" when three are listed). It can't clear a number, date or
   large count that tier 1 failed.

## Evidence

```console
onus deck.pptx --evidence facts.md --evidence data.json
onus memo.docx --evidence source_deck.pptx
onus deck.pptx --evidence ~/.harness/sessions/2026-10-01.jsonl     # an agent transcript
onus deck.pptx --evidence-text "ARR $2.4B; NRR 118%"
onus deck.pptx --evidence ~/src/app --evidence-exclude 'tests/*'   # a repository
```

Evidence can be `.md`/`.txt`, `.json`/`.jsonl` (flattened to text), `.pptx`/`.docx`,
or a Messages-API-shaped **agent transcript**. A transcript counts only what came
*into* the conversation: the user's messages and the results of tools that fetch
information. Excluded are the agent's own text, `<system-reminder>` blocks, and
tools that mostly echo the agent's own work back to it (`read`, `write`,
`edit`, `bash`, `ls`, `find`, `grep`, configurable via
`transcript-exclude-tools`). Otherwise a fabrication becomes its own evidence
the moment the agent reads back the file it wrote.

### A directory

A directory, typically a git repository for a deck about the code in it, is
read as its files, each named by its path (`app/README.md#3`). Three things
keep a whole repository from drowning the claims in noise:

- **What's read.** In a git repository, only tracked files, so build output
  and anything gitignored never count. Elsewhere, a walk that skips `.git`,
  `node_modules`, `.venv`, caches and the like. Only known text types (prose,
  data, code), `.pptx`/`.docx`, and `README`-style names are read; lockfiles,
  minified bundles, binaries and files over 256 KB are skipped.
  `--evidence-exclude GLOB` skips more (`'tests/*'`, `'*.csv'`). A note says
  what was read: `evidence: 138 tracked files from my-app/ (skipped 161
  other types, 1 lockfiles)`.
- **What the judge sees.** The six passages it gets per claim are ranked by
  BM25, so a rare word counts more than a common one, with code weighted at
  half of prose. A README paragraph beats the code that implements it.
- **When a number counts.** A repository contains every small number there
  is, so a number read from a directory supports a claim only if its own
  sentence (its line, in code and tables, plus a table's header) shares a
  word with the claim. `MAX_TURNS = 40` supports "the loop stops after 40
  turns"; "113 unsupported claims" doesn't support "113 tool calls per deck".
  No arithmetic is derived from code. A file named on its own keeps the
  looser rule: any matching number counts.

Measured on a workshop deck checked against its own repository: of six
made-up numbers, five matched something in the repo under the looser rule;
under this one none did, and the one true number still passed. On the deck's
42 real numbered claims, one, a chart label "Build 6", went from "supported"
(by "drifted on six axes") to flagged, which was correct.

## Charts

A native chart object carries its numbers exactly, so it is read with no
model: one block per category and value, printed as PowerPoint would from
the chart's number format, plus its title and any custom data label, under
`slide 2 (chart 1)`. A date on the category axis is written as a date, a
negative value with "minus". A value left in "General" format is a bare
magnitude, so tier 1 matches it against evidence of any kind at that
magnitude: `2400000` is supported by `$2.4M`, `0.88` by `88%`. A deck used
as evidence contributes each chart as a passage. A scatter or bubble series
contributes its y values only, and charts in Word documents are not read.

## Images

A picture (a chart exported from Canva or Google Slides, a KPI tile, a
screenshot) goes to the model, which writes out what it prints, one line per
value, and the lines are checked like the slide's text:

```
deck.pptx:slide 3 (image 1): ONS001 $3.1M: no number like it in the evidence
    Fare revenue, 2025: $3.1M
```

Only what is printed is transcribed, never a bar height read off an axis. The
model says when a picture holds nothing to check; onus does not judge one by
its size. PNG, JPEG, GIF and WebP are sent, other types and oversized files
are skipped with a note, and a repeated picture is read once. `--no-images`
(`images = false` in config) or `--no-llm` leaves pictures unread. In Word
files, pictures in paragraphs and table cells are read; headers, footers and
VML are not. The JSON report's `origin` field says whether a claim came from
text, a chart or a transcription. Pictures inside evidence documents are not
read.

## Rules

| Code | Checked by | Rule |
|---|---|---|
| ONS001 | exact | A number the evidence does not contain or imply |
| ONS002 | exact | A year, quarter, date or duration the evidence does not contain |
| ONS003 | exact, model may clear | A small count (12 or under) the evidence does not state |
| ONS004 | exact | A number supported only by arithmetic *(off by default; "show your working")* |
| ONS101 | model | A factual claim the evidence does not support |
| ONS102 | model | A claim the evidence contradicts |
| ONS201 | model | A number the extractor missed *(off by default; a measure of the extractor)* |

**Exit codes:** `0` every claim supported, `1` findings, `2` usage or file error.

`--no-llm` runs tier 1 only: offline, deterministic, free. Without it onus
needs a provider key (`ANTHROPIC_API_KEY` for the default model) and stops
with a message, rather than silently degrading, if the key is missing.

`--report claims.json` writes the **fact-check report**: every claim, its
status, and the evidence passage that settled it. That's useful to a person
reviewing the deck, not only to the agent that wrote it.

## Evals

`evals/cases.json` holds labelled cases from real agent runs. Each case lists
what must be flagged and what must not be.

```console
$ uv run python evals/run.py               # numbers only
case                                     recall  false +  claims
deck: bike-share annual update           7/7           0      16
memo: planted fabrications               4/4           0       9

$ uv run python evals/run.py --model       # the full pipeline
deck: bike-share annual update           9/9           0      19
memo: planted fabrications               4/4           0       9
```

The deterministic layer is held to perfect recall and no false positives on
these cases, as a test (`tests/test_cli.py`), because it's the part allowed
to refuse an answer. The model tier is measured, not guaranteed: it can miss a
claim with no number in it (an older Sonnet missed one on this deck), and
results vary by model and run.

## Limits

- **Magnitude, not direction.** "Down 33%" and "up 50%" are both derivable from
  1.4x and 2.1x; tier 1 doesn't know which way the metric moved.
- **Presence, not subject.** A number is supported if a named evidence file
  contains it anywhere (from a directory, its sentence must share a word with
  the claim). "NRR 42%" passes against "ARR up 42%". Catching a right number on
  the wrong subject is tier 2's job, and it currently judges only claims that
  tier 1 can't settle.
- **A year and a count can look alike.** Anything from 1950 to 2099 could be
  either, so the words around it decide which it reads as ("2000 users" is a
  count, "in 2000" a year) and which rule code a finding gets. Matching tries
  both readings, so such a number is supported if the evidence states it
  either way.
- **A range is read from one line, with its dash set tight.** A tail that
  wraps to the next line is still read (`between 18 and 24` / `months`), but a
  spaced dash joins a label to a value (`Headcount 12 - 15%`), so a range
  spaced that way reads as two separate numbers, and a unit the scanner
  doesn't know (`12-15 bikes`, `2-3 kW`) leaves the high end unchecked.
- **Claims with a number and a qualitative part** ("118% NRR tells us expansion
  is structural") are settled by their number.
- **A native chart's values are its data, not what a viewer sees.** Every
  value in its series is checked, labelled on the chart or not, and an axis
  title or display unit ("Millions") is not read. A chart in a picture
  contributes only what is printed in it, as the model reads it: a value it
  cannot make out, or a number it misreads, is a miss or a false finding in
  the model tier.

## Configuration

`onus.toml` (or `.onus.toml`, or `[tool.onus]` in `pyproject.toml`), found
from the document's folder upward:

```toml
select = ["ONS0", "ONS1"]
ignore = ["ONS003"]
extend-select = ["ONS004"]
llm = true
images = true                            # transcribe pictures with the model
model = "anthropic:claude-sonnet-5-5"    # any pydantic-ai model string
judge-threshold = 0.7
concurrency = 8
transcript-exclude-tools = ["read", "write", "edit", "bash", "ls", "find", "grep"]
```

## Development

```console
uv sync
uv run pytest -q --cov          # hermetic: the model is pydantic-ai's TestModel/FunctionModel
uv run python evals/run.py      # the labelled eval, offline
uv run ruff check src tests evals
uv run mypy                     # types
```

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE).

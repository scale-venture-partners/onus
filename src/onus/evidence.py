"""What the claims are checked against.

Evidence is a list of passages, each with a source, and every quantity in
them parsed once. It can be plain files (notes, a data-room export, the
prompt), other documents (.pptx, .docx), JSON, an agent's session
transcript, or a whole directory -- a git repository, say, for a deck about
the code in it.

A directory brings three problems a single file doesn't, and each has an
answer here. Most of it is noise: lockfiles, binaries, generated files -- so
only tracked files of known text types under a size limit are read. Code
outweighs prose by volume -- so passages are ranked for the judge with BM25,
code weighted below prose. And a repository holds every small number there
is: ports, versions, loop bounds, "slide 17", "113 unsupported claims" -- so
a number read from a directory supports a claim only when its own sentence
(its line, in code or data) shares a word with the claim. `MAX_TURNS = 40`
supports "40 turns"; "on slide 17" does not support "a run costs $17". No
arithmetic is derived from code. A file named on its own is the user's
chosen source, and keeps the looser rule: any matching number counts.

A transcript needs care, because most of what is in it is the agent itself.
Only what came *into* the conversation counts: the user's messages and the
results of tools that fetch information. The agent's own text is excluded,
and so are tools that mostly echo the agent's own work back to it -- reading
the builder script it just wrote, or a hook's reminder quoting the slide.
Otherwise a fabrication becomes its own evidence the moment the agent reads
its file back.
"""

from __future__ import annotations

import fnmatch
import json
import math
import os
import re
import subprocess
from collections import Counter
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path

from onus import numbers

# Tools whose results are, by default, the agent's own work coming back to it.
LOCAL_TOOLS = ("read", "write", "edit", "bash", "ls", "find", "grep")


# What a directory contributes. Prose and data are read as they are; code is
# read too, but ranked lower and held to a stricter standard for numbers.
PROSE_SUFFIXES = {".md", ".markdown", ".mdx", ".rst", ".txt", ".adoc", ".org", ".tex", ".html", ".htm"}
DATA_SUFFIXES = {".json", ".jsonl", ".csv", ".tsv", ".yaml", ".yml", ".toml", ".ini", ".cfg"}
DOCUMENT_SUFFIXES = {".pptx", ".docx"}
CODE_SUFFIXES = {".py", ".pyi", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".kt",
                 ".swift", ".rb", ".php", ".c", ".h", ".cc", ".cpp", ".hpp", ".cs", ".scala", ".sh", ".bash",
                 ".zsh", ".sql", ".r", ".jl", ".lua", ".ex", ".exs", ".vue", ".svelte", ".css", ".scss"}
PROSE_NAMES = {"readme", "changelog", "changes", "history", "notes", "authors", "contributing", "license"}
LOCKFILES = {"uv.lock", "poetry.lock", "pipfile.lock", "package-lock.json", "npm-shrinkwrap.json", "yarn.lock",
             "pnpm-lock.yaml", "cargo.lock", "gemfile.lock", "go.sum", "composer.lock", "bun.lockb"}
# Outside a git repository there is no list of tracked files; skip what never is one.
SKIP_DIRS = {".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv", ".tox", ".nox", "dist",
             "build", "target", ".next", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".idea", ".vscode"}
MAX_FILE_BYTES = 256_000  # per file, in a directory; a file named on its own has no limit
CODE_WEIGHT = 0.5         # a code passage's rank against an equally relevant prose one
TABLE_SUFFIXES = {".csv", ".tsv"}
_SENTENCE_END = re.compile(r"[.!?](?=\s)|\n\s*\n")


@dataclass
class Passage:
    source: str
    text: str
    kind: str = "prose"  # "prose" | "code" | "table"
    strict: bool = False  # read from a directory: a number needs context to count
    header: str = ""      # a table's first line, context for every row
    quantities: list[numbers.Quantity] = field(default_factory=list)
    derived: list[numbers.Quantity] = field(default_factory=list)

    def __post_init__(self):
        self.quantities = numbers.find(self.text)
        if self.kind == "code":
            return  # pairs of numbers in code license no arithmetic
        for sentence in numbers.sentences(self.text):
            self.derived += numbers.derived(numbers.find(sentence))

    @cached_property
    def quote_key(self) -> str:
        return _quote_key(self.text)

    def can_support(self, quantity: numbers.Quantity, claim_text: str) -> bool:
        """Whether this passage's `quantity` may support a claim. In a named file, always;
        in code, or anything read from a directory, only if the number's context -- its
        line in code and tables, its sentence in prose -- shares a word with the claim."""
        if not (self.strict or self.kind == "code"):
            return True
        return bool(_named(self._context(quantity)) & _named(claim_text))

    def _context(self, quantity: numbers.Quantity) -> str:
        text, a, b = self.text, quantity.start, quantity.end
        if self.kind in ("code", "table"):
            start = text.rfind("\n", 0, a) + 1
            end = text.find("\n", b)
            return f"{self.header} {text[start:end if end >= 0 else len(text)]}"
        bounds = [m.end() for m in _SENTENCE_END.finditer(text, 0, a)]
        stop = _SENTENCE_END.search(text, b)
        return text[bounds[-1] if bounds else 0:stop.start() if stop else len(text)]


@dataclass
class Evidence:
    passages: list[Passage] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def add(self, source: str, text: str, kind: str = "prose", strict: bool = False) -> None:
        header = text.split("\n", 1)[0] if kind == "table" else ""
        for i, chunk in enumerate(_chunks(text)):
            self.passages.append(Passage(f"{source}#{i + 1}" if i else source, chunk, kind, strict, header))
        self.__dict__.pop("_index", None)

    def __bool__(self) -> bool:
        return bool(self.passages)

    def contains(self, quote: str) -> Passage | None:
        """The passage holding `quote` word for word.

        Words and numbers must match exactly and in order; case, whitespace,
        punctuation and markup don't count. A judge quoting a markdown source
        drops its `**bold**` and backticks, and quoting a sentence it often
        drops a comma -- rejecting those as fabricated quotes turned four
        supported claims into "unsupported" on one deck.
        """
        needle = _quote_key(quote)
        if not needle.strip():
            return None
        for p in self.passages:
            if needle in p.quote_key:
                return p
        return None

    def relevant(self, query: str, k: int = 6) -> list[Passage]:
        """The `k` passages most relevant to `query`, by BM25 with code weighted
        below prose -- enough context for a judge without sending everything.

        BM25 rather than shared-word counts: across a repository, a common word
        ("model", "test") matches thousands of passages and says little; a rare
        one says a lot. And a README paragraph should beat the code that
        implements it when both match equally.
        """
        terms = set(_tokens(query))
        index = self._index
        scored = []
        for p, (tf, length) in zip(self.passages, index["docs"], strict=True):
            score = 0.0
            for t in terms & tf.keys():
                n = tf[t]
                score += index["idf"][t] * n * 2.2 / (n + 1.2 * (0.25 + 0.75 * length / index["avg"]))
            scored.append(score * (CODE_WEIGHT if p.kind == "code" else 1.0))
        order = sorted(range(len(self.passages)), key=lambda i: -scored[i])
        return [self.passages[i] for i in order[:k] if scored[i] > 0] or [self.passages[i] for i in order[:k]]

    @cached_property
    def _index(self) -> dict:
        docs = [(Counter(_tokens(p.text)), 0) for p in self.passages]
        docs = [(tf, sum(tf.values())) for tf, _ in docs]
        df = Counter(t for tf, _ in docs for t in tf)
        n = len(docs)
        return {"docs": docs, "avg": max(1.0, sum(d for _, d in docs) / max(1, n)),
                "idf": {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}}


STOP = set("the a an and or of to in on for with by at is are was were be been this that these those it its as "
           "from our we you they their has have had not but".split())


def _tokens(text: str) -> list[str]:
    """Words for ranking: identifiers split (`maxTurns`, `max_turns` -> max, turns)."""
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
    return [w.strip(".") for w in _words(text) if w.strip(".")]


def _named(text: str) -> set[str]:
    """The words of `text` that name something (no numbers): what a line of code and a
    claim must share before a number in one supports the other."""
    return {w for w in _tokens(text) if any(c.isalpha() for c in w) and len(w) > 2}


def _words(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9$%.]+", text.lower()) if w not in STOP and len(w) > 1]


# A number keeps its separators and units ("$2.4B", "1,200", "38%"); a word its
# apostrophe ("don't"). Everything else -- punctuation, markdown, quote marks --
# is not part of what a quote says.
_QUOTE_TOKEN = re.compile(r"\$?\d+(?:[.,:]\d+)*%?|[a-z]+(?:'[a-z]+)*")


def _quote_key(text: str) -> str:
    """Text as a space-delimited word sequence, for whole-word containment."""
    text = text.lower().replace("\u2019", "'").replace("\u2018", "'")
    return " " + " ".join(_QUOTE_TOKEN.findall(text)) + " "


def _chunks(text: str, size: int = 1500) -> list[str]:
    """Paragraph-aligned chunks, so a judge sees whole passages."""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks, current = [], ""
    for p in paragraphs:
        if current and len(current) + len(p) > size:
            chunks.append(current)
            current = ""
        current = f"{current}\n\n{p}" if current else p
    return chunks + ([current] if current else [])


def _strings(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    return [] if value is None or isinstance(value, bool) else [str(value)]


def _block_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content
                         if not (isinstance(b, dict) and b.get("type") == "image"))
    return ""


def _is_reminder(text: str) -> bool:
    return text.lstrip().startswith("<system-reminder>")


def add_transcript(evidence: Evidence, lines: list[dict], source: str, exclude_tools=LOCAL_TOOLS) -> None:
    """A Messages-API-shaped session log: one {"role", "content"} per line."""
    tool_names = {}
    for line in lines:
        content = line.get("content")
        if line.get("role") == "assistant" and isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    tool_names[block.get("id")] = block.get("name")
    for n, line in enumerate(lines, start=1):
        if line.get("role") != "user":
            continue
        content = line.get("content")
        if isinstance(content, str):
            if not _is_reminder(content):
                evidence.add(f"{source}:user:{n}", content)
            continue
        for block in content or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_result":
                name = tool_names.get(block.get("tool_use_id"), "tool")
                if name in exclude_tools or block.get("is_error"):
                    continue
                text = _block_text(block.get("content"))
                if text.strip():
                    evidence.add(f"{source}:{name}:{n}", text)
            elif block.get("type") == "text" and not _is_reminder(block.get("text", "")):
                evidence.add(f"{source}:user:{n}", block["text"])


def load(paths=(), texts=(), exclude_tools=LOCAL_TOOLS, exclude=()) -> Evidence:
    """Evidence from files, directories and inline texts. `exclude` holds globs, matched
    against paths inside a directory (`tests/*`, `*.csv`)."""
    evidence = Evidence()
    for i, text in enumerate(texts, start=1):
        evidence.add(f"text:{i}", text)
    for raw in paths:
        path = Path(raw)
        if path.is_dir():
            _load_directory(evidence, path, exclude_tools, exclude)
        else:
            _load_file(evidence, path, path.name, exclude_tools, path.read_text)
    return evidence


def _load_file(evidence: Evidence, path: Path, source: str, exclude_tools, read, strict: bool = False) -> None:
    from onus import documents

    def add(src, text, kind="prose"):
        evidence.add(src, text, kind, strict)

    suffix = path.suffix.lower()
    if suffix == ".jsonl":
        lines = [json.loads(line) for line in read().splitlines() if line.strip()]
        if lines and all(isinstance(x, dict) and "role" in x for x in lines):
            add_transcript(evidence, lines, source, exclude_tools)
        else:
            add(source, "\n\n".join(s for x in lines for s in _strings(x)))
    elif suffix == ".json":
        add(source, "\n\n".join(_strings(json.loads(read()))))
    elif suffix in DOCUMENT_SUFFIXES:
        doc = documents.load(path)
        add(source, "\n\n".join(b.text for b in doc.blocks))
    else:
        add(source, read(), "code" if suffix in CODE_SUFFIXES else "table" if suffix in TABLE_SUFFIXES else "prose")


def _listed_files(root: Path) -> tuple[list[str], bool]:
    """Paths under `root`, relative to it: git's tracked files when `root` is in a
    repository (so .gitignore'd output never counts), else a walk that skips the
    usual tool and environment folders. Returns (paths, from_git)."""
    try:
        out = subprocess.run(["git", "-C", str(root), "ls-files", "-z"], capture_output=True, timeout=60)
        if out.returncode == 0:
            return sorted(p for p in out.stdout.decode("utf-8", "replace").split("\0") if p), True
    except (OSError, subprocess.SubprocessError):
        pass
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith("."))
        found += [Path(dirpath, f).relative_to(root).as_posix() for f in filenames]
    return sorted(found), False


def _skip_reason(rel: str, path: Path, exclude) -> str | None:
    name = path.name.lower()
    if any(fnmatch.fnmatch(rel, g) or fnmatch.fnmatch(rel, g.rstrip("/") + "/*") for g in exclude):
        return "excluded"
    if name in LOCKFILES:
        return "lockfiles"
    suffix = path.suffix.lower()
    known = PROSE_SUFFIXES | DATA_SUFFIXES | DOCUMENT_SUFFIXES | CODE_SUFFIXES
    if not (suffix in known or (not suffix and name in PROSE_NAMES)) or name.endswith((".min.js", ".min.css")):
        return "other types"
    if not path.is_file():
        return "other types"  # a submodule, a dangling link
    if path.stat().st_size > MAX_FILE_BYTES and suffix not in DOCUMENT_SUFFIXES:
        return "too large"
    return None


def _load_directory(evidence: Evidence, root: Path, exclude_tools, exclude) -> None:
    listed, from_git = _listed_files(root)
    label = root.resolve().name
    loaded = 0
    skipped: Counter[str] = Counter()
    for rel in listed:
        path = root / rel
        reason, text = _skip_reason(rel, path, exclude), ""
        if reason is None and path.suffix.lower() not in DOCUMENT_SUFFIXES:
            data = path.read_bytes()
            if b"\0" in data[:8192]:
                reason = "binary"
            else:
                try:
                    text = data.decode("utf-8")
                except UnicodeDecodeError:
                    reason = "binary"
        if reason:
            skipped[reason] += 1
            continue
        try:
            _load_file(evidence, path, f"{label}/{rel}", exclude_tools, lambda text=text: text, strict=True)
        except Exception as e:  # one malformed file shouldn't sink a repository
            skipped["unreadable"] += 1
            evidence.notes.append(f"evidence: skipped {label}/{rel} ({type(e).__name__}: {e})")
            continue
        loaded += 1
    what = "tracked files" if from_git else "files"
    detail = ", ".join(f"{n} {r}" for r, n in sorted(skipped.items(), key=lambda x: (-x[1], x[0])))
    evidence.notes.append(f"evidence: {loaded} {what} from {label}/" + (f" (skipped {detail})" if detail else ""))

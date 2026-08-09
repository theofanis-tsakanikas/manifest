"""The engine-free core, enforced.

`src/manifest/core/` holds the logic claims 1 to 6 are about: the normalised document
representation, confidence handling, threshold derivation, provenance verification,
reconciliation, entity resolution, versioning. The rule is that it imports the standard
library and nothing else, that it is a pure function of its arguments, and that **it never
learns which engine produced a value**.

Three rules, checked by reading the syntax tree and then the source text.

**Nothing but the standard library and `manifest.core` itself.** No cloud SDK, no OCR wheel,
no test helper reaching back in. Dynamic imports are refused too — `importlib` and
`__import__` — because a rule a single indirection defeats will be defeated by a single
indirection.

**No ambient state.** No clock, no randomness, no environment, no filesystem, no network.
This is the half that protects claim 3. A `datetime.now()` inside a version identifier does
not raise, does not log and does not fail a test; it makes a re-extraction differ from the
extraction it is reproducing, and the difference shows up as a diff nobody can explain.

**No engine, named anywhere.** Not in an import, not in a type name, not in a comment. This
is the rule the other two exist to make possible and it is the one that decays first, because
the natural way to handle a quirk is `if engine == "…"`. The moment that line exists, the
normalised representation has stopped being the contract with the cloud and has become a
suggestion — and the claim that a local engine and a managed service are interchangeable
behind it is no longer true. A text scan rather than an AST scan on purpose: a string literal,
a dictionary key and a comment are all ways the name gets in, and none of them is an import.

The consequence, stated plainly so nobody has to discover it: **the core cannot special-case
anything.** An engine quirk is handled in the adapter that produces the normalised
representation, or it is handled by a declared field in that representation. There is no
third option, and that constraint is the deliverable.

All three rules are attacked in `scripts/gate_proof.py`.
"""

from __future__ import annotations

import ast
import re
import sys
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Final

#: Standard-library modules the core may not use either. Being in the standard library says
#: nothing about whether a module makes a function a pure function of its arguments.
_FORBIDDEN_STDLIB: Final[dict[str, str]] = {
    "socket": "the core never opens a connection",
    "ssl": "the core never opens a connection",
    "urllib": "the core never opens a connection",
    "http": "the core never opens a connection",
    "ftplib": "the core never opens a connection",
    "smtplib": "the core never opens a connection",
    "subprocess": "the core never starts a process; an engine is a process",
    "random": "a re-extraction must produce an identical record; a random source guarantees it will not",  # noqa: E501
    "secrets": "a re-extraction must produce an identical record; a random source guarantees it will not",  # noqa: E501
    "importlib": "a dynamic import is the import rule with one indirection in front of it",
}

#: Callables that read something the arguments did not supply. Matched on the dotted
#: expression and on the bare attribute name, so `datetime.now()`, `dt.now()` and a method of
#: our own called `now()` are all refused — the last one deliberately. An `ExtractedAt.now()`
#: is the most natural API in the world and it is exactly what must not exist here.
_FORBIDDEN_CALLS: Final[dict[str, str]] = {
    "now": "reads the wall clock; a document version is derived from content, never from the machine",  # noqa: E501
    "utcnow": "reads the wall clock; a document version is derived from content, never from the machine",  # noqa: E501
    "today": "reads the wall clock; a document version is derived from content, never from the machine",  # noqa: E501
    "time": "reads the wall clock; the core is given its instants, it does not read one",
    "monotonic": "reads the wall clock; timing belongs in observability, not in the core",
    "perf_counter": "reads the wall clock; measurement belongs in observability, not in the core",
    "time_ns": "reads the wall clock; the core is given its instants, it does not read one",
    "getenv": "reads the environment; a published field must depend on its inputs and nothing else",
    "urandom": "a re-extraction must produce an identical record",
    "uuid1": "a re-extraction must produce an identical record; ids are derived, never generated",
    "uuid4": "a re-extraction must produce an identical record; ids are derived, never generated",
    "open": "reads the filesystem; the core is given its pages, it does not go and find them",
    "input": "reads a terminal",
    "eval": "arbitrary evaluation has no place behind a published field",
    "exec": "arbitrary evaluation has no place behind a published field",
    "__import__": "a dynamic import is the import rule with one indirection in front of it",
    "import_module": "a dynamic import is the import rule with one indirection in front of it",
}

#: Attribute reads with the same problem. `os.environ["REGION"]` is not a call.
_FORBIDDEN_ATTRIBUTES: Final[dict[str, str]] = {
    "os.environ": "reads the environment; a published field must depend on its inputs alone",
}

#: Every extraction engine this project can route to, present or future, and the SDKs that
#: reach them. Matched case-insensitively on a word boundary over the whole source text —
#: comments and string literals included, because those are how the name actually gets in.
#:
#: The cloud vendor's own name is deliberately **not** on this list. A core docstring is
#: allowed to say where the adapters live; what it may not do is name the thing that read the
#: page, because that is the fact the normalised representation exists to erase.
_ENGINE_NAMES: Final[tuple[str, ...]] = (
    "boto3",
    "botocore",
    "textract",
    "bedrock",
    "comprehend",
    "tesseract",
    "pytesseract",
    "paddleocr",
    "paddlepaddle",
    "easyocr",
    "doctr",
    "rapidocr",
    "trocr",
    "kraken",
    "ocropus",
    "abbyy",
    "formrecognizer",
    "documentai",
    "documentintelligence",
    "surya",
    "mistralocr",
)

_ENGINE_PATTERN: Final = re.compile(
    r"\b(" + "|".join(re.escape(name) for name in _ENGINE_NAMES) + r")\b",
    re.IGNORECASE,
)

_ALLOWED_PACKAGE: Final = "manifest.core"


@dataclass(frozen=True, slots=True)
class Finding:
    """One violation, with the reason it is one.

    `path` is repository-relative so the output is the same on a laptop and on a runner — a
    finding whose text differs between the two is a finding somebody has to translate.
    """

    path: str
    line: int
    rule: str
    detail: str
    reason: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}  [{self.rule}] {self.detail} — {self.reason}"


def scan(core_root: Path, repository_root: Path) -> list[Finding]:
    """Every violation under `core_root`, in file and line order.

    Every violation, not the first: a caller who fixes one import and reruns to find the next
    learns the gate one refusal at a time, and gives up before the end.
    """
    findings: list[Finding] = []
    for path in sorted(core_root.rglob("*.py")):
        findings.extend(_scan_file(path, repository_root))
    return findings


def _scan_file(path: Path, repository_root: Path) -> Iterator[Finding]:
    source = path.read_text(encoding="utf-8")
    relative = path.relative_to(repository_root).as_posix()
    yield from _engine_findings(source, relative)
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        yield Finding(
            relative,
            exc.lineno or 0,
            "unparseable",
            f"{exc.msg}",
            "a module the gate cannot read is a module the gate is not checking",
        )
        return
    package = _package_of(path, repository_root)
    yield from _import_findings(tree, relative, package)
    yield from _ambient_findings(tree, relative)


def _package_of(path: Path, repository_root: Path) -> str:
    """The dotted package a module lives in, for resolving relative imports.

    `src/manifest/core/geometry.py` is in package `manifest.core`; a `from . import x` in it
    means `manifest.core.x`, and a `from .. import x` means `manifest.x` — which is outside
    the core and is exactly what this needs to be able to say.
    """
    parts = path.relative_to(repository_root / "src").with_suffix("").parts
    return ".".join(parts[:-1])


def _engine_findings(source: str, relative: str) -> Iterator[Finding]:
    for number, line in enumerate(source.splitlines(), start=1):
        for match in _ENGINE_PATTERN.finditer(line):
            yield Finding(
                relative,
                number,
                "engine name",
                f"`{match.group(0)}`",
                "the core never learns which engine produced a value; a quirk is handled in "
                "the adapter or declared in the normalised representation, never here",
            )


def _import_findings(tree: ast.AST, relative: str, package: str) -> Iterator[Finding]:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield from _judge_module(alias.name, node.lineno, relative)
        elif isinstance(node, ast.ImportFrom):
            module = _resolve(node, package)
            if module is None:
                yield Finding(
                    relative,
                    node.lineno,
                    "import",
                    f"relative import escaping {_ALLOWED_PACKAGE}",
                    "the core does not reach back into the rest of the package",
                )
                continue
            yield from _judge_module(module, node.lineno, relative)


def _resolve(node: ast.ImportFrom, package: str) -> str | None:
    """The absolute module an `ImportFrom` names, or None if it climbs past the package root."""
    if not node.level:
        return node.module or ""
    parts = package.split(".")
    if node.level > len(parts):
        return None
    base = parts[: len(parts) - (node.level - 1)]
    return ".".join([*base, node.module]) if node.module else ".".join(base)


def _judge_module(module: str, line: int, relative: str) -> Iterator[Finding]:
    if not module:
        return
    root = module.split(".", maxsplit=1)[0]
    if module == _ALLOWED_PACKAGE or module.startswith(f"{_ALLOWED_PACKAGE}."):
        return
    if root == "manifest":
        yield Finding(
            relative,
            line,
            "import",
            f"`{module}`",
            f"the core imports only itself and the standard library; {module} is outside "
            f"{_ALLOWED_PACKAGE}",
        )
        return
    if root in _FORBIDDEN_STDLIB:
        yield Finding(relative, line, "import", f"`{module}`", _FORBIDDEN_STDLIB[root])
        return
    if root == "__future__" or root in sys.stdlib_module_names:
        return
    yield Finding(
        relative,
        line,
        "import",
        f"`{module}`",
        "not in the standard library; the core runs with no cloud SDK and no engine, which "
        "is the only reason claims 1 to 6 can be checked without an account",
    )


def _ambient_findings(tree: ast.AST, relative: str) -> Iterator[Finding]:
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            dotted = _dotted(node.func)
            name = dotted.rsplit(".", 1)[-1] if dotted else ""
            if name in _FORBIDDEN_CALLS:
                yield Finding(
                    relative,
                    node.lineno,
                    "ambient state",
                    f"`{dotted}(...)`",
                    _FORBIDDEN_CALLS[name],
                )
        elif isinstance(node, ast.Attribute):
            dotted = _dotted(node)
            if dotted in _FORBIDDEN_ATTRIBUTES:
                yield Finding(
                    relative,
                    node.lineno,
                    "ambient state",
                    f"`{dotted}`",
                    _FORBIDDEN_ATTRIBUTES[dotted],
                )


def _dotted(node: ast.expr) -> str:
    """`a.b.c` for an attribute chain, `f` for a bare name, `""` for anything else."""
    parts: list[str] = []
    current: ast.expr = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
        return ".".join(reversed(parts))
    return ""


def report(findings: Iterable[Finding]) -> str:
    listed = list(findings)
    if not listed:
        return (
            "core-pure: the core imports no cloud SDK and no engine, reads no ambient state, "
            "and names no engine"
        )
    lines = [f"core-pure: {len(listed)} violation(s) in the core", ""]
    lines.extend(f"  {finding}" for finding in listed)
    lines.append("")
    lines.append(
        "The core is where claims 1 to 6 are proved. Every one of them is a claim that can be "
        "checked on a laptop with no account and no engine, and each line above is that "
        "property being given up. The logic moves out to an adapter; the tests do not move in."
    )
    return "\n".join(lines)

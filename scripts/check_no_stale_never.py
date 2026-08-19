#!/usr/bin/env python3
"""No file claims the estate has never done something it has done.

**Nine files said "nothing has ever been applied" after the estate had been applied five times,
and a tenth wave said "never called" after all three managed readers had been called.** Both were
found by reading rather than by a check, twice, a week apart — and the second wave used different
wording from the first, which is why a grep for last week's phrasing found nothing.

The pattern this closes is not a typo. A repository states its own posture in prose, the posture
changes on a Tuesday, and the prose keeps asserting the old one in every file that was not open
that day. It is the overclaim this project spends its life removing, pointing the wrong way: an
*under*claim, which reads as modesty and is just as false.

**Facts are declared here, not inferred**, because "has Textract been called?" is not a question
a script can answer from the tree — it is a question about the world, and the answer lives in a
recording, a CloudTrail entry or a dated line in `docs/DECISIONS.md`. What the script does is
mechanical and worth having: given a fact that is now true, refuse any file still asserting its
negation.

    python3 scripts/check_no_stale_never.py
"""

from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
GREEN, RED, DIM, RESET = "\033[32m", "\033[31m", "\033[2m", "\033[0m"

#: Where prose about the system lives. Not `docs/DECISIONS.md` or `docs/NEXT.md`, which keep
#: superseded text on purpose and mark it, and not `CHANGELOG.md`, which is a record of what was
#: true when. Those three are the house's way of remembering; the rest must be current.
#: **`docs/` was missing from this list on the first version, and the exemptions below hid it.**
#: Three doc files are exempt because they keep superseded text on purpose — which reads as though
#: the rest of the directory was considered and included. It was not scanned at all, so the five
#: documents that describe the system to a reader were the only prose in the repository this
#: check could not see.
SEARCHED = ("src", "scripts", "evals", "contracts", "infra", "tests", "docs")
ALSO = ("CLAUDE.md", "README.md", "SECURITY.md")
#: `test_authored_aws_fixtures.py` is the file that *enforces* the AUTHORED.md rule, so it has to
#: quote the sentence it forbids. An enforcer reading as a violator is the same confusion this
#: script's own `not in` guard exists for, one level up.
EXEMPT = (
    "docs/DECISIONS.md",
    "docs/NEXT.md",
    "CHANGELOG.md",
    "scripts/check_no_stale_never.py",
    "tests/extraction/test_authored_aws_fixtures.py",
)

#: Each entry: what became true, when, and the patterns that assert its negation.
#:
#: A pattern here is a claim somebody would have to delete a fact to keep. `never called` beside
#: a managed reader is one; `never been applied` beside the estate is another. Past-tense
#: narration — *"it was never called, and that is why this exists"* — is deliberately not matched,
#: because that is history and the house style is to keep history.
STALE = {
    "the estate has been applied and torn down, repeatedly, since 2026-08-10": (
        r"nothing (?:is|has) (?:ever )?(?:been )?applied",
        r"(?:has|have) never been (?:applied|dispatched)",
        r"the first deploy .{0,40} has never been run",
    ),
    "all three managed readers have been called: Textract and the LLM on 2026-08-15, "
    "Bedrock Data Automation on 2026-08-13": (
        r"\*\*never called\*\*",
        r"never called\.",
        r"no response has ever been captured",
        r"no page has yet been sent",
        r"tiers are never called",
    ),
}


def main() -> int:
    files = [
        path
        for directory in SEARCHED
        for path in (ROOT / directory).rglob("*")
        if path.is_file() and path.suffix in {".py", ".yaml", ".yml", ".tf", ".md"}
    ] + [ROOT / name for name in ALSO]

    problems: list[str] = []
    for path in sorted(files):
        relative = str(path.relative_to(ROOT))
        if any(relative.startswith(e) for e in EXEMPT):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for fact, patterns in STALE.items():
            for pattern in patterns:
                for match in re.finditer(pattern, text, re.IGNORECASE):
                    line = text.count("\n", 0, match.start()) + 1
                    # **A line that forbids the phrase is not a line that asserts it**, and the
                    # first run of this check flagged the very test that enforces the rule —
                    # `assert "no response has ever been captured" not in note`. A checker that
                    # cannot tell a claim from its refutation reports the fix as the defect.
                    source = text.splitlines()[line - 1]
                    if re.search(r"\bnot in\b|\bmay not\b|\bmust not\b", source):
                        continue
                    problems.append(f"{relative}:{line} asserts {match.group(0)!r}, and {fact}")

    print(f"stale-never: {len(files)} file(s) read against {len(STALE)} declared fact(s)")
    if problems:
        print(f"\n{RED}a file claims the system has never done something it has done{RESET}\n")
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        print(
            f"\n{DIM}Correct the sentence, or — if it is history — move it to docs/DECISIONS.md, "
            f"docs/NEXT.md or CHANGELOG.md, which keep superseded text and mark it.{RESET}"
        )
        return 1
    print(f"  {GREEN}ok{RESET}    no file asserts a negation that has stopped being true")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

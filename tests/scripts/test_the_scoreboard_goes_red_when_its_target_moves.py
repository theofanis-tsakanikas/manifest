"""The scoreboard check, attacked at the place it has silently stopped working three times.

`check_the_scoreboard` exists because the README's figures drift. It has itself gone quiet
three separate times, each in the same shape and none of them red:

  1. it verified three figures while twenty on the claim table drifted;
  2. it read the stack line by paragraph index, and a banner moved the line;
  3. `-qq` removed pytest's `N passed` line, so the test count was never extracted at all and
     the README's figure was compared against nothing for as long as the check had existed.

Each was fixed at its cause. None was fixed at the mechanism, which is that every figure reader
in that file did `if not found: continue` — correct when the harness did not run, and a silent
pass when the harness ran and its summary line moved.

These tests attack the mechanism. `gate-proof` cannot: mutating the scoreboard means running a
full `make preflight` per mutation, which is the whole suite plus checkov plus terraform, so the
harness that breaks every other gate on purpose cannot afford to break this one. So it is broken
here instead, with synthetic reports, and the requirement is Attestor's third rule — **a check
whose target has moved is stale, not passed.**
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

_spec = importlib.util.spec_from_file_location("preflight", ROOT / "scripts" / "preflight.py")
assert _spec and _spec.loader
preflight = importlib.util.module_from_spec(_spec)
sys.modules["preflight"] = preflight
_spec.loader.exec_module(preflight)


CLAIM_1 = "claim 1 · thresholds"


def _result(name: str, output: str, status: str = "pass"):
    check = preflight.Check("correctness", name, [], "for the test")
    return preflight.Result(check, status, 0.0, output)


CLAIM_1_LINE = "36 fields — 5 with a derived threshold, 31 always-review"
README_WITH_ROW = "| **claim 1** | 36 fields, 5 derived, 31 always-review |"


def test_a_claim_row_that_agrees_is_not_a_problem() -> None:
    report = preflight.Report([_result(CLAIM_1, CLAIM_1_LINE)])
    assert preflight._claim_table_disagreements(report, README_WITH_ROW) == []


def test_a_claim_row_that_disagrees_is_reported() -> None:
    report = preflight.Report([_result(CLAIM_1, CLAIM_1_LINE)])
    problems = preflight._claim_table_disagreements(
        report, "| **claim 1** | 36 fields, 4 derived, 32 always-review |"
    )
    assert problems, "a README row stating figures the harness did not produce must be reported"


def test_a_harness_that_renamed_its_summary_line_is_stale_not_passed() -> None:
    """The failure this file exists for: the harness ran, and the pattern found nothing.

    Before this was fixed the loop did `continue` here, so the README's claim-1 figures were
    unverified and the check reported green — indistinguishable, from the outside, from a run
    where they had been checked and agreed.
    """
    report = preflight.Report([_result(CLAIM_1, "everything is fine, thanks for asking")])
    problems = preflight._claim_table_disagreements(report, README_WITH_ROW)
    assert problems, "a summary line the check can no longer read must fail, not skip"
    assert "stale" in problems[0]


def test_a_harness_that_did_not_run_is_skipped() -> None:
    """The other half, and the reason `continue` was there in the first place.

    `--fast` and `--group` run a subset. A figure whose producer was not in the run is not
    stale — there is nothing to be stale about — and failing here would make every partial run
    red for having been partial.
    """
    assert preflight._claim_table_disagreements(preflight.Report([]), README_WITH_ROW) == []


def test_a_harness_that_failed_does_not_also_report_stale() -> None:
    """A failed producer prints whatever it printed on the way down.

    The run is already red under the producer's own name, which is where a reader should be
    looking. A second finding pointing at `preflight.py` would send them to the wrong file.
    """
    report = preflight.Report([_result(CLAIM_1, "Traceback ...", status="fail")])
    assert preflight._claim_table_disagreements(report, README_WITH_ROW) == []


ECE_LINE = "ECE 0.0815 on ours against 0.1102 on theirs"


def test_a_prose_figure_that_agrees_is_not_a_problem() -> None:
    report = preflight.Report([_result("the out-of-distribution column", ECE_LINE)])
    readme = "scores an ECE of 0.0815 here and 0.1102 on paper nobody here designed"
    assert preflight._prose_figure_disagreements(report, readme) == []


def test_a_prose_figure_the_readme_does_not_state_is_reported() -> None:
    report = preflight.Report([_result("the out-of-distribution column", ECE_LINE)])
    problems = preflight._prose_figure_disagreements(report, "an ECE of 0.0371, once upon a time")
    assert problems, "a prose figure that has drifted from its harness must be reported"


def test_a_prose_harness_whose_line_moved_is_stale_not_passed() -> None:
    report = preflight.Report([_result("the out-of-distribution column", "calibration: fine")])
    problems = preflight._prose_figure_disagreements(report, "an ECE of 0.0815")
    assert problems, "a prose pattern that matches nothing must fail, not skip"
    assert "stale" in problems[0]


def test_every_claim_figure_names_the_check_that_actually_prints_it() -> None:
    """The defect of 2026-08-19, and the reason these are keyed by exact name.

    This was `startswith(f"claim {n}")`. `claim 1 · the loop` sorts before `claim 1 · thresholds`
    in `CHECKS`, so claim 1's figures were read out of `evals.feedback` — a harness that does not
    print them — and the README's claim-1 row went unverified from the day the loop check was
    added. A prefix is how a check quietly acquires a second candidate.
    """
    names = {check.name for check in preflight.CHECKS}
    for claim, (produced_by, _) in preflight.CLAIM_FIGURES.items():
        assert produced_by in names, f"claim {claim} names a check that does not exist"
        assert sum(name.startswith(produced_by) for name in names) == 1, (
            f"{produced_by!r} is a prefix of another check name; match it exactly or the reader "
            f"can pick the wrong harness"
        )


def test_every_declared_prose_figure_names_a_check_that_exists() -> None:
    """A key nobody produces is a figure nobody verifies, and it looks exactly like one nobody
    needed to. The names in `PROSE_FIGURES` are check names, matched exactly, and a check
    renamed in `CHECKS` leaves the entry here pointing at nothing — permanently skipped, by the
    branch above that is *correct*."""
    names = {check.name for check in preflight.CHECKS}
    missing = sorted(set(preflight.PROSE_FIGURES) - names)
    assert not missing, f"PROSE_FIGURES names checks that do not exist: {missing}"


def test_the_test_count_extractor_still_reads_pytest() -> None:
    """The third silent failure, asserted directly.

    `-qq` removed the line this reads and nothing noticed. The extractor is now required to
    find a count in pytest's actual summary, so a future flag that suppresses it fails here
    rather than quietly uncoupling the README's test figure from the suite.
    """
    report = preflight.Report([_result("test suite", "309 passed, 2 skipped in 41.83s")])
    assert preflight._scoreboard_figures(report)["tests"] == "309 passing"


BADGE = "![](https://img.shields.io/badge/tests-{n}%20passing-2ea44f)"
SENTENCE = "the suite at **{m} passing**"


def test_a_badge_and_a_sentence_that_agree_are_one_number() -> None:
    readme = BADGE.format(n=512) + "\n" + SENTENCE.format(m=512)
    assert preflight._counts_stated(readme)["tests"] == {"512"}


def test_a_badge_and_a_sentence_that_disagree_are_two() -> None:
    """The failure of 2026-08-19: badge 502, sentence 512, same page, check green.

    The badges are shields.io URLs, so their figures are percent-encoded and matched none of the
    patterns written for prose. The first number a reader sees was the one number nothing
    checked.
    """
    readme = BADGE.format(n=502) + "\n" + SENTENCE.format(m=512)
    assert preflight._counts_stated(readme)["tests"] == {"502", "512"}


def test_the_gate_proof_badge_is_counted_too() -> None:
    readme = (
        "![](https://img.shields.io/badge/gate--proof-58%20planted%20%C2%B7%2058%20refused-2ea44f)"
        "\n`gate-proof` at **57 refused, 0 accepted, 0 stale**"
    )
    assert preflight._counts_stated(readme)["gate-proof mutations"] == {"57", "58"}


def test_the_claim_table_row_is_counted_too() -> None:
    """The fourth silent failure, 2026-08-20. Same fact, fourth shape, nothing matched it.

    `| The gates are attacked | 57 planted violations, each refused by name |` sat in the
    contents table while the badge twelve lines up said 61. `(\\d+) planted gate violations`
    was in the patterns; `(\\d+) planted violations` — the same sentence without the noun —
    was not.
    """
    readme = (
        "| [The gates are attacked](#x) | 57 planted violations, each refused by name |"
        "\n`gate-proof` at **61 refused, 0 accepted, 0 stale**"
    )
    assert preflight._counts_stated(readme)["gate-proof mutations"] == {"57", "61"}


def test_a_screenshots_alt_text_is_counted_too() -> None:
    """Alt text is the figure a reader who cannot see the image is given, and it drifted.

    It is also the only copy of the number that no sighted reader ever proof-reads, which is
    why it was 57 over a picture that read 56 for a day.
    """
    readme = (
        '<img src="images/gate_proof1.png" alt="gate-proof: 57 refused, 0 accepted, 0 stale">'
        "\n`gate-proof` at **61 refused, 0 accepted, 0 stale**"
    )
    assert preflight._counts_stated(readme)["gate-proof mutations"] == {"57", "61"}


def test_an_html_bolded_figure_is_counted_too() -> None:
    """`**57 refused**` was matched and `<b>57 refused</b>` was not.

    The README's captions are inside `<p align="center">` blocks, where markdown emphasis does
    not render — so every caption bolds with `<b>`, and every caption was therefore outside the
    one pattern written for bold.
    """
    readme = (
        "<sub><b>57 refused, 0 accepted, 0 stale</b> — the whole run</sub>"
        "\n`gate-proof` at **61 refused, 0 accepted, 0 stale**"
    )
    assert preflight._counts_stated(readme)["gate-proof mutations"] == {"57", "61"}


def test_this_repositorys_own_readme_states_one_number_per_fact() -> None:
    """The regression the three tests above are abstractions of.

    Everything else in this file uses synthetic READMEs, deliberately — a test that reads the
    real page goes red for reasons that have nothing to do with the mechanism. This one is the
    exception on purpose: the failure being guarded against is *this page disagreeing with
    itself*, and there is no synthetic stand-in for that.
    """
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for fact, stated in preflight._counts_stated(readme).items():
        assert len(stated) == 1, f"README states {sorted(stated)} for {fact}; it is one fact"

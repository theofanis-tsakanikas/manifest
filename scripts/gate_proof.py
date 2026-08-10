#!/usr/bin/env python3
"""Break every gate on purpose, and require the real gate to refuse it.

A test suite tells you the code does what it does. It does not tell you the *gates* still
bite, because a gate that has quietly stopped checking anything passes every test it has.
This script plants a genuine violation and demands a refusal.

Three rules keep it a proof rather than a ritual — the same three as Attestor and Watermark,
because they were each learned by getting one of them wrong:

**Green first.** Every mutation runs against a repository that currently passes. A refusal
from an already-broken baseline proves nothing.

**A non-zero exit is not evidence.** The *named* check must be the thing that failed, with a
message that names the violation. A mutation that happens to cause an unrelated crash is
reported as a failure of the proof, not as a pass — otherwise the day a gate is deleted, its
mutation still "passes" because the import now fails.

**A mutation whose target has moved is STALE.** If the code a mutation edits no longer looks
the way it expects, the mutation is not silently skipped and is not counted as a pass. It is
reported, and the run is red, because a proof that quietly stopped running is worse than one
that never existed.

Every mutation here is a mistake somebody could plausibly make. An absurd one proves the gate
refuses absurdity, which nobody doubted.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Mutation:
    name: str
    gate: str
    #: The command that must fail, run inside the mutated copy.
    command: list[str]
    #: A phrase the failure must contain. Its presence is what makes the refusal *the* refusal
    #: rather than any old error.
    expect: str
    apply: Callable[[Path], bool]
    #: Why this mutation is worth planting.
    rationale: str


def _replace(path: Path, old: str, new: str) -> bool:
    """Edit a file, returning False if the target text is not there any more."""
    text = path.read_text(encoding="utf-8")
    if old not in text:
        return False
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    return True


# ── The mutations ────────────────────────────────────────────────────────────


def _reach_for_the_cloud_from_the_core(root: Path) -> bool:
    """Fetch a page from object storage inside the core.

    The plausible version is not a rewrite, it is a convenience: a function that took a page
    now takes a key, because the caller had a key. Nothing fails on the machine that wrote it
    — and every claim in this repository silently becomes a claim about a machine with
    credentials.
    """
    path = root / "src/manifest/core/geometry.py"
    text = path.read_text(encoding="utf-8")
    return _replace(path, "import math\n", "import math\n\nimport boto3\n") and bool(text)


def _let_the_core_learn_which_engine_read_the_page(root: Path) -> bool:
    """One comment naming an engine.

    Not an import, not a branch — a comment, which is how it actually starts. It is left in
    while somebody works out whether the quirk is real, and by the time the branch appears
    underneath it, nobody remembers the comment was the first step.
    """
    return _replace(
        root / "src/manifest/core/geometry.py",
        "**Rounding out, never in.**",
        "**Rounding out, never in.** Textract reports fractions already, so this is a no-op there.",
    )


def _read_the_clock_inside_the_core(root: Path) -> bool:
    """Stamp a box with the time it was constructed.

    Reads as observability. It is claim 3: the same document re-extracted at the same engine
    version now produces a different record, and the diff report shows a change nobody made.
    """
    return _replace(
        root / "src/manifest/core/geometry.py",
        "    def __post_init__(self) -> None:\n",
        "    def __post_init__(self) -> None:\n"
        "        import datetime\n\n"
        "        _seen_at = datetime.datetime.now()  # noqa: DTZ005\n",
    )


def _let_an_engine_name_in_as_long_as_it_is_not_an_import(root: Path) -> bool:
    """Scan only the syntax tree, not the source text.

    The plausible fix for a false positive: "we only care about actual imports". It reads as
    precision and it removes the only part of the rule that catches a string literal, a
    dictionary key or a branch on an engine name — which is every way the name really gets in.
    """
    return _replace(
        root / "src/manifest/gates/core_purity.py",
        "    yield from _engine_findings(source, relative)\n",
        "",
    )


def _round_a_crop_to_the_nearest_pixel(root: Path) -> bool:
    """Round to nearest instead of outward.

    The most defensible-looking change in this file, and it is the one that makes claim 2 lie
    in the dangerous direction: a crop half a pixel short can cut the stroke off a digit, the
    verifier re-reads a damaged crop, and the gate reports that a *correct* record was false.
    A provenance check that manufactures failures gets muted within a week.
    """
    return _replace(
        root / "src/manifest/core/geometry.py",
        "        left = math.floor(self.left * page.width)\n"
        "        top = math.floor(self.top * page.height)\n"
        "        right = max(left + 1, math.ceil(self.right * page.width))\n"
        "        bottom = max(top + 1, math.ceil(self.bottom * page.height))\n",
        "        left = round(self.left * page.width)\n"
        "        top = round(self.top * page.height)\n"
        "        right = max(left + 1, round(self.right * page.width))\n"
        "        bottom = max(top + 1, round(self.bottom * page.height))\n",
    )


def _let_two_boxes_that_only_touch_overlap(root: Path) -> bool:
    """Compare the overlap against zero rather than against the tolerance.

    This is not hypothetical: it is what the first version of the module did, and the test
    caught it. `0.1 + 0.2` is not `0.3`, so two boxes meeting at an edge produce an overlap of
    the order of 1e-17 and `iou` reports agreement between a recorded box and the word beside
    it. The gate has to keep refusing the version that was already written once.
    """
    return _replace(
        root / "src/manifest/core/geometry.py",
        "        if right - left <= _EPSILON or bottom - top <= _EPSILON:\n",
        "        if right <= left or bottom <= top:\n",
    )


def _clamp_a_coordinate_that_left_the_page(root: Path) -> bool:
    """Clamp a coordinate that left the page instead of refusing it.

    Reads as robustness — "be liberal in what you accept". What it accepts is an adapter with
    a sign error or an unsubtracted offset, and the result is an archive of provenance records
    every one of which points at the top-left corner of its page.

    This mutation earned its place the hard way: on first run it was **accepted**, because
    every test written until then happened to be satisfied by a different raise further down.
    The gap was in the suite, not in the gate, and nothing but planting the violation would
    have shown it.
    """
    return _replace(
        root / "src/manifest/core/geometry.py",
        "    if -_EPSILON <= value <= 1 + _EPSILON:\n        return min(max(value, 0.0), 1.0)\n",
        "    return min(max(value, 0.0), 1.0)\n    if False:\n        pass\n",
    )


def _let_a_blank_crop_verify(root: Path) -> bool:
    """Drop the ink floor to zero.

    The plausible version is not a deletion, it is a tuning: somebody sees the gate refuse a
    faint but correct record, lowers the floor to make it pass, and lowers it to zero because
    that is where the last complaint stops. What is left is a check that measures ink and never
    refuses on it, so a box pointing at the margin verifies — and claim 2 keeps reporting green
    over records that point at nothing.
    """
    return _replace(
        root / "src/manifest/gates/provenance.py",
        "INK_FLOOR: Final = 0.02",
        "INK_FLOOR: Final = 0.0",
    )


def _let_a_shorter_reread_verify_a_longer_record(root: Path) -> bool:
    """Make containment work in both directions.

    Reads as symmetry, and it accepts `89` as verification of `8959`. A padded crop legitimately
    shows *more* than the record; a crop that shows less is a record claiming a value the page
    does not carry, and this is the direction where the difference is money.
    """
    return _replace(
        root / "src/manifest/gates/provenance.py",
        "    return bool(published) and published in read",
        "    return bool(published) and (published in read or read in published)",
    )


def _stop_re_reading_the_crop(root: Path) -> bool:
    """Trust the ink and skip the second recognition path.

    The expensive layer, and the first one somebody removes on a busy afternoon: it costs a
    reader pass per published field. Layer A still runs, so a box on the margin is still
    refused and most of claim 2 keeps its shape — while the only layer that can tell ink from
    *the right* ink stops running.
    """
    path = root / "src/manifest/gates/provenance.py"
    text = path.read_text(encoding="utf-8")
    marker = "    if not agreement.agree and not _is_contained("
    if marker not in text:
        return False
    start = text.index(marker)
    end = text.index("):", start) + len("):")
    path.write_text(text[:start] + "    if False:" + text[end:], encoding="utf-8")
    return True


def _report_an_unreadable_page_as_verified(root: Path) -> bool:
    """Treat a page nothing could open as a page that checked out.

    Attestor's laundering, in this project's costume: "we could not check" becomes "it was
    fine". A field whose provenance nothing has looked at has not been verified, and a batch
    that lost its rasters would publish everything.
    """
    path = root / "src/manifest/gates/provenance.py"
    text = path.read_text(encoding="utf-8")
    marker = "verdict=Verdict.UNCHECKABLE"
    if text.count(marker) < 1:
        return False
    path.write_text(text.replace(marker, "verdict=Verdict.VERIFIED"), encoding="utf-8")
    return True


def _let_a_container_number_skip_its_own_arithmetic(root: Path) -> bool:
    """Stop checking a self-checking field.

    Free and absolute, so removing it looks like removing redundancy. What it removes is the
    only check on any of these documents that can prove a read wrong without a second opinion.
    """
    return _replace(
        root / "src/manifest/gates/provenance.py",
        "    if provenance.self_checking:",
        "    if False:",
    )


def _remove_a_layer_from_the_teardown(root: Path) -> bool:
    """Delete the lakehouse teardown and leave its deploy job in place.

    The asymmetry is acquired one layer at a time, and never on purpose: somebody adds a layer
    to the deploy workflow, and the destroy file is the one they do not have open. What is left
    is a repository that reads as complete and an estate that cannot be fully torn down.
    """
    path = root / ".github/workflows/destroy.yml"
    text = path.read_text(encoding="utf-8")
    start = text.find("  lakehouse:\n")
    if start < 0:
        return False
    end = text.find("\n  extraction:", start)
    if end < 0:
        return False
    path.write_text(text[:start] + text[end + 1 :], encoding="utf-8")
    return True


def _let_the_teardown_stop_at_the_first_failure(root: Path) -> bool:
    """Drop `always()` from a teardown job.

    Reads as tidier, and it is the failure the whole file exists to prevent: the destroy stops
    where it broke, reports a failure nobody reads to the end of, and leaves the expensive half
    running.
    """
    return _replace(
        root / ".github/workflows/destroy.yml",
        "    needs: lakehouse\n    if: always() && !cancelled()",
        "    needs: lakehouse",
    )


def _let_a_push_trigger_the_deploy(root: Path) -> bool:
    """Add a push trigger to the deploy workflow.

    The most plausible mistake in this repository: somebody wants main to deploy itself. It
    turns a gated, human-dispatched, confirmed apply into something that happens because a pull
    request merged.
    """
    return _replace(
        root / ".github/workflows/deploy.yml",
        "on:\n  workflow_dispatch:",
        "on:\n  push:\n    branches: [main]\n  workflow_dispatch:",
    )


def _widen_an_injection_rule_past_its_object(root: Path) -> bool:
    """Let an override rule fire on anything "previous" rather than on an instruction.

    Framed as thoroughness every time: the narrow rule "misses" a document that says `ignore the
    previous`, so somebody drops the noun class. What it then catches is `Please disregard the
    previous packing list` — an amendment note, on real paperwork, every day.

    This mutation replaced an earlier one that pointed at the *imperative* anchor and was
    reported **accepted**, because removing that anchor changed nothing the suite could see. The
    harness was right and the comment beside the rule was wrong: the imperative was not what
    separated an attack from ordinary prose. The object was.
    """
    return _replace(
        root / "src/manifest/security/injection.py",
        'r"preceding\\s+)?" + _INSTRUCTION_NOUN,',
        'r"preceding\\s+)?(?:\\w+)\\b",',
    )


def _escape_the_envelope_instead_of_refusing_it(root: Path) -> bool:
    """Strip the delimiter out of hostile text rather than refusing the document.

    Reads as robustness and is the weaker of the two designs by a wide margin: escaping is a
    transformation that has to be right every time, refusing is a property that cannot be got
    wrong. There is no legitimate document containing this string.
    """
    return _replace(
        root / "src/manifest/security/injection.py",
        "    if delimiter in text:\n        raise EnvelopeError(",
        "    if False:\n        raise EnvelopeError(",
    )


def _drop_a_mangled_character_instead_of_refusing_the_number(root: Path) -> bool:
    """Filter a money value down to digits and separators, discarding the rest.

    This is not hypothetical — it is what the first version of `_amount` did, and it turned
    `75.812;15` into `75.81215` and then into **75.8**, so an invoice worth seventy-five
    thousand read as one worth seventy-five. The line-total check then reported a confident
    answer in the wrong direction, which is the worst shape a number can fail in.
    """
    # The *filter*, not the guard. Removing the guard alone changes nothing observable —
    # `core.quantity.parse` still refuses a stray character downstream, and the first version
    # of this mutation was ACCEPTED for exactly that reason. What produced the real defect was
    # discarding the character before parsing ever saw it.
    return _replace(
        root / "src/manifest/core/lineitems.py",
        "    stripped = text.strip()",
        '    text = "".join(c for c in text if c.isdigit() or c in ".,")\n'
        "    stripped = text.strip()",
    )


def _require_a_header_on_every_page(root: Path) -> bool:
    """Stop reading a table once the header is behind you.

    The naive implementation, and the one the corpus exists to punish: a table continuing on the
    next page prints no header, so a reader that needs one stops at the break and reports a
    complete table that is short by every row after it — with a printed total that still looks
    plausible.
    """
    return _replace(
        root / "src/manifest/core/lineitems.py",
        "            # A continuation page. From the very top, because the table resumes without\n"
        "            # announcing itself.\n"
        "            below = 0.0",
        "            continue",
    )


def _transcribe_a_value_bootstrap_already_published(root: Path) -> bool:
    """Put the state bucket back into a repository variable.

    The shape the deploy actually had, and it is always framed as simpler: a value in a settings
    page is one fewer moving part than a parameter store. What it costs is that renaming the
    state bucket in `infra/bootstrap` produces a deploy that fails on a backend nobody can find,
    with the fix in a settings page rather than in a diff — and nothing in the repository can
    see the mismatch.
    """
    return _replace(
        root / ".github/workflows/deploy.yml",
        '-backend-config="bucket=$TF_STATE_BUCKET"',
        '-backend-config="bucket=${{ vars.STATE_BUCKET }}"',
    )


def _write_the_grants_and_never_attach_them(root: Path) -> bool:
    """Leave every permission in the file and none of them on the role.

    The purest form of "written but not wired": every grant visible in a diff, a reviewer reads
    them, and the role has none of them. The first version of the gate read the policy document
    and stopped there, so this mutation was **accepted** — which is how the gate learned to
    check the attachment rather than the text.
    """
    return _replace(
        root / "infra/bootstrap/deploy_permissions.tf",
        'resource "aws_iam_role_policy" "deploy_estate" {',
        'resource "aws_iam_role_policy" "deploy_estate_unattached" {\n  count = 0\n',
    )


def _take_away_the_deploy_roles_permission_to_build_the_network(root: Path) -> bool:
    """Drop the EC2 grants and leave the rest.

    **This was the repository's actual state**, generalised: six Terraform layers written and a
    role that could read and write state and create nothing. `terraform validate` knows nothing
    about IAM and checkov scans what a policy *grants* rather than what an apply *needs*, so
    nothing caught it — the deploy would have failed on `ec2:CreateVpc` four minutes in, with
    the environment approval already spent.
    """
    path = root / "infra/bootstrap/deploy_permissions.tf"
    text = path.read_text(encoding="utf-8")
    if '"ec2:' not in text:
        return False
    # Every one of them. Removing a single action leaves the others and the gate — which checks
    # that a grant for the service exists at all — correctly reports nothing, because a role
    # with most of its EC2 actions is a role the gate cannot judge offline. What it can judge
    # is a service with no grant whatsoever, which is what this plants.
    path.write_text(text.replace('"ec2:', '"ec2NOTHING:'), encoding="utf-8")
    return True


def _let_a_layer_run_without_the_variables_it_requires(root: Path) -> bool:
    """Stop supplying `expires_at` to the batch teardown.

    **This was the repository's actual state**, on three of the five layers in `destroy.yml` at
    once. Each declared a variable with no default, nothing in the workflow supplied it, and
    three separate gates reported green: `terraform validate` never asks for a variable value,
    `checkov` reads resources rather than runs, and `tf_validate.py` calls both.

    So the repository claimed a teardown path that would have halted at an input prompt with no
    terminal to appear on — the estate standing, the destroy approval already given, and the
    only remaining route a human with credentials at a laptop. A destroy that cannot run is
    worse than no destroy, because the repository says there is one.
    """
    path = root / ".github/workflows/destroy.yml"
    text = path.read_text(encoding="utf-8")
    destroy = "terraform -chdir=infra/batch destroy -auto-approve -refresh=false"
    marker = f"{destroy} -var 'expires_at=1970-01-01'"
    if marker not in text:
        return False
    path.write_text(text.replace(marker, destroy), encoding="utf-8")
    return True


def _stop_publishing_what_the_next_layer_reads(root: Path) -> bool:
    """Remove `data_key_arn` from what foundation publishes.

    The lakehouse requires it and has no default for it, and it arrives by name: the workflow
    resolves `/manifest/foundation/*` in bulk and turns each parameter into the `TF_VAR_` of the
    same name. That is a good mechanism and a quiet one — nothing in the YAML mentions the
    variable, so dropping the publication looks like a tidy-up of an unused output.
    """
    path = root / "infra/foundation/published.tf"
    text = path.read_text(encoding="utf-8")
    if "data_key_arn " not in text:
        return False
    lines = [
        line
        for line in text.splitlines(keepends=True)
        if not line.strip().startswith("data_key_arn ")
    ]
    path.write_text("".join(lines), encoding="utf-8")
    return True


def _let_a_failed_resolve_pass_for_an_empty_value(root: Path) -> bool:
    """Fold the parameter read back inside its `echo`.

    **This was the repository's actual state** in every job of both workflows. It reads as the
    tidier line — one command instead of two — and it is the difference between a job that stops
    on a missing parameter and one that carries an empty string into a backend configuration.
    `set -e` cannot see it: the command it judges is the `echo`, and the `echo` worked.
    """
    return _replace(
        root / ".github/workflows/deploy.yml",
        "          TF_STATE_BUCKET=$(aws ssm get-parameter",
        '          echo "TF_STATE_BUCKET=$(aws ssm get-parameter',
    )


def _name_a_function_that_nothing_creates(root: Path) -> bool:
    """Replace the gate's resource reference with the string it used to be.

    **This was the repository's actual state.** The state machine invoked
    `"${var.project}-provenance-gate"` and no layer created it: `terraform validate`, `checkov`
    and `tf_validate.py` all reported green on a pipeline whose central control did not exist.
    The step's `Catch` then turned the resulting `ResourceNotFoundException` into a trip to the
    review queue, so the failure mode was 100% of documents going to a human while every
    dashboard stayed green.
    """
    return _replace(
        root / "infra/extraction/pipeline.tf",
        '"FunctionName" : aws_lambda_function.provenance_gate.arn,',
        '"FunctionName" : "${var.project}-provenance-gate",',
    )


def _write_a_variable_on_one_line(root: Path) -> bool:
    """Collapse a required variable's declaration onto one line.

    Not a behaviour change at all — `variable "x" { type = string }` and the three-line form are
    identical to Terraform. It is a change to how the *gate* sees it, and the gate used to see
    nothing: its pattern required a closing brace at the start of a line, so every single-line
    declaration was invisible.

    **This was the repository's actual state on 27 declarations across four layers**, and they
    were the cross-layer references — precisely the variables most likely to go unsupplied. The
    check that exists to prove a layer can evaluate was skipping the ones that mattered, and
    reporting green.
    """
    path = root / "infra/batch/variables.tf"
    text = path.read_text(encoding="utf-8")
    marker = 'variable "expires_at" {'
    if marker not in text:
        return False
    # Give the layer a *new* required variable in the collapsed form. If the gate still parses
    # single-line blocks it reports it; if the regex came back, it sees nothing.
    path.write_text(
        text + '\nvariable "unsupplied_by_anything" { type = string }\n', encoding="utf-8"
    )
    return True


def _stop_deploying_the_threshold_artefact(root: Path) -> bool:
    """Remove the step that uploads the thresholds the handler reads.

    **This was the repository's actual state.** The extraction handler read a threshold artefact
    keyed by reader identity and nothing anywhere created one, so the first document after a
    deploy would have failed on `NoSuchKey`, been caught by the state machine, and gone to
    review. Every offline check passed, because every offline check reads
    `recordings/thresholds.json` directly and never asks how it reaches the estate.

    The mutation is a deletion of one workflow step, which is also how it would happen: an
    upload step reads like build scaffolding next to a `terraform apply`.
    """
    path = root / ".github/workflows/deploy.yml"
    text = path.read_text(encoding="utf-8")
    if "scripts/thresholds_artefact.py" not in text:
        return False
    kept = [line for line in text.splitlines(keepends=True) if "thresholds_artefact.py" not in line]
    path.write_text("".join(kept), encoding="utf-8")
    return True


def _stop_starting_the_pipeline(root: Path) -> bool:
    """Delete the rule that starts an execution.

    **Also the repository's actual state.** The machine, the queue, the tables and the buckets
    all existed and a document landing in the landing zone caused nothing whatever. It is the
    hardest of these to see, because there is no error anywhere: the estate is up, everything
    reports healthy, and the documents simply sit in the bucket.
    """
    path = root / "infra/extraction/trigger.tf"
    text = path.read_text(encoding="utf-8")
    if "aws_cloudwatch_event_target" not in text:
        return False
    path.write_text(
        text.replace("states:StartExecution", "states:DescribeExecution"), encoding="utf-8"
    )
    return True


def _stop_uploading_the_pages_the_gate_re_reads(root: Path) -> bool:
    """Let the reader rasterise to a temporary directory and let it go.

    **This was the repository's actual state, and it was found last.** The provenance gate
    re-opens the page to check that the recorded box carries ink and that the crop re-reads to
    the published value — claim 2's whole argument — and the pages existed only inside a
    `TemporaryDirectory` in a process that had already exited.

    Nothing would have errored. The gate would have found no page, reported every field
    *uncheckable* — which is a refusal, correctly — and the pipeline would have queued 100% of
    its volume while reporting success. The sixth instance of one shape: something reads an
    artefact nothing writes.
    """
    return _replace(
        root / "src/manifest/handlers/read_tier0.py",
        '                Key=f"renders/{request.document_id}/page-{number:04d}.png",',
        '                Key=f"scratch/{request.document_id}/page-{number:04d}.png",',
    )


MUTATIONS: tuple[Mutation, ...] = (
    Mutation(
        "reach for the cloud from inside the core",
        "core purity",
        [sys.executable, "scripts/check_core_is_pure.py"],
        "boto3",
        _reach_for_the_cloud_from_the_core,
        "Nothing fails on the machine that wrote it; the suite just stops being runnable by "
        "a stranger, and that was the whole of the evidence.",
    ),
    Mutation(
        "let the core learn which engine read the page",
        "core purity",
        [sys.executable, "scripts/check_core_is_pure.py"],
        "engine name",
        _let_the_core_learn_which_engine_read_the_page,
        "A comment, which is how it starts. The branch arrives later and nobody remembers "
        "the comment was the first step.",
    ),
    Mutation(
        "read the clock inside the core",
        "core purity",
        [sys.executable, "scripts/check_core_is_pure.py"],
        "ambient state",
        _read_the_clock_inside_the_core,
        "Reads as observability. It is claim 3: the same document, the same engine version, "
        "a different record.",
    ),
    Mutation(
        "check only imports for an engine name, not the source text",
        "core purity",
        ["pytest", "-q", "tests/gates/test_core_purity.py", "-x"],
        "test_an_engine_named_anywhere_is_refused",
        _let_an_engine_name_in_as_long_as_it_is_not_an_import,
        "The plausible fix for a false positive. It removes the half of the rule that catches "
        "a string literal, a dictionary key and a branch.",
    ),
    Mutation(
        "round a crop to the nearest pixel",
        "crop geometry",
        ["pytest", "-q", "tests/core/test_geometry.py", "-x"],
        "test_a_crop_is_never_smaller_than_the_box_it_came_from",
        _round_a_crop_to_the_nearest_pixel,
        "Makes claim 2 fail in the dangerous direction: the verifier re-reads a damaged crop "
        "and reports a correct record as false.",
    ),
    Mutation(
        "let two boxes that only touch overlap",
        "crop geometry",
        ["pytest", "-q", "tests/core/test_geometry.py", "-x"],
        "test_boxes_that_only_touch_do_not_intersect",
        _let_two_boxes_that_only_touch_overlap,
        "Exactly what the first version of the module did. The gate has to keep refusing the "
        "version that was already written once.",
    ),
    Mutation(
        "clamp a coordinate that left the page instead of refusing it",
        "crop geometry",
        ["pytest", "-q", "tests/core/test_geometry.py", "-x"],
        "test_a_coordinate_far_outside_the_page_is_refused_rather_than_clamped",
        _clamp_a_coordinate_that_left_the_page,
        "Reads as robustness. What it accepts is an adapter with a sign error, and an archive "
        "of provenance records all pointing at the top-left corner. Accepted on first run — "
        "the gap was in the suite, and only planting the violation showed it.",
    ),
    Mutation(
        "let a blank crop verify",
        "provenance · ink",
        ["pytest", "-q", "tests/gates/test_provenance.py", "-x"],
        "test_a_blank_crop_is_refused_by_the_ink_layer",
        _let_a_blank_crop_verify,
        "Not a deletion — a tuning. Lowered once to stop a complaint, and again, until it "
        "measures ink and never refuses on it.",
    ),
    Mutation(
        "let a shorter re-read verify a longer record",
        "provenance · containment",
        ["pytest", "-q", "tests/gates/test_provenance.py", "-x"],
        "test_a_reread_carrying_less_than_the_record_is_refused",
        _let_a_shorter_reread_verify_a_longer_record,
        "Reads as symmetry. Accepts `89` as verification of `8959`, which is the direction "
        "where the difference is money.",
    ),
    Mutation(
        "stop re-reading the crop",
        "provenance · second path",
        ["pytest", "-q", "tests/gates/test_provenance.py", "-x"],
        "test_ink_present_but_the_wrong_ink_is_refused_by_the_reread_layer",
        _stop_re_reading_the_crop,
        "The expensive layer. Layer A still runs, so most of claim 2 keeps its shape while the "
        "only layer that can tell ink from the *right* ink stops running.",
    ),
    Mutation(
        "report an unreadable page as verified",
        "provenance · uncheckable",
        ["pytest", "-q", "tests/gates/test_provenance.py", "-x"],
        "test_a_page_that_cannot_be_read_is_uncheckable_rather_than_verified",
        _report_an_unreadable_page_as_verified,
        "'We could not check' becoming 'it was fine'. A batch that lost its rasters would "
        "publish everything.",
    ),
    Mutation(
        "let a container number skip its own arithmetic",
        "provenance · arithmetic",
        ["pytest", "-q", "tests/gates/test_provenance.py", "-x"],
        "test_a_self_checking_field_is_refused_by_its_own_arithmetic_first",
        _let_a_container_number_skip_its_own_arithmetic,
        "Looks like removing redundancy. Removes the only check on these documents that can "
        "prove a read wrong without a second opinion.",
    ),
    Mutation(
        "remove a layer from the teardown",
        "deploy path",
        [sys.executable, "scripts/check_deploy_path.py"],
        "left standing",
        _remove_a_layer_from_the_teardown,
        "Acquired one layer at a time and never on purpose. The repository reads as complete "
        "and the estate cannot be fully torn down.",
    ),
    Mutation(
        "let the teardown stop at the first failure",
        "deploy path",
        [sys.executable, "scripts/check_deploy_path.py"],
        "expensive half",
        _let_the_teardown_stop_at_the_first_failure,
        "Reads as tidier. The destroy stops where it broke and leaves the expensive half running.",
    ),
    Mutation(
        "let a push trigger the deploy",
        "deploy path",
        [sys.executable, "scripts/check_deploy_path.py"],
        "workflow_dispatch",
        _let_a_push_trigger_the_deploy,
        "The most plausible mistake here: somebody wants main to deploy itself. An apply that "
        "happens because a pull request merged.",
    ),
    Mutation(
        "widen an injection rule past its object",
        "injection false positives",
        # The unit suite, not the corpus eval. The corpus's free-text fields carry the planted
        # attempts and nothing else, so an unanchored rule scores identically on it — the
        # sentences that separate a rule from a keyword ("per the shipper's instructions", "any
        # override of the standard tariff") are in `tests/security`, which is where the
        # false-positive property actually lives. The first version of this mutation pointed at
        # the eval and was ACCEPTED, which is the harness saying the eval cannot see this.
        ["pytest", "-q", "tests/security", "-x"],
        "test_ordinary_trade_prose_is_not_flagged",
        _widen_an_injection_rule_past_its_object,
        "Framed as thoroughness. Catches one more attack and every amendment note that says "
        "'disregard the previous packing list'. Replaced an earlier mutation the harness "
        "reported as accepted, which is how the rule's real anchor was found.",
    ),
    Mutation(
        "escape the envelope delimiter instead of refusing it",
        "injection envelope",
        ["pytest", "-q", "tests/security", "-x"],
        "test_a_document_containing_the_delimiter_is_refused_not_escaped",
        _escape_the_envelope_instead_of_refusing_it,
        "Escaping is a transformation that has to be right; refusing is a property that cannot "
        "be got wrong.",
    ),
    Mutation(
        "drop a mangled character instead of refusing the number",
        "line-value arithmetic",
        ["pytest", "-q", "tests/core/test_lineitems.py", "-x"],
        "test_a_number_with_a_mangled_separator_is_refused",
        _drop_a_mangled_character_instead_of_refusing_the_number,
        "What the first version did: 75.812;15 became 75.8, and an invoice worth seventy-five "
        "thousand read as seventy-five.",
    ),
    Mutation(
        "require a table header on every page",
        "table continuation",
        ["pytest", "-q", "tests/core/test_lineitems.py", "-x"],
        "test_a_table_is_followed_past_a_page_break_with_no_repeated_header",
        _require_a_header_on_every_page,
        "The naive implementation. Reports a complete table that is short by every row after "
        "the break, with a printed total that still looks plausible.",
    ),
    Mutation(
        "transcribe a value bootstrap already published",
        "deploy resolution",
        [sys.executable, "scripts/check_deploy_path.py"],
        "transcribed value looks like an independent setting",
        _transcribe_a_value_bootstrap_already_published,
        "Always framed as simpler: one fewer moving part. What it costs is a rename that "
        "breaks the deploy with the fix in a settings page rather than a diff.",
    ),
    Mutation(
        "write the grants and never attach them",
        "deploy permissions",
        [sys.executable, "scripts/check_deploy_path.py"],
        "grants nothing",
        _write_the_grants_and_never_attach_them,
        "Every permission visible in the diff, none of them in effect. Accepted on first run, "
        "which is how the gate learned to check the attachment rather than the text.",
    ),
    Mutation(
        "take away the deploy role's permission to build the network",
        "deploy permissions",
        [sys.executable, "scripts/check_deploy_path.py"],
        "no ec2: grant",
        _take_away_the_deploy_roles_permission_to_build_the_network,
        "The repository's actual state until this gate was written, generalised: six layers "
        "and a role that could create nothing. Fails four minutes in, approval already spent.",
    ),
    Mutation(
        "let a teardown run without the variables it requires",
        "deploy evaluability",
        [sys.executable, "scripts/check_deploy_path.py"],
        "requires the variable `expires_at`",
        _let_a_layer_run_without_the_variables_it_requires,
        "The repository's actual state on three layers at once. validate, checkov and "
        "tf_validate all reported green on a teardown that would halt at a prompt.",
    ),
    Mutation(
        "stop publishing a reference the next layer resolves by name",
        "deploy evaluability",
        [sys.executable, "scripts/check_deploy_path.py"],
        "requires the variable `data_key_arn`",
        _stop_publishing_what_the_next_layer_reads,
        "Bulk resolution by name is quiet: no YAML line mentions the variable, so removing "
        "the publication reads as deleting an unused output.",
    ),
    Mutation(
        "let a failed parameter read pass for an empty value",
        "deploy resolution",
        [sys.executable, "scripts/check_deploy_path.py"],
        "reads a parameter inside an `echo`",
        _let_a_failed_resolve_pass_for_an_empty_value,
        "The repository's actual state in every job of both workflows. The tidier-looking "
        "line is the one `set -e` cannot see fail.",
    ),
    Mutation(
        "invoke a function by name instead of by reference",
        "deploy wiring",
        [sys.executable, "scripts/check_deploy_path.py"],
        "names a target as a literal",
        _name_a_function_that_nothing_creates,
        "The repository's actual state: the provenance gate was invoked by a name no layer "
        "created, and the step's Catch turned that into every document going to a human.",
    ),
    Mutation(
        "declare a required variable on one line",
        "deploy evaluability",
        [sys.executable, "scripts/check_deploy_path.py"],
        "requires the variable `unsupplied_by_anything`",
        _write_a_variable_on_one_line,
        "The repository's actual state on 27 declarations across four layers: the gate's "
        "pattern needed a closing brace at line start, so the cross-layer references — the "
        "ones most likely to be missing — were the ones it never saw.",
    ),
    Mutation(
        "stop deploying the artefact the handler reads",
        "deploy artefacts",
        [sys.executable, "scripts/check_deploy_path.py"],
        "reads objects under `thresholds/` and nothing writes them",
        _stop_deploying_the_threshold_artefact,
        "The repository's actual state: the handler read a threshold artefact nothing wrote, "
        "and every offline check passed because each reads the committed file directly.",
    ),
    Mutation(
        "take away the trigger's permission to start anything",
        "trigger wiring",
        [sys.executable, "scripts/check_deploy_path.py"],
        "nothing in this layer grants `states:StartExecution`",
        _stop_starting_the_pipeline,
        "Related to the repository's actual state, in which nothing started the pipeline at "
        "all: no error anywhere, every resource healthy, and the documents simply sitting.",
    ),
    Mutation(
        "stop uploading the pages the provenance gate re-reads",
        "deploy artefacts",
        [sys.executable, "scripts/check_deploy_path.py"],
        "reads objects under `renders/` and nothing writes them",
        _stop_uploading_the_pages_the_gate_re_reads,
        "The repository's actual state, found last: the gate re-opened pages that lived only "
        "in a temporary directory, so every field would have been uncheckable and every "
        "document queued — while the pipeline reported success.",
    ),
)


# ── Running them ─────────────────────────────────────────────────────────────


def _argv(command: list[str]) -> list[str]:
    """The full argv for a mutation's check, run out of the mutated copy.

    `python -m pytest` rather than `pytest`, so the copy is the code under test rather than
    whatever is on PATH. A bare script path is run as a script; anything else is passed
    through untouched.
    """
    if command[0] == "pytest":
        return [sys.executable, "-m", "pytest", *command[1:]]
    return list(command)


def _run(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    # PYTHONPATH points at the *copy*, not at the editable install. Without this the mutation
    # is planted in a directory nothing imports from, every gate passes, and the script
    # reports a perfect score while proving nothing at all.
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(cwd / "src")
    return subprocess.run(  # noqa: S603 — fixed command lists, no shell
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
        env=environment,
    )


def main() -> int:
    print("gate-proof: establishing the baseline")
    # `tests/` only, and deliberately not the slow eval harnesses: the baseline exists to prove
    # the repository is green before a violation is planted, and a baseline that took four
    # minutes would be a gate-proof nobody ran while changing a gate.
    baseline = _run([sys.executable, "-m", "pytest", "-q"], ROOT)
    if baseline.returncode != 0:
        print("the suite is not green; every mutation below would be meaningless", file=sys.stderr)
        print(baseline.stdout[-4000:], file=sys.stderr)
        return 1
    print("  baseline green\n")

    passes: list[str] = []
    failures: list[str] = []
    stale: list[str] = []

    for mutation in MUTATIONS:
        with tempfile.TemporaryDirectory() as raw:
            copy = Path(raw) / "manifest"
            shutil.copytree(
                ROOT,
                copy,
                ignore=shutil.ignore_patterns(
                    ".venv",
                    ".venv-checkov",
                    ".git",
                    "__pycache__",
                    ".pytest_cache",
                    ".ruff_cache",
                    "out",
                    # The rendered corpus is gigabytes and no mutation touches it. Copying it
                    # per mutation would make this take an hour, and a proof nobody has time
                    # to run is a proof that stops being run.
                    "rendered",
                ),
            )
            try:
                applied = mutation.apply(copy)
            except (ValueError, OSError) as exc:
                applied = False
                print(f"         ({type(exc).__name__}: {exc})")
            if not applied:
                stale.append(mutation.name)
                print(f"  STALE  {mutation.name} — its target has moved; the proof is not running")
                continue

            result = _run(_argv(mutation.command), copy)
            output = (result.stdout + result.stderr).lower()

            if result.returncode == 0:
                failures.append(mutation.name)
                print(f"  FAIL   {mutation.name} — {mutation.gate} accepted the violation")
            elif mutation.expect.lower() not in output:
                failures.append(mutation.name)
                print(
                    f"  FAIL   {mutation.name} — something failed, but not {mutation.gate}; "
                    f"{mutation.expect!r} is absent from the output"
                )
            else:
                passes.append(mutation.name)
                print(f"  ok     {mutation.name} — refused by {mutation.gate}")

    print()
    print(f"gate-proof: {len(passes)} refused, {len(failures)} accepted, {len(stale)} stale")
    if stale:
        print("\nstale mutations point at code that has moved. Update them:", file=sys.stderr)
        for name in stale:
            print(f"  {name}", file=sys.stderr)
    return 1 if failures or stale else 0


if __name__ == "__main__":
    raise SystemExit(main())

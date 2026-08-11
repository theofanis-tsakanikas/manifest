#!/usr/bin/env python3
"""Every abstention in the deployed pipeline reaches the review queue.

**The defect this exists to make impossible, which the estate shipped with.**

The state machine had two terminal states, `Publish` and `QueueForReview`, and they were
mutually exclusive. A document with twelve fields where eight cleared their thresholds and four
abstained took the publish path — and the four that abstained never reached a human. They were
written into the record marked as queued, and nothing was sent to the queue.

Nothing failed. The execution succeeded, the record was accurate about what it did and did not
contain, and the queue stayed empty. Doctrine rule 1 says *abstention is the safe state — and
abstention is not free*; claim 5 says the queue is a declared finite resource and exceeding it
fails the build. Both are statements about a queue that, in the estate, only ever received
documents where **every** field abstained. The capacity model was measuring the empty set.

**Why a check and not just a fix.** The fix is one `Choice` state, and the next person to
simplify this machine deletes it in good faith: it looks like a redundant branch on the happy
path. Nothing in the execution history would say otherwise — a run that drops four abstentions
is indistinguishable from a run that had none. So the routing property is asserted here, from
the definition, offline, with no AWS account.

**What it reads.** The `jsonencode({...})` block in `infra/extraction/pipeline.tf` is HCL, not
JSON, so this does not parse it as data. It checks the routing relationships by name, which is
the level the property lives at: which state follows which, and whether the path from a verified
document to `Publish` passes a decision about abstentions.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PIPELINE = ROOT / "infra" / "extraction" / "pipeline.tf"

#: The queue-sending states. Both send to `aws_sqs_queue.review`; a third one that sent
#: somewhere else would be a review queue nobody is watching.
QUEUE_RESOURCE = "arn:aws:states:::sqs:sendMessage"


def _state_bodies(text: str) -> dict[str, str]:
    """Each top-level state's block, by name.

    Brace-counted rather than pattern-matched: a state body contains nested objects, and a
    regex that stopped at the first `}` would read half of every state and report whatever it
    found there.
    """
    #: What makes a block a *state* rather than any other nested object: it declares a `Type`
    #: from Amazon States Language. Matching on indentation instead — which this did, at exactly
    #: six spaces — is matching on formatting, and it broke the moment the states were wrapped in
    #: a `merge(...)` and `terraform fmt` moved them two columns right. A control that fails on
    #: whitespace is a control somebody edits until it stops complaining. Twice now.
    kinds = "Task|Choice|Fail|Succeed|Pass|Wait|Parallel|Map"
    states: dict[str, str] = {}
    # `[ \t]+`, not `\s+`: `\s` matches newlines too, so a greedy run would start on an
    # earlier line and every indentation measured from it would be wrong.
    for match in re.finditer(r"^([ \t]+)(\w+) = \{$", text, re.MULTILINE):
        name = match.group(2)
        depth = 0
        index = match.end() - 1
        for position in range(index, len(text)):
            if text[position] == "{":
                depth += 1
            elif text[position] == "}":
                depth -= 1
                if depth == 0:
                    body = text[index : position + 1]
                    # `Type` at *this* block's own level, not anywhere inside it. Without the
                    # indentation anchor the `locals` block that holds the escalation states
                    # matched too, because one of the states nested in it declares a `Type`.
                    inner = match.group(1) + "  "
                    if re.search(rf'\n{inner}Type\s*=\s*"({kinds})"', body):
                        states[name] = body
                    break
    return states


def main() -> int:
    text = PIPELINE.read_text(encoding="utf-8")
    states = _state_bodies(text)
    problems: list[str] = []

    if "Publish" not in states or "QueueForReview" not in states:
        print(
            "the state machine has no Publish or no QueueForReview state. Either this file "
            "moved or the check is reading the wrong shape — both are reasons to stop rather "
            "than to pass.",
            file=sys.stderr,
        )
        return 1

    queueing = {
        name for name, body in states.items() if QUEUE_RESOURCE in body and "review.url" in body
    }
    if not queueing:
        problems.append(
            "no state sends to the review queue. Every abstention in this pipeline would be "
            "recorded and forgotten"
        )

    # **The property.** Some state must branch on how many fields abstained, and its positive
    # branch must send to the queue. Without it the publish path is unconditional and the
    # abstentions on a partly-published document go nowhere.
    deciders = [
        name
        for name, body in states.items()
        if '"Choice"' in body and "queued_count" in body and "NumericGreaterThan" in body
    ]
    if not deciders:
        problems.append(
            "no Choice state branches on `queued_count`. A document that published some of "
            "itself and abstained on the rest takes the publish path and its abstentions reach "
            "nobody — the failure the estate shipped with, and the one that leaves claim 5's "
            "capacity model measuring the empty set"
        )
    # **At least one, not every one.** There are two decisions on `queued_count` and they have
    # different jobs: one sends the page up a tier, the other sends what still abstains to a
    # human. Requiring every decider to lead to the queue would have made adding the escalation
    # branch look like a regression — and the honest property was always "a document that
    # abstains reaches a person", not "every mention of queued_count means the queue".
    reaching_the_queue = [
        name
        for name in deciders
        if (found := re.search(r'Next\s*=\s*"(\w+)"', states[name])) and found.group(1) in queueing
    ]
    if deciders and not reaching_the_queue:
        problems.append(
            f"{len(deciders)} state(s) branch on `queued_count` and none of them sends to the "
            f"review queue: {', '.join(sorted(deciders))}. The decision is there and the "
            f"consequence is not"
        )

    # The abstention path must continue to `Publish` rather than end, or the fix trades one
    # silent loss for another: the publishable fields would be queued and never written.
    for name in queueing:
        body = states[name]
        continues = re.search(r'Next\s*=\s*"(\w+)"', body)
        # **Matched as a pattern, not as a literal.** The first version looked for the exact
        # strings `End = true` and `End       = true`, which is a check on alignment: adding
        # `ResultPath` to this state made `terraform fmt` re-align the block, and the gate
        # reported that a state which plainly ends does not end. A control that fails on
        # whitespace is a control somebody edits until it stops complaining.
        ends = re.search(r"End\s*=\s*true", body) is not None
        if not continues and not ends:
            problems.append(f"{name} neither ends nor names a Next state")
        if continues and continues.group(1) == "Publish" and "ResultPath = null" not in body:
            problems.append(
                f"{name} continues to Publish without discarding its result. The SQS response "
                f"would replace the state's output, Publish would write a message id into the "
                f"records bucket where a customs record belongs, and the execution would still "
                f"report success"
            )

    if problems:
        print(f"pipeline routing: {len(problems)} problem(s)\n", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        return 1

    print(
        f"pipeline routing: {len(queueing)} state(s) send to the review queue, and a document "
        f"that publishes some of itself still owes the rest to a human."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

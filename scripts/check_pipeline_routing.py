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

    # **A state written on one line is still a state**, and the brace-counting loop below never
    # saw one: it anchors on `name = {` at the *end* of a line. `Done = { Type = "Succeed" }` is
    # the whole of a terminal success and `terraform fmt` keeps it on one line, so the
    # reachability check reported that a transition named a state the machine did not declare —
    # a check reading a formatting convention, which is the defect decision 24 is about, found
    # by the check written to catch a different one.
    for match in re.finditer(rf'^[ \t]+(\w+) = \{{ Type = "({kinds})" \}}$', text, re.MULTILINE):
        states[match.group(1)] = match.group(0)
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


#: A ternary has two arms. Named because `>= 2` in the split below is the difference between
#: reading a conditional transition and reading a plain one.
_TERNARY_ARMS = 2


def _transitions(body: str) -> tuple[set[str], set[str]]:
    """`(where this state goes when a flag is off, where it goes when the flag is on)`.

    A plain `Next = "X"` goes in both. A `Next = flag ? "A" : "B"` splits: the conditions in this
    file all read *"the feature is absent"* — `var.search_endpoint == ""` — so the first arm is
    the off shape and the second is the on shape, which is the order a reader expects anyway.
    """
    off: set[str] = set()
    on: set[str] = set()
    for line in body.splitlines():
        if not re.search(r"\b(Next|Default)\s*=", line):
            continue
        names = re.findall(r'"(\w+)"', line.split("=", 1)[1])
        if "?" in line and len(names) >= _TERNARY_ARMS:
            off.add(names[0])
            on.add(names[1])
        else:
            off |= set(names)
            on |= set(names)
    for block in re.finditer(r"Catch\s*=\s*\[(.*?)\]", body, re.DOTALL):
        caught = set(re.findall(r'Next\s*=\s*"(\w+)"', block.group(1)))
        off |= caught
        on |= caught
    return off, on


def _reachable(states: dict[str, str], start: str, shape: int) -> set[str]:
    """Everything a walk from `StartAt` actually arrives at, in one shape of the definition."""
    seen: set[str] = set()
    frontier = [start]
    while frontier:
        name = frontier.pop()
        if name in seen:
            continue
        seen.add(name)
        if name in states:
            frontier.extend(_transitions(states[name])[shape])
    return seen


def _reachability(states: dict[str, str]) -> list[str]:
    """Every state is reachable **in the shape it exists in**, and every transition names a state.

    **Step Functions refuses an unreachable state, and only at apply.** The machine had one:
    `Done` existed for the search-off shape and `IndexTheRecord` carried `End = true`, so with
    search *on* nothing arrived at `Done` — *MISSING_TRANSITION_TARGET*, six minutes into an
    extraction apply with the whole gate already spent. `terraform validate` cannot see it; the
    definition is a string until the API parses it.

    **And the obvious version of this check would have missed it**, which is worth more than the
    check. Collecting every name that appears after a `Next` finds `Done` in
    `var.search_endpoint == "" ? "Done" : "IndexTheRecord"` and calls it reached — in a
    definition where, for either value of the flag, it is not. A ternary mentions both arms and
    submits one. So the walk is transitive, it starts where the machine starts, and it runs once
    per shape.

    States that exist only in one shape are excluded from that shape's verdict rather than
    reported: the escalation states are merged in by a `for ... if`, so with the flag off they
    are not in the definition at all and "unreachable" would be the wrong word for them.
    """
    text = PIPELINE.read_text(encoding="utf-8")
    start = re.search(r'StartAt\s*=\s*"(\w+)"', text)
    if not start:
        return ["the definition declares no StartAt; this check is reading the wrong shape"]

    problems: list[str] = []
    named: set[str] = set()
    for body in states.values():
        for side in _transitions(body):
            named |= side
    for missing in sorted(named - set(states)):
        problems.append(
            f'a transition names the state "{missing}", which the machine does not declare. '
            f"Step Functions refuses the definition at apply, after the gate has run"
        )

    conditional = _states_only_present_when_a_flag_is_on(text)
    for shape, label in ((0, "off"), (1, "on")):
        arrived = _reachable(states, start.group(1), shape)
        for orphan in sorted(set(states) - arrived):
            if orphan in conditional:
                continue
            problems.append(
                f'with the optional features {label}, nothing arrives at the state "{orphan}". '
                f"Step Functions refuses an unreachable state at apply — and a dead state reads "
                f"as part of the machine to anybody reviewing the file"
            )
    return problems


def _states_only_present_when_a_flag_is_on(text: str) -> set[str]:
    """States merged in by a `for ... if`, which are absent rather than unreachable when off.

    Every `<something>_states` local, not one named list. There were two by the time this was
    written — the escalation tiers and the search surface — and naming the first one would have
    made the second read as a defect the day it was added, which is how a check gets edited
    until it stops complaining rather than until it is right.
    """
    absent: set[str] = set()
    for local in re.finditer(r"^  (\w+_states)\s*=\s*\{$", text, re.MULTILINE):
        block = text[local.end() : text.index("\n  }", local.end())]
        absent |= set(re.findall(r"^\s{4}(\w+) = \{", block, re.MULTILINE))
    return absent


def main() -> int:
    text = PIPELINE.read_text(encoding="utf-8")
    states = _state_bodies(text)
    problems: list[str] = _reachability(states)

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

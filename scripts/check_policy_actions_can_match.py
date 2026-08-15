#!/usr/bin/env python3
"""Every action in the deploy policy sits in a statement whose resources could match it.

**Where this came from.** The teardown of 2026-08-15 left thirty-three SageMaker lineage
entities standing. `scripts/sagemaker_lineage.py` had been written to remove them, the four
verbs it needs had been added to `infra/bootstrap/deploy_permissions.tf`, and the run still
failed with `AccessDeniedException` on `sagemaker:ListActions`. The verbs were in a statement
whose `resources` block names SQS queues, DynamoDB tables and state machines — and nothing
else. A `sagemaker:` action scoped to a DynamoDB table grants exactly nothing.

That is decision 24 in its purest form: **the failure this project produces most is a check
reading the wrong thing.** The permission existed, in the file, spelled correctly, reviewed,
and it was inert. Nothing in the estate could tell, because Terraform validates that the
document is well-formed and Checkov asks whether it is too broad. Neither asks the only
question that matters here: *could this action ever fire?*

**The rule.** For every statement, every action's service prefix must appear as the service
field of at least one resource ARN in that same statement. A statement with `resources = ["*"]`
passes everything, which is correct — that really does match.

**The aliases are declared, not inferred**, in `contracts/deploy/policy_services.yaml`. A few
actions genuinely name another service's ARN — `sts:AssumeRole` is granted on an *IAM* role,
`iam:PassRole` on the role being passed — and pretending otherwise would mean either a false
red or a `["*"]` written to silence one. A declared alias is a claim somebody can argue with;
an inferred one is a hole.

This is a lint, not a permission model. It cannot tell whether an action is *sufficient* — only
whether it is *reachable*. An unreachable one is always a defect.

    python3 scripts/check_policy_actions_can_match.py
"""

from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
POLICIES = ROOT / "infra" / "bootstrap" / "deploy_permissions.tf"
ALIASES = ROOT / "contracts" / "deploy" / "policy_services.yaml"

GREEN, RED, DIM, RESET = "\033[32m", "\033[31m", "\033[2m", "\033[0m"

#: `arn:aws:<service>:<region>:<account>:<rest>` — the third field, which is what an action's
#: prefix is compared against. Interpolations (`${var.project}`) never appear in that field,
#: so a plain split is honest here rather than convenient.
_ARN_SERVICE = re.compile(r"^arn:aws:([a-z0-9-]+):")


def _statements(source: str) -> list[tuple[int, str]]:
    """Every `statement { ... }` block, with the line it starts on.

    Brace-matched rather than regex-matched: a statement contains nested `condition` and
    `principals` blocks, and a non-greedy match to the first `}` would cut one in half and
    quietly check a fragment. Quoted braces are not tracked because none appear in this file;
    a stray one would produce a parse that swallows the rest of the file, which fails loudly.
    """
    blocks: list[tuple[int, str]] = []
    for opening in re.finditer(r"^\s*statement\s*\{", source, flags=re.MULTILINE):
        depth, index = 0, opening.end() - 1
        while index < len(source):
            if source[index] == "{":
                depth += 1
            elif source[index] == "}":
                depth -= 1
                if depth == 0:
                    break
            index += 1
        blocks.append((source.count("\n", 0, opening.start()) + 1, source[opening.end() : index]))
    return blocks


def _listed(block: str, attribute: str) -> list[str]:
    """The quoted strings assigned to `actions` or `resources` in one statement.

    **Bracket-matched, for the reason the first version of this had to be rewritten.** It read
    to a closing `]` sitting alone on a line, which is how the multi-line lists are formatted —
    and so it saw nothing at all in `resources = ["*"]`, the single most common shape in the
    file. Every statement then reported *"resources name nothing"* and the lint went red on all
    three hundred and eighty-nine actions. A check that fails on everything names nothing, and
    would have been switched off within a day. Matching the bracket handles both shapes because
    it is reading the syntax rather than the house style.
    """
    opening = re.search(rf"^[ \t]*{attribute}\s*=\s*\[", block, re.MULTILINE)
    if not opening:
        return []
    depth, index = 0, opening.end() - 1
    while index < len(block):
        if block[index] == "[":
            depth += 1
        elif block[index] == "]":
            depth -= 1
            if depth == 0:
                break
        index += 1
    body = re.sub(r"#.*?$", "", block[opening.end() : index], flags=re.MULTILINE)
    return re.findall(r'"([^"]+)"', body)


def _aliases() -> dict[str, set[str]]:
    """action prefix -> the ARN services it may legitimately be granted on.

    Parsed with a small reader rather than PyYAML: this runs on a bare CI runner in the same
    job as the Terraform lint, and a lint that needs a dependency installed is a lint somebody
    eventually skips. The file's shape is `prefix:` followed by `  - service` lines.
    """
    if not ALIASES.exists():
        return {}
    mapping: dict[str, set[str]] = {}
    current: str | None = None
    for raw in ALIASES.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        if not line.startswith((" ", "-")) and line.endswith(":"):
            current = line[:-1].strip()
            mapping.setdefault(current, set())
        elif current and line.strip().startswith("- "):
            mapping[current].add(line.strip()[2:].strip().strip("\"'"))
    return mapping


def main() -> int:
    source = POLICIES.read_text(encoding="utf-8")
    aliases = _aliases()
    unreachable: list[str] = []
    checked = 0

    for line_number, block in _statements(source):
        actions = _listed(block, "actions")
        resources = _listed(block, "resources")
        if not actions:
            continue
        if "*" in resources:
            checked += len(actions)
            continue

        services = {
            match.group(1) for resource in resources if (match := _ARN_SERVICE.match(resource))
        }
        sid_match = re.search(r'sid\s*=\s*"([^"]+)"', block)
        sid = sid_match.group(1) if sid_match else f"line {line_number}"

        for action in actions:
            checked += 1
            prefix = action.split(":", 1)[0]
            allowed = {prefix} | aliases.get(prefix, set())
            if not (allowed & services):
                unreachable.append(
                    f"  {RED}unreachable{RESET}  {action}\n"
                    f"              in {sid} (line {line_number}), whose resources name "
                    f"{', '.join(sorted(services)) or 'nothing'}"
                )

    print(f"deploy policy — {checked} action(s) across {len(_statements(source))} statement(s)")
    if unreachable:
        print()
        print("\n".join(unreachable))
        print(
            f"\n{RED}An action whose statement cannot match it is granted in the file and "
            f"absent in the account.{RESET}\n"
            f"{DIM}Move it to a statement whose resources cover it, or declare the "
            f"cross-service grant in {ALIASES.relative_to(ROOT)}.{RESET}"
        )
        return 1

    print(f"  {GREEN}ok{RESET}    every action sits where its resources could match it")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Every optional feature plans cleanly with its flag both on and off.

**The failure this catches is the worst kind of optional: one that breaks the configuration it
is absent from.**

`enable_escalation_tiers` was added twice with the same defect. A `local` that builds the
escalation states indexes `aws_lambda_function.escalate[0]`, and a `local` is evaluated whether
or not its result is used — so with the flag off, `[0]` on a zero-count resource failed at plan
time. Then a data source with no `count` of its own interpolated a log group's ARN, which is
null when the group does not exist, and null does not go into a string.

Neither is visible to `terraform validate`: both are type-correct and both refer to things that
exist in the configuration. They appear only when something asks Terraform to *plan* the layer
with the feature switched off — which is the default, which is what every ordinary deploy does.

So this plans each layer twice, with realistic-looking values for everything else, and requires
both to succeed.

**It needs credentials, and it is the only check here that does — stated rather than hidden.**
These layers read `aws_caller_identity`, `aws_region` and a managed prefix list, so a plan calls
AWS however little it intends to. Two earlier versions of this file claimed to be offline and
were not: the first read the real Terraform state, the second failed on the provider and I
nearly wrote the claim anyway.

What it does avoid is *shared* state. Each layer is copied to a temporary directory with a
`backend "local"` override, so the plan reasons about an empty state — every resource appears as
"to add" — and no lock is taken on the estate anybody else might be deploying.

**The copy is the whole point, and it was learned the expensive way.** This ran in the layer's
own directory and re-initialised it with `-reconfigure` to pick up the local backend. It removed
the override afterwards and did not, could not, restore what it had overwritten: `.terraform`
was left pointing at a local backend. The deploy's next command is `terraform apply` against the
S3 backend it had initialised one line earlier, and it refused with *"Backend type changed from
local to s3"* — an error about the workflow's own `init`, produced by a check that had already
printed a pass.

A check that mutates what it inspects is a check that can break the thing it certifies, and no
amount of tidying up afterwards makes that safe: it cannot restore a backend configuration it
was never told. Isolation costs one provider download per layer and removes the failure mode
rather than sequencing around it.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: The optional features, and the layer each one lives in. A feature added without an entry here
#: is a feature nobody plans with off — which is how both defects above reached a deploy.
OPTIONAL: dict[str, str] = {
    "extraction": "enable_escalation_tiers",
    "foundation": "enable_escalation_tiers",
}


#: Values that satisfy a variable's shape without naming anything real. Deliberately obvious
#: placeholders: if one ever reaches an API, it fails loudly rather than pointing at a resource.
def _placeholder(name: str, kind: str) -> str:
    if "list" in kind:
        if "subnet" in name:
            return '["subnet-00000000000000000"]'
        if "arn" in name:
            return '["arn:aws:bedrock:eu-central-1::foundation-model/example"]'
        return "[]"
    if "bool" in kind:
        return "false"
    if "number" in kind:
        return "60"
    if name.endswith("_arn") or name.endswith("_arns"):
        return '"arn:aws:kms:eu-central-1:111111111111:key/00000000-0000-0000-0000-000000000000"'
    if "digest" in name:
        return '"sha256:' + "0" * 64 + '"'
    if "bucket" in name:
        return f'"manifest-{name.replace("_bucket", "")}-111111111111"'
    if "vpc_id" in name:
        return '"vpc-00000000000000000"'
    if "security_group" in name:
        return '"sg-00000000000000000"'
    if "expires" in name:
        return '"2026-12-31"'
    if "email" in name:
        return '"placeholder@example.invalid"'
    return '"x"'


def _variables(layer: str) -> dict[str, str]:
    """Every variable the layer declares without a default, with a placeholder value."""
    text = (ROOT / "infra" / layer / "variables.tf").read_text(encoding="utf-8")
    values: dict[str, str] = {}
    # Brace-counted rather than split on a blank line: a variable block contains `validation`
    # blocks and multi-line descriptions, and stopping at the first empty line reads half of one
    # — which loses the `type` and hands Terraform a string where it wanted a number.
    for match in re.finditer(r'^variable "(\w+)" \{', text, re.MULTILINE):
        name = match.group(1)
        depth, index = 0, match.end() - 1
        for position in range(index, len(text)):
            if text[position] == "{":
                depth += 1
            elif text[position] == "}":
                depth -= 1
                if depth == 0:
                    body = text[index : position + 1]
                    break
        else:
            continue
        if re.search(r"^\s*default\s*=", body, re.MULTILINE):
            continue
        kind = (re.search(r"type\s*=\s*(\S+)", body) or [None, "string"])[1]
        values[name] = _placeholder(name, kind)
    return values


def _last_line(output: str) -> str:
    lines = [line for line in output.strip().splitlines() if line.strip()]
    return lines[-1] if lines else "(no output)"


#: A backend that lives in a temporary directory, so a plan needs no credentials and touches no
#: shared state. Written as an override because Terraform merges `*_override.tf` over the
#: configuration it sits next to, which is the documented way to replace a backend block without
#: editing the file that declares it.
OVERRIDE = """terraform {
  backend "local" {}
}
"""


#: What must not be copied into the working copy. `.terraform` carries the *caller's* backend
#: initialisation, which is the state this check exists not to touch; the state files are the
#: real estate's and a plan that read them would take a lock on it.
NOT_COPIED = shutil.ignore_patterns(".terraform", "*.tfstate", "*.tfstate.backup", "*.tfplan")


def _working_copy(layer: str, destination: Path) -> Path:
    """The layer's configuration, alone, with a local backend written over its own.

    `.terraform.lock.hcl` comes along deliberately: the provider version this plans against must
    be the one the deploy applies with, and the price of the isolation is re-downloading it.
    """
    shutil.copytree(ROOT / "infra" / layer, destination, ignore=NOT_COPIED)
    (destination / "zz_local_backend_override.tf").write_text(OVERRIDE, encoding="utf-8")
    return destination


def _init(copy: Path) -> tuple[bool, str]:
    """Initialise the working copy, which has no backend but the local one written into it."""
    result = subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["terraform", f"-chdir={copy}", "init", "-input=false", "-no-color"],  # noqa: S607
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0, (result.stderr or result.stdout)[-800:]


def _plan(copy: Path, flag: str, enabled: bool, varfile: Path) -> tuple[bool, str]:
    result = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [  # noqa: S607
            "terraform",
            f"-chdir={copy}",
            "plan",
            "-input=false",
            "-no-color",
            "-refresh=false",
            f"-var-file={varfile}",
            f"-var={flag}={'true' if enabled else 'false'}",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0, (result.stderr or result.stdout)[-1200:]


def main() -> int:
    problems: list[str] = []
    for layer, flag in OPTIONAL.items():
        with tempfile.TemporaryDirectory(prefix=f"optional-{layer}-") as scratch:
            copy = _working_copy(layer, Path(scratch) / layer)
            problems.extend(_plan_both_ways(layer, flag, copy))

    if problems:
        print(f"\noptional layers: {len(problems)} problem(s)\n", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        return 1

    print(
        f"optional layers: {len(OPTIONAL)} feature(s) plan with their flag both on and off. "
        f"`terraform validate` cannot see this — both shapes are type-correct."
    )
    return 0


def _plan_both_ways(layer: str, flag: str, copy: Path) -> list[str]:
    problems: list[str] = []
    ready, output = _init(copy)
    if not ready:
        return [f"infra/{layer} will not initialise: {_last_line(output)}"]
    values = _variables(layer)
    with tempfile.NamedTemporaryFile("w", suffix=".tfvars", delete=False) as handle:
        handle.write("".join(f"{name} = {value}\n" for name, value in values.items()))
        varfile = Path(handle.name)
    try:
        for enabled in (False, True):
            ok, output = _plan(copy, flag, enabled, varfile)
            state = "on" if enabled else "off"
            if ok:
                print(f"  {layer:12} {flag} {state:3} — plans")
            else:
                problems.append(
                    f"infra/{layer} does not plan with {flag}={state}. An optional feature that "
                    f"breaks the configuration it is absent from is one every ordinary deploy "
                    f"trips over, because absent is the default:\n"
                    f"      {_last_line(output)}"
                )
    finally:
        varfile.unlink(missing_ok=True)
    return problems


if __name__ == "__main__":
    raise SystemExit(main())

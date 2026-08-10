#!/usr/bin/env python3
"""The deploy path exists, tears itself down, and has never been dispatched.

Three checks, and the third is the one that would be embarrassing to get wrong.

**A destroy workflow exists for every layer the deploy workflow applies.** A repository with a
deploy path and no teardown path is how an estate gets left standing, and the asymmetry is easy
to acquire one layer at a time: somebody adds a layer to `deploy.yml` and the destroy file is
the one they do not have open.

**The destroy order is the deploy order reversed.** Foundation holds the keys and the network
everything else is encrypted with and attached to. Destroying it first leaves orphans that
cannot be deleted because the key that encrypts them is already scheduled for deletion, and the
teardown then reports success while the expensive half is still running.

**Neither workflow can be triggered by anything but a human.** `workflow_dispatch` and nothing
else. A `push`, a `schedule` or a `pull_request` trigger on either of these is an apply that
happens because somebody merged something.

Its mutations are in `scripts/gate_proof.py`.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"

#: Layers `deploy.yml` is allowed to apply without a matching destroy job, and why. Empty, and
#: it stays empty: the moment there is an entry here, the teardown is incomplete by design.
EXEMPT: frozenset[str] = frozenset()


def _load(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def _layer_jobs(workflow: dict) -> list[str]:
    """Jobs that touch a Terraform layer, in file order."""
    return [
        name
        for name, job in workflow["jobs"].items()
        if any(
            "terraform -chdir=infra/" in str(step.get("run", "")) for step in job.get("steps", [])
        )
    ]


#: The only repository variable either workflow may reference. Everything else a deploy needs
#: is a name `infra/bootstrap` already chose, and is published to SSM for the workflow to
#: resolve — see `infra/bootstrap/published.tf`.
#:
#: This check exists because the first version of the deploy carried **four** transcribed
#: variables: the role ARN, the state bucket, the access-log bucket and the alert address. The
#: access-log one was the worst, because `infra/foundation` creates that bucket and did not
#: output it, so the value had to be hand-typed to match a name Terraform computed.
ONLY_VARIABLE = "AWS_ACCOUNT_ID"

#: The prefix bootstrap publishes under, and the prefix the deploy role is granted. A grant on
#: `/manifest/*` would let a compromised deploy read every parameter any later layer writes.
PARAMETER_PREFIX = "/${{ env.PROJECT }}/bootstrap/"


def _transcribed_variables(text: str) -> set[str]:
    return set(re.findall(r"vars\.(\w+)", text)) - {ONLY_VARIABLE}


def _parameters_read(text: str) -> set[str]:
    """SSM names a workflow resolves, however it spells the call."""
    names = set(re.findall(r"read_param\s+(\w+)", text))
    names |= set(re.findall(r"/bootstrap/(\w+)\"", text))
    return names


def _parameters_published() -> set[str]:
    published = (ROOT / "infra" / "bootstrap" / "published.tf").read_text(encoding="utf-8")
    block = re.search(r"published = \{(.*?)\n  \}", published, re.DOTALL)
    names = set(re.findall(r"^\s*(\w+)\s*=", block.group(1), re.MULTILINE)) if block else set()
    names |= set(re.findall(r'name\s*=\s*"/\$\{var\.project\}/bootstrap/(\w+)"', published))
    return names


def _check_resolution() -> list[str]:
    """The deploy resolves what bootstrap published, and transcribes nothing else."""
    problems: list[str] = []
    published = _parameters_published()

    for name in ("deploy.yml", "destroy.yml"):
        text = (WORKFLOWS / name).read_text(encoding="utf-8")

        transcribed = _transcribed_variables(text)
        if transcribed:
            problems.append(
                f"{name} references repository variable(s) {sorted(transcribed)}. Every one of "
                f"those is a name infra/bootstrap already chose: publish it and resolve it. A "
                f"transcribed value looks like an independent setting, and when it drifts the "
                f"fix lands in a settings page rather than in a diff"
            )

        read = _parameters_read(text)
        missing = read - published
        if missing:
            problems.append(
                f"{name} reads /bootstrap/{sorted(missing)} which infra/bootstrap does not "
                f"publish. The deploy would fail after the environment approval, on a "
                f"parameter that does not exist"
            )
        if "aws ssm get-parameter" in text and not read:
            problems.append(f"{name} calls ssm get-parameter in a shape this check cannot read")

    grants = (ROOT / "infra" / "bootstrap" / "deploy_permissions.tf").read_text(encoding="utf-8")
    if "ssm:GetParameter" not in grants:
        problems.append(
            "the deploy role has no ssm:GetParameter grant, so the resolve step fails before "
            "the first terraform init"
        )
    if "parameter/${var.project}/bootstrap/*" not in grants:
        problems.append(
            "the deploy role's SSM grant is not scoped to the bootstrap prefix. A grant on "
            "/${var.project}/* would let a compromised deploy read every parameter any later "
            "layer ever writes"
        )
    return problems


def _check_permissions_exist() -> list[str]:
    """The deploy role can create the things the layers declare.

    Not a simulation of an apply — nothing offline can be — but a check that the role has *a*
    grant for each service the estate uses. It exists because the role had none: six layers were
    written and the policy still covered only the state backend, so the first `terraform apply`
    would have failed on `ec2:CreateVpc` with the reviewer's approval already spent.

    **What it cannot judge, stated so nobody reads more into a pass.** Whether the grant is
    *sufficient*. A role holding `ec2:CreateVpc` and not `ec2:CreateSubnet` looks identical to
    this check, and only a plan against a real account would tell them apart — which needs
    credentials this repository does not use. What a pass means is that no service the estate
    declares has been forgotten entirely, which is the failure that actually happened here.
    """
    grants = (ROOT / "infra" / "bootstrap" / "deploy_permissions.tf").read_text(encoding="utf-8")

    # **Attached, not merely written.** The first version of this check read the policy document
    # and stopped there, and `gate-proof` caught it: renaming the `aws_iam_role_policy` resource
    # left every grant in the file and none of them on the role, and the check passed. A policy
    # document with no attachment is the "written but not wired" failure in its purest form —
    # every permission visible in a diff, none of them in effect.
    attached = re.search(
        r'resource\s+"aws_iam_role_policy"\s+"\w+"\s*\{[^}]*?role\s*=\s*aws_iam_role\.deploy\.id',
        grants,
        re.DOTALL,
    )
    if not attached:
        return [
            "infra/bootstrap/deploy_permissions.tf declares grants that are not attached to "
            "aws_iam_role.deploy. Every permission is visible in the diff and none is in "
            "effect, which is the shape a reviewer is least likely to catch"
        ]
    if re.search(r'resource\s+"aws_iam_role_policy"\s+"\w+"\s*\{\s*count\s*=\s*0', grants):
        return [
            "the deploy role's estate policy is attached with count = 0. It exists, it reads "
            "correctly, and it grants nothing"
        ]

    required = {
        "ec2": "infra/foundation declares a VPC, subnets and endpoints",
        "kms": "every layer encrypts with a customer-managed key",
        "s3": "infra/foundation declares the data zones",
        "sns": "the alert topic the budget guard publishes to",
        "events": "the expiry rule the reaper runs on",
        "budgets": "the budget guard that disables this very role",
        "iam": "every layer creates at least one service role",
        "sqs": "infra/extraction declares the review queue",
        "dynamodb": "the decision record and the reprocessing ledger",
        "states": "the extraction state machine",
        "glue": "the lakehouse catalog",
        "athena": "the lakehouse workgroup",
        "emr-serverless": "infra/batch",
        "redshift-serverless": "infra/analytics",
        "logs": "every layer writes to a log group",
        "ssm": "the deploy resolves what bootstrap published",
    }
    if "iam:CreateServiceLinkedRole" not in grants:
        return [
            "the deploy role cannot create a service-linked role. EMR Serverless, Redshift "
            "Serverless and Athena each create one on first use, and without the grant the "
            "apply fails as an IAM error in the middle of creating something else"
        ]
    return [
        f"the deploy role has no {service}: grant, and {why}. A deploy fails on this four "
        f"minutes in, after the environment approval has been given"
        for service, why in sorted(required.items())
        if f'"{service}:' not in grants
    ]


def _required_variables(layer: str) -> set[str]:
    """Variables the layer declares with no default — the ones a run must supply.

    A default is matched as an **assignment** at the start of a line, never as the word.

    The first version searched the block for the string `default`, and that is a false-negative
    machine: three variables whose own descriptions said "there is no default" were read as
    having one and were never checked. It was found by adding those three, watching the gate
    report one problem instead of four, and asking why. A gate that under-reports is worse than
    no gate, because the green is the part people act on.
    """
    variables = ROOT / "infra" / layer / "variables.tf"
    if not variables.exists():
        return set()
    blocks = re.findall(
        r'variable "([^"]+)" \{(.*?)\n\}', variables.read_text(encoding="utf-8"), re.S
    )
    return {name for name, body in blocks if not re.search(r"^\s*default\s*=", body, re.M)}


def _check_every_layer_can_evaluate() -> list[str]:
    """A required variable nobody supplies is an apply — or a destroy — that stops and waits.

    This is the check that was missing, and the gap it left is the one worth naming: three of
    the five layers in `destroy.yml` declared a variable with no default, nothing in the
    workflow supplied it, and everything reported green. `terraform validate` does not ask for
    variable values, `checkov` reads resources rather than runs, and `tf_validate.py` calls
    both. So a teardown path that would have halted at the first prompt — with the estate
    standing and the approval already given — passed three separate gates.

    A destroy that cannot run is worse than no destroy at all, because the repository claims
    one.
    """
    problems: list[str] = []

    for name in ("deploy.yml", "destroy.yml"):
        workflow = _load(name)
        for job_name, job in workflow["jobs"].items():
            steps = "\n".join(str(step.get("run", "")) for step in job.get("steps", []))
            layers = set(re.findall(r"terraform -chdir=infra/(\w+)", steps))
            for layer in sorted(layers):
                for variable in sorted(_required_variables(layer)):
                    supplied = (
                        f"-var '{variable}=" in steps
                        or f'-var "{variable}=' in steps
                        or f"TF_VAR_{variable}=" in steps
                        # Resolved in bulk from a path whose parameter names are the variable
                        # names — see `infra/foundation/published.tf`.
                        or (
                            "get-parameters-by-path" in steps
                            and variable in _parameters_published_by_layers()
                        )
                    )
                    if not supplied:
                        problems.append(
                            f"{name}:{job_name} runs terraform in infra/{layer}, which requires "
                            f"the variable `{variable}`, and nothing in the job supplies it. The "
                            f"run stops at a prompt that has no terminal to appear on"
                        )
    return problems


def _parameters_published_by_layers() -> set[str]:
    """Names published under `/<project>/<layer>/*` by any layer other than bootstrap."""
    names: set[str] = set()
    for published in ROOT.glob("infra/*/published.tf"):
        if published.parent.name == "bootstrap":
            continue
        body = published.read_text(encoding="utf-8")
        block = re.search(r"published = \{(.*?)\n  \}", body, re.S)
        if block:
            names |= set(re.findall(r"^\s*(\w+)\s*=", block.group(1), re.M))
    return names


def _check_resolves_fail_loudly() -> list[str]:
    """A resolve that finds nothing must stop the job, not continue with an empty value.

    Two shell shapes make a failed read invisible, and both were in these workflows:

    `echo "VAR=$(aws ssm get-parameter ...)"` — the command `set -e` judges is the `echo`, which
    succeeds. A missing parameter becomes `VAR=`, and the job carries on to
    `-backend-config="bucket="` four minutes later, with the environment approval spent.

    A `while read` over `get-parameters-by-path` that matches nothing — reading nothing is a
    successful read. No variable is set, and the run stops at an input prompt that has no
    terminal to appear on.

    Both are the same failure as a required variable nobody supplies, arriving through a
    different door, so they are refused in the same place.
    """
    problems: list[str] = []

    for name in ("deploy.yml", "destroy.yml"):
        text = (WORKFLOWS / name).read_text(encoding="utf-8")

        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("echo ") and "$(aws ssm get-parameter" in stripped:
                problems.append(
                    f"{name} reads a parameter inside an `echo`: {stripped[:70]}... A failed "
                    f"read is a successful echo, so the value silently becomes empty. Assign it "
                    f"to a variable first, which is the form `set -e` can see fail"
                )
            if stripped.startswith("echo ") and "$(read_param" in stripped and "=$(" in stripped:
                problems.append(
                    f"{name} reads a parameter inside an `echo`: {stripped[:70]}... Same reason"
                )

        for job_name, job in _load(name)["jobs"].items():
            steps = "\n".join(str(step.get("run", "")) for step in job.get("steps", []))
            if "get-parameters-by-path" in steps and "-eq 0" not in steps:
                problems.append(
                    f"{name}:{job_name} resolves a whole SSM path and never checks that it "
                    f"found anything. An empty path is a missing layer, and reading nothing "
                    f"is a successful read"
                )

    return problems


def _check_nothing_names_what_nothing_creates() -> list[str]:
    """A state machine may not invoke a function by name. It must reference the resource.

    **This is the check for the failure that started all of them.** `VerifyProvenance` invoked
    `"${var.project}-provenance-gate"` — a string — and no layer created that function. Terraform
    was happy, because a string is a string. checkov was happy, because it reads resources.
    `terraform validate` was happy, because nothing was malformed. On a real deployment every
    document would have failed there, been caught by the step's `Catch`, and gone to a human: a
    pipeline reporting success while sending 100% of its volume to review.

    A Terraform *reference* cannot have that bug. `aws_lambda_function.provenance_gate.arn` does
    not resolve unless the resource exists, and the graph makes the dependency real. A string
    literal is a promise nobody checks.
    """
    problems: list[str] = []

    for definition in sorted(ROOT.glob("infra/*/*.tf")):
        text = definition.read_text(encoding="utf-8")
        for line in text.splitlines():
            stripped = line.strip()
            if '"FunctionName"' not in stripped and '"StateMachineArn"' not in stripped:
                continue
            # A reference looks like `aws_lambda_function.x.arn`; a promise looks like a string
            # with a project prefix in it.
            if "aws_lambda_function." in stripped or "aws_sfn_state_machine." in stripped:
                continue
            problems.append(
                f"{definition.relative_to(ROOT)} names a target as a literal: "
                f"{stripped[:80]}. Reference the resource instead — a string does not fail when "
                f"the thing it names was never created, it fails at the first invocation, "
                f"inside a Catch, as a document quietly going to a human"
            )

    return problems


def main() -> int:
    problems: list[str] = []

    deploy = _load("deploy.yml")
    destroy = _load("destroy.yml")

    # `on:` parses as the boolean True in YAML 1.1, which is a trap worth knowing about: a
    # check that looked up the string key would find nothing and pass silently.
    for name, workflow in (("deploy.yml", deploy), ("destroy.yml", destroy)):
        triggers = workflow.get("on", workflow.get(True))
        if set(triggers) != {"workflow_dispatch"}:
            problems.append(
                f"{name} is triggered by {sorted(triggers)}. Only `workflow_dispatch` is "
                f"allowed: any other trigger is an apply that happens because somebody merged "
                f"something"
            )

    problems.extend(_check_resolution())
    problems.extend(_check_permissions_exist())
    problems.extend(_check_every_layer_can_evaluate())
    problems.extend(_check_resolves_fail_loudly())
    problems.extend(_check_nothing_names_what_nothing_creates())

    applied = _layer_jobs(deploy)
    destroyed = _layer_jobs(destroy)

    missing = [layer for layer in applied if layer not in destroyed and layer not in EXEMPT]
    if missing:
        problems.append(
            f"deploy.yml applies {missing} and destroy.yml does not destroy them. A repository "
            f"with a deploy path and no teardown path is how an estate gets left standing"
        )

    shared = [layer for layer in applied if layer in destroyed]
    expected = list(reversed(shared))
    actual = [layer for layer in destroyed if layer in shared]
    if actual != expected:
        problems.append(
            f"destroy.yml tears down in {actual} where the reverse of the deploy order is "
            f"{expected}. Foundation holds the keys and the network everything else uses; "
            f"destroying it first leaves orphans that cannot be deleted, and the teardown "
            f"reports success with the expensive half still running"
        )

    # A destroy job must run even when the destroy job before it failed — and **only** then.
    #
    # The first version of this check demanded `always()` on every job with a `needs`, and it
    # was wrong in the dangerous direction: `analytics` and `batch` depend on `confirm`, and
    # `always()` there would have torn the estate down after the confirmation was *refused*.
    # The rule is about surviving a failed teardown, not about ignoring a refused one, so the
    # requirement applies only where the dependency is another layer.
    for name in destroyed:
        needs = destroy["jobs"][name].get("needs") or []
        needs = [needs] if isinstance(needs, str) else needs
        depends_on_a_layer = any(dependency in destroyed for dependency in needs)
        condition = str(destroy["jobs"][name].get("if", ""))
        if depends_on_a_layer and "always()" not in condition:
            problems.append(
                f"destroy.yml job {name!r} depends on another layer's teardown and does not "
                f"run when it fails. A teardown that stops at the first error leaves exactly "
                f"the expensive half standing"
            )
        if not depends_on_a_layer and "always()" in condition:
            problems.append(
                f"destroy.yml job {name!r} runs unconditionally and does not depend on another "
                f"layer's teardown, so it would also run after the confirmation was refused. "
                f"`always()` is for surviving a failed teardown, not for ignoring a refused one"
            )

    for name in applied:
        if deploy["jobs"][name].get("environment") != "deploy":
            problems.append(f"deploy.yml job {name!r} does not name the protected environment")
    for name in destroyed:
        if destroy["jobs"][name].get("environment") != "destroy":
            problems.append(f"destroy.yml job {name!r} does not name the protected environment")

    if problems:
        print(f"deploy-path: {len(problems)} problem(s)\n", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        return 1

    print(
        f"deploy-path: {len(applied)} layer(s) applied, all torn down in reverse order, both "
        f"workflows human-dispatch only and gated behind a protected environment. "
        f"**Neither has ever been dispatched.**"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

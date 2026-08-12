#!/usr/bin/env python3
"""The deploy path exists and tears itself down. Whether it has been walked is not asked here.

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
from fnmatch import fnmatch
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"

sys.path.insert(0, str(ROOT / "scripts"))
from destroy_references import PLACEHOLDERS as _RESOLVER_PLACEHOLDERS  # noqa: E402

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


def _resolver_covers_what_layers_publish() -> list[str]:
    """The resolver stands in for everything a layer publishes, not a subset of it.

    `destroy_references.py` fills in placeholders for references a partial teardown has already
    removed. If it knows fewer names than `infra/<layer>/published.tf` publishes, the gap is
    invisible until a destroy stops at an input prompt with no terminal to appear on — which is
    exactly how `ledger_table_arn` was found, one variable at a time, on the run that needed it.

    Two lists that must agree is one list that will drift, so this compares them.
    """
    problems: list[str] = []
    for layer, names in _RESOLVER_PLACEHOLDERS.items():
        published = ROOT / "infra" / layer / "published.tf"
        if not published.exists():
            continue
        text = published.read_text(encoding="utf-8")
        block = re.search(r"published = \{(.*?)\n  \}", text, re.DOTALL)
        if not block:
            continue
        declared = set(re.findall(r"^\s*(\w+)\s*=", block.group(1), re.MULTILINE))
        missing = declared - set(names)
        if missing:
            problems.append(
                f"infra/{layer}/published.tf publishes {sorted(missing)} and "
                f"scripts/destroy_references.py has no placeholder for them. A teardown re-run "
                f"after a partial failure stops at an input prompt with no terminal to appear on"
            )
    return problems


def _resolver_supplies(steps: str) -> set[str]:
    """The variable names `scripts/destroy_references.py` sets, for the layers a job resolves.

    **Read from the resolver rather than assumed.** The teardown used to set every reference
    with a literal `TF_VAR_x=` line, which this check could see. Those seven blocks are one
    script now — it reads the same SSM paths and stands in for whatever a partial teardown has
    already removed, because a teardown that cannot run after failing once is the worst property
    a teardown can have.

    Trusting the script's mere presence would make this check decoration: a resolver that
    quietly stopped publishing one name would pass, and the deploy would stop at a prompt with
    no terminal to appear on. So the names come from `PLACEHOLDERS`, which is the same structure
    the script iterates when it fills them in.
    """
    supplied: set[str] = set()
    for layer, names in _RESOLVER_PLACEHOLDERS.items():
        if f"--layer {layer}" in steps:
            supplied |= set(names)
    return supplied


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
    # **Every policy is attached, not merely one of them.**
    #
    # The grants were a single inline `aws_iam_role_policy` until IAM refused it: the 10 KB
    # ceiling on inline policies is an aggregate across the role, and six layers do not fit. They
    # are six managed policies now, joined by `aws_iam_role_policy_attachment`.
    #
    # That changed what this check has to prove. "At least one attachment exists" was enough for
    # one policy and is worthless for six — detach `network` and five attachments remain, so the
    # check passes while the deploy role cannot build a VPC. `gate-proof` demonstrated exactly
    # that: the retargeted mutation was accepted until this was rewritten to pair them.
    declared = set(re.findall(r'resource\s+"aws_iam_policy"\s+"(\w+)"', grants))
    # An attachment carrying `count = 0` renders to nothing. It reads as present in the file and
    # is absent from the account — the same "written but not wired" shape one level down — so the
    # block is discarded before the policy it names is counted as attached.
    attached_names: set[str] = set()
    for block in re.finditer(
        r'resource\s+"aws_iam_role_policy_attachment"\s+"\w+"\s*\{(.*?)\n\}', grants, re.DOTALL
    ):
        body = block.group(1)
        if re.search(r"^\s*count\s*=\s*0\s*$", body, re.M):
            continue
        attached_names |= set(re.findall(r"policy_arn\s*=\s*aws_iam_policy\.(\w+)\.arn", body))
    inline = re.search(
        r'resource\s+"aws_iam_role_policy"\s+"\w+"\s*\{[^}]*?role\s*=\s*aws_iam_role\.deploy\.id',
        grants,
        re.DOTALL,
    )

    #: Policies that exist to be attached by something other than the role — the budget action
    #: attaches this one when the ceiling is reached, and it must NOT be on the role by default.
    ATTACHED_BY_A_GUARD = {"budget_brake"}

    orphans = declared - attached_names - ATTACHED_BY_A_GUARD
    if orphans and not inline:
        return [
            f"policies {sorted(orphans)} are declared and never attached to the deploy role. "
            f"Every permission in them is visible in a diff and none is in effect, which is the "
            f"shape a reviewer is least likely to catch"
        ]
    if not declared and not inline:
        return [
            "infra/bootstrap/deploy_permissions.tf declares no policy attached to "
            "aws_iam_role.deploy in any form this check recognises"
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
        "lambda": "the tier-0 reader, the extraction handler and the provenance gate",
        "ecr": "the reader image the container functions run from",
        "sagemaker": "infra/extraction's opt-in classification endpoint",
        "aoss": "infra/lakehouse's opt-in search surface",
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


def _variable_blocks(text: str) -> list[tuple[str, str]]:
    """Every `variable "name" { ... }` and its body, found by matching braces.

    A regex was used here and it missed **27 declarations across four layers** — every variable
    written on a single line, `variable "vpc_id" { type = string }`, because the pattern looked
    for a closing brace at the start of a line. Those are exactly the cross-layer references,
    so the check that exists to prove a layer can evaluate was silently skipping the variables
    most likely to be missing.

    Brace matching rather than a cleverer regex: HCL nests (`validation`, `dynamic`), and a
    pattern that handles one level of nesting is a pattern that fails on two.
    """
    blocks: list[tuple[str, str]] = []
    for match in re.finditer(r'variable\s+"([^"]+)"\s*\{', text):
        depth, index = 0, match.end() - 1
        while index < len(text):
            if text[index] == "{":
                depth += 1
            elif text[index] == "}":
                depth -= 1
                if depth == 0:
                    break
            index += 1
        blocks.append((match.group(1), text[match.end() : index]))
    return blocks


def _required_variables(layer: str) -> set[str]:
    """Variables the layer declares with no default — the ones a run must supply.

    Two bugs lived in the earlier versions of this function, and both under-reported, which is
    the direction that matters: a gate that misses a problem is worse than no gate, because the
    green is what people act on.

    The first searched each block for the *word* `default`, so three variables whose own
    descriptions said "there is no default" were read as having one. The second used a regex
    that required the closing brace at the start of a line, so every single-line declaration was
    invisible — 27 of them, and all the cross-layer references among them.
    """
    variables = ROOT / "infra" / layer / "variables.tf"
    if not variables.exists():
        return set()
    return {
        name
        for name, body in _variable_blocks(variables.read_text(encoding="utf-8"))
        if not re.search(r"(^|\n)\s*default\s*=", body)
    }


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
                        # Or supplied by the teardown's resolver, which reads the same SSM paths
                        # and stands in for whatever a partial teardown already removed. The
                        # names it supplies are read from the script itself rather than assumed:
                        # a resolver that stopped publishing one of them would go undetected if
                        # this check simply trusted its presence.
                        or variable in _resolver_supplies(steps)
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


def _check_a_trigger_can_actually_fire() -> list[str]:
    """An event target's role grants the action that target needs.

    The deploy-role check above asks whether the estate can be *built*. This asks whether it can
    *run*, which is a different question and was not being asked at all: a rule with a target and
    a role that cannot invoke it deploys cleanly, reports healthy, and does nothing. There is no
    error anywhere — the documents simply sit in the bucket.

    Narrow on purpose. It knows one pairing: a target that is a state machine needs
    `states:StartExecution`. A general "does this role permit this target" check would need to
    model IAM, which nothing offline can do honestly; what it can do is refuse the one wiring
    this repository actually has, and refuse a new pairing it does not recognise rather than
    passing it silently.
    """
    problems: list[str] = []

    for definition in sorted(ROOT.glob("infra/*/*.tf")):
        text = definition.read_text(encoding="utf-8")
        for target in re.finditer(
            r'resource\s+"aws_cloudwatch_event_target"\s+"(\w+)"\s*\{(.*?)\n\}', text, re.S
        ):
            name, body = target.group(1), target.group(2)
            if "aws_sfn_state_machine" not in body:
                continue
            if "role_arn" not in body:
                problems.append(
                    f"{definition.relative_to(ROOT)}: event target `{name}` starts a state "
                    f"machine and names no role. It deploys cleanly and never fires"
                )
                continue
            # The role's policy is in the same layer; a grant elsewhere would be a cross-layer
            # dependency this repository does not have.
            layer = "\n".join(
                path.read_text(encoding="utf-8") for path in sorted(definition.parent.glob("*.tf"))
            )
            if "states:StartExecution" not in layer:
                problems.append(
                    f"{definition.relative_to(ROOT)}: event target `{name}` starts a state "
                    f"machine and nothing in this layer grants `states:StartExecution`. The "
                    f"estate deploys, every resource reports healthy, and no document is ever "
                    f"processed — the hardest of these failures to see, because nothing errors"
                )

    return problems


def _check_the_runtime_artefacts_are_deployed() -> list[str]:
    """Every object prefix a handler *reads* is written by a handler or by the deploy.

    **The family of failure this closes has now produced six findings in this repository**, and
    every one of them had the same shape: something names an artefact — a function, a parameter,
    an object — that nothing creates. Terraform is content, checkov is content, `terraform
    validate` is content, and the failure arrives at the first invocation, after the approval,
    usually inside a `Catch` that turns it into a document quietly going to a human.

    Two of the six were object prefixes, and they are why this check reads the handlers rather
    than a hand-maintained list:

    - `thresholds/` — the extraction handler read it and nothing wrote it. The deploy now
      renders and uploads it, so a *step* satisfies the requirement.
    - `renders/` — the provenance gate read it and nothing wrote it, because the reader
      rasterised to a temporary directory and let it go. Every field would have been reported
      uncheckable, which is a refusal, and the pipeline would have queued 100% of its volume
      while reporting success. Another *handler* satisfies it.

    A hand-maintained list of "artefacts to check" would have contained neither, because both
    were written and forgotten in the same commit as the code that reads them. Reading the
    handlers means a new prefix is covered the moment somebody adds one.
    """
    problems: list[str] = []
    deploy = (WORKFLOWS / "deploy.yml").read_text(encoding="utf-8")

    #: A prefix a *deploy step* may satisfy instead of a handler, and the producer that must
    #: appear in the workflow for it to count.
    deployed = {"thresholds/": "scripts/thresholds_artefact.py"}

    #: Only strings used as an object key or a key prefix. Matching bare `word/` anywhere would
    #: collect `application/json` and `image/png` from content types — a check that reports
    #: three imaginary problems is a check somebody turns off before it finds the real one.
    key_context = re.compile(
        r"(?:Key=|prefix=|key\s*=\s*|_load_json\([^,]+,\s*)f?\"([a-z][a-z0-9_]*)/"
    )

    written: set[str] = set()
    read: set[str] = set()
    for path in sorted(ROOT.glob("src/manifest/handlers/*.py")):
        source = path.read_text(encoding="utf-8")
        prefixes = set(key_context.findall(source))
        # A file that puts objects is credited with the prefixes it names; a file that fetches
        # them is charged with them. `read_tier0` does both, and its own prefixes are written by
        # it, so it settles its own account — which is correct and is why the sets are unioned
        # rather than partitioned by file.
        if "put_object(" in source:
            written |= prefixes
        if "get_object(" in source or "download_file(" in source or "prefix=" in source:
            read |= prefixes

    for prefix in sorted(read - written):
        key = f"{prefix}/"
        producer = deployed.get(key)
        if producer and producer in deploy:
            continue
        problems.append(
            f"a handler reads objects under `{key}` and nothing writes them — no handler puts "
            f"an object there and no deploy step produces one. The first invocation after the "
            f"deploy fails on a missing object, inside a Catch, as a document quietly going to "
            f"a human"
        )

    return problems


def _check_actions_are_pinned() -> list[str]:
    """Every third-party action is pinned to a commit SHA, never to a tag.

    **The shortest path from somebody else's compromised account to this AWS estate.** A tag
    moves: `actions/checkout@v4` is whatever the maintainer last pointed v4 at. `deploy.yml`
    holds `id-token: write` and assumes a role that can build the entire estate, so an upstream
    account takeover becomes arbitrary code with those credentials — without this repository
    having done anything wrong.

    A local `uses: ./.github/...` is exempt: it resolves inside this repository at the commit
    being run, so there is no third party and nothing to pin.
    """
    problems: list[str] = []

    for path in sorted(WORKFLOWS.glob("*.yml")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            stripped = line.strip()
            if not stripped.startswith("- uses:") and not stripped.startswith("uses:"):
                continue
            reference = stripped.split("uses:", 1)[1].split("#")[0].strip()
            if reference.startswith("./") or not reference:
                continue
            if "@" not in reference:
                problems.append(f"{path.name}:{number} uses `{reference}` with no version at all")
                continue
            pinned = reference.rsplit("@", 1)[1]
            if not re.fullmatch(r"[0-9a-f]{40}", pinned):
                problems.append(
                    f"{path.name}:{number} pins `{reference}` to a tag rather than a commit. A "
                    f"tag moves, and this workflow can assume a role that builds the estate — "
                    f"an upstream account takeover becomes code running with those credentials"
                )

    return problems


def _acceptance() -> dict:
    import datetime  # noqa: PLC0415

    path = ROOT / "contracts" / "deploy" / "acceptance.yaml"
    if not path.exists():
        return {}
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    entry = dict(loaded.get("acceptance") or {})
    entry["_today"] = datetime.date.today()
    return entry


def _approval_expiry() -> str:
    return str(_acceptance().get("expires_on", "no expiry recorded"))


def _check_the_approval_removal_is_accepted() -> list[str]:
    """Taking the reviewer off is allowed. Doing it silently, or forever, is not.

    This repository claimed "gated behind a protected environment" in four places. The reviewer
    requirement was then deliberately removed so that a first deploy could be iterated on
    without a person approving each attempt to discover what is broken — which is a defensible
    reason and is exactly how an approval becomes a reflex.

    What is not defensible is the sentence staying. So the removal is recorded as an acceptance
    with a name, a date, a reason and an **expiry**, in the same shape as
    `contracts/review/acceptance.yaml` — and this check refuses a missing one and refuses an
    expired one. An acceptance with no end is a decision nobody will revisit.
    """
    import datetime  # noqa: PLC0415

    entry = _acceptance()
    if not entry:
        return [
            "the deploy environments carry no required reviewer and nothing records that as a "
            "decision. `contracts/deploy/acceptance.yaml` is where it goes: who accepted it, "
            "why, and when it ends. A control removed without a record is a control the "
            "documentation still claims"
        ]

    missing = [
        field
        for field in ("accepted_by", "accepted_on", "expires_on", "why", "ends_when")
        if not entry.get(field)
    ]
    if missing:
        return [f"contracts/deploy/acceptance.yaml is missing {missing}"]

    expires = entry["expires_on"]
    expires = (
        expires if isinstance(expires, datetime.date) else datetime.date.fromisoformat(str(expires))
    )
    if expires < entry["_today"]:
        return [
            f"the acceptance for removing the deploy approval expired on {expires}. Doctrine "
            f"rule 6: exceptions expire, and on expiry the finding returns. Either the first "
            f"deploy is done and the reviewer requirement goes back on, or somebody accepts it "
            f"again, by name, with a new date"
        ]

    return []


def _check_every_env_reference_is_defined() -> list[str]:
    """A `${{ env.X }}` that is not defined renders as the empty string, silently.

    **This cost the first real deploy.** `deploy.yml` used `${{ env.PROJECT }}` in the role ARN
    and in every SSM path and never defined it, so the workflow tried to assume `role/-deploy`
    and read `//bootstrap/state_bucket`. The error was *"Not authorized to perform
    sts:AssumeRoleWithWebIdentity"*, which reads as a trust-policy problem — the investigation
    went to the OIDC provider's audience list and the subject claim before it reached the ARN.

    `destroy.yml` defined it correctly. Nothing compared the two files, because an undefined
    variable in GitHub Actions is not an error and produces no warning anywhere.
    """
    problems: list[str] = []

    for path in sorted(WORKFLOWS.glob("*.yml")):
        text = path.read_text(encoding="utf-8")
        defined = set(yaml.safe_load(text).get("env") or {})
        used = set(re.findall(r"\$\{\{\s*env\.(\w+)\s*\}\}", text))
        for name in sorted(used - defined):
            problems.append(
                f"{path.name} uses `env.{name}` and never defines it. An undefined variable "
                f"renders as an empty string rather than failing, so this becomes a wrong ARN "
                f"or a wrong path and the error it produces names something else entirely"
            )

    return problems


def _check_shell_variables_are_assigned_in_their_job() -> list[str]:
    """A `$VAR` passed to `-var` is assigned somewhere in the same job.

    **This cost a cycle after the image had already been built and pushed.** The extraction job
    passed `-var "escalation_model_arns=[\"$ESCALATION_MODEL_ARN\"]"` and never assigned that
    variable: the resolve step existed in three other jobs and had never been added to this one.
    Terraform received `[""]`, and the variable's own validation refused it — correctly, which
    is the only reason this failed loudly instead of deploying a role that could invoke every
    model in the account.

    An unset shell variable expands to the empty string. Nothing warns, and the value it
    produces is usually *almost* right, which is the shape that gets deployed.
    """
    problems: list[str] = []

    for name in ("deploy.yml", "destroy.yml"):
        workflow = _load(name)
        for job_name, job in workflow["jobs"].items():
            steps = "\n".join(str(step.get("run", "")) for step in job.get("steps", []))
            if not steps:
                continue
            # Line by line, and every `$VAR` on a line that passes `-var`. The first version
            # of this matched from `-var` up to the first quote — which is the character
            # immediately after it — so it found nothing and reported green over the very bug
            # it was written for. A gate that cannot bite is worse than no gate.
            used: set[str] = set()
            for line in steps.splitlines():
                if "-var" in line:
                    used |= set(re.findall(r"\$\{?(\w+)", line))
            used -= {"GITHUB_ENV", "GITHUB_OUTPUT", "GITHUB_WORKSPACE"}
            for variable in sorted(used):
                # A real assignment only. `echo "VAR=$VAR" >> $GITHUB_ENV` was accepted here
                # and it is precisely the shape that fails: with nothing assigned, the echo
                # writes `VAR=` and the variable is defined as the empty string for every later
                # step. The check counted the export and called it a value.
                assigned = (
                    bool(re.search(rf"^\s*{variable}=", steps, re.M))
                    or f"TF_VAR_{variable}" in steps
                    or variable in _resolver_supplies(steps)
                )
                if not assigned:
                    problems.append(
                        f"{name}:{job_name} passes `${variable}` to terraform and never assigns "
                        f"it. An unset shell variable expands to the empty string — nothing "
                        f"warns, and the value it produces is usually almost right"
                    )

    return problems


#: The teardown's own tools. Every AWS call these make runs as the deploy role, and none of them
#: appears in any Terraform layer — which is exactly why their permissions went unchecked.
TEARDOWN_SCRIPTS = ("estate_sweep.py", "destroy_references.py", "empty_bucket.py")

#: boto3's client name is not always the IAM prefix.
_IAM_PREFIX = {
    "stepfunctions": "states",
    "resourcegroupstaggingapi": "tag",
}

#: Calls whose IAM action is not the PascalCase of the method name. Each one is a place where
#: the API and the permission were named by different people, and every entry here is a bug
#: somebody would otherwise find at teardown time.
_ACTION_FOR = {
    ("s3", "list_buckets"): "s3:ListAllMyBuckets",
    ("s3", "head_bucket"): "s3:ListBucket",
    ("s3", "list_object_versions"): "s3:ListBucketVersions",
    ("s3", "delete_objects"): "s3:DeleteObject",
}


def _calls_made(source: str) -> set[tuple[str, str]]:
    """Every (service, method) pair a teardown script calls, read from the source.

    Deliberately syntactic. An AST walk would resolve more, and what it would buy is calls
    behind indirection — which these scripts do not have and must not acquire, because a
    permission that cannot be read off the source is a permission nobody audits.
    """
    clients = {
        variable: service
        for variable, service in re.findall(
            r"(\w+)\s*=\s*(?:session|boto3)\.client\(\"([\w-]+)\"\)", source
        )
    }
    calls: set[tuple[str, str]] = set()

    # `session.client("sns").get_topic_attributes(...)` — no variable in between.
    for service, method in re.findall(r"(?:session|boto3)\.client\(\"([\w-]+)\"\)\.(\w+)", source):
        calls.add((service, method))

    # `x = session.client("sqs")` … `x.list_queues(...)` / `x.get_paginator("list_queues")`.
    for variable, method in re.findall(r"\b(\w+)\.get_paginator\(\"(\w+)\"\)", source):
        if variable in clients:
            calls.add((clients[variable], method))
    for variable, method in re.findall(
        r"\b(\w+)\.((?:list|describe|get|delete|put|head)_\w+)\(", source
    ):
        if variable in clients and method != "get_paginator":
            calls.add((clients[variable], method))

    return calls


def _granted_actions() -> set[str]:
    """Every action string in the deploy role's policy documents, wildcards included."""
    grants = (ROOT / "infra" / "bootstrap" / "deploy_permissions.tf").read_text(encoding="utf-8")
    return set(re.findall(r'"([a-z0-9-]+:[A-Za-z0-9*]+)"', grants))


def _check_the_teardown_scripts_can_run() -> list[str]:
    """Every AWS call the teardown's own scripts make has a grant on the deploy role.

    **This is the check the first real teardown needed and did not have.** All five layer jobs
    destroyed cleanly and the run still reported failure, on `kms:ListKeys` — a call
    `scripts/estate_sweep.py` makes and no Terraform layer declares. `_check_permissions_exist`
    derives what the role needs from what the *layers* declare, so a call that lives only in a
    script is outside the list it is checking against, and every offline gate reported green
    while the teardown's final step could not run at all.

    That failure has the worst available shape: the estate was down, the money had stopped, and
    the report said otherwise. A teardown whose verdict is wrong in the reassuring direction is
    one thing; wrong in the alarming direction teaches somebody to stop reading it, which is how
    the next real leftover survives.

    **What a pass does not mean.** That the grant is *sufficient* — a resource-scoped or
    tag-conditioned statement matches here exactly as a `"*"` one does, and only a real call
    tells them apart. What it means is that no call has been forgotten entirely, which is the
    failure that actually happened.
    """
    granted = _granted_actions()
    problems: list[str] = []
    for script in TEARDOWN_SCRIPTS:
        source = (ROOT / "scripts" / script).read_text(encoding="utf-8")
        for service, method in sorted(_calls_made(source)):
            prefix = _IAM_PREFIX.get(service, service)
            action = _ACTION_FOR.get(
                (service, method), f"{prefix}:{''.join(part.title() for part in method.split('_'))}"
            )
            if not any(fnmatch(action, allowed) for allowed in granted):
                problems.append(
                    f"scripts/{script} calls {service}.{method} and the deploy role has no "
                    f"grant matching `{action}`. It is not in any layer, so nothing else looks "
                    f"for it — the teardown reaches its last step, is refused, and reports a "
                    f"failure over an estate that is already gone"
                )
    return problems


def _hcl_blocks(text: str, header: str) -> dict[str, str]:
    """Every `<header> "name" { ... }` and its body, matched by counting braces."""
    blocks: dict[str, str] = {}
    for match in re.finditer(rf'{header}\s+"(\w+)"\s*\{{', text):
        depth, start = 0, match.end() - 1
        for position in range(start, len(text)):
            if text[position] == "{":
                depth += 1
            elif text[position] == "}":
                depth -= 1
                if depth == 0:
                    blocks[match.group(1)] = text[start : position + 1]
                    break
    return blocks


def _check_every_vpc_function_can_attach_to_it() -> list[str]:
    """A function in a VPC has a role that may create a network interface.

    **Lambda creates the interface itself, using the execution role, before any code runs.** So
    the omission does not surface as a function that cannot reach the network — it surfaces as
    `CreateFunction` being refused outright, with `InvalidParameterValueException: The provided
    execution role does not have permissions to call CreateNetworkInterface on EC2`, minutes into
    an apply that has already built everything before it.

    Three functions in this layer carried the statement and the fourth did not. That is the
    shape the omission always has: the block is copied while there are two of it and reasoned
    about freshly when a new one is written, and the fresh reasoning is about what the *feature*
    needs — a tier to read from, a key to decrypt with — while attaching to the network is what
    the *runtime* needs and belongs to no feature at all.

    `terraform validate` cannot see it: the role exists, the policy is well-formed, and the
    reference resolves. Only an apply, or this, pairs the two.
    """
    problems: list[str] = []
    for layer in sorted((ROOT / "infra").iterdir()):
        if not layer.is_dir():
            continue
        text = "\n".join(path.read_text(encoding="utf-8") for path in sorted(layer.glob("*.tf")))
        documents = _hcl_blocks(text, r'data\s+"aws_iam_policy_document"')

        #: role name -> the policy documents attached to it, inline or managed.
        granted: dict[str, list[str]] = {}
        for body in _hcl_blocks(text, r'resource\s+"aws_iam_role_policy"').values():
            role = re.search(r"role\s*=\s*aws_iam_role\.(\w+)", body)
            document = re.search(r"data\.aws_iam_policy_document\.(\w+)", body)
            if role and document:
                granted.setdefault(role.group(1), []).append(documents.get(document.group(1), ""))

        for name, body in _hcl_blocks(text, r'resource\s+"aws_lambda_function"').items():
            if "vpc_config" not in body:
                continue
            role = re.search(r"role\s*=\s*aws_iam_role\.(\w+)", body)
            if not role:
                problems.append(
                    f"infra/{layer.name}: aws_lambda_function.{name} runs in a VPC and its role "
                    f"is not a reference this check can follow"
                )
                continue
            attached = granted.get(role.group(1), [])
            if not any("ec2:CreateNetworkInterface" in document for document in attached):
                problems.append(
                    f"infra/{layer.name}: aws_lambda_function.{name} has a vpc_config and "
                    f"aws_iam_role.{role.group(1)} cannot ec2:CreateNetworkInterface. Lambda "
                    f"makes the interface with this role before any code runs, so CreateFunction "
                    f"itself is refused — minutes into an apply, after everything before it built"
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
    problems.extend(_check_the_teardown_scripts_can_run())
    problems.extend(_check_every_vpc_function_can_attach_to_it())
    problems.extend(_check_every_layer_can_evaluate())
    problems.extend(_check_resolves_fail_loudly())
    problems.extend(_check_nothing_names_what_nothing_creates())
    problems.extend(_check_the_runtime_artefacts_are_deployed())
    problems.extend(_check_a_trigger_can_actually_fire())
    problems.extend(_check_actions_are_pinned())
    problems.extend(_check_every_env_reference_is_defined())
    problems.extend(_check_shell_variables_are_assigned_in_their_job())

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

    # **The environment is named, and that is a load-bearing statement rather than a policy.**
    #
    # The OIDC trust in `infra/bootstrap/oidc.tf` names
    # `repo:owner@id/repo@id:environment:deploy` and nothing else, so a run outside the
    # environment cannot obtain credentials at all. This is the part that cannot be forgotten:
    # remove the environment and the deploy stops working, loudly, at STS.
    #
    # What this check no longer claims is that the environment carries a **required reviewer**.
    # That protection was deliberately taken off — see `contracts/deploy/acceptance.yaml`, which
    # names who accepted it, why, and when it ends — and a check that went on printing
    # "protected" would be describing a control that is not there. Which is the whole failure
    # this repository exists to argue against.
    for name in applied:
        if deploy["jobs"][name].get("environment") != "deploy":
            problems.append(
                f"deploy.yml job {name!r} does not name the `deploy` environment. The OIDC "
                f"trust is scoped to it, so this job could not obtain credentials at all"
            )
    for name in destroyed:
        if destroy["jobs"][name].get("environment") != "destroy":
            problems.append(
                f"destroy.yml job {name!r} does not name the `destroy` environment. Same "
                f"reason: the trust is scoped to it"
            )

    problems.extend(_check_the_approval_removal_is_accepted())

    if problems:
        print(f"deploy-path: {len(problems)} problem(s)\n", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        return 1

    # **The sentence that used to be here, and why it is gone.**
    #
    # This line ended with "**Neither workflow has been dispatched.**" — printed as a result, by
    # a script that reads YAML files and has never spoken to GitHub. It could not have known. It
    # was true when it was written, it went false the moment `deploy.yml` was first dispatched,
    # and nothing about this check would have changed to say so.
    #
    # That is the exact defect this repository exists to argue against, in miniature: a control
    # that reports something outside its own evidence. What this script can prove is the shape
    # of the path — every applied layer torn down, in reverse, by human dispatch only, scoped to
    # the environment its trust names. Whether anyone has walked it is run history, which lives
    # in GitHub and in `docs/DECISIONS.md` 14, and it is not this gate's to assert.
    print(
        f"deploy-path: {len(applied)} layer(s) applied, all torn down in reverse order, both "
        f"workflows human-dispatch only, and every job scoped to the environment the OIDC trust "
        f"names. Required reviewers are OFF under a dated acceptance that expires "
        f"({_approval_expiry()}). This checks the path, not whether it has been walked — see "
        f"docs/DECISIONS.md 14 for what has actually been applied, and when."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

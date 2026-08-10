#!/usr/bin/env python3
"""What is still standing after the teardown, and whether that is allowed.

**The step this replaces printed a table and always passed.** `destroy.yml` ended by listing
everything tagged for this project and saying "anything listed above still exists and still
costs money" — then exited zero. A report that cannot go red is a report nobody reads twice,
and the failure it was written to catch is the one where a teardown succeeds and leaves an
estate behind.

Two sweeps, because neither is sufficient alone.

**By tag**, through the Resource Groups Tagging API: broad, service-agnostic, and blind to
anything that was never tagged. Every layer above bootstrap sets `manifest:project` through
provider default tags, so this is the sweep that finds a resource whose type nobody thought to
enumerate.

**By name**, service by service: narrow, and the only one that finds an *untagged* leftover —
which is the more likely kind, because a resource created outside Terraform is exactly the
resource nobody tagged. Anything whose name begins with the project prefix is this project's.

**What is allowed to remain, and how that list is decided.** `infra/bootstrap` is deliberately
not destroyed: it holds the state bucket, the lock table and the OIDC role this teardown
assumes, and a teardown that destroyed its own credentials halfway through would leave the rest
standing and unreachable. So bootstrap's resources must survive — and rather than hand-write
which ones, this reads **bootstrap's own Terraform state** and treats exactly what it created as
expected. A hand-written allowlist is a pattern that grows until it swallows a real leftover;
a state file cannot flatter itself.

Everything else is a finding, and a finding is a non-zero exit.
"""

from __future__ import annotations

import argparse
import re
import sys


#: Read-only sweeps, one per service this estate uses. Each yields the names of things that
#: belong to the project, judged by prefix. Written out per service rather than inferred,
#: because a service missing from this list is a service whose leftovers are invisible — and a
#: silent gap in a teardown check is worse than no check.
def _by_name(session, project: str) -> list[str]:
    return sorted(set(_core_services(session, project) + _edge_services(session, project)))


def _core_services(session, project: str) -> list[str]:
    """Storage, compute and orchestration — the layers that hold data or cost money per hour."""
    found: list[str] = []

    def add(kind: str, names) -> None:
        for name in names:
            if name.startswith(project):
                found.append(f"{kind}: {name}")

    s3 = session.client("s3")
    add("s3", (b["Name"] for b in s3.list_buckets().get("Buckets", ())))

    lambda_ = session.client("lambda")
    for page in lambda_.get_paginator("list_functions").paginate():
        add("lambda", (f["FunctionName"] for f in page.get("Functions", ())))

    states = session.client("stepfunctions")
    for page in states.get_paginator("list_state_machines").paginate():
        add("states", (m["name"] for m in page.get("stateMachines", ())))

    logs = session.client("logs")
    for page in logs.get_paginator("describe_log_groups").paginate():
        # Log groups are named by their service, not by the project, so the prefix test is on
        # the tail. `/aws/lambda/manifest-read-tier0` is this project's; `/aws/lambda/other` is
        # not, and this account holds other projects.
        for group in page.get("logGroups", ()):
            name = group["logGroupName"]
            if re.search(rf"(^|/){re.escape(project)}[-/]", name):
                found.append(f"logs: {name}")

    return found


def _edge_services(session, project: str) -> list[str]:
    """The registry, the tables, the queue, the network and the identities around them."""
    found: list[str] = []

    def add(kind: str, names) -> None:
        for name in names:
            if name.startswith(project):
                found.append(f"{kind}: {name}")

    ecr = session.client("ecr")
    for page in ecr.get_paginator("describe_repositories").paginate():
        add("ecr", (r["repositoryName"] for r in page.get("repositories", ())))

    ddb = session.client("dynamodb")
    for page in ddb.get_paginator("list_tables").paginate():
        add("dynamodb", page.get("TableNames", ()))

    sqs = session.client("sqs")
    queues = sqs.list_queues(QueueNamePrefix=project).get("QueueUrls", ())
    found.extend(f"sqs: {url.rsplit('/', 1)[-1]}" for url in queues)

    ec2 = session.client("ec2")
    vpcs = ec2.describe_vpcs(Filters=[{"Name": "tag:Name", "Values": [f"{project}*"]}])
    found.extend(f"vpc: {v['VpcId']}" for v in vpcs.get("Vpcs", ()))
    groups = ec2.describe_security_groups(
        Filters=[{"Name": "group-name", "Values": [f"{project}*"]}]
    )
    found.extend(f"sg: {g['GroupName']} ({g['GroupId']})" for g in groups.get("SecurityGroups", ()))

    glue = session.client("glue")
    for page in glue.get_paginator("get_databases").paginate():
        add("glue", (d["Name"] for d in page.get("DatabaseList", ())))

    iam = session.client("iam")
    for page in iam.get_paginator("list_roles").paginate():
        add("iam-role", (r["RoleName"] for r in page.get("Roles", ())))
    for page in iam.get_paginator("list_policies").paginate(Scope="Local"):
        add("iam-policy", (p["PolicyName"] for p in page.get("Policies", ())))

    ssm = session.client("ssm")
    for page in ssm.get_paginator("describe_parameters").paginate():
        for parameter in page.get("Parameters", ()):
            if parameter["Name"].lstrip("/").startswith(project):
                found.append(f"ssm: {parameter['Name']}")

    return found


def _tagged(session, project: str, layer: str | None = None) -> set[str]:
    """Every ARN carrying this project's tag, optionally narrowed to one layer.

    **The layer tag is the allowlist, and nobody wrote it by hand.** Each Terraform layer sets
    `manifest:layer` in its own provider block, so a resource's layer is a property the layer
    that created it declared. `bootstrap` is the one that survives a teardown — it holds the
    state bucket, the lock table and the role the teardown assumes — and asking for that tag is
    a question with an answer, where a hand-maintained list of bootstrap's resources would be a
    pattern that grows until it swallows a real leftover.

    The first version of this read bootstrap's Terraform state instead, which was wrong for a
    duller reason: bootstrap creates the state bucket, so it cannot use it as a backend. Its
    state is a file on the author's laptop, and a teardown check that only works on one machine
    is a teardown check that does not run."""
    filters = [{"Key": f"{project}:project", "Values": [project]}]
    if layer is not None:
        filters.append({"Key": f"{project}:layer", "Values": [layer]})
    found: set[str] = set()
    for page in (
        session.client("resourcegroupstaggingapi")
        .get_paginator("get_resources")
        .paginate(TagFilters=filters)
    ):
        found.update(entry["ResourceARN"] for entry in page.get("ResourceTagMappingList", ()))
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    arguments = parser.parse_args()

    import boto3  # noqa: PLC0415 - a teardown tool; the offline suite must import this module

    session = boto3.Session()
    identity = session.client("sts").get_caller_identity()
    region = session.region_name or ""

    keep_arns = _tagged(session, arguments.project, layer="bootstrap")
    tagged = sorted(_tagged(session, arguments.project))
    named = _by_name(session, arguments.project)

    leftover_tagged = sorted(a for a in tagged if a not in keep_arns)
    # The name sweep has no tags to consult, so a named resource is excused only when its name
    # appears inside one of bootstrap's ARNs. Deliberately a substring test: an ARN spells a
    # bucket as `arn:aws:s3:::name` and a role as `.../role/name`, and matching the whole ARN
    # would excuse nothing while looking like it worked.
    leftover_named = sorted(
        entry for entry in named if not any(entry.split(": ", 1)[1] in arn for arn in keep_arns)
    )

    print(f"estate sweep — project {arguments.project}, account {identity['Account']}, {region}\n")
    print(f"  bootstrap keeps   {len(keep_arns)}, by its own manifest:layer tag")
    print(f"  tagged, remaining {len(leftover_tagged)}")
    print(f"  named,  remaining {len(leftover_named)}\n")

    for entry in leftover_tagged:
        print(f"  TAGGED  {entry}")
    for entry in leftover_named:
        print(f"  NAMED   {entry}")

    if not leftover_tagged and not leftover_named:
        print(
            "\nnothing outside bootstrap is standing. The two sweeps disagree about nothing,\n"
            "which is the only result that means both of them ran."
        )
        return 0

    print(
        f"\n{len(leftover_tagged) + len(leftover_named)} resource(s) survived the teardown.\n"
        f"Each one costs money and each one is a defect in destroy.yml, not an accident — the\n"
        f"correction belongs in that file so the next teardown takes it without anybody\n"
        f"remembering to.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

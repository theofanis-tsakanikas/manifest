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


def _still_exists(session, arn: str) -> bool:
    """Ask the owning service whether this ARN is still there.

    **The tagging API's index lags deletion**, by minutes and sometimes longer. After a teardown
    it happily returns a security group, twelve VPC endpoints and a flow log that
    `describe-*` answers `NotFound` for. A report that lists things already gone is a report
    that cries wolf, and the entire point of making this one able to go red was so somebody
    would believe it.

    So every tagged ARN is confirmed against the service that owns it. Anything this function
    does not know how to check is reported as surviving: over-reporting costs a reader a minute,
    under-reporting costs money for as long as nobody looks.
    """
    #: An ARN is `arn:partition:service:region:account:resource`, so the resource is the sixth
    #: field and the service is the third. Named rather than indexed inline, because a bare `5`
    #: in a slice is the kind of thing that gets "tidied" into a `4`.
    service_field, resource_field = 2, 5
    parts = arn.split(":")
    service = parts[service_field]
    tail = parts[resource_field] if len(parts) > resource_field else ""
    kind, _, identifier = tail.partition("/")

    def gone(call, **kwargs) -> bool:
        """Absent if the call refuses **or** answers with nothing.

        Both halves are needed and the second was missing. Some of these APIs raise `NotFound`
        for an id that does not exist; others — `describe_flow_logs`,
        `describe_security_group_rules` — return an empty list and no error at all. Checking
        only for an exception reported a deleted flow log as surviving, which is a phantom in
        the one report whose entire value is that it can be believed.
        """
        try:
            answer = call(**kwargs)
        except Exception as error:
            return "NotFound" in error.__class__.__name__ or "NotFound" in str(error)
        if isinstance(answer, dict):
            listed = [value for value in answer.values() if isinstance(value, list)]
            if listed and not any(listed):
                return True
        return False

    ec2 = None
    if service == "ec2":
        ec2 = session.client("ec2")
        checks = {
            "vpc-endpoint": lambda i: ec2.describe_vpc_endpoints(VpcEndpointIds=[i]),
            "security-group": lambda i: ec2.describe_security_groups(GroupIds=[i]),
            "security-group-rule": lambda i: ec2.describe_security_group_rules(
                SecurityGroupRuleIds=[i]
            ),
            "subnet": lambda i: ec2.describe_subnets(SubnetIds=[i]),
            "vpc": lambda i: ec2.describe_vpcs(VpcIds=[i]),
            "route-table": lambda i: ec2.describe_route_tables(RouteTableIds=[i]),
            "vpc-flow-log": lambda i: ec2.describe_flow_logs(FlowLogIds=[i]),
            "network-interface": lambda i: ec2.describe_network_interfaces(NetworkInterfaceIds=[i]),
        }
        check = checks.get(kind)
        return not gone(lambda: check(identifier)) if check else True
    if service == "s3":
        return not gone(session.client("s3").head_bucket, Bucket=parts[resource_field])
    if service == "lambda":
        return not gone(session.client("lambda").get_function, FunctionName=identifier)
    if service == "sqs":
        return not gone(session.client("sqs").get_queue_url, QueueName=parts[resource_field])
    if service == "dynamodb":
        return not gone(session.client("dynamodb").describe_table, TableName=identifier)
    if service == "states":
        return not gone(session.client("stepfunctions").describe_state_machine, stateMachineArn=arn)
    if service == "logs":
        groups = session.client("logs").describe_log_groups(logGroupNamePrefix=tail.split(":")[-1])
        return bool(groups.get("logGroups"))
    if service == "ecr":
        return not gone(session.client("ecr").describe_repositories, repositoryNames=[identifier])
    if service == "events":
        return not gone(session.client("events").describe_rule, Name=identifier)
    if service == "athena":
        return not gone(session.client("athena").get_work_group, WorkGroup=identifier)
    if service == "sns":
        return not gone(session.client("sns").get_topic_attributes, TopicArn=arn)
    # Unknown service: report it. The direction that costs money is the one that stays silent.
    return True


def _bootstrap_names(session, project: str) -> set[str]:
    """Bootstrap's IAM roles and policies, by name, asked of IAM rather than of the tagging API.

    **The name sweep reported bootstrap's own identities as leftovers.** The tag sweep excused
    them correctly — they carry `manifest:layer = bootstrap` — but the tagging API does not
    return IAM policies reliably, so their ARNs were absent from the exclusion set and the name
    sweep listed all nine as survivors. A teardown report that cries wolf about the one layer
    that is *supposed* to survive is a report somebody stops reading, and a report nobody reads
    is the same as the one that could not go red.

    IAM is asked directly instead, which is the service that actually knows.
    """
    iam = session.client("iam")
    keep: set[str] = set()
    for page in iam.get_paginator("list_roles").paginate():
        for role in page.get("Roles", ()):
            if not role["RoleName"].startswith(project):
                continue
            tags = iam.list_role_tags(RoleName=role["RoleName"]).get("Tags", ())
            if any(t["Key"] == f"{project}:layer" and t["Value"] == "bootstrap" for t in tags):
                keep.add(role["RoleName"])
    for page in iam.get_paginator("list_policies").paginate(Scope="Local"):
        for policy in page.get("Policies", ()):
            if not policy["PolicyName"].startswith(project):
                continue
            tags = iam.list_policy_tags(PolicyArn=policy["Arn"]).get("Tags", ())
            if any(t["Key"] == f"{project}:layer" and t["Value"] == "bootstrap" for t in tags):
                keep.add(policy["PolicyName"])
    return keep


def _keys_pending_deletion(session) -> set[str]:
    """Keys already scheduled for deletion, which is as removed as a KMS key can be made.

    KMS enforces a waiting period of seven to thirty days and refuses to delete sooner.
    Reporting those keys as survivors would end every teardown red for doing exactly what it
    was told — the fastest way to teach somebody to ignore the report.
    """
    kms = session.client("kms")
    pending: set[str] = set()
    for page in kms.get_paginator("list_keys").paginate():
        for entry in page.get("Keys", ()):
            try:
                metadata = kms.describe_key(KeyId=entry["KeyId"])["KeyMetadata"]
            except Exception as error:
                # A key this account can list and cannot describe belongs to another principal,
                # and is not ours to report either way. Printed rather than passed over in
                # silence: a sweep that quietly skipped keys would under-report, and
                # under-reporting is the direction that costs money.
                print(f"  (skipping key {entry['KeyId']}: {error.__class__.__name__})")
                continue
            if metadata.get("KeyState") == "PendingDeletion":
                pending.add(metadata["Arn"])
    return pending


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
    keep_names = _bootstrap_names(session, arguments.project)
    tagged = sorted(_tagged(session, arguments.project))
    named = _by_name(session, arguments.project)

    # A key scheduled for deletion is not a key left standing.
    pending = _keys_pending_deletion(session)
    leftover_tagged = sorted(
        a for a in tagged if a not in keep_arns and a not in pending and _still_exists(session, a)
    )
    # The name sweep has no tags to consult, so a named resource is excused only when its name
    # appears inside one of bootstrap's ARNs. Deliberately a substring test: an ARN spells a
    # bucket as `arn:aws:s3:::name` and a role as `.../role/name`, and matching the whole ARN
    # would excuse nothing while looking like it worked.
    leftover_named = sorted(
        entry
        for entry in named
        if entry.split(": ", 1)[1] not in keep_names
        and not any(entry.split(": ", 1)[1] in arn for arn in keep_arns)
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

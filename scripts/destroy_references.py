#!/usr/bin/env python3
"""Resolve a layer's published references for a teardown, or stand in for them.

**The defect this closes made the teardown un-retryable, which is the worst property a
teardown can have.**

Every layer resolves its neighbours' outputs from SSM before running `terraform destroy`,
because a destroy evaluates the configuration and the variables have to have values. Foundation
runs last, correctly — it holds the keys and the network everything else is encrypted with and
attached to — and it takes its own parameters with it.

So after a teardown that failed part-way, which is the only kind anybody re-runs, the layers
above can no longer resolve anything and every one of them stops before Terraform starts. The
estate sits in the one state where the workflow written to remove it refuses to run: on the
first real teardown, 46 resources standing and no path through the CI to any of them.

**Why placeholders are correct here and would be wrong in a deploy.** A deploy that cannot
resolve the foundation must stop: it is about to build on values it does not have. A destroy is
*removing* those resources, so the values only have to satisfy Terraform's variable validation —
nothing is read from them. `destroy.yml` already reasons exactly this way about
`reader_image_digest`, which is passed as all zeros so that it satisfies the format and could
never name a real image.

The placeholders are well-formed and obviously unreal for the same reason: if one is ever used
by something that matters, it fails loudly rather than pointing at a resource that exists.
"""

from __future__ import annotations

import argparse
import os
import sys

#: What each layer publishes, and what stands in when it is already gone. Names are the SSM
#: parameter names under `/<project>/<layer>/`; values are placeholders shaped like the real
#: thing. `{account}` and `{region}` are filled in from the caller.
PLACEHOLDERS: dict[str, dict[str, str]] = {
    "foundation": {
        "vpc_id": "vpc-00000000000000000",
        "private_subnet_ids": '["subnet-00000000000000000"]',
        "endpoint_security_group_id": "sg-00000000000000000",
        "data_key_arn": "arn:aws:kms:{region}:{account}:key/00000000-0000-0000-0000-000000000000",
        "logs_key_arn": "arn:aws:kms:{region}:{account}:key/00000000-0000-0000-0000-000000000000",
        "landing_bucket": "{project}-landing-{account}",
        "records_bucket": "{project}-records-{account}",
        "evidence_bucket": "{project}-evidence-{account}",
        "access_logs_bucket": "{project}-access-logs-{account}",
        "alerts_topic_arn": "arn:aws:sns:{region}:{account}:{project}-alerts",
        "reader_repository_url": "{account}.dkr.ecr.{region}.amazonaws.com/{project}-reader",
    },
    "extraction": {
        "state_machine_arn": "arn:aws:states:{region}:{account}:stateMachine:{project}-extraction",
        "review_queue_arn": "arn:aws:sqs:{region}:{account}:{project}-review",
        "review_queue_url": "https://sqs.{region}.amazonaws.com/{account}/{project}-review",
        "decisions_table_arn": "arn:aws:dynamodb:{region}:{account}:table/{project}-decisions",
        "ledger_table_arn": (
            "arn:aws:dynamodb:{region}:{account}:table/{project}-reprocessing-ledger"
        ),
        "ledger_table_name": "{project}-reprocessing-ledger",
    },
    "lakehouse": {
        "lake_bucket": "{project}-lake-{account}",
        "glue_database": "{project}_records",
        "athena_workgroup": "{project}-analysis",
    },
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--layer", required=True, choices=sorted(PLACEHOLDERS))
    parser.add_argument("--region", required=True)
    parser.add_argument("--account", required=True)
    arguments = parser.parse_args()

    import boto3  # noqa: PLC0415 - a teardown tool; the offline suite imports this module

    ssm = boto3.client("ssm")
    prefix = f"/{arguments.project}/{arguments.layer}/"
    resolved: dict[str, str] = {}
    paginator = ssm.get_paginator("get_parameters_by_path")
    for page in paginator.paginate(Path=prefix.rstrip("/"), Recursive=True):
        for parameter in page.get("Parameters", ()):
            resolved[parameter["Name"].removeprefix(prefix)] = parameter["Value"]

    substitutions = {
        "project": arguments.project,
        "region": arguments.region,
        "account": arguments.account,
    }
    stood_in: list[str] = []
    for name, template in PLACEHOLDERS[arguments.layer].items():
        if name not in resolved:
            resolved[name] = template.format(**substitutions)
            stood_in.append(name)

    lines = "".join(f"TF_VAR_{name}={value}\n" for name, value in sorted(resolved.items()))
    destination = os.environ.get("GITHUB_ENV")
    if destination:
        with open(destination, "a", encoding="utf-8") as handle:
            handle.write(lines)
    else:
        sys.stdout.write(lines)

    real = len(resolved) - len(stood_in)
    print(f"{arguments.layer}: {real} reference(s) resolved from SSM", file=sys.stderr)
    if stood_in:
        print(
            f"::warning::{arguments.layer}: {len(stood_in)} reference(s) are placeholders — "
            f"{', '.join(sorted(stood_in))}. This is a re-run after a partial teardown, or the "
            f"layer is already gone. A destroy removes these resources, so the values need only "
            f"satisfy variable validation; nothing reads them.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

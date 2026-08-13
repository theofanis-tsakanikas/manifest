#!/usr/bin/env python3
"""Submit one bulk reprocessing run, and refuse to submit one nobody looked at.

**An operation, not a deploy.** Deploys go through CI because they change the estate; this
changes nothing and spends money — a different kind of decision, made by a person, with the plan
in front of them. So this prints what the run would do, and submits only when told to twice: once
by running it, once by `--submit`.

**The plan comes from the same pure function the job uses.** `manifest.core.scale.plan` is
called here against the real ledger and the real landing bucket, so the counts printed are the
counts the job will act on rather than an estimate of them. A submitter that guessed would be a
second implementation of the one decision claim 7 is about.

**It cannot be made to re-do work.** There is no "force" flag and none is wanted: a document
already recorded at this reader is planned as `SKIP` before any executor sees it, and the way to
make the system read it again is to change the reader — which is a new version, a diff, and a
ledger entry, exactly as doctrine rule 4 requires.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

GREEN, RED, DIM, RESET = "\033[32m", "\033[31m", "\033[2m", "\033[0m"

#: Where the driver script and the packaged code live in the records bucket.
JOB_PREFIX = "jobs/reprocess"

#: The Spark submit parameters. Small on purpose: the work is waiting on Step Functions rather
#: than computing, so executors are cheap and few, and a large default is a large bill for a job
#: that spends its life polling.
SUBMIT_PARAMETERS = (
    "--conf spark.executor.cores=2 "
    "--conf spark.executor.memory=4g "
    "--conf spark.executor.instances=2 "
    "--conf spark.driver.cores=2 "
    "--conf spark.driver.memory=4g"
)

#: **The region, on both sides, because a custom image does not inherit one.**
#:
#: EMR Serverless sets `AWS_REGION` in its own release image. A custom image is a different
#: image, and the interpreter installed into it reads an environment that has none — so every
#: boto3 client in the driver raised `NoRegionError: You must specify a region`, which reads as
#: a credential problem and is a missing environment variable.
#:
#: Driver *and* executors: the driver plans and reads the ledger, the executors start state
#: machine executions, and both make AWS calls. Setting one leaves the other failing later, in a
#: task rather than at start-up, which is the more expensive half to debug.
REGION_PARAMETERS = (
    "--conf spark.emr-serverless.driverEnv.AWS_REGION={region} "
    "--conf spark.emr-serverless.driverEnv.AWS_DEFAULT_REGION={region} "
    "--conf spark.executorEnv.AWS_REGION={region} "
    "--conf spark.executorEnv.AWS_DEFAULT_REGION={region}"
)


def _client(name: str):
    import boto3  # noqa: PLC0415 - the offline suite imports this module without AWS

    return boto3.client(name)


def _region() -> str:
    """The region this submitter is talking to, handed on to the job.

    Read from the session rather than written down: a constant here would be a second place the
    estate's region lives, and the first one is whatever the caller's credentials point at.
    """
    import boto3  # noqa: PLC0415

    region = boto3.Session().region_name
    if not region:
        raise SystemExit(
            "this session has no region, so the job would be submitted without one and every "
            "boto3 client on the driver would raise NoRegionError. Set AWS_REGION."
        )
    return region


def _reference(project: str, path: str) -> str:
    return _client("ssm").get_parameter(Name=f"/{project}/{path}")["Parameter"]["Value"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default="manifest")
    parser.add_argument(
        "--reader",
        required=True,
        help="The reader identity to process at, e.g. 'tesseract 5.5.0'. Never guessed.",
    )
    parser.add_argument("--partition-size", type=int, default=200)
    parser.add_argument(
        "--submit",
        action="store_true",
        help="Actually start the run. Without it this prints the plan and stops.",
    )
    arguments = parser.parse_args(argv)

    from pipelines.reprocess import _read_ledger_table, _sources, read_ledger  # noqa: PLC0415

    from manifest.core.scale import Disposition, plan  # noqa: PLC0415

    landing = _reference(arguments.project, "foundation/landing_bucket")
    records = _reference(arguments.project, "foundation/records_bucket")
    application = _reference(arguments.project, "batch/application_id")
    job_role = _reference(arguments.project, "batch/job_role_arn")
    ledger_table = _reference(arguments.project, "extraction/ledger_table_name")
    state_machine = _reference(arguments.project, "extraction/state_machine_arn")

    sources = _sources(landing)
    documents = sorted(key for key in sources if key != "__bucket__")
    ledger = read_ledger(_read_ledger_table(ledger_table))
    the_plan = plan(documents, ledger, arguments.reader)

    print(f"{DIM}application {application}, reader {arguments.reader!r}{RESET}")
    print(f"  {len(documents)} document(s) in {landing}, {len(ledger)} ledger entry/entries")
    for disposition in Disposition:
        items = the_plan.of(disposition)
        print(f"  {disposition.value:<9} {len(items)}")
    print(f"  work: {the_plan.work}")

    if not the_plan.work:
        print(
            f"\n{GREEN}nothing to do{RESET} — every document is already recorded at this reader. "
            f"That is claim 7's idempotence arriving before a cluster is started rather than "
            f"after, which is where it is worth having."
        )
        return 0

    if not arguments.submit:
        print(f"\n{DIM}not submitted. Re-run with --submit to start it.{RESET}")
        return 0

    entry_point = f"s3://{records}/{JOB_PREFIX}/reprocess.py"
    packaged = f"s3://{records}/{JOB_PREFIX}/manifest.zip"
    started = _client("emr-serverless").start_job_run(
        applicationId=application,
        executionRoleArn=job_role,
        name=f"reprocess-{arguments.reader.replace(' ', '-')}",
        jobDriver={
            "sparkSubmit": {
                "entryPoint": entry_point,
                "entryPointArguments": [
                    "--reader",
                    arguments.reader,
                    "--landing-bucket",
                    landing,
                    "--ledger-table",
                    ledger_table,
                    "--state-machine",
                    state_machine,
                    "--partition-size",
                    str(arguments.partition_size),
                    # The one place the dry run is turned off, and it takes an explicit flag on
                    # the submitter to get here. `reprocess.py` defaults to planning and
                    # printing, so a job started by accident costs a cluster and changes nothing.
                    "--no-dry-run",
                ],
                "sparkSubmitParameters": (
                    f"{SUBMIT_PARAMETERS} "
                    f"{REGION_PARAMETERS.format(region=_region())} "
                    f"--py-files {packaged}"
                ),
            }
        },
        configurationOverrides={
            "monitoringConfiguration": {
                "s3MonitoringConfiguration": {"logUri": f"s3://{records}/{JOB_PREFIX}/logs/"}
            }
        },
    )
    print(f"\n{GREEN}submitted{RESET} {started['jobRunId']}")
    print(json.dumps({"application": application, "run": started["jobRunId"]}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Empty a versioned bucket completely, so the teardown can delete it.

**The defect this replaces.** `destroy.yml` emptied its buckets with
`aws s3 rm "s3://$BUCKET" --recursive || true`. Every bucket in this estate has versioning
enabled, and on a versioned bucket that command deletes nothing — it writes a *delete marker*
over each current version and leaves the noncurrent versions in place. The bucket is then
fuller than it started. `terraform destroy` fails on `BucketNotEmpty`, four jobs into a
teardown, and `|| true` has already thrown away the only message that would have explained it.

That is precisely how an estate gets left standing: a teardown that ran, reported success on
the step that mattered, and removed nothing.

**Why a deletion tool gets a refusal before it gets a feature.** This deletes customs records
irreversibly, and it is invoked by a workflow that has permission to do it across an account
shared with other projects. So it refuses any bucket whose name does not begin with the prefix
passed as `--project`, and the workflow passes a name it read from SSM or a Terraform output
rather than one anybody typed. A tool that could be pointed anywhere is one wrong variable away
from being pointed somewhere else.

**It fails loudly.** A bucket that would not empty is the teardown's most important finding,
not a line to swallow. The exit code is non-zero and the count of what remains is printed.
"""

from __future__ import annotations

import argparse
import sys

#: S3 accepts at most this many keys per delete request. Documented limit, not a chosen batch
#: size — asking for more is an API error rather than a slow request.
BATCH = 1000


def _refuse_foreign(bucket: str, project: str) -> None:
    if not project:
        raise SystemExit("--project is required. Without it this tool has no bucket it refuses.")
    if not bucket.startswith(f"{project}-"):
        raise SystemExit(
            f"refusing {bucket!r}: it does not begin with {project + '-'!r}.\n\n"
            f"This account holds other projects' buckets. A teardown that can empty any bucket "
            f"it is handed is one wrong variable away from emptying somebody else's, and the "
            f"failure would be silent and total."
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bucket")
    parser.add_argument("--project", required=True, help="the only prefix this will act on")
    parser.add_argument(
        "--missing-ok",
        action="store_true",
        help="a bucket that is already gone is success, not failure",
    )
    arguments = parser.parse_args()
    _refuse_foreign(arguments.bucket, arguments.project)

    import boto3  # noqa: PLC0415 - a teardown tool; the offline suite must import this module
    from botocore.exceptions import ClientError  # noqa: PLC0415

    s3 = boto3.client("s3")
    removed = 0
    markers = 0

    paginator = s3.get_paginator("list_object_versions")
    try:
        pages = paginator.paginate(Bucket=arguments.bucket)
        batch: list[dict[str, str]] = []
        for page in pages:
            # Versions and delete markers are two lists and both have to go. Deleting only the
            # first is the failure this script exists to fix, one level down: a bucket holding
            # nothing but delete markers is still not an empty bucket.
            for entry in page.get("Versions", ()):
                batch.append({"Key": entry["Key"], "VersionId": entry["VersionId"]})
                removed += 1
            for entry in page.get("DeleteMarkers", ()):
                batch.append({"Key": entry["Key"], "VersionId": entry["VersionId"]})
                markers += 1
            while len(batch) >= BATCH:
                _delete(s3, arguments.bucket, batch[:BATCH])
                batch = batch[BATCH:]
        if batch:
            _delete(s3, arguments.bucket, batch)
    except ClientError as error:
        code = error.response.get("Error", {}).get("Code", "")
        if code in {"NoSuchBucket", "404"} and arguments.missing_ok:
            print(f"{arguments.bucket}: already gone")
            return 0
        print(f"{arguments.bucket}: {error}", file=sys.stderr)
        return 1

    # Proving it rather than assuming it. The delete calls can partially succeed, and a teardown
    # that reports "emptied" on a bucket with objects left in it is the exact class of lie this
    # replaces.
    remaining = s3.list_object_versions(Bucket=arguments.bucket, MaxKeys=1)
    left = len(remaining.get("Versions", ())) + len(remaining.get("DeleteMarkers", ()))
    print(f"{arguments.bucket}: removed {removed} version(s) and {markers} delete marker(s)")
    if left:
        print(
            f"{arguments.bucket}: still not empty. The destroy that follows would fail on "
            f"BucketNotEmpty, so this fails here instead, where the reason is visible.",
            file=sys.stderr,
        )
        return 1
    return 0


def _delete(client: object, bucket: str, objects: list[dict[str, str]]) -> None:
    response = client.delete_objects(  # type: ignore[attr-defined]
        Bucket=bucket, Delete={"Objects": objects, "Quiet": True}
    )
    errors = response.get("Errors", ())
    if errors:
        first = errors[0]
        raise SystemExit(
            f"{bucket}: {len(errors)} object(s) refused deletion, first is "
            f"{first.get('Key')!r}: {first.get('Code')} {first.get('Message')}"
        )


if __name__ == "__main__":
    raise SystemExit(main())

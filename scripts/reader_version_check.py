#!/usr/bin/env python3
"""The image's reader and the recording's reader are the same reader.

**The failure this exists to make impossible.**

Every threshold in this repository is derived from `recordings/ocr/manifest.json`, and the
extraction handler looks its thresholds up by the *exact* identity of the reader that produced
the reading in front of it — `thresholds/reference-ocr@tesseract-5.5.2.json`. Two readers' 0.8
are different events, so keying by identity is right, and a deployment carrying thresholds for
a reader other than the one reading is refused rather than approximated.

That refusal is correct and it arrives far too late to be useful. It arrives after the image
builds, after the functions are created, after the trigger fires, on the first real document —
and it arrives wearing an IAM error's clothes, because the handler has no permission to list
the bucket and so a missing object and a denied one are the same response.

Which is exactly what happened. The recording was made on the author's laptop by Homebrew's
`tesseract 5.5.2`; the image is Debian, which carries `5.5.0`; the image asserted only the
*series* and let it through. Four layers and one green deploy later, the first document failed
with `s3:ListBucket is not authorized`, naming nothing.

**What this checks, and why it is only this.** The `EXPECTED_READER_VERSION` argument in
`Dockerfile` and the `reader_version` field in the recording must be the same string. Not
compatible, not the same series — the same string. The image asserts that argument against the
binary it actually installed, so the chain closes: binary equals argument equals recording, and
every link fails loudly on its own.

It runs offline, in CI, on every push, and needs neither the binary nor a build.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = ROOT / "Dockerfile"
RECORDING = ROOT / "recordings" / "ocr" / "manifest.json"

#: The argument, at the start of a line so a mention in a comment cannot satisfy it. Both quoted
#: and bare forms are accepted because Docker accepts both, and a check that only understood the
#: form in the file today would pass silently the day somebody removed the quotes.
DECLARATION = re.compile(
    r'^ARG\s+EXPECTED_READER_VERSION=(?:"(?P<quoted>[^"]*)"|(?P<bare>\S+))\s*$',
    re.MULTILINE,
)


def main() -> int:
    if not DOCKERFILE.exists():
        print(f"{DOCKERFILE} does not exist", file=sys.stderr)
        return 1
    if not RECORDING.exists():
        print(
            f"{RECORDING} does not exist. There is no recording, so there is nothing for the "
            f"image's reader to agree with, and every threshold in this repository is derived "
            f"from a file that is not here.",
            file=sys.stderr,
        )
        return 1

    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    matches = DECLARATION.findall(dockerfile)
    if not matches:
        print(
            "Dockerfile declares no EXPECTED_READER_VERSION. Without it the image installs "
            "whatever the base carries, and the first document after a deploy fails on a "
            "threshold artefact keyed to a reader that never ran.",
            file=sys.stderr,
        )
        return 1
    if len(matches) > 1:
        print(
            f"Dockerfile declares EXPECTED_READER_VERSION {len(matches)} times. Two "
            f"declarations mean the last one wins and the other is decoration a reader will "
            f"trust.",
            file=sys.stderr,
        )
        return 1
    declared = (matches[0][0] or matches[0][1]).strip()

    recorded = str(json.loads(RECORDING.read_text(encoding="utf-8"))["reader_version"]).strip()

    if declared != recorded:
        print(
            f"the image expects the reader to be '{declared}' and the recording was made by "
            f"'{recorded}'.\n\n"
            f"Every threshold in recordings/thresholds.json is a statement about "
            f"'{recorded}', and the deployed handler asks for its thresholds by the identity "
            f"of the reader that produced the reading — so this deployment would build, apply, "
            f"trigger, and then fail to find an artefact for a reader nothing shipped one for.\n\n"
            f"Re-record through .github/workflows/record.yml, which runs the reader inside this "
            f"image. Read the movement it prints, per field, with N. Then commit the recording, "
            f"the derived thresholds and this ARG in one commit.",
            file=sys.stderr,
        )
        return 1

    print(f"the image's reader and the recording's reader are both '{recorded}'")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

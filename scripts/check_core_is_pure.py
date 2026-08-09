#!/usr/bin/env python3
"""The core imports no cloud SDK, reads no ambient state, and names no engine.

The rule and the reasoning live in `src/manifest/gates/core_purity.py`. This is the runner:
`make core-pure`, a CI step, a preflight check, and the target of three mutations in
`scripts/gate_proof.py`.
"""

from __future__ import annotations

import sys
from pathlib import Path

from manifest.gates.core_purity import report, scan

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "src" / "manifest" / "core"


def main() -> int:
    findings = scan(CORE, ROOT)
    print(report(findings), file=sys.stderr if findings else sys.stdout)
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())

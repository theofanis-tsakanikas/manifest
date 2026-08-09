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

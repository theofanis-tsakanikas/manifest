"""The optional-layers check may not touch the layer it inspects.

**This is a regression guard for a defect that reported a pass and then broke the deploy.**

`scripts/check_optional_layers_plan.py` used to run `terraform init -reconfigure` in the layer's
own directory to pick up a local-backend override. It removed the override afterwards and left
`.terraform` initialised against the local backend — so `deploy.yml`, whose next command is
`terraform apply` against the S3 backend it had configured one line earlier, failed with
*"Backend type changed from local to s3"*. The check had already printed a pass.

Nothing offline could see it: the check's own exit code was zero, `terraform validate` does not
read `.terraform`, and the damage is a side effect on a directory rather than a value anybody
compares. What is checkable offline is the *shape* — the check operates on a copy, so no
terraform invocation in it may be pointed at `infra/`.

A stricter test would run the check and compare the directory before and after, and it is not
this one on purpose: the check needs credentials, and the offline suite has none.
"""

from __future__ import annotations

import re
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_optional_layers_plan.py"


def test_no_terraform_command_is_aimed_at_the_real_layer() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    aimed_at_the_layer = re.findall(r'f?"-chdir=infra/[^"]*"', source)
    assert not aimed_at_the_layer, (
        f"{SCRIPT.name} runs terraform against {aimed_at_the_layer} — the real layer directory. "
        f"An init there rewrites the backend the caller configured, and the caller is a deploy "
        f"whose next command is an apply against it"
    )


def test_the_backend_override_is_written_into_the_copy() -> None:
    """The override lands beside the copy, never beside the configuration under version control.

    An override left in `infra/<layer>/` after a crash is a layer that silently plans and
    applies against local state — the same defect with a longer fuse, because it survives the
    run that created it.
    """
    source = SCRIPT.read_text(encoding="utf-8")
    assert 'ROOT / "infra" / layer / "zz_local_backend_override.tf"' not in source, (
        "the local-backend override is written into the version-controlled layer. If the process "
        "dies before it is removed, every later plan and apply in that directory reasons about "
        "an empty state and reports every resource as new"
    )
    assert '(destination / "zz_local_backend_override.tf")' in source

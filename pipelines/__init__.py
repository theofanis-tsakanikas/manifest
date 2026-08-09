"""The jobs that would run on the estate. Written, validated, never executed.

Every one of these is a **thin adapter** over `manifest.core`. That is the same rule the
extraction adapters follow and it is load-bearing for the same reason: the logic a claim is
about must be checkable on a laptop with no cluster, so the cluster gets the scheduling and the
core keeps the decisions.

`scripts/check_job_is_thin.py` reads the import graph and refuses a job that decides anything
itself.
"""

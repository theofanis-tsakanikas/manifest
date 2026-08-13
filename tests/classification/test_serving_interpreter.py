"""The two files that ship inside SageMaker's container must run on **its** Python, not ours.

**Found by a 500 on the endpoint's first real request.** `artefact.py` exists to remove a version
coupling — the container serves scikit-learn 1.2.1 and this repository runs 3.12, so the artefact
is JSON rather than a pickle. What that argument missed is that the *scorer* is copied into the
container too, and the container is **Python 3.9**. `zip(..., strict=True)` is 3.10. The endpoint
came up `InService`, answered `ping`, and returned `TypeError: zip() takes no keyword arguments`
to the first question anybody asked it.

Nothing else in this repository has this constraint: every other module runs on an interpreter
`pyproject.toml` declares. These two run on one AWS chose, and that fact was in a comment in a
Terraform file rather than anywhere a check could reach it.

**What this does not do.** It is not a general 3.9 compatibility checker — writing one would be
a project. It refuses the constructs that are *plausible in this code* and silent until called:
each one parses on 3.9 or raises only at runtime, which is what makes them expensive.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

#: The two modules `scripts/train_classifier.py` copies into `model.tar.gz`. Read from the
#: packaging rather than listed by hand — a third module added there would otherwise ship
#: unchecked, which is the shape of every other defect this repository has recorded.
SHIPPED = tuple(
    sorted(Path("src/manifest/classification") / name for name in ("inference.py", "artefact.py"))
)

#: What SageMaker's scikit-learn 1.2-1 container runs. Read out of its own log line:
#: `/miniconda3/lib/python3.9/site-packages/...`.
CONTAINER_PYTHON = (3, 9)


def test_the_packaging_and_this_check_name_the_same_files() -> None:
    """A module added to the archive and not to `SHIPPED` would ship unchecked."""
    packaging = Path("scripts/train_classifier.py").read_text(encoding="utf-8")

    for module in SHIPPED:
        assert module.name in packaging


@pytest.mark.parametrize("module", SHIPPED, ids=lambda path: path.name)
def test_no_call_uses_a_keyword_the_container_does_not_have(module: Path) -> None:
    """`zip(strict=)` is 3.10, `math.exp` is not, and only one of them raises at runtime.

    A `TypeError` for an unexpected keyword happens when the line executes, so a build, a deploy
    and a health check all pass before anybody sees it.
    """
    too_new = {"zip": {"strict"}, "map": {"strict"}}

    for node in ast.walk(ast.parse(module.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        forbidden = too_new.get(node.func.id, set())
        used = {keyword.arg for keyword in node.keywords}
        offending = sorted(forbidden & used)
        assert not offending, (
            f"{module}:{node.lineno} calls {node.func.id}() with {offending}, which "
            f"Python {CONTAINER_PYTHON[0]}.{CONTAINER_PYTHON[1]} does not accept. This file is "
            f"copied into SageMaker's container and runs on its interpreter, not on ours — the "
            f"failure is a 500 on a request, not an error at build time"
        )


@pytest.mark.parametrize("module", SHIPPED, ids=lambda path: path.name)
def test_no_syntax_the_container_cannot_parse(module: Path) -> None:
    """`match`, and unions written outside an annotation.

    Annotations are safe — both files carry `from __future__ import annotations`, so every one is
    a string the container never evaluates. A `X | Y` in a *default* or a runtime `isinstance`
    would be evaluated, and 3.9 raises `TypeError: unsupported operand type(s)`.
    """
    tree = ast.parse(module.read_text(encoding="utf-8"))
    annotations = {
        id(node.annotation)
        for node in ast.walk(tree)
        if isinstance(node, ast.AnnAssign | ast.arg) and node.annotation is not None
    } | {id(node.returns) for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}

    for node in ast.walk(tree):
        assert not isinstance(node, ast.Match), (
            f"{module}:{node.lineno} uses a match statement, which Python 3.9 cannot parse. "
            f"The container would fail to import the module at all"
        )
        if (
            isinstance(node, ast.BinOp)
            and isinstance(node.op, ast.BitOr)
            and id(node) not in annotations
            and any(isinstance(side, ast.Name | ast.Constant) for side in (node.left, node.right))
        ):
            # Names only: `a | b` on two sets is fine and common, and this cannot tell them apart
            # without types. Reported rather than asserted-on blindly would be worse — a check
            # nobody trusts is a check nobody reads — so it is narrow: capitalised names, which
            # is what a type looks like.
            names = [
                side.id
                for side in (node.left, node.right)
                if isinstance(side, ast.Name) and side.id[:1].isupper()
            ]
            assert not names, (
                f"{module}:{node.lineno} builds a union of {names} outside an annotation. "
                f"Python 3.9 evaluates it and raises; this file runs on the container's "
                f"interpreter, not on ours"
            )


@pytest.mark.parametrize("module", SHIPPED, ids=lambda path: path.name)
def test_the_module_carries_the_future_import_that_makes_its_annotations_free(module: Path) -> None:
    """Without it, every `dict[str, Any]` in a signature is evaluated on import.

    3.9 accepts `dict[str, Any]` — that one is 3.9 — but not `X | Y`, and the files are annotated
    freely on the assumption that annotations cost nothing. This is what makes that true.
    """
    tree = ast.parse(module.read_text(encoding="utf-8"))
    futures = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "__future__"
        for alias in node.names
    }

    assert "annotations" in futures

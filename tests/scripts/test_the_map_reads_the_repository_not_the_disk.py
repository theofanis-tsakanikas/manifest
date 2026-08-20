"""The map check, attacked at the oracle that made it answer differently on different machines.

`check_the_map_matches_the_ground.py` asserts that every repository path the prose names is
really here. For as long as it existed it asked `Path.exists()`, and the disk is not the
repository: `corpus/rendered/` and `infra/bootstrap/terraform.tfvars` are git-ignored, sit on the
author's laptop, and are absent from every clone. So the check was **green locally and red in
CI, on the same commit**, for nineteen hours — and the answer nobody could inspect was the one
that looked authoritative.

`gate-proof` did not catch it and could not have. Its mutation for this gate plants a path that
exists nowhere at all, which is refused whether the oracle reads the disk or the index, so the
oracle itself was never the subject. Worse, `gate_proof.py` copies the repository *without*
`.git`, so a mutation cannot exercise the branch CI actually runs.

So it is attacked here instead, against real temporary repositories with real ignored files —
the same reason `test_the_scoreboard_goes_red_when_its_target_moves.py` uses synthetic reports.
The property under test is not "the check finds dead paths". It is **the check gives the same
answer wherever it runs.**
"""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load(root: Path):
    """A fresh copy of the check, rooted at a repository built for one test."""
    spec = importlib.util.spec_from_file_location(
        "map_check", ROOT / "scripts" / "check_the_map_matches_the_ground.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.ROOT = root
    module.GENERATED = root / "contracts" / "ci" / "generated_paths.yaml"
    return module


def _repository(tmp_path: Path, ignored_present: bool = True) -> Path:
    """A repository with one tracked path and one git-ignored path that exists on disk."""
    tmp_path.mkdir(parents=True, exist_ok=True)

    def run(*args: str) -> None:
        subprocess.run(  # noqa: S603
            ["git", "-C", str(tmp_path), *args],  # noqa: S607
            check=True,
            capture_output=True,
        )

    run("init", "-q")
    run("config", "user.email", "t@example.com")
    run("config", "user.name", "t")

    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "real.py").write_text("# tracked\n", encoding="utf-8")
    (tmp_path / ".gitignore").write_text("corpus/rendered/\n", encoding="utf-8")
    run("add", "-A")
    run("commit", "-qm", "one tracked script")

    if ignored_present:
        (tmp_path / "corpus" / "rendered").mkdir(parents=True)
        (tmp_path / "corpus" / "rendered" / "page.png").write_bytes(b"\x89PNG")
    return tmp_path


@pytest.fixture
def repo(tmp_path):
    return _repository(tmp_path)


def test_git_tracked_files_and_their_directories_count_as_present(repo) -> None:
    tracked = _load(repo)._tracked()
    assert "scripts/real.py" in tracked
    assert "scripts" in tracked, "prose names directories; git tracks only files"


def test_a_git_ignored_path_sitting_on_the_disk_is_not_in_the_repository(repo) -> None:
    """The defect, stated directly: the file is right there, and it is not part of the repo."""
    assert (repo / "corpus" / "rendered" / "page.png").exists()
    assert "corpus/rendered" not in _load(repo)._tracked()


def test_the_answer_does_not_depend_on_whether_the_ignored_file_is_present(tmp_path) -> None:
    """The property the old oracle broke, asserted as a property rather than as a case.

    Same commit, two machines — one where the generated artefact has been built and one fresh
    from a clone. A gate that distinguishes them is reporting on the machine, not on the commit.
    """
    with_it = _load(_repository(tmp_path / "a", ignored_present=True))._tracked()
    without_it = _load(_repository(tmp_path / "b", ignored_present=False))._tracked()
    assert with_it == without_it


def _prose(repo: Path, line: str) -> None:
    (repo / "README.md").write_text(line + "\n", encoding="utf-8")
    (repo / "docs").mkdir(exist_ok=True)


def _declare(repo: Path, body: str) -> None:
    (repo / "contracts" / "ci").mkdir(parents=True, exist_ok=True)
    (repo / "contracts" / "ci" / "generated_paths.yaml").write_text(body, encoding="utf-8")


def test_prose_pointing_at_an_ignored_path_is_reported(repo) -> None:
    """The two real findings CI reported and a laptop could not reproduce."""
    _prose(repo, "The pages live in `corpus/rendered/`.")
    problems = _load(repo)._paths_that_go_nowhere()
    assert any("corpus/rendered" in p for p in problems)
    assert any("does not contain it" in p for p in problems)


def test_a_declared_producer_excuses_the_path(repo) -> None:
    _prose(repo, "The pages live in `corpus/rendered/`.")
    _declare(repo, "produced_not_committed:\n  corpus/rendered: 'made by `make corpus`'\n")
    assert _load(repo)._paths_that_go_nowhere() == []


def test_a_declaration_no_prose_needs_any_more_is_reported(repo) -> None:
    """The same defect wearing the opposite sign.

    An excuse outlives the sentence that needed it, and the next dead pointer at that path is
    excused by a line nobody has re-read.
    """
    _prose(repo, "Nothing here names a generated path.")
    _declare(repo, "produced_not_committed:\n  corpus/rendered: 'made by `make corpus`'\n")
    problems = _load(repo)._paths_that_go_nowhere()
    assert any("no prose in this repository names it any more" in p for p in problems)


def test_a_declaration_for_a_path_git_now_tracks_is_reported(repo) -> None:
    """Committing the artefact is the good outcome; leaving the excuse behind is not.

    A stale entry goes on excusing a path that is really here, which is a live hiding place for
    the next real finding about it.
    """
    _prose(repo, "The script is `scripts/real.py`.")
    _declare(repo, "produced_not_committed:\n  scripts/real.py: 'made by something'\n")
    problems = _load(repo)._paths_that_go_nowhere()
    assert any("git tracks it" in p for p in problems)

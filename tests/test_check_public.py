"""scripts/check_public.py: the denylist scanner that guards the public tree and history."""

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/check_public.py"
_spec = importlib.util.spec_from_file_location("check_public", SCRIPT)
assert _spec and _spec.loader
cp = importlib.util.module_from_spec(_spec)
sys.modules["check_public"] = cp
_spec.loader.exec_module(cp)

SECRET = "zebra-alpha-42"
PATTERNS = f"# comment\n\n{SECRET}\n10\\.9\\.8\\.\n"


def _main(*args: str, stdin: str = "", env: dict[str, str] | None = None) -> int:
    return cp.main(list(args), stdin_text=stdin, environ=env or {})


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    ).stdout


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    _git(r, "config", "user.email", "t@example.com")
    _git(r, "config", "user.name", "t")
    (r / "a.txt").write_text("clean\n")
    _git(r, "add", ".")
    _git(r, "commit", "-q", "-m", "first")
    return r


def test_load_patterns_skips_blank_and_comment_lines():
    pats = cp.load_patterns(PATTERNS)
    assert [p.pattern for p in pats] == [SECRET, r"10\.9\.8\."]


def test_patterns_match_case_insensitively():
    (p,) = cp.load_patterns("Zebra")
    assert p.search("a ZEBRA here")


def test_invalid_pattern_names_its_line_not_its_text():
    with pytest.raises(cp.PatternError) as e:
        cp.load_patterns("ok\n(unclosed\n")
    assert "line 2" in str(e.value) and "unclosed" not in str(e.value)


def test_clean_tree_exits_zero(tmp_path, capsys):
    (tmp_path / "f.py").write_text("print('hi')\n")
    assert _main("--patterns-stdin", str(tmp_path), stdin=PATTERNS) == 0
    assert "0 hits" in capsys.readouterr().out


def test_hits_report_location_and_pattern_number_but_never_the_text(tmp_path, capsys):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub/f.md").write_text(f"one\nhost {SECRET} two\nip 10.9.8.7\n")
    rc = _main("--patterns-stdin", str(tmp_path), stdin=PATTERNS)
    out = capsys.readouterr()
    assert rc == 1
    assert "sub/f.md:2: pattern #1" in out.out
    assert "sub/f.md:3: pattern #2" in out.out
    text = out.out + out.err
    assert SECRET not in text and "10.9.8" not in text and "host" not in text


def test_file_name_itself_is_checked(tmp_path, capsys):
    (tmp_path / f"{SECRET}.txt").write_text("x\n")
    assert _main("--patterns-stdin", str(tmp_path), stdin=PATTERNS) == 1
    out = capsys.readouterr().out
    assert "<path>: pattern #1" in out and SECRET not in out


def test_binary_files_are_scanned(tmp_path):
    (tmp_path / "b.bin").write_bytes(b"\x00\xff" + SECRET.encode() + b"\x00")
    assert _main("--patterns-stdin", str(tmp_path), stdin=PATTERNS) == 1


def test_skips_vcs_and_cache_directories(tmp_path):
    for d in (".git", ".venv", "__pycache__", ".mypy_cache", "node_modules"):
        (tmp_path / d).mkdir()
        (tmp_path / d / "x").write_text(SECRET)
    assert _main("--patterns-stdin", str(tmp_path), stdin=PATTERNS) == 0


def test_patterns_from_file_and_env(tmp_path):
    (tmp_path / "f").write_text(SECRET)
    pf = tmp_path.parent / "pats.txt"
    pf.write_text(PATTERNS)
    assert _main("--patterns-file", str(pf), str(tmp_path / "f")) == 1
    env = {"DENY": PATTERNS}
    assert _main("--patterns-env", "DENY", str(tmp_path / "f"), env=env) == 1


def test_no_patterns_is_an_error_not_a_pass(tmp_path, capsys):
    (tmp_path / "f").write_text(SECRET)
    assert _main("--patterns-env", "MISSING", str(tmp_path)) == 2
    assert _main("--patterns-stdin", str(tmp_path), stdin="# only comments\n") == 2
    assert "no patterns" in capsys.readouterr().err


def test_optional_patterns_file_that_is_absent_passes(tmp_path, capsys):
    """The pre-commit hook: no local denylist means nothing to check, not a failure."""
    (tmp_path / "f").write_text(SECRET)
    missing = tmp_path / "nope.txt"
    assert _main("--patterns-file", str(missing), "--optional", str(tmp_path)) == 0
    assert "skipped" in capsys.readouterr().out


def test_git_mode_scans_tracked_files_only(repo):
    (repo / "untracked.txt").write_text(SECRET)
    assert _main("--patterns-stdin", "--git", str(repo), stdin=PATTERNS) == 0
    (repo / "a.txt").write_text(f"now {SECRET}\n")
    _git(repo, "add", "a.txt")
    assert _main("--patterns-stdin", "--git", str(repo), stdin=PATTERNS) == 1


def test_commit_messages_and_history_are_scanned(repo, capsys):
    (repo / "a.txt").write_text(f"leak {SECRET}\n")
    _git(repo, "commit", "-qam", "add it")
    (repo / "a.txt").write_text("clean again\n")
    _git(repo, "commit", "-qam", f"remove {SECRET}")
    rc = _main("--patterns-stdin", "--git", str(repo), "--commits", "HEAD", stdin=PATTERNS)
    out = capsys.readouterr().out
    assert rc == 1
    assert "message line 1: pattern #1" in out
    assert "a.txt (added line): pattern #1" in out
    assert SECRET not in out


def test_author_identity_is_scanned(repo, capsys):
    _git(repo, "-c", f"user.email={SECRET}@example.com", "commit", "-q", "--allow-empty", "-m", "x")
    rc = _main("--patterns-stdin", "--git", str(repo), "--commits", "HEAD", stdin=PATTERNS)
    assert rc == 1 and "author/committer: pattern #1" in capsys.readouterr().out


def test_script_runs_as_a_cli(tmp_path):
    (tmp_path / "f").write_text(SECRET)
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--patterns-stdin", str(tmp_path)],
        input=PATTERNS,
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode == 1 and "pattern #1" in r.stdout and SECRET not in r.stdout

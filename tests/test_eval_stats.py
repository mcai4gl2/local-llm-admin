import pytest

from local_llm_admin.eval_stats import MIN_PAIRED_N, mean_ci, paired


def test_mean_ci_is_deterministic_and_brackets_the_mean():
    s = [1, 0, 1, 1, 0, 1, 1, 1, 0, 1]
    a, b = mean_ci(s), mean_ci(s)
    assert a == b and a.lo <= a.mean <= a.hi and a.mean == 0.7 and a.n == 10


def test_mean_ci_degenerate():
    assert mean_ci([1.0] * 5).lo == mean_ci([1.0] * 5).hi == 1.0
    one = mean_ci([0.5])
    assert (one.lo, one.hi) == (0.5, 0.5)


def test_paired_clear_win():
    a = {f"c{i}": 1.0 for i in range(40)}
    b = {f"c{i}": (0.0 if i < 30 else 1.0) for i in range(40)}
    p = paired(a, b)
    assert p.verdict == "better" and p.wins == 30 and p.ties == 10 and p.losses == 0


def test_paired_noise_is_no_clear_difference():
    a = {"c1": 1.0, "c2": 0.0, "c3": 1.0, "c4": 0.0}
    b = {"c1": 0.0, "c2": 1.0, "c3": 1.0, "c4": 0.0}
    assert paired(a, b).verdict == "no clear difference"


def test_paired_uses_shared_cases_only():
    assert paired({"a": 1.0, "x": 1.0}, {"a": 0.0, "y": 0.0}).n == 1


def test_paired_clear_loss_is_worse():
    a = {f"c{i}": (0.0 if i < 30 else 1.0) for i in range(40)}
    b = {f"c{i}": 1.0 for i in range(40)}
    p = paired(a, b)
    assert p.verdict == "worse" and p.losses == 30 and p.wins == 0


def test_empty_inputs_raise():
    with pytest.raises(ValueError):
        mean_ci([])
    with pytest.raises(ValueError):
        paired({"a": 1.0}, {"b": 0.0})


def test_paired_one_case_is_no_clear_difference_even_when_zero_width():
    p = paired({"a": 1.0}, {"a": 0.0})
    assert p.verdict == "no clear difference"
    assert (p.n, p.wins, p.losses, p.ties) == (1, 1, 0, 0)
    assert p.lo == p.hi == 1.0


def test_paired_below_min_n_is_no_clear_difference():
    a = {f"c{i}": 1.0 for i in range(MIN_PAIRED_N - 1)}
    b = {f"c{i}": 0.0 for i in range(MIN_PAIRED_N - 1)}
    p = paired(a, b)
    assert p.verdict == "no clear difference" and p.wins == MIN_PAIRED_N - 1


def test_paired_at_min_n_can_be_better():
    a = {f"c{i}": 1.0 for i in range(MIN_PAIRED_N)}
    b = {f"c{i}": 0.0 for i in range(MIN_PAIRED_N)}
    assert paired(a, b).verdict == "better"


def test_iters_below_one_raises():
    with pytest.raises(ValueError):
        mean_ci([1.0, 0.0], iters=0)
    with pytest.raises(ValueError):
        paired({"a": 1.0}, {"a": 0.0}, iters=0)
    with pytest.raises(ValueError):
        mean_ci([1.0, 0.0], iters=-3)


def test_paired_noise_at_full_n_is_no_clear_difference():
    a = {f"c{i}": float(i % 2) for i in range(20)}
    b = {f"c{i}": float((i + 1) % 2) for i in range(20)}
    p = paired(a, b)
    assert p.n == 20 and p.verdict == "no clear difference"

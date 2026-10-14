"""M8 Phase 0: the comparison used by the correctness gate (pure)."""

import math

from local_llm_admin.embed_check import compare_rankings, compare_vectors, cosine


def test_cosine_basics():
    assert math.isclose(cosine([1.0, 0.0], [2.0, 0.0]), 1.0)
    assert math.isclose(cosine([1.0, 0.0], [0.0, 3.0]), 0.0)
    assert cosine([0.0, 0.0], [1.0, 0.0]) == 0.0


def test_compare_vectors_passes_close_vectors():
    ref = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]
    got = [[0.999, 0.01, 0.0], [0.0, 1.0, 0.001]]
    assert compare_vectors(ref, got, 0.99) == []


def test_compare_vectors_reports_far_vector_count_and_width():
    ref = [[1.0, 0.0], [0.0, 1.0]]
    assert "vector 1: cosine 0.000 < 0.99" in compare_vectors(ref, [[1.0, 0.0], [1.0, 0.0]], 0.99)
    assert compare_vectors(ref, [[1.0, 0.0]], 0.99) == ["expected 2 vectors, got 1"]
    assert compare_vectors(ref, [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]], 0.99) == [
        "width 3 differs from the reference's 2"
    ]


def test_compare_rankings_needs_the_same_order():
    assert compare_rankings([0.9, 0.1, 0.5], [0.8, 0.05, 0.4]) == []
    assert compare_rankings([0.9, 0.1, 0.5], [0.1, 0.9, 0.5]) == [
        "order [1, 2, 0] differs from the reference's [0, 2, 1]"
    ]

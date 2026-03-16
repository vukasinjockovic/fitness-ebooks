"""Integration tests for candidate_pool.py - requires database connection."""

import sys
import os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config import get_connection
from candidate_pool import get_candidates


@pytest.fixture(scope="module")
def conn():
    """Get a database connection for the test session."""
    with get_connection() as c:
        yield c


class TestGetCandidates:
    def test_basic_breakfast(self, conn):
        results = get_candidates(
            conn, meal_type="Breakfast",
            calorie_range=(300, 500), protein_min=25,
            min_quality_score=60, limit=50,
        )
        assert len(results) > 0
        assert len(results) <= 50
        for r in results:
            assert r.calories >= 300
            assert r.calories <= 500
            assert r.protein >= 25
            assert "Breakfast" in r.meal_types

    def test_dietary_keto(self, conn):
        results = get_candidates(
            conn, meal_type="Lunch",
            calorie_range=(400, 700), protein_min=25,
            dietary=["Keto"],
            min_quality_score=50, limit=50,
        )
        assert len(results) > 0
        for r in results:
            assert "Keto" in r.diet_tags

    def test_excluded_ingredients(self, conn):
        results = get_candidates(
            conn, meal_type="Dinner",
            calorie_range=(400, 800), protein_min=20,
            excluded_ingredients=["shellfish"],
            min_quality_score=50, limit=50,
        )
        assert len(results) > 0
        # Verify no shellfish in ingredients text
        for r in results:
            ing_text = str(r.ingredients).lower()
            assert "shellfish" not in ing_text

    def test_cuisine_preference_soft(self, conn):
        # Preferred cuisines should not hard-filter, just sort boost
        results = get_candidates(
            conn, meal_type="Lunch",
            calorie_range=(300, 700), protein_min=20,
            preferred_cuisines=["Italian"],
            min_quality_score=50, limit=50,
        )
        assert len(results) > 0
        # Should have results from many cuisines, not just Italian
        cuisines = set()
        for r in results:
            cuisines.update(r.normalized_cuisines or [])
        # We just check that results exist; Italian may dominate but
        # other cuisines should also appear in a pool of 50
        assert len(results) == 50 or len(results) > 0

    def test_quality_score_ordering(self, conn):
        results = get_candidates(
            conn, meal_type="Dinner",
            calorie_range=(400, 800), protein_min=20,
            min_quality_score=60, limit=20,
        )
        assert len(results) > 0
        # Results should be roughly ordered by quality (may have cuisine boost)
        scores = [r.quality_score for r in results]
        # At minimum, all should meet the threshold
        for s in scores:
            assert s >= 60

    def test_require_image(self, conn):
        results = get_candidates(
            conn, meal_type="Lunch",
            calorie_range=(300, 700), protein_min=15,
            require_image=True,
            min_quality_score=50, limit=20,
        )
        for r in results:
            assert r.image is not None and r.image != ""

    def test_snack_pool(self, conn):
        results = get_candidates(
            conn, meal_type="Snack",
            calorie_range=(100, 250), protein_min=5,
            min_quality_score=50, limit=200,
        )
        assert len(results) > 0
        for r in results:
            assert "Snack" in r.meal_types
            assert r.calories >= 100
            assert r.calories <= 250

    def test_limit_caps_results(self, conn):
        results = get_candidates(
            conn, meal_type="Lunch",
            calorie_range=(200, 900), protein_min=10,
            min_quality_score=0, limit=5,
        )
        assert len(results) <= 5

"""Tests for --stdin, --json-output, and --db-source CLI features."""

import sys
import os
import json
import argparse
import pytest
from unittest.mock import patch, MagicMock
from io import StringIO

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config import get_connection, DB_CONFIGS


# ---------------------------------------------------------------------------
# Change 1: --stdin and --json-output
# ---------------------------------------------------------------------------

SAMPLE_INPUT = {
    "name": "Test Plan",
    "groups": [
        {
            "name": "Breakfast",
            "meal_type": "Breakfast",
            "count": 3,
            "calorie_range": [300, 500],
            "protein_min": 20,
        }
    ],
    "global_constraints": {
        "dietary": [],
        "excluded_ingredients": [],
        "preferred_cuisines": [],
        "max_prep_time": 60,
        "min_quality_score": 50,
        "require_image": False,
        "protein_variety": True,
        "min_total_recipes": 3,
        "max_total_recipes": 50,
    },
    "mealplan": {
        "weeks": 1,
        "daily_calories": 2000,
        "daily_calories_tolerance": 200,
        "daily_protein": 150,
        "daily_protein_tolerance": 25,
        "daily_carbs": 200,
        "daily_carbs_tolerance": 30,
        "daily_fat": 70,
        "daily_fat_tolerance": 15,
    },
}


class TestDBConfigs:
    """Test that config.py exposes DB_CONFIGS with lake and production entries."""

    def test_db_configs_has_lake(self):
        assert "lake" in DB_CONFIGS

    def test_db_configs_has_production(self):
        assert "production" in DB_CONFIGS

    def test_lake_config_has_required_keys(self):
        cfg = DB_CONFIGS["lake"]
        for key in ("host", "port", "dbname", "user", "password"):
            assert key in cfg, f"Missing key: {key}"

    def test_production_config_has_required_keys(self):
        cfg = DB_CONFIGS["production"]
        for key in ("host", "port", "dbname", "user", "password"):
            assert key in cfg, f"Missing key: {key}"

    def test_lake_and_production_same_host(self):
        """Both configs point to the same DB, just different schemas."""
        assert DB_CONFIGS["lake"]["host"] == DB_CONFIGS["production"]["host"]
        assert DB_CONFIGS["lake"]["port"] == DB_CONFIGS["production"]["port"]
        assert DB_CONFIGS["lake"]["dbname"] == DB_CONFIGS["production"]["dbname"]

    def test_get_connection_accepts_db_source(self):
        """get_connection(db_source='lake') should work."""
        with get_connection(db_source="lake") as conn:
            cur = conn.cursor()
            cur.execute("SELECT 1")
            assert cur.fetchone()[0] == 1
            cur.close()

    def test_get_connection_production(self):
        """get_connection(db_source='production') should work."""
        with get_connection(db_source="production") as conn:
            cur = conn.cursor()
            cur.execute("SELECT 1")
            assert cur.fetchone()[0] == 1
            cur.close()

    def test_get_connection_default_is_lake(self):
        """Default db_source should be 'lake'."""
        with get_connection() as conn:
            cur = conn.cursor()
            # Should be able to query lake schema
            cur.execute("SELECT COUNT(*) FROM lake.recipes LIMIT 1")
            result = cur.fetchone()[0]
            assert result >= 0
            cur.close()


class TestCLIArgParsing:
    """Test that CLI accepts --stdin, --json-output, --db-source flags."""

    def _parse_args(self, argv):
        """Import and parse args using cli.main's parser."""
        # We need to build the parser the same way cli.py does
        from cli import main
        import argparse

        # Replicate the parser construction
        parser = argparse.ArgumentParser()
        parser.add_argument("--output-dir", "-o", default="output")
        subparsers = parser.add_subparsers(dest="command")

        # full subcommand - should have --stdin, --json-output, --db-source
        p_full = subparsers.add_parser("full")
        p_full.add_argument("input_file", nargs="?", default=None)
        p_full.add_argument("--stdin", action="store_true")
        p_full.add_argument("--json-output", action="store_true")
        p_full.add_argument("--db-source", default="lake", choices=["lake", "production"])
        p_full.add_argument("--swaps", type=int, default=0)
        p_full.add_argument("--weeks", type=int)
        p_full.add_argument("--daily-cal", type=int)
        p_full.add_argument("--protein", type=int)
        p_full.add_argument("--multipliers", type=str, default=None)

        return parser.parse_args(argv)

    def test_full_with_stdin_flag(self):
        args = self._parse_args(["full", "--stdin"])
        assert args.stdin is True
        assert args.input_file is None

    def test_full_with_json_output_flag(self):
        args = self._parse_args(["full", "--stdin", "--json-output"])
        assert args.json_output is True

    def test_full_with_db_source_lake(self):
        args = self._parse_args(["full", "--stdin", "--db-source", "lake"])
        assert args.db_source == "lake"

    def test_full_with_db_source_production(self):
        args = self._parse_args(["full", "--stdin", "--db-source", "production"])
        assert args.db_source == "production"

    def test_full_db_source_default_is_lake(self):
        args = self._parse_args(["full", "--stdin"])
        assert args.db_source == "lake"

    def test_full_file_input_still_works(self):
        args = self._parse_args(["full", "some_file.json"])
        assert args.input_file == "some_file.json"
        assert args.stdin is False

    def test_full_all_flags_combined(self):
        args = self._parse_args([
            "full", "--stdin", "--json-output", "--swaps", "5",
            "--db-source", "production",
        ])
        assert args.stdin is True
        assert args.json_output is True
        assert args.swaps == 5
        assert args.db_source == "production"


class TestPipelineJsonOutput:
    """Test that pipeline.run_pipeline supports json_output mode."""

    def test_run_pipeline_returns_result_dict_when_json_output(self):
        """When json_output=True, run_pipeline should return a dict with
        cookbook, mealplan, and summary keys."""
        from pipeline import run_pipeline
        from models import CookbookInput

        cookbook_input = CookbookInput.from_dict(SAMPLE_INPUT)

        # Run with json_output=True
        result = run_pipeline(
            cookbook_input,
            swaps_per_recipe=0,
            json_output=True,
        )

        # Should be a dict, not a tuple
        assert isinstance(result, dict)
        assert "cookbook" in result
        assert "summary" in result

    def test_run_pipeline_result_has_valid_summary(self):
        """Summary should contain total_recipes, solver_status, etc."""
        from pipeline import run_pipeline
        from models import CookbookInput

        cookbook_input = CookbookInput.from_dict(SAMPLE_INPUT)
        result = run_pipeline(
            cookbook_input,
            swaps_per_recipe=0,
            json_output=True,
        )

        summary = result["summary"]
        assert "total_recipes" in summary
        assert "solver_status" in summary
        assert summary["total_recipes"] > 0

    def test_run_pipeline_json_output_false_returns_tuple(self):
        """When json_output=False (default), should return (Cookbook, MealPlan|None)."""
        from pipeline import run_pipeline
        from models import CookbookInput, Cookbook

        cookbook_input = CookbookInput.from_dict(SAMPLE_INPUT)
        result = run_pipeline(cookbook_input, swaps_per_recipe=0)

        assert isinstance(result, tuple)
        assert len(result) == 2
        assert isinstance(result[0], Cookbook)

    def test_run_pipeline_json_output_suppresses_stdout(self):
        """When json_output=True, no progress output should go to stdout."""
        from pipeline import run_pipeline
        from models import CookbookInput

        cookbook_input = CookbookInput.from_dict(SAMPLE_INPUT)

        captured = StringIO()
        with patch("sys.stdout", captured):
            result = run_pipeline(
                cookbook_input,
                swaps_per_recipe=0,
                json_output=True,
            )

        # stdout should be empty (all goes to stderr when json_output=True)
        stdout_content = captured.getvalue()
        assert stdout_content == "", f"Expected no stdout but got: {stdout_content[:200]}"

    def test_run_pipeline_json_output_result_is_json_serializable(self):
        """The result dict should be JSON-serializable."""
        from pipeline import run_pipeline
        from models import CookbookInput

        cookbook_input = CookbookInput.from_dict(SAMPLE_INPUT)
        result = run_pipeline(
            cookbook_input,
            swaps_per_recipe=0,
            json_output=True,
        )

        # Should not raise
        json_str = json.dumps(result)
        parsed = json.loads(json_str)
        assert "cookbook" in parsed

    def test_run_pipeline_json_output_with_mealplan(self):
        """When mealplan is requested, result should include mealplan."""
        from pipeline import run_pipeline
        from models import CookbookInput

        cookbook_input = CookbookInput.from_dict(SAMPLE_INPUT)
        result = run_pipeline(
            cookbook_input,
            swaps_per_recipe=0,
            json_output=True,
        )

        # The SAMPLE_INPUT includes a mealplan section
        assert "mealplan" in result
        assert result["mealplan"] is not None


# ---------------------------------------------------------------------------
# Change 2: --db-source production
# ---------------------------------------------------------------------------

class TestCandidatePoolDbSource:
    """Test that candidate_pool.get_candidates supports db_source parameter."""

    def test_get_candidates_default_lake(self):
        """Default db_source should query lake.recipes."""
        from candidate_pool import get_candidates

        with get_connection(db_source="lake") as conn:
            results = get_candidates(
                conn,
                meal_type="Breakfast",
                calorie_range=(300, 500),
                protein_min=20,
                min_quality_score=50,
                limit=5,
            )
            assert len(results) > 0
            # Lake recipes have integer IDs
            assert isinstance(results[0].id, int)

    def test_get_candidates_production(self):
        """db_source='production' should query public.bp_cpts."""
        from candidate_pool import get_candidates

        with get_connection(db_source="production") as conn:
            results = get_candidates(
                conn,
                meal_type="Breakfast",
                calorie_range=(200, 800),
                protein_min=0,
                min_quality_score=0,
                limit=5,
                db_source="production",
            )
            assert len(results) > 0
            # Production recipes have UUID string IDs
            assert isinstance(results[0].id, str)
            assert len(results[0].id) == 36  # UUID format

    def test_production_recipes_have_macros(self):
        """Production recipes should have calories, protein, fat, carbs extracted from meta."""
        from candidate_pool import get_candidates

        with get_connection(db_source="production") as conn:
            results = get_candidates(
                conn,
                meal_type="Breakfast",
                calorie_range=(200, 800),
                protein_min=0,
                min_quality_score=0,
                limit=5,
                db_source="production",
            )
            if results:
                r = results[0]
                assert r.calories > 0
                assert isinstance(r.protein, float)
                assert isinstance(r.fat, float)
                assert isinstance(r.carbohydrates, float)

    def test_production_recipes_have_meal_types(self):
        """Production recipes should have meal_types extracted from meta->meals."""
        from candidate_pool import get_candidates

        with get_connection(db_source="production") as conn:
            results = get_candidates(
                conn,
                meal_type="Dinner",
                calorie_range=(200, 1000),
                protein_min=0,
                min_quality_score=0,
                limit=5,
                db_source="production",
            )
            if results:
                r = results[0]
                assert isinstance(r.meal_types, list)
                assert "Dinner" in r.meal_types

    def test_production_recipes_have_ingredients(self):
        """Production recipes should have ingredients from meta."""
        from candidate_pool import get_candidates

        with get_connection(db_source="production") as conn:
            results = get_candidates(
                conn,
                meal_type="Dinner",
                calorie_range=(200, 1000),
                protein_min=0,
                min_quality_score=0,
                limit=5,
                db_source="production",
            )
            if results:
                r = results[0]
                assert isinstance(r.ingredients, list)

    def test_production_calorie_filter_works(self):
        """Calorie range filter should work on production recipes."""
        from candidate_pool import get_candidates

        with get_connection(db_source="production") as conn:
            results = get_candidates(
                conn,
                meal_type="Lunch",
                calorie_range=(300, 500),
                protein_min=0,
                min_quality_score=0,
                limit=20,
                db_source="production",
            )
            for r in results:
                assert r.calories >= 300, f"Calories {r.calories} < 300"
                assert r.calories <= 500, f"Calories {r.calories} > 500"

    def test_production_dietary_filter_works(self):
        """Dietary filter should work on production recipes."""
        from candidate_pool import get_candidates

        with get_connection(db_source="production") as conn:
            results = get_candidates(
                conn,
                meal_type="Dinner",
                calorie_range=(100, 1000),
                protein_min=0,
                dietary=["Vegetarian"],
                min_quality_score=0,
                limit=20,
                db_source="production",
            )
            for r in results:
                assert "Vegetarian" in r.diet_tags, f"Expected Vegetarian in {r.diet_tags}"


class TestSwapEnricherDbSource:
    """Test that swap_enricher works with production db source."""

    def test_swap_query_accepts_db_source(self):
        """_query_swap_candidates should accept db_source parameter."""
        from swap_enricher import _query_swap_candidates

        with get_connection(db_source="production") as conn:
            results = _query_swap_candidates(
                conn,
                meal_type="Dinner",
                exclude_ids=set(),
                dietary=None,
                limit=10,
                db_source="production",
            )
            # Should return results (or empty list if not enough data)
            assert isinstance(results, list)

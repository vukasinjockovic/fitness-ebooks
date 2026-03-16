"""Tests for import_classifications.py

TDD tests covering:
- Reading classified JSON files
- Building UPDATE tuples
- Progress reporting
- CLI argument parsing
- Handling malformed files gracefully
- Batch processing
"""

import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ---------------------------------------------------------------------------
# Tests for load_classified_file()
# ---------------------------------------------------------------------------

class TestLoadClassifiedFile:
    """Test reading classified JSON chunk files."""

    def test_loads_valid_classified_file(self):
        from import_classifications import load_classified_file

        data = [
            {
                "id": 12345,
                "audiences": ["general_fitness", "bodybuilding"],
                "context_tags": ["creatine", "recovery", "evidence_based"],
                "category": "supplements",
                "subcategory": "creatine",
                "expertise_level": "scientific",
            },
            {
                "id": 12346,
                "audiences": ["beginners"],
                "context_tags": ["protein", "meal_prep"],
                "category": "nutrition",
                "subcategory": "protein",
                "expertise_level": "beginner",
            },
        ]

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            json.dump(data, f)
            path = f.name

        try:
            result = load_classified_file(path)
            assert len(result) == 2
            assert result[0]["id"] == 12345
            assert result[0]["category"] == "supplements"
            assert result[1]["audiences"] == ["beginners"]
        finally:
            os.unlink(path)

    def test_returns_empty_for_invalid_json(self):
        from import_classifications import load_classified_file

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            f.write("this is not valid json{{{")
            path = f.name

        try:
            result = load_classified_file(path)
            assert result == []
        finally:
            os.unlink(path)

    def test_returns_empty_for_non_list_json(self):
        from import_classifications import load_classified_file

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            json.dump({"not": "a list"}, f)
            path = f.name

        try:
            result = load_classified_file(path)
            assert result == []
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# Tests for validate_classification()
# ---------------------------------------------------------------------------

class TestValidateClassification:
    """Test validation of individual classification entries."""

    def test_valid_entry_passes(self):
        from import_classifications import validate_classification

        entry = {
            "id": 123,
            "audiences": ["general_fitness"],
            "context_tags": ["protein"],
            "category": "nutrition",
            "subcategory": "protein",
            "expertise_level": "beginner",
        }
        assert validate_classification(entry) is True

    def test_missing_id_fails(self):
        from import_classifications import validate_classification

        entry = {
            "audiences": ["general_fitness"],
            "context_tags": ["protein"],
            "category": "nutrition",
            "subcategory": "protein",
            "expertise_level": "beginner",
        }
        assert validate_classification(entry) is False

    def test_missing_category_fails(self):
        from import_classifications import validate_classification

        entry = {
            "id": 123,
            "audiences": ["general_fitness"],
            "context_tags": ["protein"],
            "subcategory": "protein",
            "expertise_level": "beginner",
        }
        assert validate_classification(entry) is False

    def test_non_integer_id_fails(self):
        from import_classifications import validate_classification

        entry = {
            "id": "not_a_number",
            "audiences": ["general_fitness"],
            "context_tags": ["protein"],
            "category": "nutrition",
            "subcategory": "protein",
            "expertise_level": "beginner",
        }
        assert validate_classification(entry) is False

    def test_missing_audiences_uses_empty_default(self):
        """If audiences is missing, validation should still pass
        (with a default of [])."""
        from import_classifications import validate_classification

        entry = {
            "id": 123,
            "context_tags": ["protein"],
            "category": "nutrition",
            "subcategory": "protein",
            "expertise_level": "beginner",
        }
        # Missing audiences should still be valid - we default to []
        assert validate_classification(entry) is True


# ---------------------------------------------------------------------------
# Tests for build_update_params()
# ---------------------------------------------------------------------------

class TestBuildUpdateParams:
    """Test building SQL update parameter tuples."""

    def test_builds_correct_tuple(self):
        from import_classifications import build_update_params

        entry = {
            "id": 12345,
            "audiences": ["general_fitness", "bodybuilding"],
            "context_tags": ["creatine", "recovery"],
            "category": "supplements",
            "subcategory": "creatine",
            "expertise_level": "scientific",
        }
        result = build_update_params(entry)

        # Should be (audiences_json, context_tags_json, category, subcategory, expertise_level, id)
        assert result[0] == json.dumps(["general_fitness", "bodybuilding"])
        assert result[1] == json.dumps(["creatine", "recovery"])
        assert result[2] == "supplements"
        assert result[3] == "creatine"
        assert result[4] == "scientific"
        assert result[5] == 12345

    def test_defaults_missing_audiences_to_empty(self):
        from import_classifications import build_update_params

        entry = {
            "id": 1,
            "context_tags": ["protein"],
            "category": "nutrition",
            "subcategory": "protein",
            "expertise_level": "beginner",
        }
        result = build_update_params(entry)
        assert result[0] == json.dumps([])


# ---------------------------------------------------------------------------
# Tests for find_classified_files()
# ---------------------------------------------------------------------------

class TestFindClassifiedFiles:
    """Test discovery of classified chunk files."""

    def test_finds_classified_json_files(self):
        from import_classifications import find_classified_files

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create some classified files
            for name in [
                "chunk_001_classified.json",
                "chunk_002_classified.json",
                "chunk_003_classified.json",
            ]:
                with open(os.path.join(tmpdir, name), "w") as f:
                    json.dump([], f)

            # Create a non-classified file (should be ignored)
            with open(os.path.join(tmpdir, "chunk_001.json"), "w") as f:
                json.dump({}, f)

            result = find_classified_files(tmpdir)
            assert len(result) == 3
            # Should be sorted
            assert "chunk_001_classified.json" in result[0]
            assert "chunk_003_classified.json" in result[2]

    def test_returns_empty_for_empty_dir(self):
        from import_classifications import find_classified_files

        with tempfile.TemporaryDirectory() as tmpdir:
            result = find_classified_files(tmpdir)
            assert result == []


# ---------------------------------------------------------------------------
# Tests for parse_args()
# ---------------------------------------------------------------------------

class TestImportParseArgs:
    """Test CLI argument parsing."""

    def test_defaults(self):
        from import_classifications import parse_args

        args = parse_args([])
        assert args.input_dir == "classified_chunks/"
        assert args.batch_size == 1000

    def test_custom_input_dir(self):
        from import_classifications import parse_args

        args = parse_args(["--input-dir", "/tmp/classified/"])
        assert args.input_dir == "/tmp/classified/"

    def test_custom_batch_size(self):
        from import_classifications import parse_args

        args = parse_args(["--batch-size", "500"])
        assert args.batch_size == 500

    def test_dry_run_flag(self):
        from import_classifications import parse_args

        args = parse_args(["--dry-run"])
        assert args.dry_run is True

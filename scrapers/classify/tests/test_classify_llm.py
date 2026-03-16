"""Tests for classify_with_llm.py

TDD tests covering:
- Loading chunk files
- Sub-batching articles for LLM calls
- Building the prompt from template + articles
- Parsing LLM JSON responses
- Resume logic (skip already-classified chunks)
- Output file writing
- CLI argument parsing
- Retry/backoff logic
"""

import json
import os
import sys
import tempfile
import textwrap

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ---------------------------------------------------------------------------
# Tests for load_chunk_file()
# ---------------------------------------------------------------------------

class TestLoadChunkFile:
    """Test loading input chunk JSON files."""

    def test_loads_valid_chunk(self):
        from classify_with_llm import load_chunk_file

        data = {
            "chunk_id": 1,
            "total_chunks": 10,
            "articles": [
                {"id": 1, "title": "Test", "excerpt": "Body",
                 "source_domain": "a.com", "source_category": "cat",
                 "tags": [], "word_count": 100},
            ],
        }

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            json.dump(data, f)
            path = f.name

        try:
            result = load_chunk_file(path)
            assert result["chunk_id"] == 1
            assert len(result["articles"]) == 1
        finally:
            os.unlink(path)

    def test_raises_on_invalid_json(self):
        from classify_with_llm import load_chunk_file

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            f.write("not json")
            path = f.name

        try:
            with pytest.raises(Exception):
                load_chunk_file(path)
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# Tests for sub_batch_articles()
# ---------------------------------------------------------------------------

class TestSubBatchArticles:
    """Test splitting articles into sub-batches for LLM calls."""

    def test_single_batch_when_small(self):
        from classify_with_llm import sub_batch_articles

        articles = [{"id": i} for i in range(10)]
        batches = list(sub_batch_articles(articles, batch_size=50))
        assert len(batches) == 1
        assert len(batches[0]) == 10

    def test_multiple_batches(self):
        from classify_with_llm import sub_batch_articles

        articles = [{"id": i} for i in range(120)]
        batches = list(sub_batch_articles(articles, batch_size=50))
        assert len(batches) == 3
        assert len(batches[0]) == 50
        assert len(batches[1]) == 50
        assert len(batches[2]) == 20

    def test_empty_articles(self):
        from classify_with_llm import sub_batch_articles

        batches = list(sub_batch_articles([], batch_size=50))
        assert len(batches) == 0

    def test_exact_batch_size(self):
        from classify_with_llm import sub_batch_articles

        articles = [{"id": i} for i in range(50)]
        batches = list(sub_batch_articles(articles, batch_size=50))
        assert len(batches) == 1
        assert len(batches[0]) == 50


# ---------------------------------------------------------------------------
# Tests for build_llm_prompt()
# ---------------------------------------------------------------------------

class TestBuildLLMPrompt:
    """Test building the prompt for the LLM from template + articles."""

    def test_includes_article_data(self):
        from classify_with_llm import build_llm_prompt

        articles = [
            {"id": 1, "title": "Test Article", "excerpt": "About creatine",
             "source_domain": "pubmed.ncbi.nlm.nih.gov",
             "source_category": "11_scientific_papers",
             "tags": ["creatine"], "word_count": 500},
        ]
        prompt = build_llm_prompt(articles)

        assert "Test Article" in prompt
        assert "About creatine" in prompt
        assert "pubmed.ncbi.nlm.nih.gov" in prompt
        assert "11_scientific_papers" in prompt

    def test_includes_classification_instructions(self):
        from classify_with_llm import build_llm_prompt

        articles = [{"id": 1, "title": "T", "excerpt": "E",
                     "source_domain": "d", "source_category": "c",
                     "tags": [], "word_count": 0}]
        prompt = build_llm_prompt(articles)

        # Should mention the classification fields
        assert "audiences" in prompt
        assert "context_tags" in prompt
        assert "category" in prompt
        assert "subcategory" in prompt
        assert "expertise_level" in prompt

    def test_multiple_articles_all_included(self):
        from classify_with_llm import build_llm_prompt

        articles = [
            {"id": i, "title": f"Article {i}", "excerpt": f"Body {i}",
             "source_domain": "d.com", "source_category": "cat",
             "tags": [], "word_count": 100}
            for i in range(5)
        ]
        prompt = build_llm_prompt(articles)
        for i in range(5):
            assert f"Article {i}" in prompt


# ---------------------------------------------------------------------------
# Tests for parse_llm_response()
# ---------------------------------------------------------------------------

class TestParseLLMResponse:
    """Test parsing JSON from LLM response text."""

    def test_parses_clean_json_array(self):
        from classify_with_llm import parse_llm_response

        response = json.dumps([
            {"id": 1, "audiences": ["general_fitness"],
             "context_tags": ["protein"], "category": "nutrition",
             "subcategory": "protein", "expertise_level": "beginner"},
        ])
        result = parse_llm_response(response)
        assert len(result) == 1
        assert result[0]["id"] == 1

    def test_parses_json_with_markdown_code_fence(self):
        from classify_with_llm import parse_llm_response

        response = '```json\n[{"id": 1, "audiences": [], "context_tags": [], "category": "training", "subcategory": "strength", "expertise_level": "beginner"}]\n```'
        result = parse_llm_response(response)
        assert len(result) == 1
        assert result[0]["category"] == "training"

    def test_parses_json_with_surrounding_text(self):
        from classify_with_llm import parse_llm_response

        response = 'Here are the classifications:\n[{"id": 1, "audiences": [], "context_tags": [], "category": "training", "subcategory": "strength", "expertise_level": "beginner"}]\nDone!'
        result = parse_llm_response(response)
        assert len(result) == 1

    def test_returns_empty_on_unparseable(self):
        from classify_with_llm import parse_llm_response

        result = parse_llm_response("This is not JSON at all")
        assert result == []

    def test_returns_empty_on_non_array(self):
        from classify_with_llm import parse_llm_response

        result = parse_llm_response('{"id": 1}')
        assert result == []


# ---------------------------------------------------------------------------
# Tests for is_chunk_already_classified()
# ---------------------------------------------------------------------------

class TestResumeLogic:
    """Test resume-safe chunk detection."""

    def test_not_classified_when_no_output_exists(self):
        from classify_with_llm import is_chunk_already_classified

        with tempfile.TemporaryDirectory() as tmpdir:
            result = is_chunk_already_classified(
                "chunk_001.json", tmpdir
            )
            assert result is False

    def test_classified_when_output_exists(self):
        from classify_with_llm import is_chunk_already_classified

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create the classified output file
            outpath = os.path.join(tmpdir, "chunk_001_classified.json")
            with open(outpath, "w") as f:
                json.dump([{"id": 1}], f)

            result = is_chunk_already_classified(
                "chunk_001.json", tmpdir
            )
            assert result is True

    def test_classified_output_filename(self):
        from classify_with_llm import classified_output_name

        assert classified_output_name("chunk_001.json") == "chunk_001_classified.json"
        assert classified_output_name("chunk_042.json") == "chunk_042_classified.json"


# ---------------------------------------------------------------------------
# Tests for write_classified_output()
# ---------------------------------------------------------------------------

class TestWriteClassifiedOutput:
    """Test writing classified results to output file."""

    def test_writes_valid_json(self):
        from classify_with_llm import write_classified_output

        classifications = [
            {"id": 1, "audiences": ["general_fitness"],
             "context_tags": ["protein"], "category": "nutrition",
             "subcategory": "protein", "expertise_level": "beginner"},
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            write_classified_output(
                tmpdir, "chunk_001.json", classifications
            )
            outpath = os.path.join(tmpdir, "chunk_001_classified.json")
            assert os.path.exists(outpath)

            with open(outpath) as f:
                data = json.load(f)
            assert len(data) == 1
            assert data[0]["id"] == 1


# ---------------------------------------------------------------------------
# Tests for parse_args()
# ---------------------------------------------------------------------------

class TestClassifyParseArgs:
    """Test CLI argument parsing."""

    def test_defaults(self):
        from classify_with_llm import parse_args

        args = parse_args([])
        assert args.input_dir == "classify_chunks/"
        assert args.output_dir == "classified_chunks/"
        assert args.batch_size == 50
        assert args.delay == 0.5

    def test_custom_batch_size(self):
        from classify_with_llm import parse_args

        args = parse_args(["--batch-size", "25"])
        assert args.batch_size == 25

    def test_custom_delay(self):
        from classify_with_llm import parse_args

        args = parse_args(["--delay", "1.0"])
        assert args.delay == 1.0

    def test_custom_dirs(self):
        from classify_with_llm import parse_args

        args = parse_args([
            "--input-dir", "/tmp/in/",
            "--output-dir", "/tmp/out/",
        ])
        assert args.input_dir == "/tmp/in/"
        assert args.output_dir == "/tmp/out/"

    def test_max_retries(self):
        from classify_with_llm import parse_args

        args = parse_args(["--max-retries", "5"])
        assert args.max_retries == 5

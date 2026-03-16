"""Tests for export_for_classification.py

TDD tests covering:
- YAML frontmatter stripping from body_markdown
- Excerpt extraction (first 300 words)
- Chunk file writing (JSON format)
- CLI argument parsing
- Progress reporting
- Memory-efficient fetchmany usage
"""

import json
import os
import sys
import tempfile
import textwrap

import pytest

# Add parent dir to path so we can import the module
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ---------------------------------------------------------------------------
# Tests for strip_frontmatter()
# ---------------------------------------------------------------------------

class TestStripFrontmatter:
    """Test YAML frontmatter removal from body_markdown."""

    def test_strips_yaml_frontmatter(self):
        from export_for_classification import strip_frontmatter

        text = textwrap.dedent("""\
            ---
            title: "Test Article"
            author: "John"
            ---

            This is the body content.""")

        result = strip_frontmatter(text)
        assert result == "This is the body content."

    def test_no_frontmatter_returns_unchanged(self):
        from export_for_classification import strip_frontmatter

        text = "Just a plain body with no frontmatter."
        result = strip_frontmatter(text)
        assert result == text

    def test_frontmatter_with_complex_yaml(self):
        from export_for_classification import strip_frontmatter

        text = textwrap.dedent("""\
            ---
            title: "Complex"
            tags: ["a", "b", "c"]
            nested:
              key: value
            ---

            Body after complex YAML.""")

        result = strip_frontmatter(text)
        assert result == "Body after complex YAML."

    def test_frontmatter_with_leading_newlines(self):
        from export_for_classification import strip_frontmatter

        text = "\n\n---\ntitle: Test\n---\n\nBody here."
        result = strip_frontmatter(text)
        assert result == "Body here."

    def test_triple_dashes_in_body_not_stripped(self):
        """Frontmatter is only the first --- ... --- block."""
        from export_for_classification import strip_frontmatter

        text = textwrap.dedent("""\
            ---
            title: Test
            ---

            Body content.

            ---

            More content after horizontal rule.""")

        result = strip_frontmatter(text)
        assert "Body content." in result
        assert "More content after horizontal rule." in result


# ---------------------------------------------------------------------------
# Tests for extract_excerpt()
# ---------------------------------------------------------------------------

class TestExtractExcerpt:
    """Test excerpt extraction from body text."""

    def test_short_body_returns_full_text(self):
        from export_for_classification import extract_excerpt

        body = "Short body with only ten words here right now."
        result = extract_excerpt(body, max_words=300)
        assert result == body

    def test_long_body_truncated_to_max_words(self):
        from export_for_classification import extract_excerpt

        # Build a body with 500 words
        words = ["word"] * 500
        body = " ".join(words)
        result = extract_excerpt(body, max_words=300)
        assert len(result.split()) == 300

    def test_empty_body_returns_empty(self):
        from export_for_classification import extract_excerpt

        assert extract_excerpt("", max_words=300) == ""
        assert extract_excerpt("   ", max_words=300) == ""

    def test_strips_markdown_headers_from_excerpt(self):
        from export_for_classification import extract_excerpt

        body = "## Introduction\n\nThis is the content after the header."
        result = extract_excerpt(body, max_words=300)
        # Should include the content, headers can be stripped or kept
        assert "content after the header" in result

    def test_default_max_words_is_300(self):
        from export_for_classification import extract_excerpt

        words = ["word"] * 500
        body = " ".join(words)
        result = extract_excerpt(body)
        assert len(result.split()) == 300


# ---------------------------------------------------------------------------
# Tests for build_article_record()
# ---------------------------------------------------------------------------

class TestBuildArticleRecord:
    """Test building the export record from a DB row."""

    def test_builds_record_with_all_fields(self):
        from export_for_classification import build_article_record

        row = {
            "id": 12345,
            "title": "Creatine and Recovery",
            "body_markdown": "---\ntitle: Creatine\n---\n\nThis is about creatine supplementation.",
            "source_domain": "pubmed.ncbi.nlm.nih.gov",
            "source_category": "11_scientific_papers",
            "tags": ["Creatine", "Recovery"],
            "word_count": 450,
        }
        result = build_article_record(row)

        assert result["id"] == 12345
        assert result["title"] == "Creatine and Recovery"
        assert "creatine supplementation" in result["excerpt"]
        assert result["source_domain"] == "pubmed.ncbi.nlm.nih.gov"
        assert result["source_category"] == "11_scientific_papers"
        assert result["tags"] == ["Creatine", "Recovery"]
        assert result["word_count"] == 450

    def test_excerpt_has_frontmatter_stripped(self):
        from export_for_classification import build_article_record

        row = {
            "id": 1,
            "title": "Test",
            "body_markdown": "---\ntitle: X\nauthor: Y\n---\n\nActual body here.",
            "source_domain": "example.com",
            "source_category": "1_fitness",
            "tags": [],
            "word_count": 10,
        }
        result = build_article_record(row)
        assert "---" not in result["excerpt"]
        assert "title: X" not in result["excerpt"]
        assert "Actual body here." in result["excerpt"]

    def test_null_tags_becomes_empty_list(self):
        from export_for_classification import build_article_record

        row = {
            "id": 1,
            "title": "Test",
            "body_markdown": "Body",
            "source_domain": "example.com",
            "source_category": "1_fitness",
            "tags": None,
            "word_count": 10,
        }
        result = build_article_record(row)
        assert result["tags"] == []

    def test_null_word_count_becomes_zero(self):
        from export_for_classification import build_article_record

        row = {
            "id": 1,
            "title": "Test",
            "body_markdown": "Body",
            "source_domain": "example.com",
            "source_category": "1_fitness",
            "tags": [],
            "word_count": None,
        }
        result = build_article_record(row)
        assert result["word_count"] == 0


# ---------------------------------------------------------------------------
# Tests for write_chunk_file()
# ---------------------------------------------------------------------------

class TestWriteChunkFile:
    """Test chunk file JSON output."""

    def test_writes_valid_json_file(self):
        from export_for_classification import write_chunk_file

        articles = [
            {"id": 1, "title": "Article 1", "excerpt": "Body 1",
             "source_domain": "a.com", "source_category": "cat1",
             "tags": [], "word_count": 100},
            {"id": 2, "title": "Article 2", "excerpt": "Body 2",
             "source_domain": "b.com", "source_category": "cat2",
             "tags": ["t1"], "word_count": 200},
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            write_chunk_file(tmpdir, chunk_id=1, total_chunks=10, articles=articles)
            filepath = os.path.join(tmpdir, "chunk_001.json")

            assert os.path.exists(filepath)
            with open(filepath) as f:
                data = json.load(f)

            assert data["chunk_id"] == 1
            assert data["total_chunks"] == 10
            assert len(data["articles"]) == 2
            assert data["articles"][0]["id"] == 1
            assert data["articles"][1]["title"] == "Article 2"

    def test_chunk_filename_zero_padded(self):
        from export_for_classification import write_chunk_file

        with tempfile.TemporaryDirectory() as tmpdir:
            write_chunk_file(tmpdir, chunk_id=42, total_chunks=999, articles=[])
            assert os.path.exists(os.path.join(tmpdir, "chunk_042.json"))

    def test_creates_output_dir_if_missing(self):
        from export_for_classification import write_chunk_file

        with tempfile.TemporaryDirectory() as tmpdir:
            subdir = os.path.join(tmpdir, "nested", "deep")
            write_chunk_file(subdir, chunk_id=1, total_chunks=1, articles=[])
            assert os.path.exists(os.path.join(subdir, "chunk_001.json"))


# ---------------------------------------------------------------------------
# Tests for parse_args()
# ---------------------------------------------------------------------------

class TestParseArgs:
    """Test CLI argument parsing."""

    def test_defaults(self):
        from export_for_classification import parse_args

        args = parse_args([])
        assert args.chunk_size == 500
        assert args.output_dir == "classify_chunks/"

    def test_custom_chunk_size(self):
        from export_for_classification import parse_args

        args = parse_args(["--chunk-size", "1000"])
        assert args.chunk_size == 1000

    def test_custom_output_dir(self):
        from export_for_classification import parse_args

        args = parse_args(["--output-dir", "/tmp/my_chunks/"])
        assert args.output_dir == "/tmp/my_chunks/"

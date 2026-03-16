"""Tests for process_yt_claude.py — Claude Code Haiku batch processor.

TDD tests covering:
- Transcript parsing (same as groq, shared module)
- Batch export: grouping transcripts into batches
- Batch file format (JSON with batch_id, transcripts array)
- Text truncation for Haiku context (words 500-3000)
- Import: reading Claude Code results back
- Resume-safe logic (skip existing outputs)
- CLI subcommand parsing (export / import)
"""

import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "youtube"))


# ---------------------------------------------------------------------------
# Tests for prepare_transcript_for_batch
# ---------------------------------------------------------------------------

class TestPrepareTranscriptForBatch:
    """Test preparing a single transcript for a Claude Code batch."""

    def test_returns_required_fields(self):
        from process_yt_claude import prepare_transcript_for_batch

        content = """---
source_id: "abc123"
title: "Muscle Building"
channel: "jeff-nippard"
word_count: 5000
---

""" + " ".join([f"word{i}" for i in range(5000)])

        result = prepare_transcript_for_batch(content)
        assert result["video_id"] == "abc123"
        assert result["channel"] == "jeff-nippard"
        assert result["title"] == "Muscle Building"
        assert "text" in result

    def test_trims_to_words_500_to_3000(self):
        from process_yt_claude import prepare_transcript_for_batch

        words = " ".join([f"w{i}" for i in range(5000)])
        content = f"---\nsource_id: x\ntitle: T\nchannel: c\n---\n\n{words}"

        result = prepare_transcript_for_batch(content)
        text_words = result["text"].split()
        # Should have ~2500 words (500..3000)
        assert len(text_words) == 2500
        assert text_words[0] == "w500"
        assert text_words[-1] == "w2999"

    def test_short_transcript_includes_all(self):
        from process_yt_claude import prepare_transcript_for_batch

        words = " ".join([f"w{i}" for i in range(400)])
        content = f"---\nsource_id: short\ntitle: Short\nchannel: c\n---\n\n{words}"

        result = prepare_transcript_for_batch(content)
        # Short transcript: include everything
        assert len(result["text"].split()) == 400

    def test_medium_transcript_trims_intro_only(self):
        from process_yt_claude import prepare_transcript_for_batch

        # 800 words: skip first 500, take all remaining (only 300 words)
        words = " ".join([f"w{i}" for i in range(800)])
        content = f"---\nsource_id: med\ntitle: Medium\nchannel: c\n---\n\n{words}"

        result = prepare_transcript_for_batch(content)
        text_words = result["text"].split()
        assert text_words[0] == "w500"
        assert len(text_words) == 300


# ---------------------------------------------------------------------------
# Tests for create_batches
# ---------------------------------------------------------------------------

class TestCreateBatches:
    """Test splitting transcripts into batches of N."""

    def test_single_batch_when_few_transcripts(self):
        from process_yt_claude import create_batches

        transcripts = [
            {"video_id": f"v{i}", "channel": "c", "title": f"T{i}", "text": "words"}
            for i in range(3)
        ]
        batches = create_batches(transcripts, batch_size=5)
        assert len(batches) == 1
        assert batches[0]["batch_id"] == 1
        assert len(batches[0]["transcripts"]) == 3

    def test_multiple_batches(self):
        from process_yt_claude import create_batches

        transcripts = [
            {"video_id": f"v{i}", "channel": "c", "title": f"T{i}", "text": "words"}
            for i in range(12)
        ]
        batches = create_batches(transcripts, batch_size=5)
        assert len(batches) == 3
        assert len(batches[0]["transcripts"]) == 5
        assert len(batches[1]["transcripts"]) == 5
        assert len(batches[2]["transcripts"]) == 2

    def test_batch_ids_sequential(self):
        from process_yt_claude import create_batches

        transcripts = [
            {"video_id": f"v{i}", "channel": "c", "title": f"T{i}", "text": "words"}
            for i in range(10)
        ]
        batches = create_batches(transcripts, batch_size=3)
        for i, batch in enumerate(batches):
            assert batch["batch_id"] == i + 1

    def test_empty_input(self):
        from process_yt_claude import create_batches

        batches = create_batches([], batch_size=5)
        assert batches == []

    def test_batch_contains_prompt(self):
        from process_yt_claude import create_batches

        transcripts = [
            {"video_id": "v1", "channel": "c", "title": "T1", "text": "words"}
        ]
        batches = create_batches(transcripts, batch_size=5)
        assert "prompt" in batches[0]
        assert "segment" in batches[0]["prompt"].lower()


# ---------------------------------------------------------------------------
# Tests for export_batches (write to disk)
# ---------------------------------------------------------------------------

class TestExportBatches:
    """Test writing batch files to output directory."""

    def test_creates_batch_files(self):
        from process_yt_claude import export_batches

        batches = [
            {"batch_id": 1, "prompt": "p", "transcripts": [
                {"video_id": "v1", "channel": "c", "title": "T", "text": "w"}
            ]},
            {"batch_id": 2, "prompt": "p", "transcripts": [
                {"video_id": "v2", "channel": "c", "title": "T2", "text": "w2"}
            ]},
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = export_batches(batches, tmpdir)
            assert len(paths) == 2
            assert os.path.exists(paths[0])
            assert os.path.exists(paths[1])
            with open(paths[0]) as f:
                data = json.load(f)
            assert data["batch_id"] == 1

    def test_batch_filenames(self):
        from process_yt_claude import export_batches

        batches = [
            {"batch_id": 1, "prompt": "p", "transcripts": []},
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = export_batches(batches, tmpdir)
            assert "batch_001.json" in os.path.basename(paths[0])


# ---------------------------------------------------------------------------
# Tests for import_results
# ---------------------------------------------------------------------------

class TestImportResults:
    """Test importing Claude Code processed results."""

    def test_imports_valid_result_file(self):
        from process_yt_claude import import_result_file

        result_data = [
            {
                "video_id": "abc123",
                "channel": "jeff-nippard",
                "title": "Test",
                "segments": [
                    {"segment_id": 1, "title": "Topic", "summary": "s",
                     "claims": [], "audiences": [], "context_tags": [],
                     "category": "training", "subcategory": "hypertrophy",
                     "expertise_level": "beginner"}
                ]
            }
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            input_file = os.path.join(tmpdir, "batch_001_results.json")
            with open(input_file, "w") as f:
                json.dump(result_data, f)

            results = import_result_file(input_file)
            assert len(results) == 1
            assert results[0]["video_id"] == "abc123"

    def test_writes_to_output_dir(self):
        from process_yt_claude import write_imported_results

        results = [
            {
                "video_id": "abc123",
                "channel": "jeff-nippard",
                "title": "Test",
                "total_words": 5000,
                "segments": []
            }
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = write_imported_results(results, tmpdir)
            assert len(paths) == 1
            expected = os.path.join(tmpdir, "jeff-nippard", "abc123.json")
            assert paths[0] == expected
            assert os.path.exists(expected)


# ---------------------------------------------------------------------------
# Tests for CLI subcommands
# ---------------------------------------------------------------------------

class TestParseArgsClaude:
    """Test CLI argument parsing for process_yt_claude.py."""

    def test_export_subcommand(self):
        from process_yt_claude import parse_args

        args = parse_args(["export", "--input-dir", "/in", "--output-dir", "/out"])
        assert args.command == "export"
        assert args.input_dir == "/in"
        assert args.output_dir == "/out"

    def test_export_batch_size(self):
        from process_yt_claude import parse_args

        args = parse_args(["export", "--batch-size", "10"])
        assert args.batch_size == 10

    def test_export_default_batch_size(self):
        from process_yt_claude import parse_args

        args = parse_args(["export"])
        assert args.batch_size == 5

    def test_import_subcommand(self):
        from process_yt_claude import parse_args

        args = parse_args(["import", "--input-dir", "/results", "--output-dir", "/out"])
        assert args.command == "import"
        assert args.input_dir == "/results"
        assert args.output_dir == "/out"


# ---------------------------------------------------------------------------
# Tests for build_batch_prompt
# ---------------------------------------------------------------------------

class TestBuildBatchPrompt:
    """Test building the compact prompt for Claude Code batches."""

    def test_includes_expected_instructions(self):
        from process_yt_claude import build_batch_prompt

        prompt = build_batch_prompt()
        assert "segment" in prompt.lower()
        assert "segment_id" in prompt
        assert "summary" in prompt
        assert "claims" in prompt
        assert "category" in prompt
        assert "audiences" in prompt

    def test_prompt_is_compact(self):
        from process_yt_claude import build_batch_prompt

        prompt = build_batch_prompt()
        # Should be under ~500 words for token efficiency
        assert len(prompt.split()) < 500


# ---------------------------------------------------------------------------
# Tests for resume-safe on export
# ---------------------------------------------------------------------------

class TestExportResumeSafe:
    """Test that export skips transcripts that already have output."""

    def test_filters_already_processed(self):
        from process_yt_claude import filter_unprocessed

        transcripts = [
            ("jeff-nippard", "v1", "/path/v1.md"),
            ("jeff-nippard", "v2", "/path/v2.md"),
            ("jeff-nippard", "v3", "/path/v3.md"),
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            # Simulate v2 already processed
            channel_dir = os.path.join(tmpdir, "jeff-nippard")
            os.makedirs(channel_dir)
            with open(os.path.join(channel_dir, "v2.json"), "w") as f:
                json.dump({}, f)

            remaining = filter_unprocessed(transcripts, tmpdir)
            video_ids = [r[1] for r in remaining]
            assert "v1" in video_ids
            assert "v2" not in video_ids
            assert "v3" in video_ids

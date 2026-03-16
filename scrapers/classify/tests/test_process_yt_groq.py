"""Tests for process_yt_groq.py — Groq gpt-oss-120b YouTube transcript processor.

TDD tests covering:
- YAML frontmatter parsing
- Text trimming (skip intro 500 words, outro 200 words)
- Word-based chunking with overlap
- Prompt building for segment extraction
- LLM response parsing (segment JSON)
- Segment merging/deduplication across chunks
- Output path generation
- Resume-safe logic (skip existing outputs)
- CLI argument parsing
- Rate limiter / retry logic
- Request payload building
"""

import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "youtube"))


# ---------------------------------------------------------------------------
# Tests for YAML frontmatter parsing
# ---------------------------------------------------------------------------

class TestParseTranscript:
    """Test parsing YAML frontmatter + body from transcript .md files."""

    def test_parses_frontmatter_fields(self):
        from process_yt_groq import parse_transcript

        content = """---
source_id: "abc123"
source_domain: "youtube.com"
source_url: "https://www.youtube.com/watch?v=abc123"
title: "How to Build Muscle"
author: "Jeff Nippard"
channel: "jeff-nippard"
date_published: null
tags: []
content_type: "transcript"
source_tier: "tier3"
word_count: 5000
duration_seconds: 1800
transcript_type: "auto-generated"
language: "en"
---

This is the transcript body text that follows after frontmatter."""

        result = parse_transcript(content)
        assert result["source_id"] == "abc123"
        assert result["title"] == "How to Build Muscle"
        assert result["channel"] == "jeff-nippard"
        assert result["author"] == "Jeff Nippard"
        assert result["word_count"] == 5000
        assert result["duration_seconds"] == 1800
        assert "transcript body text" in result["body"]

    def test_body_excludes_frontmatter(self):
        from process_yt_groq import parse_transcript

        content = """---
source_id: "x"
title: "Test"
channel: "test-channel"
word_count: 100
---

Actual body content starts here."""

        result = parse_transcript(content)
        assert "---" not in result["body"]
        assert "source_id" not in result["body"]
        assert "Actual body content starts here" in result["body"]

    def test_handles_missing_optional_fields(self):
        from process_yt_groq import parse_transcript

        content = """---
source_id: "minimal"
title: "Minimal"
channel: "test"
---

Body text."""

        result = parse_transcript(content)
        assert result["source_id"] == "minimal"
        assert result.get("word_count") is None or result.get("word_count") == 0
        assert result.get("duration_seconds") is None or result.get("duration_seconds") == 0

    def test_handles_no_frontmatter(self):
        from process_yt_groq import parse_transcript

        content = "Just plain text with no YAML frontmatter at all."
        result = parse_transcript(content)
        assert result["body"] == content.strip()
        assert result["source_id"] == ""


# ---------------------------------------------------------------------------
# Tests for text trimming (skip intro/outro)
# ---------------------------------------------------------------------------

class TestTrimTranscriptText:
    """Test trimming intro (first 500 words) and outro (last 200 words)."""

    def test_trims_intro_and_outro(self):
        from process_yt_groq import trim_transcript_text

        # Build text: 500 intro words + 1000 middle words + 200 outro words
        intro = " ".join([f"intro{i}" for i in range(500)])
        middle = " ".join([f"middle{i}" for i in range(1000)])
        outro = " ".join([f"outro{i}" for i in range(200)])
        text = f"{intro} {middle} {outro}"

        result = trim_transcript_text(text, skip_start=500, skip_end=200)
        words = result.split()
        assert words[0] == "middle0"
        assert words[-1] == "middle999"
        assert len(words) == 1000

    def test_short_transcript_returns_all(self):
        from process_yt_groq import trim_transcript_text

        text = " ".join([f"word{i}" for i in range(400)])
        result = trim_transcript_text(text, skip_start=500, skip_end=200)
        # When text is shorter than skip_start, return everything
        assert len(result.split()) == 400

    def test_custom_skip_values(self):
        from process_yt_groq import trim_transcript_text

        words = " ".join([f"w{i}" for i in range(100)])
        result = trim_transcript_text(words, skip_start=10, skip_end=10)
        result_words = result.split()
        assert result_words[0] == "w10"
        assert result_words[-1] == "w89"
        assert len(result_words) == 80

    def test_transcript_exactly_at_boundaries(self):
        from process_yt_groq import trim_transcript_text

        # Exactly 700 words = 500 intro + 200 outro, nothing left
        text = " ".join([f"w{i}" for i in range(700)])
        result = trim_transcript_text(text, skip_start=500, skip_end=200)
        # Should return empty or the full text (depending on policy)
        # When nothing remains, return empty
        assert result == ""


# ---------------------------------------------------------------------------
# Tests for word-based chunking
# ---------------------------------------------------------------------------

class TestChunkText:
    """Test splitting text into ~2500 word chunks with overlap."""

    def test_short_text_single_chunk(self):
        from process_yt_groq import chunk_text

        text = " ".join([f"word{i}" for i in range(500)])
        chunks = chunk_text(text, chunk_size=2500, overlap=200)
        assert len(chunks) == 1
        assert len(chunks[0]["text"].split()) == 500
        assert chunks[0]["word_range"] == [0, 500]

    def test_exact_chunk_size(self):
        from process_yt_groq import chunk_text

        text = " ".join([f"word{i}" for i in range(2500)])
        chunks = chunk_text(text, chunk_size=2500, overlap=200)
        assert len(chunks) == 1

    def test_two_chunks_with_overlap(self):
        from process_yt_groq import chunk_text

        text = " ".join([f"word{i}" for i in range(4000)])
        chunks = chunk_text(text, chunk_size=2500, overlap=200)
        assert len(chunks) == 2
        # First chunk: words 0..2499
        assert chunks[0]["word_range"][0] == 0
        assert chunks[0]["word_range"][1] == 2500
        # Second chunk starts 200 words before the end of the first
        assert chunks[1]["word_range"][0] == 2300  # 2500 - 200
        # Overlap means both chunks share words 2300..2499

    def test_three_chunks(self):
        from process_yt_groq import chunk_text

        text = " ".join([f"w{i}" for i in range(6000)])
        chunks = chunk_text(text, chunk_size=2500, overlap=200)
        assert len(chunks) == 3

    def test_chunk_indices_are_sequential(self):
        from process_yt_groq import chunk_text

        text = " ".join([f"w{i}" for i in range(5000)])
        chunks = chunk_text(text, chunk_size=2500, overlap=200)
        for i, chunk in enumerate(chunks):
            assert chunk["chunk_index"] == i

    def test_empty_text_returns_empty(self):
        from process_yt_groq import chunk_text

        chunks = chunk_text("", chunk_size=2500, overlap=200)
        assert chunks == []


# ---------------------------------------------------------------------------
# Tests for prompt building
# ---------------------------------------------------------------------------

class TestBuildSegmentPrompt:
    """Test building the LLM prompt for segment extraction."""

    def test_includes_transcript_text(self):
        from process_yt_groq import build_segment_prompt

        text = "This is a test transcript about protein synthesis."
        prompt = build_segment_prompt(text, title="Protein Talk", channel="test")
        assert "protein synthesis" in prompt

    def test_includes_output_format(self):
        from process_yt_groq import build_segment_prompt

        prompt = build_segment_prompt("text", title="T", channel="c")
        assert "segment_id" in prompt
        assert "summary" in prompt
        assert "claims" in prompt
        assert "audiences" in prompt
        assert "context_tags" in prompt
        assert "category" in prompt
        assert "subcategory" in prompt
        assert "expertise_level" in prompt

    def test_includes_audience_list(self):
        from process_yt_groq import build_segment_prompt

        prompt = build_segment_prompt("text", title="T", channel="c")
        assert "bodybuilding" in prompt
        assert "muscle_gain" in prompt
        assert "general_fitness" in prompt

    def test_includes_category_list(self):
        from process_yt_groq import build_segment_prompt

        prompt = build_segment_prompt("text", title="T", channel="c")
        assert "training" in prompt
        assert "nutrition" in prompt
        assert "supplements" in prompt


# ---------------------------------------------------------------------------
# Tests for LLM response parsing
# ---------------------------------------------------------------------------

class TestParseSegmentResponse:
    """Test parsing LLM segment extraction responses."""

    def test_parses_clean_json_array(self):
        from process_yt_groq import parse_segment_response

        response = json.dumps([
            {
                "segment_id": 1,
                "title": "Rep ranges",
                "summary": "Discussion of rep ranges",
                "claims": ["8-12 reps optimal"],
                "audiences": ["bodybuilding"],
                "context_tags": ["hypertrophy"],
                "category": "training",
                "subcategory": "hypertrophy",
                "expertise_level": "intermediate"
            }
        ])
        result = parse_segment_response(response)
        assert len(result) == 1
        assert result[0]["title"] == "Rep ranges"

    def test_parses_markdown_fenced_json(self):
        from process_yt_groq import parse_segment_response

        response = '```json\n[{"segment_id":1,"title":"Test","summary":"s","claims":[],"audiences":[],"context_tags":[],"category":"training","subcategory":"hypertrophy","expertise_level":"beginner"}]\n```'
        result = parse_segment_response(response)
        assert len(result) == 1

    def test_parses_with_thinking_tags(self):
        from process_yt_groq import parse_segment_response

        response = '<think>reasoning here</think>\n[{"segment_id":1,"title":"T","summary":"s","claims":[],"audiences":[],"context_tags":[],"category":"training","subcategory":"strength","expertise_level":"beginner"}]'
        result = parse_segment_response(response)
        assert len(result) == 1

    def test_returns_empty_on_garbage(self):
        from process_yt_groq import parse_segment_response

        result = parse_segment_response("I cannot help with that request.")
        assert result == []

    def test_handles_nested_json_in_text(self):
        from process_yt_groq import parse_segment_response

        response = 'Here are the segments:\n[{"segment_id":1,"title":"T","summary":"s","claims":[],"audiences":[],"context_tags":[],"category":"training","subcategory":"strength","expertise_level":"beginner"}]\nHope this helps!'
        result = parse_segment_response(response)
        assert len(result) == 1


# ---------------------------------------------------------------------------
# Tests for segment merging across chunks
# ---------------------------------------------------------------------------

class TestMergeSegments:
    """Test merging and deduplicating segments from overlapping chunks."""

    def test_no_overlap_concatenates(self):
        from process_yt_groq import merge_segments

        segments_a = [
            {"segment_id": 1, "title": "Protein intake guidelines", "word_range": [0, 1000],
             "summary": "About protein", "claims": ["claim1"]},
        ]
        segments_b = [
            {"segment_id": 1, "title": "Sleep optimization strategies", "word_range": [2300, 3500],
             "summary": "About sleep", "claims": ["claim2"]},
        ]
        merged = merge_segments([segments_a, segments_b])
        assert len(merged) == 2
        # IDs should be renumbered
        assert merged[0]["segment_id"] == 1
        assert merged[1]["segment_id"] == 2

    def test_overlapping_segments_deduplicated(self):
        from process_yt_groq import merge_segments

        # Two chunks produce segments with similar titles in the overlap zone
        segments_a = [
            {"segment_id": 1, "title": "Protein intake", "word_range": [0, 1200],
             "summary": "Protein is important", "claims": ["1g/lb is optimal"]},
            {"segment_id": 2, "title": "Creatine benefits", "word_range": [1200, 2500],
             "summary": "Creatine is effective", "claims": ["5g daily is optimal"]},
        ]
        segments_b = [
            {"segment_id": 1, "title": "Creatine benefits", "word_range": [2300, 3800],
             "summary": "Creatine is very effective", "claims": ["5g daily sufficient", "loading not needed"]},
            {"segment_id": 2, "title": "Sleep and recovery", "word_range": [3800, 5000],
             "summary": "Sleep matters", "claims": ["7-9 hours optimal"]},
        ]
        merged = merge_segments([segments_a, segments_b])
        # The "Creatine benefits" segments should be merged
        assert len(merged) == 3
        titles = [s["title"] for s in merged]
        assert titles.count("Creatine benefits") == 1

    def test_empty_input(self):
        from process_yt_groq import merge_segments

        assert merge_segments([]) == []

    def test_single_chunk_no_merge_needed(self):
        from process_yt_groq import merge_segments

        segments = [
            {"segment_id": 1, "title": "Topic", "word_range": [0, 2000],
             "summary": "s", "claims": []},
        ]
        merged = merge_segments([segments])
        assert len(merged) == 1

    def test_merged_segment_combines_claims(self):
        from process_yt_groq import merge_segments

        segments_a = [
            {"segment_id": 1, "title": "Protein", "word_range": [1000, 2500],
             "summary": "Short summary A", "claims": ["claim A"]},
        ]
        segments_b = [
            {"segment_id": 1, "title": "Protein", "word_range": [2300, 4000],
             "summary": "Longer summary about protein B", "claims": ["claim A", "claim B"]},
        ]
        merged = merge_segments([segments_a, segments_b])
        assert len(merged) == 1
        # Claims should be deduplicated union
        assert "claim A" in merged[0]["claims"]
        assert "claim B" in merged[0]["claims"]
        assert len(merged[0]["claims"]) == 2


# ---------------------------------------------------------------------------
# Tests for output path generation
# ---------------------------------------------------------------------------

class TestOutputPath:
    """Test generating output file paths."""

    def test_generates_correct_path(self):
        from process_yt_groq import output_path

        result = output_path("youtube_processed", "jeff-nippard", "abc123")
        assert result == os.path.join("youtube_processed", "jeff-nippard", "abc123.json")

    def test_different_channels(self):
        from process_yt_groq import output_path

        result = output_path("/out", "huberman-lab", "xyz789")
        assert result == "/out/huberman-lab/xyz789.json"


# ---------------------------------------------------------------------------
# Tests for resume-safe logic
# ---------------------------------------------------------------------------

class TestIsAlreadyProcessed:
    """Test resume-safe: skip already processed videos."""

    def test_returns_false_when_no_output(self):
        from process_yt_groq import is_already_processed

        with tempfile.TemporaryDirectory() as tmpdir:
            assert is_already_processed(tmpdir, "test-channel", "vid123") is False

    def test_returns_true_when_output_exists(self):
        from process_yt_groq import is_already_processed

        with tempfile.TemporaryDirectory() as tmpdir:
            channel_dir = os.path.join(tmpdir, "test-channel")
            os.makedirs(channel_dir)
            with open(os.path.join(channel_dir, "vid123.json"), "w") as f:
                json.dump({"video_id": "vid123"}, f)
            assert is_already_processed(tmpdir, "test-channel", "vid123") is True


# ---------------------------------------------------------------------------
# Tests for building final output document
# ---------------------------------------------------------------------------

class TestBuildOutputDocument:
    """Test building the final output JSON document."""

    def test_contains_required_fields(self):
        from process_yt_groq import build_output_document

        segments = [
            {"segment_id": 1, "title": "Topic A", "text": "some text",
             "word_range": [500, 2000], "summary": "s", "claims": [],
             "audiences": [], "context_tags": [], "category": "training",
             "subcategory": "hypertrophy", "expertise_level": "beginner"},
        ]
        doc = build_output_document(
            video_id="abc123",
            channel="jeff-nippard",
            title="Test Video",
            total_words=5000,
            segments=segments,
        )
        assert doc["video_id"] == "abc123"
        assert doc["channel"] == "jeff-nippard"
        assert doc["title"] == "Test Video"
        assert doc["total_words"] == 5000
        assert len(doc["segments"]) == 1
        assert doc["segments"][0]["segment_id"] == 1


# ---------------------------------------------------------------------------
# Tests for request payload building
# ---------------------------------------------------------------------------

class TestBuildRequestPayload:
    """Test building the HTTP request payload for Groq API."""

    def test_contains_model_and_messages(self):
        from process_yt_groq import build_request_payload

        payload = build_request_payload("Test prompt", "openai/gpt-oss-120b")
        assert payload["model"] == "openai/gpt-oss-120b"
        assert len(payload["messages"]) == 2
        assert payload["messages"][0]["role"] == "system"
        assert payload["messages"][1]["role"] == "user"
        assert payload["messages"][1]["content"] == "Test prompt"

    def test_has_temperature_and_max_tokens(self):
        from process_yt_groq import build_request_payload

        payload = build_request_payload("prompt", "model")
        assert "temperature" in payload
        assert "max_tokens" in payload


# ---------------------------------------------------------------------------
# Tests for CLI argument parsing
# ---------------------------------------------------------------------------

class TestParseArgs:
    """Test CLI argument parsing for process_yt_groq.py."""

    def test_defaults(self):
        from process_yt_groq import parse_args

        args = parse_args([])
        assert "5-youtube-transcripts" in args.input_dir or args.input_dir is not None
        assert args.output_dir is not None
        assert args.concurrency == 3
        assert args.max_rpm == 40
        assert "gpt-oss-120b" in args.model

    def test_custom_args(self):
        from process_yt_groq import parse_args

        args = parse_args([
            "--input-dir", "/custom/input",
            "--output-dir", "/custom/output",
            "--concurrency", "10",
            "--max-rpm", "100",
            "--model", "custom-model",
        ])
        assert args.input_dir == "/custom/input"
        assert args.output_dir == "/custom/output"
        assert args.concurrency == 10
        assert args.max_rpm == 100
        assert args.model == "custom-model"


# ---------------------------------------------------------------------------
# Tests for discover_transcripts
# ---------------------------------------------------------------------------

class TestDiscoverTranscripts:
    """Test discovering transcript files from input directory."""

    def test_finds_md_files_in_channel_dirs(self):
        from process_yt_groq import discover_transcripts

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create channel/articles structure
            channel_dir = os.path.join(tmpdir, "jeff-nippard", "articles")
            os.makedirs(channel_dir)
            with open(os.path.join(channel_dir, "abc123.md"), "w") as f:
                f.write("---\nsource_id: abc123\ntitle: Test\nchannel: jeff-nippard\n---\nBody")
            with open(os.path.join(channel_dir, "def456.md"), "w") as f:
                f.write("---\nsource_id: def456\ntitle: Test2\nchannel: jeff-nippard\n---\nBody2")

            results = discover_transcripts(tmpdir)
            assert len(results) == 2
            # Each result should be a (channel, video_id, filepath) tuple
            channels = {r[0] for r in results}
            assert "jeff-nippard" in channels

    def test_skips_non_md_files(self):
        from process_yt_groq import discover_transcripts

        with tempfile.TemporaryDirectory() as tmpdir:
            channel_dir = os.path.join(tmpdir, "test-channel", "articles")
            os.makedirs(channel_dir)
            with open(os.path.join(channel_dir, "vid.md"), "w") as f:
                f.write("---\nsource_id: vid\ntitle: T\nchannel: test-channel\n---\nB")
            with open(os.path.join(channel_dir, "notes.txt"), "w") as f:
                f.write("not a transcript")

            results = discover_transcripts(tmpdir)
            assert len(results) == 1

    def test_empty_dir_returns_empty(self):
        from process_yt_groq import discover_transcripts

        with tempfile.TemporaryDirectory() as tmpdir:
            results = discover_transcripts(tmpdir)
            assert results == []


# ---------------------------------------------------------------------------
# Tests for write_output
# ---------------------------------------------------------------------------

class TestWriteOutput:
    """Test writing processed output to disk."""

    def test_creates_channel_dir_and_file(self):
        from process_yt_groq import write_output

        with tempfile.TemporaryDirectory() as tmpdir:
            doc = {"video_id": "abc123", "channel": "test", "segments": []}
            path = write_output(tmpdir, "test", "abc123", doc)
            assert os.path.exists(path)
            with open(path) as f:
                data = json.load(f)
            assert data["video_id"] == "abc123"

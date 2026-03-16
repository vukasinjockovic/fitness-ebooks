# YouTube Transcript Processor

Segments YouTube transcripts into topics, extracts claims, classifies audiences/tags using two approaches.

## Input Format

Transcripts live at:
```
/home/vuk/fitness-books/scrapers/content/5-youtube-transcripts/CHANNEL/articles/VIDEO_ID.md
```

Each `.md` file has YAML frontmatter (source_id, title, channel, duration_seconds, word_count) followed by raw transcript text. Transcripts range from ~500 to ~27,000 words.

## Output Format

Both scripts produce the same JSON format at `youtube_processed/CHANNEL/VIDEO_ID.json`:

```json
{
  "video_id": "2kwl5LiuCs4",
  "channel": "jeff-nippard",
  "title": "The Smartest Way To Build Muscle",
  "total_words": 14718,
  "segments": [
    {
      "segment_id": 1,
      "title": "Optimal rep ranges for hypertrophy",
      "summary": "Jeff reviews 3 meta-analyses showing 8-15 reps are optimal...",
      "claims": ["8-12 reps optimal for hypertrophy when volume equated"],
      "audiences": ["bodybuilding", "muscle_gain", "intermediate"],
      "context_tags": ["hypertrophy", "rep_ranges", "evidence_based"],
      "category": "training",
      "subcategory": "hypertrophy",
      "expertise_level": "intermediate"
    }
  ]
}
```

## Processing Logic

Both scripts:
1. Skip the first ~500 words (intro/sponsor garbage)
2. Skip the last ~200 words (outro)
3. Send remaining text to an LLM for topic segmentation
4. Classify each segment (audiences, tags, category, expertise)

## Approach 1: Groq API (`process_yt_groq.py`)

Uses async httpx to call Groq's gpt-oss-120b model. Splits long transcripts into ~2500-word chunks with 200-word overlap, processes each chunk, then merges overlapping segments.

```bash
CLASSIFY_API_KEY=gsk_xxx \
CLASSIFY_API_BASE=https://api.groq.com/openai/v1 \
python3 youtube/process_yt_groq.py \
  --input-dir /home/vuk/fitness-books/scrapers/content/5-youtube-transcripts/ \
  --output-dir youtube_processed/ \
  --concurrency 3 \
  --max-rpm 40 \
  --model openai/gpt-oss-120b
```

### Features
- Async with semaphore-controlled concurrency
- RPM rate limiter (default 40 RPM for Groq free tier)
- Retry with exponential backoff (429, 500/502/503)
- Resume-safe: skips videos with existing output JSON
- Handles thinking tags, markdown fences in LLM output
- Segment merging across chunk boundaries using title similarity

### Arguments
| Flag | Default | Description |
|------|---------|-------------|
| `--input-dir` | `../content/5-youtube-transcripts/` | Transcript source directory |
| `--output-dir` | `youtube_processed/` | Output directory |
| `--concurrency` | `3` | Max parallel API requests |
| `--max-rpm` | `40` | Rate limit (requests/minute) |
| `--model` | `openai/gpt-oss-120b` | Model identifier |

### Environment Variables
| Variable | Required | Description |
|----------|----------|-------------|
| `CLASSIFY_API_KEY` | Yes | Groq API key (`gsk_...`) |
| `CLASSIFY_API_BASE` | No | API base URL (default: `https://api.groq.com/openai/v1`) |

## Approach 2: Claude Code Haiku (`process_yt_claude.py`)

Two-phase workflow designed for Claude Code `/fast` (Haiku) processing.

### Phase 1: Export Batches

Reads transcripts, trims to ~2500 words (words 500-3000), groups into batches of 5.

```bash
python3 youtube/process_yt_claude.py export \
  --input-dir /home/vuk/fitness-books/scrapers/content/5-youtube-transcripts/ \
  --output-dir youtube_batches/ \
  --batch-size 5
```

Output: `youtube_batches/batch_001.json`, `batch_002.json`, etc.

Each batch file contains:
```json
{
  "batch_id": 1,
  "prompt": "Segment each transcript into topics...",
  "transcripts": [
    {"video_id": "xxx", "channel": "jeff-nippard", "title": "...", "text": "..."}
  ]
}
```

### Phase 2: Process with Claude Code

Feed batch files to Claude Code Haiku:
```
Read youtube_batches/batch_001.json. Follow the prompt. Process each transcript.
Return ONLY the JSON array output.
```

Save each result to `youtube_results/batch_001_results.json`.

### Phase 3: Import Results

```bash
python3 youtube/process_yt_claude.py import \
  --input-dir youtube_results/ \
  --output-dir youtube_processed/
```

### Export Arguments
| Flag | Default | Description |
|------|---------|-------------|
| `--input-dir` | `../content/5-youtube-transcripts/` | Transcript source |
| `--output-dir` | `youtube_batches/` | Batch output directory |
| `--processed-dir` | `youtube_processed/` | Already-processed dir (for resume) |
| `--batch-size` | `5` | Transcripts per batch |

### Import Arguments
| Flag | Default | Description |
|------|---------|-------------|
| `--input-dir` | `youtube_results/` | Claude Code results directory |
| `--output-dir` | `youtube_processed/` | Final output directory |

## Cost/Speed/Quality Comparison

| Metric | Groq gpt-oss-120b | Claude Code Haiku |
|--------|-------------------|-------------------|
| **Cost per transcript** | ~$0.001 (Groq pricing) | ~$0.003 (Haiku pricing) |
| **Speed** | ~3-5 sec/transcript (API latency) | ~1-2 sec/transcript (local inference) |
| **Quality** | Good for factual extraction | Better instruction following |
| **Context limit** | 128K tokens | 200K tokens |
| **Batch efficiency** | 1 transcript per call (chunked) | 5 transcripts per call |
| **Setup** | API key only | Claude Code with /fast mode |
| **Automation** | Fully automated | Semi-automated (manual Claude Code step) |
| **Best for** | Bulk processing, hands-off | Higher quality, smaller batches |

### Token Budget (Claude Code Haiku)

Per batch of 5 transcripts:
- Input: ~2500 words/transcript x 5 = ~12,500 words = ~16K tokens + prompt (~100 tokens)
- Output: ~1K tokens/transcript x 5 = ~5K tokens
- Total: ~21K tokens per batch (well within Haiku's 200K context)

### Token Budget (Groq)

Per chunk (~2500 words):
- Input: ~3,250 tokens (text) + ~200 tokens (prompt) = ~3,450 tokens
- Output: ~500-1,000 tokens
- Total: ~4,000-4,500 tokens per API call

## Testing

```bash
# Run all YouTube processor tests
python3 -m pytest tests/test_process_yt_groq.py tests/test_process_yt_claude.py -v

# Run just groq tests
python3 -m pytest tests/test_process_yt_groq.py -v

# Run just claude tests
python3 -m pytest tests/test_process_yt_claude.py -v
```

## Data

Source channels (27 total): athlean-x, biolayne-yt, blogilates, buff-dudes, fitmencook, foundmyfitness-yt, greg-doucette, huberman-lab, jeff-nippard, jeremy-ethier, krissy-cela, madfit, mario-tomic, megsquats, mindpumptv, natacha-oceane, nutritionfacts-yt, protein-chef, remington-james, renaissance-periodization, sean-nalewanyj, sydney-cummings, thomas-delauer, will-tennyson.

Transcript sizes range from ~500 words (short vlog) to ~20,000 words (Huberman Lab episodes).

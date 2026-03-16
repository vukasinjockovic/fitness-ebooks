#!/usr/bin/env python3
"""
Massively parallel YouTube transcript scraper using 200 Tor SOCKS5 proxies.

Phase 1: Enumerate video IDs per channel using yt-dlp (sequential, no proxy).
Phase 2: Scrape transcripts via youtube-transcript-api through Tor circuits.

Key design: A ProxyPool pre-tests all proxies, tracks which are working in
real-time, and enforces per-proxy rate limits to avoid burning good IPs.

Usage:
    python3 scrape_all_transcripts.py --phase1
    python3 scrape_all_transcripts.py --phase2
    python3 scrape_all_transcripts.py --phase1 --phase2
    python3 scrape_all_transcripts.py --phase2 --channel jeff-nippard --limit 20 --workers 10
"""

import argparse
import functools
import os
import random
import re
import subprocess
import sys
import threading
import time

# Force unbuffered stdout for background execution visibility
os.environ["PYTHONUNBUFFERED"] = "1"
print = functools.partial(print, flush=True)  # type: ignore[assignment]
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api.proxies import GenericProxyConfig
from youtube_transcript_api._errors import (
    CouldNotRetrieveTranscript,
    NoTranscriptFound,
    TranscriptsDisabled,
    VideoUnavailable,
    RequestBlocked,
    IpBlocked,
    YouTubeRequestFailed,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent

# 200 Tor SOCKS5 proxy ports
ALL_PROXY_PORTS = list(range(61000, 61100)) + list(range(10300, 10400))

# Per-proxy rate limit: minimum seconds between requests on the same proxy
PROXY_COOLDOWN = 2.0

# Maximum retries per video (cycles through available good proxies)
MAX_RETRIES = 15

# Delay between retries on RequestBlocked
BLOCKED_BACKOFF_BASE = 1.0

# Test video ID used to probe proxies (short, known to have a transcript)
PROBE_VIDEO_ID = "dQw4w9WgXcQ"

CHANNELS = {
    # Batch 1 - Tier A
    "athlean-x": "UCe0TLA0EsQbE-MjuHXevj2A",
    "jeff-nippard": "@JeffNippard",
    "jeremy-ethier": "@JeremyEthier",
    "huberman-lab": "@hubermanlab",
    "will-tennyson": "@WillTennyson",
    "renaissance-periodization": "@RenaissancePeriodization",
    "thomas-delauer": "/c/ThomasDeLauerOfficial",
    "greg-doucette": "@GregDoucette",
    # Batch 2 - Tier B
    "buff-dudes": "@BuffDudes",
    "natacha-oceane": "UCjfG0dyMUiqKleUnkX6zBrA",
    "nutritionfacts-yt": "@NutritionFactsOrg",
    "sean-nalewanyj": "@sean_nalewanyj",
    "foundmyfitness-yt": "@FoundMyFitness",
    "remington-james": "@TheRemingtonJames",
    "fitmencook": "@FitMenCook",
    "biolayne-yt": "UCqMBA83S0TnfTlTeE5j1mgQ",
    # Batch 3 - Tier C
    "protein-chef": "@TheProteinChef",
    "mario-tomic": "@MarioTomicOfficial",
    "mindpumptv": "@MindPumpShow",
    "megsquats": "@megsquats",
}

# Channel display names for frontmatter
CHANNEL_AUTHORS = {
    "athlean-x": "ATHLEAN-X (Jeff Cavaliere)",
    "jeff-nippard": "Jeff Nippard",
    "jeremy-ethier": "Jeremy Ethier",
    "huberman-lab": "Andrew Huberman",
    "will-tennyson": "Will Tennyson",
    "renaissance-periodization": "Renaissance Periodization",
    "thomas-delauer": "Thomas DeLauer",
    "greg-doucette": "Greg Doucette",
    "buff-dudes": "Buff Dudes",
    "natacha-oceane": "Natacha Oceane",
    "nutritionfacts-yt": "NutritionFacts.org",
    "sean-nalewanyj": "Sean Nalewanyj",
    "foundmyfitness-yt": "Dr. Rhonda Patrick",
    "remington-james": "Remington James",
    "fitmencook": "FitMenCook",
    "biolayne-yt": "Layne Norton",
    "protein-chef": "The Protein Chef",
    "mario-tomic": "Mario Tomic",
    "mindpumptv": "Mind Pump",
    "megsquats": "Meg Squats",
}

# Auto-caption artifacts to remove
CAPTION_NOISE = re.compile(
    r"\[(?:Music|Applause|Laughter|Cheering|Cheers|Foreign|"
    r"foreign|music|applause|laughter|__)"
    r"(?:\s[^\]]*?)?\]",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# ProxyPool: Thread-safe pool with health tracking and rate limiting
# ---------------------------------------------------------------------------

class ProxyPool:
    """Manages proxy ports with health tracking and per-proxy rate limiting.

    Proxies are pre-tested; only working ones are offered to workers.
    Each proxy has a cooldown timer to prevent rapid reuse.
    Proxies that fail with RequestBlocked are temporarily quarantined.
    """

    def __init__(self, ports: list[int], cooldown: float = PROXY_COOLDOWN):
        self._lock = threading.Lock()
        self._all_ports = list(ports)
        self._cooldown = cooldown
        # port -> last_used_time
        self._last_used: dict[int, float] = {}
        # port -> quarantine_until_time (temporarily blocked)
        self._quarantine: dict[int, float] = {}
        # Ports that passed the initial probe
        self._good_ports: list[int] = []
        # Condition to signal when a proxy becomes available
        self._available = threading.Condition(self._lock)

    def probe_all(self, test_video_id: str = PROBE_VIDEO_ID,
                  max_workers: int = 50) -> list[int]:
        """Test all proxies in parallel, return list of working ports."""
        print(f"[ProxyPool] Probing {len(self._all_ports)} proxies...")

        good = []
        bad = 0
        lock = threading.Lock()

        def test_one(port: int) -> None:
            nonlocal bad
            try:
                proxy = GenericProxyConfig(
                    https_url=f"socks5h://127.0.0.1:{port}"
                )
                api = YouTubeTranscriptApi(proxy_config=proxy)
                api.fetch(test_video_id, languages=["en"])
                with lock:
                    good.append(port)
            except (RequestBlocked, IpBlocked):
                with lock:
                    bad += 1
            except Exception:
                with lock:
                    bad += 1

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(test_one, p) for p in self._all_ports]
            for f in as_completed(futures):
                f.result()  # propagate unexpected errors

        with self._lock:
            self._good_ports = sorted(good)
            for p in self._good_ports:
                self._last_used[p] = 0.0

        print(f"[ProxyPool] {len(good)} working / {bad} blocked "
              f"out of {len(self._all_ports)} total")
        return list(self._good_ports)

    @property
    def good_count(self) -> int:
        with self._lock:
            return len(self._good_ports)

    def acquire(self, timeout: float = 30.0, exclude: set[int] | None = None) -> int | None:
        """Get a proxy port that is off cooldown. Blocks until one is available.

        Args:
            timeout: Max seconds to wait.
            exclude: Set of ports to skip (already tried for this video).

        Returns:
            A proxy port, or None if timed out.
        """
        deadline = time.monotonic() + timeout
        exclude = exclude or set()

        with self._available:
            while True:
                now = time.monotonic()
                if now >= deadline:
                    return None

                best_port = None
                best_ready_at = float("inf")

                for port in self._good_ports:
                    if port in exclude:
                        continue
                    # Check quarantine
                    q_until = self._quarantine.get(port, 0.0)
                    if now < q_until:
                        continue
                    # Check cooldown
                    ready_at = self._last_used.get(port, 0.0) + self._cooldown
                    if ready_at <= now:
                        # Immediately available
                        self._last_used[port] = now
                        return port
                    if ready_at < best_ready_at:
                        best_ready_at = ready_at
                        best_port = port

                # No port immediately ready; wait until the soonest one cools down
                if best_port is not None:
                    wait = best_ready_at - now
                else:
                    wait = min(1.0, deadline - now)

                if wait > 0:
                    self._available.wait(timeout=min(wait, deadline - now))

    def release_ok(self, port: int) -> None:
        """Mark proxy as successfully used (still good)."""
        pass  # Nothing special needed; cooldown is enforced in acquire.

    def release_blocked(self, port: int, quarantine_seconds: float = 30.0) -> None:
        """Mark proxy as blocked. Quarantine it temporarily."""
        with self._lock:
            self._quarantine[port] = time.monotonic() + quarantine_seconds
            # Wake waiters so they skip this port
            self._available.notify_all()

    def release_error(self, port: int) -> None:
        """Mark proxy as having a transient error. Short quarantine."""
        with self._lock:
            self._quarantine[port] = time.monotonic() + 5.0
            self._available.notify_all()


# ---------------------------------------------------------------------------
# Thread-safe counters
# ---------------------------------------------------------------------------

class Stats:
    def __init__(self):
        self._lock = threading.Lock()
        self.success = 0
        self.failed = 0
        self.no_transcript = 0
        self.skipped = 0
        self.total = 0
        self._start_time = time.monotonic()

    def inc_success(self):
        with self._lock:
            self.success += 1

    def inc_failed(self):
        with self._lock:
            self.failed += 1

    def inc_no_transcript(self):
        with self._lock:
            self.no_transcript += 1

    def inc_skipped(self):
        with self._lock:
            self.skipped += 1

    def snapshot(self):
        with self._lock:
            done = self.success + self.failed + self.no_transcript + self.skipped
            elapsed = time.monotonic() - self._start_time
            rate = self.success / elapsed if elapsed > 0 else 0
            return (
                done,
                self.total,
                self.success,
                self.failed,
                self.no_transcript,
                self.skipped,
                rate,
            )


# ---------------------------------------------------------------------------
# Phase 1: Enumerate video IDs via yt-dlp
# ---------------------------------------------------------------------------

def enumerate_channel(channel_name: str, handle: str) -> int:
    """Enumerate all video IDs for a channel using yt-dlp --flat-playlist."""
    channel_dir = BASE_DIR / channel_name
    channel_dir.mkdir(parents=True, exist_ok=True)
    tsv_path = channel_dir / "video_ids.tsv"

    # Build yt-dlp URL
    if handle.startswith("UC"):
        url = f"https://www.youtube.com/channel/{handle}/videos"
    elif handle.startswith("/c/"):
        url = f"https://www.youtube.com{handle}/videos"
    else:
        url = f"https://www.youtube.com/{handle}/videos"

    print(f"[Phase 1] Enumerating {channel_name} ({handle})...")

    cmd = [
        "yt-dlp",
        "--flat-playlist",
        "--print", "%(id)s\t%(title)s\t%(duration)s",
        "--no-warnings",
        "--ignore-errors",
        url,
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
        )

        lines = [l.strip() for l in result.stdout.strip().split("\n") if l.strip()]
        if not lines:
            print(f"  WARNING: No videos found for {channel_name}")
            if result.stderr:
                for line in result.stderr.strip().split("\n")[:5]:
                    print(f"    stderr: {line}")
            return 0

        with open(tsv_path, "w") as f:
            f.write("video_id\ttitle\tduration_seconds\n")
            for line in lines:
                f.write(line + "\n")

        print(f"  Found {len(lines)} videos for {channel_name}")
        return len(lines)

    except subprocess.TimeoutExpired:
        print(f"  TIMEOUT enumerating {channel_name}")
        return 0
    except Exception as e:
        print(f"  ERROR enumerating {channel_name}: {e}")
        return 0


def run_phase1(channels: dict[str, str]) -> None:
    """Run Phase 1: enumerate all video IDs."""
    print("=" * 70)
    print("PHASE 1: Enumerating video IDs")
    print("=" * 70)

    total = 0
    for channel_name, handle in channels.items():
        count = enumerate_channel(channel_name, handle)
        total += count

    print(f"\n[Phase 1 Complete] Total videos enumerated: {total}")


# ---------------------------------------------------------------------------
# Phase 2: Scrape transcripts
# ---------------------------------------------------------------------------

def load_video_list(channel_name: str) -> list[tuple[str, str, str]]:
    """Load video IDs from TSV. Returns list of (video_id, title, duration)."""
    tsv_path = BASE_DIR / channel_name / "video_ids.tsv"
    if not tsv_path.exists():
        return []

    videos = []
    with open(tsv_path) as f:
        header = True
        for line in f:
            if header:
                header = False
                continue
            parts = line.strip().split("\t")
            if len(parts) >= 1:
                vid = parts[0]
                title = parts[1] if len(parts) > 1 else ""
                dur = parts[2] if len(parts) > 2 else ""
                videos.append((vid, title, dur))
    return videos


def clean_transcript_text(text: str) -> str:
    """Clean auto-caption artifacts from transcript text."""
    text = CAPTION_NOISE.sub("", text)
    text = re.sub(r"[♪♫]+", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def save_transcript_md(
    channel: str,
    video_id: str,
    title: str,
    text: str,
    is_generated: bool,
    language_code: str,
    duration_str: str,
) -> Path:
    """Save transcript as markdown with YAML frontmatter."""
    articles_dir = BASE_DIR / channel / "articles"
    articles_dir.mkdir(parents=True, exist_ok=True)
    out_path = articles_dir / f"{video_id}.md"

    word_count = len(text.split())
    transcript_type = "auto-generated" if is_generated else "manual"

    try:
        duration_seconds = int(float(duration_str)) if duration_str and duration_str != "NA" else None
    except (ValueError, TypeError):
        duration_seconds = None

    safe_title = title.replace('"', '\\"') if title else ""
    author = CHANNEL_AUTHORS.get(channel, channel)

    frontmatter = f"""---
source_id: "{video_id}"
source_domain: "youtube.com"
source_url: "https://www.youtube.com/watch?v={video_id}"
title: "{safe_title}"
author: "{author}"
channel: "{channel}"
date_published: null
tags: []
content_type: "transcript"
source_tier: "tier3"
word_count: {word_count}
duration_seconds: {duration_seconds}
transcript_type: "{transcript_type}"
language: "{language_code}"
---

{text}
"""

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(frontmatter)

    return out_path


def scrape_one_video(
    video_id: str,
    title: str,
    channel: str,
    duration_str: str,
    pool: ProxyPool,
    stats: Stats,
    failed_log: list,
    failed_lock: threading.Lock,
) -> bool:
    """Scrape a single video transcript. Returns True on success."""
    out_path = BASE_DIR / channel / "articles" / f"{video_id}.md"
    if out_path.exists():
        stats.inc_skipped()
        return True

    last_error = None
    ports_tried: set[int] = set()

    for attempt in range(MAX_RETRIES):
        # Acquire a proxy from the pool (blocks until one is off cooldown)
        port = pool.acquire(timeout=60.0, exclude=ports_tried)
        if port is None:
            # All proxies exhausted or timed out; clear exclusions and retry
            if len(ports_tried) >= pool.good_count:
                ports_tried.clear()
                port = pool.acquire(timeout=60.0)
            if port is None:
                break

        try:
            proxy = GenericProxyConfig(
                https_url=f"socks5h://127.0.0.1:{port}"
            )
            api = YouTubeTranscriptApi(proxy_config=proxy)

            # Try English first, then any language
            try:
                result = api.fetch(video_id, languages=["en"])
            except NoTranscriptFound:
                transcript_list = api.list(video_id)
                found = None
                for t in transcript_list:
                    if t.language_code.startswith("en"):
                        found = t
                        break
                if found is None:
                    for t in transcript_list:
                        found = t
                        break
                if found is None:
                    pool.release_ok(port)
                    stats.inc_no_transcript()
                    return False
                result = found.fetch()

            # Build and clean text
            raw_text = " ".join(snippet.text for snippet in result.snippets)
            cleaned = clean_transcript_text(raw_text)

            if not cleaned or len(cleaned) < 20:
                pool.release_ok(port)
                stats.inc_no_transcript()
                return False

            save_transcript_md(
                channel=channel,
                video_id=video_id,
                title=title,
                text=cleaned,
                is_generated=result.is_generated,
                language_code=result.language_code,
                duration_str=duration_str,
            )
            pool.release_ok(port)
            stats.inc_success()
            return True

        except (TranscriptsDisabled, VideoUnavailable):
            pool.release_ok(port)
            stats.inc_no_transcript()
            return False

        except (RequestBlocked, IpBlocked) as e:
            last_error = e
            ports_tried.add(port)
            pool.release_blocked(port, quarantine_seconds=60.0)
            time.sleep(BLOCKED_BACKOFF_BASE + attempt * 0.5)
            continue

        except (YouTubeRequestFailed, CouldNotRetrieveTranscript) as e:
            last_error = e
            ports_tried.add(port)
            pool.release_error(port)
            time.sleep(0.5 + attempt * 0.5)
            continue

        except Exception as e:
            last_error = e
            ports_tried.add(port)
            pool.release_error(port)
            time.sleep(0.5)
            continue

    # All retries exhausted
    stats.inc_failed()
    err_short = type(last_error).__name__ if last_error else "unknown"
    with failed_lock:
        failed_log.append(f"{video_id}\t{channel}\t{title}\t{err_short}")
    return False


def run_phase2(
    channels: dict[str, str],
    workers: int = 200,
    limit: int = 0,
    single_channel: str | None = None,
    skip_probe: bool = False,
) -> None:
    """Run Phase 2: scrape transcripts in parallel through Tor."""
    print("=" * 70)
    print(f"PHASE 2: Scraping transcripts")
    print("=" * 70)

    # --- Build proxy pool ---
    pool = ProxyPool(ALL_PROXY_PORTS)

    if skip_probe:
        # Trust all proxies (useful if you already know they work)
        pool._good_ports = list(ALL_PROXY_PORTS)
        for p in ALL_PROXY_PORTS:
            pool._last_used[p] = 0.0
        print(f"[ProxyPool] Skipping probe, using all {len(ALL_PROXY_PORTS)} proxies")
    else:
        good_ports = pool.probe_all(max_workers=50)
        if not good_ports:
            print("FATAL: No working proxies found. Aborting.")
            return

    # Cap workers to number of good proxies (no point having more)
    effective_workers = min(workers, pool.good_count)
    print(f"[Phase 2] Using {effective_workers} workers "
          f"(capped to {pool.good_count} working proxies)")

    # --- Collect videos ---
    all_videos: list[tuple[str, str, str, str]] = []

    target_channels = (
        {single_channel: channels[single_channel]} if single_channel else channels
    )

    for channel_name in target_channels:
        videos = load_video_list(channel_name)
        if not videos:
            print(f"  WARNING: No video IDs for {channel_name} (run --phase1 first)")
            continue
        for vid, title, dur in videos:
            all_videos.append((vid, title, channel_name, dur))
        print(f"  {channel_name}: {len(videos)} videos")

    if not all_videos:
        print("No videos to scrape!")
        return

    # Shuffle to distribute channels evenly across workers
    random.shuffle(all_videos)

    if limit > 0:
        all_videos = all_videos[:limit]

    total = len(all_videos)
    print(f"\nTotal videos to process: {total}")
    print(f"Workers: {effective_workers}")
    print(f"Good proxies: {pool.good_count}")
    print()

    stats = Stats()
    stats.total = total
    failed_log: list[str] = []
    failed_lock = threading.Lock()

    report_interval = max(1, min(100, total // 20))

    with ThreadPoolExecutor(max_workers=effective_workers) as executor:
        futures = {}
        for vid, title, channel, dur in all_videos:
            future = executor.submit(
                scrape_one_video,
                vid, title, channel, dur,
                pool, stats, failed_log, failed_lock,
            )
            futures[future] = (vid, channel)

        done_count = 0
        for future in as_completed(futures):
            done_count += 1
            try:
                future.result()
            except Exception as e:
                vid, ch = futures[future]
                print(f"  UNEXPECTED ERROR {vid} ({ch}): {e}")

            if done_count % report_interval == 0 or done_count == total:
                done, tot, succ, fail, no_tr, skip, rate = stats.snapshot()
                print(
                    f"  [{done}/{tot}] "
                    f"{succ} success, {fail} failed, {no_tr} no-transcript, "
                    f"{skip} skipped | {rate:.1f} vids/sec"
                )

    # Final stats
    done, tot, succ, fail, no_tr, skip, rate = stats.snapshot()
    print()
    print("=" * 70)
    print("PHASE 2 COMPLETE")
    print(f"  Success:       {succ}")
    print(f"  Failed:        {fail}")
    print(f"  No transcript: {no_tr}")
    print(f"  Skipped:       {skip}")
    print(f"  Rate:          {rate:.1f} vids/sec")
    print("=" * 70)

    if failed_log:
        failed_path = BASE_DIR / "failed_videos.txt"
        with open(failed_path, "a") as f:
            f.write(f"\n# Run at {datetime.now(timezone.utc).isoformat()}\n")
            for line in failed_log:
                f.write(line + "\n")
        print(f"\nFailed video IDs appended to: {failed_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="YouTube transcript scraper with 200 Tor proxies"
    )
    parser.add_argument("--phase1", action="store_true", help="Enumerate video IDs")
    parser.add_argument("--phase2", action="store_true", help="Scrape transcripts")
    parser.add_argument("--channel", type=str, help="Scrape only this channel")
    parser.add_argument("--workers", type=int, default=200,
                        help="Worker count (default 200)")
    parser.add_argument("--limit", type=int, default=0,
                        help="Limit to first N videos (0=all)")
    parser.add_argument("--skip-probe", action="store_true",
                        help="Skip proxy probing (trust all proxies)")

    args = parser.parse_args()

    if not args.phase1 and not args.phase2:
        parser.print_help()
        sys.exit(1)

    if args.channel and args.channel not in CHANNELS:
        print(f"Unknown channel: {args.channel}")
        print(f"Available: {', '.join(sorted(CHANNELS.keys()))}")
        sys.exit(1)

    if args.phase1:
        if args.channel:
            run_phase1({args.channel: CHANNELS[args.channel]})
        else:
            run_phase1(CHANNELS)

    if args.phase2:
        run_phase2(
            channels=CHANNELS,
            workers=args.workers,
            limit=args.limit,
            single_channel=args.channel,
            skip_probe=args.skip_probe,
        )


if __name__ == "__main__":
    main()

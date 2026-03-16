#!/usr/bin/env python3
"""
PubMed Bulk Abstract Scraper.

Searches PubMed (title/abstract) for fitness/nutrition-relevant papers,
fetches metadata + abstracts via E-utilities efetch in batches of 200 IDs,
and saves as markdown files with YAML frontmatter.

Usage:
    python3 scrape_pubmed.py                           # Run all default queries
    python3 scrape_pubmed.py --query "creatine supplementation" --max-results 5000
    python3 scrape_pubmed.py --queries-file queries.txt
    python3 scrape_pubmed.py --journal "Nutrients"     # Journal-specific search

Rate limit: 2 req/sec (conservative, NCBI allows 3 without API key).
Resume-safe: skips PMIDs already scraped (checks filename prefix).
"""

import json
import os
import re
import sys
import time
import unicodedata
import argparse
from xml.etree import ElementTree as ET
from datetime import datetime

import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ARTICLES_DIR = os.path.join(SCRIPT_DIR, "articles")

NCBI_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
TOOL = "gymzilla-scraper"
EMAIL = "contact@gymzillatribe.com"
DELAY = 0.5  # 2 req/sec

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "GymZilla/1.0 (contact@gymzillatribe.com)"
})

# Default MeSH-based and keyword queries for fitness/nutrition
DEFAULT_QUERIES = [
    # Core fitness/nutrition MeSH queries
    '"exercise"[MeSH] AND "muscle proteins"[MeSH]',
    '"resistance training"[MeSH] AND "dietary proteins"[MeSH]',
    '"resistance training"[MeSH] AND "body composition"[MeSH]',
    '"exercise"[MeSH] AND "dietary supplements"[MeSH]',
    '"sports nutritional sciences"[MeSH]',
    '"muscle, skeletal"[MeSH] AND "dietary supplements"[MeSH]',
    '"athletic performance"[MeSH] AND "diet"[MeSH]',
    '"creatine"[MeSH] AND "athletic performance"[MeSH]',
    '"high-intensity interval training"[MeSH]',

    # Keyword-based broader searches
    '"resistance training" AND "protein" AND "muscle"',
    '"exercise" AND "muscle" AND "hypertrophy"',
    '"dietary protein" AND "muscle mass"',
    '"high protein diet" AND "body composition"',
    '"creatine supplementation"',
    '"whey protein" AND "muscle"',
    '"intermittent fasting" AND "body composition"',
    '"sports nutrition"',
    '"body composition" AND "exercise" AND "diet"',

    # Supplements
    '"creatine monohydrate" AND "exercise"',
    '"beta-alanine" AND "performance"',
    '"caffeine" AND "exercise performance"',
    '"branched-chain amino acids" AND "exercise"',
    '"protein supplementation" AND "resistance training"',
    '"vitamin D" AND "muscle" AND "exercise"',
    '"omega-3" AND "exercise" AND "recovery"',

    # Weight management
    '"obesity" AND "exercise" AND "weight loss"',
    '"caloric restriction" AND "exercise"',
    '"GLP-1" AND "weight" AND "exercise"',
    '"semaglutide" AND "body composition"',
    '"tirzepatide" AND "body composition"',

    # Women's health
    '"menopause" AND "exercise" AND "muscle"',
    '"PCOS" AND "diet" AND "exercise"',
    '"female athlete" AND "nutrition"',
    '"pregnancy" AND "exercise" AND "nutrition"',

    # Aging & sarcopenia
    '"sarcopenia" AND "protein" AND "exercise"',
    '"aging" AND "muscle mass" AND "nutrition"',
    '"elderly" AND "resistance training" AND "protein"',

    # Metabolic
    '"gut microbiome" AND "exercise"',
    '"insulin sensitivity" AND "exercise" AND "diet"',
    '"ketogenic diet" AND "exercise"',
    '"carbohydrate loading" AND "performance"',

    # Recovery & performance
    '"muscle recovery" AND "nutrition"',
    '"exercise recovery" AND "protein"',
    '"sleep" AND "exercise" AND "performance"',
    '"overtraining" AND "nutrition"',

    # Specific training modalities
    '"concurrent training" AND "muscle"',
    '"endurance training" AND "nutrition"',
    '"powerlifting" AND "nutrition"',
    '"CrossFit" AND "nutrition"',

    # Review articles (high value)
    '"resistance training" AND "protein" AND review[pt]',
    '"sports nutrition" AND review[pt]',
    '"muscle hypertrophy" AND "nutrition" AND review[pt]',
    '"body composition" AND "diet" AND review[pt]',
    '"exercise" AND "supplementation" AND review[pt]',
    '"intermittent fasting" AND review[pt]',
]

# Statistics
STATS = {
    "queries_run": 0,
    "total_ids_found": 0,
    "unique_ids": 0,
    "fetched": 0,
    "saved": 0,
    "skipped": 0,
    "errors": 0,
    "start_time": None,
}


# ---------------------------------------------------------------------------
# NCBI E-utilities helpers
# ---------------------------------------------------------------------------

def ncbi_params(**kwargs):
    """Add standard NCBI params to any request."""
    params = {"tool": TOOL, "email": EMAIL}
    params.update(kwargs)
    return params


def rate_limit():
    """Sleep to respect NCBI rate limits."""
    time.sleep(DELAY)


def search_pubmed(query, retmax=10000):
    """
    Search PubMed and return list of PMIDs.
    Uses pagination (retstart) to handle >10000 results.
    PubMed limits retmax to 9999 per request.
    """
    url = f"{NCBI_BASE}/esearch.fcgi"
    all_ids = []
    retstart = 0
    page_size = min(retmax, 9999)  # PubMed caps at 9999

    while len(all_ids) < retmax:
        params = ncbi_params(
            db="pubmed",
            term=query,
            retmax=page_size,
            retstart=retstart,
            retmode="json",
            usehistory="y",
        )
        rate_limit()
        try:
            resp = SESSION.get(url, params=params, timeout=60)
            resp.raise_for_status()
            data = resp.json()
            result = data.get("esearchresult", {})
            id_list = result.get("idlist", [])
            total_count = int(result.get("count", 0))

            if retstart == 0:
                print(f"  [{total_count} total] for: {query[:80]}")

            all_ids.extend(id_list)

            # If we got fewer than page_size, we've exhausted results
            if len(id_list) < page_size:
                break
            # If we've reached the total or our cap
            if len(all_ids) >= total_count or len(all_ids) >= retmax:
                break

            retstart += len(id_list)
            if retstart > 0:
                print(f"    ...page {retstart // page_size + 1}, got {len(all_ids)} so far")
        except Exception as e:
            print(f"  ERROR searching PubMed: {e}")
            break

    # Trim to retmax
    all_ids = all_ids[:retmax]
    print(f"  [{len(all_ids)}/{retmax} cap] IDs collected")
    return all_ids


def fetch_pubmed_abstracts(pmids):
    """
    Fetch abstracts and metadata for a batch of PMIDs.
    Uses efetch with rettype=xml for structured data.
    Returns parsed list of article dicts.
    Max ~200 IDs per request to stay safe.
    """
    if not pmids:
        return []

    url = f"{NCBI_BASE}/efetch.fcgi"
    params = ncbi_params(
        db="pubmed",
        id=",".join(pmids),
        rettype="xml",
        retmode="xml",
    )
    rate_limit()
    try:
        resp = SESSION.get(url, params=params, timeout=120)
        resp.raise_for_status()
        return parse_pubmed_xml(resp.text)
    except Exception as e:
        print(f"    ERROR fetching batch of {len(pmids)}: {e}")
        return []


# ---------------------------------------------------------------------------
# XML Parsing
# ---------------------------------------------------------------------------

def get_text(elem):
    """Recursively get all text content from an XML element."""
    if elem is None:
        return ""
    parts = []
    if elem.text:
        parts.append(elem.text)
    for child in elem:
        tag = child.tag
        if tag in ("i", "italic", "Italic"):
            parts.append(f"*{get_text(child)}*")
        elif tag in ("b", "bold", "Bold"):
            parts.append(f"**{get_text(child)}**")
        elif tag in ("sup", "Superscript"):
            parts.append(f"^{get_text(child)}^")
        elif tag in ("sub", "Subscript"):
            parts.append(f"~{get_text(child)}~")
        else:
            parts.append(get_text(child))
        if child.tail:
            parts.append(child.tail)
    return "".join(parts)


def parse_pubmed_xml(xml_string):
    """
    Parse PubMed efetch XML (PubmedArticleSet) and return list of article dicts.
    """
    articles = []
    try:
        root = ET.fromstring(xml_string)
    except ET.ParseError as e:
        print(f"    XML parse error: {e}")
        return articles

    for pa in root.findall(".//PubmedArticle"):
        article = parse_pubmed_article(pa)
        if article and article.get("title"):
            articles.append(article)

    return articles


def parse_pubmed_article(pa_elem):
    """Parse a single PubmedArticle XML element."""
    result = {
        "pmid": "",
        "pmc_id": "",
        "title": "",
        "authors": [],
        "abstract": "",
        "journal": "",
        "journal_abbrev": "",
        "volume": "",
        "issue": "",
        "pages": "",
        "doi": "",
        "date_published": "",
        "mesh_terms": [],
        "keywords": [],
        "pub_types": [],
    }

    medline = pa_elem.find("MedlineCitation")
    if medline is None:
        return None

    # PMID
    pmid_elem = medline.find("PMID")
    if pmid_elem is not None and pmid_elem.text:
        result["pmid"] = pmid_elem.text.strip()

    article = medline.find("Article")
    if article is None:
        return None

    # Title
    title_elem = article.find("ArticleTitle")
    if title_elem is not None:
        result["title"] = get_text(title_elem).strip()
        # Remove trailing period if present
        if result["title"].endswith("."):
            result["title"] = result["title"][:-1]

    # Journal info
    journal = article.find("Journal")
    if journal is not None:
        j_title = journal.find("Title")
        if j_title is not None and j_title.text:
            result["journal"] = j_title.text.strip()
        j_abbrev = journal.find("ISOAbbreviation")
        if j_abbrev is not None and j_abbrev.text:
            result["journal_abbrev"] = j_abbrev.text.strip()

        ji = journal.find("JournalIssue")
        if ji is not None:
            vol = ji.find("Volume")
            if vol is not None and vol.text:
                result["volume"] = vol.text.strip()
            iss = ji.find("Issue")
            if iss is not None and iss.text:
                result["issue"] = iss.text.strip()

            # Publication date
            pub_date = ji.find("PubDate")
            if pub_date is not None:
                result["date_published"] = parse_pubmed_date(pub_date)

    # Pages
    pages = article.find("Pagination/MedlinePgn")
    if pages is not None and pages.text:
        result["pages"] = pages.text.strip()

    # Abstract
    abstract_elem = article.find("Abstract")
    if abstract_elem is not None:
        abstract_parts = []
        for atext in abstract_elem.findall("AbstractText"):
            label = atext.get("Label", "")
            text = get_text(atext).strip()
            if label:
                abstract_parts.append(f"**{label}:**\n\n{text}")
            else:
                abstract_parts.append(text)
        result["abstract"] = "\n\n".join(abstract_parts)

    # Authors
    author_list = article.find("AuthorList")
    if author_list is not None:
        for author in author_list.findall("Author"):
            last = author.find("LastName")
            fore = author.find("ForeName")
            if last is not None and last.text:
                name_parts = []
                if fore is not None and fore.text:
                    name_parts.append(fore.text.strip())
                name_parts.append(last.text.strip())
                result["authors"].append(" ".join(name_parts))

    # DOI and PMC ID from ArticleIdList
    pubmed_data = pa_elem.find("PubmedData")
    if pubmed_data is not None:
        for aid in pubmed_data.findall(".//ArticleId"):
            id_type = aid.get("IdType", "")
            if id_type == "doi" and aid.text:
                result["doi"] = aid.text.strip()
            elif id_type == "pmc" and aid.text:
                result["pmc_id"] = aid.text.strip()

    # MeSH terms
    mesh_list = medline.find("MeshHeadingList")
    if mesh_list is not None:
        for mh in mesh_list.findall("MeshHeading"):
            desc = mh.find("DescriptorName")
            if desc is not None and desc.text:
                result["mesh_terms"].append(desc.text.strip())

    # Keywords
    for kw_list in medline.findall("KeywordList"):
        for kw in kw_list.findall("Keyword"):
            if kw.text:
                result["keywords"].append(kw.text.strip())

    # Publication types
    for pt in article.findall(".//PublicationType"):
        if pt.text:
            result["pub_types"].append(pt.text.strip())

    return result


def parse_pubmed_date(pub_date_elem):
    """Parse PubMed date element into ISO date string."""
    year = pub_date_elem.find("Year")
    month = pub_date_elem.find("Month")
    day = pub_date_elem.find("Day")

    if year is None or not year.text:
        # Try MedlineDate fallback
        ml = pub_date_elem.find("MedlineDate")
        if ml is not None and ml.text:
            # Extract year from MedlineDate like "2024 Jan-Feb"
            match = re.match(r"(\d{4})", ml.text)
            if match:
                return f"{match.group(1)}-01-01"
        return ""

    parts = [year.text.strip()]

    if month is not None and month.text:
        m = month.text.strip()
        # Could be numeric or abbreviated month name
        month_map = {
            "Jan": "01", "Feb": "02", "Mar": "03", "Apr": "04",
            "May": "05", "Jun": "06", "Jul": "07", "Aug": "08",
            "Sep": "09", "Oct": "10", "Nov": "11", "Dec": "12",
        }
        if m in month_map:
            parts.append(month_map[m])
        elif m.isdigit():
            parts.append(m.zfill(2))
        else:
            parts.append("01")

        if day is not None and day.text and day.text.strip().isdigit():
            parts.append(day.text.strip().zfill(2))
        else:
            parts.append("01")
    else:
        parts.extend(["01", "01"])

    return "-".join(parts)


# ---------------------------------------------------------------------------
# Markdown generation
# ---------------------------------------------------------------------------

def slugify(text, max_len=80):
    """Convert text to filesystem-safe slug."""
    if not text:
        return "untitled"
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = text.strip("-")
    if len(text) > max_len:
        text = text[:max_len].rstrip("-")
    return text or "untitled"


def escape_yaml(text):
    """Escape text for YAML frontmatter value."""
    if not text:
        return '""'
    text = str(text).replace('"', '\\"')
    return f'"{text}"'


def format_author_short(authors):
    """Format author list for frontmatter (first author et al. if >3)."""
    if not authors:
        return "Unknown"
    if len(authors) == 1:
        return authors[0]
    last_names = []
    for a in authors:
        parts = a.split()
        if parts:
            last_names.append(parts[-1])
    if len(last_names) <= 3:
        return ", ".join(last_names)
    return f"{last_names[0]}, {last_names[1]}, {last_names[2]}, et al."


def article_to_markdown(article):
    """Convert parsed article dict to markdown string with YAML frontmatter."""
    pmid = article["pmid"]
    title = article["title"]
    authors = article["authors"]
    doi = article["doi"]
    pmc_id = article.get("pmc_id", "")

    # Build body content
    body_parts = []

    # Journal line
    journal_line_parts = []
    if article["journal"]:
        journal_line_parts.append(article["journal"])
    if article["volume"]:
        vol_str = article["volume"]
        if article["issue"]:
            vol_str += f"({article['issue']})"
        if article["pages"]:
            vol_str += f":{article['pages']}"
        journal_line_parts.append(vol_str)
    if journal_line_parts:
        body_parts.append(f"**Journal:** {', '.join(journal_line_parts)}")

    if doi:
        body_parts.append(f"**DOI:** {doi}")

    if pmc_id:
        body_parts.append(f"**PMC:** {pmc_id}")

    if authors:
        body_parts.append(f"**Authors:** {'; '.join(authors)}")

    if article["pub_types"]:
        body_parts.append(f"**Type:** {', '.join(article['pub_types'])}")

    body_parts.append("")  # separator

    # Abstract
    if article["abstract"]:
        body_parts.append("## Abstract\n")
        body_parts.append(article["abstract"])
        body_parts.append("")

    # MeSH terms section
    if article["mesh_terms"]:
        body_parts.append("## MeSH Terms\n")
        for term in article["mesh_terms"]:
            body_parts.append(f"- {term}")
        body_parts.append("")

    body = "\n\n".join(body_parts)
    word_count = len(body.split())

    # Tags: combine keywords + MeSH terms (deduped, max 15)
    all_tags = []
    seen = set()
    for tag in article["keywords"] + article["mesh_terms"]:
        tag_lower = tag.lower()
        if tag_lower not in seen:
            seen.add(tag_lower)
            all_tags.append(tag)
    all_tags = all_tags[:15]
    tags_str = json.dumps(all_tags)

    source_url = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
    author_str = format_author_short(authors)

    # Determine content subtype
    pub_types_lower = [pt.lower() for pt in article.get("pub_types", [])]
    is_review = any("review" in pt for pt in pub_types_lower)
    is_clinical = any("clinical trial" in pt for pt in pub_types_lower)
    is_meta = any("meta-analysis" in pt for pt in pub_types_lower)

    frontmatter = f"""---
source_id: "pubmed-{pmid}"
source_domain: "pubmed.ncbi.nlm.nih.gov"
source_url: {escape_yaml(source_url)}
title: {escape_yaml(title)}
author: {escape_yaml(author_str)}
date_published: {escape_yaml(article['date_published'])}
doi: {escape_yaml(doi)}
tags: {tags_str}
content_type: "scientific_paper"
source_category: "11_scientific_papers"
source_tier: "tier1"
word_count: {word_count}
has_citations: false
journal: {escape_yaml(article['journal'])}
pmid: "{pmid}"
pmc_id: {escape_yaml(pmc_id)}
is_review: {str(is_review).lower()}
is_clinical_trial: {str(is_clinical).lower()}
is_meta_analysis: {str(is_meta).lower()}
---"""

    return f"{frontmatter}\n\n# {title}\n\n{body}"


# ---------------------------------------------------------------------------
# File I/O
# ---------------------------------------------------------------------------

def get_existing_pmids(output_dir):
    """Get set of PMIDs already saved on disk."""
    existing = set()
    if not os.path.exists(output_dir):
        return existing
    for fname in os.listdir(output_dir):
        if fname.startswith("pubmed-") and fname.endswith(".md"):
            # Format: pubmed-12345678-slug.md
            parts = fname.split("-", 2)
            if len(parts) >= 2 and parts[1].isdigit():
                existing.add(parts[1])
    return existing


def save_article(article, output_dir):
    """Save article as markdown file. Returns True if saved, False if skipped."""
    pmid = article["pmid"]
    title = article["title"]

    slug = slugify(title)
    filename = f"pubmed-{pmid}-{slug}.md"
    filepath = os.path.join(output_dir, filename)

    if os.path.exists(filepath):
        return False

    markdown = article_to_markdown(article)

    os.makedirs(output_dir, exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(markdown)

    return True


# ---------------------------------------------------------------------------
# Main scraping logic
# ---------------------------------------------------------------------------

def run_queries(queries, output_dir, max_results=10000):
    """
    Run a list of PubMed search queries, collect unique PMIDs,
    fetch abstracts in batches, and save to disk.
    """
    STATS["start_time"] = time.time()

    # Get already-saved PMIDs
    existing_pmids = get_existing_pmids(output_dir)
    print(f"\nExisting articles on disk: {len(existing_pmids)}")

    # Also check for PMC-style filenames from old scraper
    if os.path.exists(output_dir):
        for fname in os.listdir(output_dir):
            if fname.startswith("pmc") and fname.endswith(".md"):
                # Old format: pmc12345-slug.md
                pass  # These are PMC IDs, not PMIDs, keep going

    # Phase 1: Collect all unique PMIDs from all queries
    print(f"\n{'='*70}")
    print(f"PHASE 1: Searching PubMed ({len(queries)} queries)")
    print(f"{'='*70}\n")

    all_pmids = set()
    for i, query in enumerate(queries):
        print(f"Query {i+1}/{len(queries)}:")
        pmids = search_pubmed(query, retmax=max_results)
        new_pmids = set(pmids) - all_pmids - existing_pmids
        all_pmids.update(new_pmids)
        STATS["queries_run"] += 1
        STATS["total_ids_found"] += len(pmids)
        print(f"    New unique PMIDs: {len(new_pmids)} (cumulative: {len(all_pmids)})")

    STATS["unique_ids"] = len(all_pmids)
    print(f"\nTotal unique new PMIDs to fetch: {len(all_pmids)}")

    if not all_pmids:
        print("No new PMIDs to fetch. Done.")
        return

    # Phase 2: Fetch abstracts in batches of 200
    print(f"\n{'='*70}")
    print(f"PHASE 2: Fetching {len(all_pmids)} abstracts")
    print(f"{'='*70}\n")

    pmid_list = sorted(all_pmids)
    batch_size = 200
    total_batches = (len(pmid_list) + batch_size - 1) // batch_size
    est_time = len(pmid_list) / batch_size * DELAY / 60

    print(f"Batch size: {batch_size}, Total batches: {total_batches}")
    print(f"Estimated time: ~{est_time:.1f} minutes")
    print(f"Output: {output_dir}\n")

    for batch_num in range(total_batches):
        start_idx = batch_num * batch_size
        end_idx = min(start_idx + batch_size, len(pmid_list))
        batch = pmid_list[start_idx:end_idx]

        articles = fetch_pubmed_abstracts(batch)
        STATS["fetched"] += len(articles)

        saved_in_batch = 0
        for article in articles:
            if save_article(article, output_dir):
                STATS["saved"] += 1
                saved_in_batch += 1
            else:
                STATS["skipped"] += 1

        if (batch_num + 1) % 10 == 0 or (batch_num + 1) == total_batches:
            elapsed = time.time() - STATS["start_time"]
            print(f"  Batch {batch_num+1}/{total_batches}: "
                  f"fetched={STATS['fetched']}, saved={STATS['saved']}, "
                  f"skipped={STATS['skipped']}, errors={STATS['errors']} "
                  f"[{elapsed:.0f}s elapsed]")

    print_summary(output_dir)


def run_journal_search(journal_name, journal_query, output_dir, max_results=10000):
    """
    Search PubMed for all articles from a specific journal.
    """
    print(f"\n{'='*70}")
    print(f"Journal: {journal_name}")
    print(f"{'='*70}\n")

    queries = [journal_query]
    run_queries(queries, output_dir, max_results)


def print_summary(output_dir):
    """Print final summary."""
    elapsed = time.time() - STATS["start_time"] if STATS["start_time"] else 0

    print(f"\n{'='*70}")
    print("SUMMARY")
    print(f"{'='*70}")
    print(f"  Queries run:      {STATS['queries_run']}")
    print(f"  Total IDs found:  {STATS['total_ids_found']}")
    print(f"  Unique new IDs:   {STATS['unique_ids']}")
    print(f"  Fetched:          {STATS['fetched']}")
    print(f"  Saved:            {STATS['saved']}")
    print(f"  Skipped:          {STATS['skipped']}")
    print(f"  Errors:           {STATS['errors']}")
    print(f"  Time:             {elapsed:.0f}s")

    # Count files on disk
    if os.path.exists(output_dir):
        count = len([f for f in os.listdir(output_dir) if f.endswith(".md")])
        print(f"\n  Total files on disk: {count}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="PubMed Bulk Abstract Scraper")
    parser.add_argument("--query", "-q", help="Single search query")
    parser.add_argument("--queries-file", "-f", help="File with one query per line")
    parser.add_argument("--max-results", "-m", type=int, default=10000,
                        help="Max results per query (default: 10000)")
    parser.add_argument("--output-dir", "-o", default=ARTICLES_DIR,
                        help="Output directory")
    parser.add_argument("--journal", "-j", help="Journal name for journal-specific search")
    parser.add_argument("--all", action="store_true",
                        help="Run all default queries")
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)

    print(f"PubMed Bulk Abstract Scraper")
    print(f"Tool: {TOOL}, Email: {EMAIL}")
    print(f"Rate limit: {DELAY}s between requests ({1/DELAY:.0f} req/sec)")
    print(f"Output: {args.output_dir}")

    if args.query:
        queries = [args.query]
    elif args.queries_file:
        with open(args.queries_file) as f:
            queries = [line.strip() for line in f if line.strip() and not line.startswith("#")]
    elif args.journal:
        journal_query = f'"{args.journal}"[journal]'
        queries = [journal_query]
    elif args.all or len(sys.argv) == 1:
        queries = DEFAULT_QUERIES
    else:
        parser.print_help()
        sys.exit(1)

    run_queries(queries, args.output_dir, args.max_results)


if __name__ == "__main__":
    main()

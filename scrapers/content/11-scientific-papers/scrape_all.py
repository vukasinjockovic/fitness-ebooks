#!/usr/bin/env python3
"""
Scrape scientific papers from PubMed Central (PMC) via NCBI E-utilities.

Phase 1: ALL JISSN papers (~1,419 in PMC, all open access, all relevant)
Phase 2: Broader PubMed searches for fitness/nutrition topics

Output: Markdown files with YAML frontmatter in jissn/articles/ and pubmed/articles/

Rate limiting: 0.4s between requests (safe for 3 req/sec without API key).
NCBI recommends tool + email params on all requests.
"""

import json
import os
import re
import sys
import time
import unicodedata
from xml.etree import ElementTree as ET

import requests

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
JISSN_DIR = os.path.join(SCRIPT_DIR, "jissn", "articles")
PUBMED_DIR = os.path.join(SCRIPT_DIR, "pubmed", "articles")

NCBI_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
TOOL = "gymzilla"
EMAIL = "contact@gymzillatribe.com"
DELAY = 0.4  # seconds between requests

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "GymZilla/1.0 (contact@gymzillatribe.com)"
})

# Counters for reporting
STATS = {
    "jissn_searched": 0,
    "jissn_fetched": 0,
    "jissn_saved": 0,
    "jissn_skipped": 0,
    "jissn_errors": 0,
    "pubmed_searched": 0,
    "pubmed_fetched": 0,
    "pubmed_saved": 0,
    "pubmed_skipped": 0,
    "pubmed_errors": 0,
}

# Phase 2 searches
PUBMED_SEARCHES = [
    '"resistance training" AND "protein" AND "muscle"',
    '"GLP-1" AND "nutrition"',
    '"semaglutide" AND "diet"',
    '"menopause" AND "nutrition" AND "exercise"',
    '"PCOS" AND "diet"',
    '"creatine supplementation"',
    '"intermittent fasting" AND "body composition"',
    '"sarcopenia" AND "protein"',
    '"ketogenic diet" AND "performance"',
]


def ncbi_params(**kwargs):
    """Add standard NCBI params to any request."""
    params = {"tool": TOOL, "email": EMAIL}
    params.update(kwargs)
    return params


def rate_limit():
    """Sleep to respect NCBI rate limits."""
    time.sleep(DELAY)


def search_pmc(query, retmax=10000):
    """Search PMC and return list of PMC IDs (numeric, without 'PMC' prefix)."""
    url = f"{NCBI_BASE}/esearch.fcgi"
    params = ncbi_params(
        db="pmc",
        term=query,
        retmax=retmax,
        retmode="json",
    )
    rate_limit()
    resp = SESSION.get(url, params=params, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    id_list = data.get("esearchresult", {}).get("idlist", [])
    print(f"  Search returned {len(id_list)} PMC IDs for: {query[:80]}")
    return id_list


def fetch_pmc_xml(pmc_id):
    """Fetch full-text JATS XML for a PMC article. Returns XML string or None."""
    url = f"{NCBI_BASE}/efetch.fcgi"
    params = ncbi_params(
        db="pmc",
        id=pmc_id,
        retmode="xml",
    )
    rate_limit()
    try:
        resp = SESSION.get(url, params=params, timeout=120)
        resp.raise_for_status()
        return resp.text
    except Exception as e:
        print(f"    ERROR fetching PMC{pmc_id}: {e}")
        return None


def slugify(text, max_len=80):
    """Convert text to filesystem-safe slug."""
    if not text:
        return "untitled"
    # Normalize unicode
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = text.strip("-")
    if len(text) > max_len:
        text = text[:max_len].rstrip("-")
    return text or "untitled"


def get_text(elem):
    """Recursively get all text content from an XML element, including tail text."""
    if elem is None:
        return ""
    parts = []
    if elem.text:
        parts.append(elem.text)
    for child in elem:
        # Handle inline elements (italic, bold, sup, sub, xref, etc.)
        tag = child.tag
        if tag in ("italic", "i"):
            parts.append(f"*{get_text(child)}*")
        elif tag in ("bold", "b"):
            parts.append(f"**{get_text(child)}**")
        elif tag in ("sup",):
            parts.append(f"^{get_text(child)}^")
        elif tag in ("sub",):
            parts.append(f"~{get_text(child)}~")
        elif tag == "xref":
            # Citation references like [1], [2]
            ref_text = get_text(child)
            if child.get("ref-type") == "bibr":
                parts.append(f"[{ref_text}]")
            else:
                parts.append(ref_text)
        elif tag == "ext-link":
            link_url = child.get("{http://www.w3.org/1999/xlink}href", "")
            link_text = get_text(child)
            if link_url:
                parts.append(f"[{link_text}]({link_url})")
            else:
                parts.append(link_text)
        else:
            parts.append(get_text(child))
        if child.tail:
            parts.append(child.tail)
    return "".join(parts)


def parse_jats_xml(xml_string, pmc_id):
    """Parse JATS XML and return a dict with article metadata and content."""
    try:
        root = ET.fromstring(xml_string)
    except ET.ParseError as e:
        print(f"    XML parse error for PMC{pmc_id}: {e}")
        return None

    # Find the article element (might be root or nested)
    article = root.find(".//article")
    if article is None:
        article = root  # Root might be the article itself

    front = article.find(".//front")
    body = article.find(".//body")
    back = article.find(".//back")

    result = {
        "pmc_id": f"PMC{pmc_id}",
        "title": "",
        "authors": [],
        "abstract": "",
        "body_sections": [],
        "references": [],
        "date_published": "",
        "doi": "",
        "keywords": [],
        "journal": "",
    }

    if front is None:
        return result

    # Title
    title_elem = front.find(".//article-title")
    if title_elem is not None:
        result["title"] = get_text(title_elem).strip()

    # Journal
    journal_elem = front.find(".//journal-title")
    if journal_elem is not None:
        result["journal"] = get_text(journal_elem).strip()

    # DOI
    for aid in front.findall(".//article-id"):
        if aid.get("pub-id-type") == "doi":
            result["doi"] = (aid.text or "").strip()
            break

    # Authors
    for contrib in front.findall(".//contrib[@contrib-type='author']"):
        surname = contrib.find(".//surname")
        given = contrib.find(".//given-names")
        if surname is not None:
            name_parts = []
            if given is not None and given.text:
                name_parts.append(given.text.strip())
            name_parts.append(surname.text.strip() if surname.text else "")
            result["authors"].append(" ".join(name_parts))

    # Publication date
    for pub_date in front.findall(".//pub-date"):
        year_elem = pub_date.find("year")
        month_elem = pub_date.find("month")
        day_elem = pub_date.find("day")
        if year_elem is not None and year_elem.text:
            parts = [year_elem.text.strip()]
            if month_elem is not None and month_elem.text:
                parts.append(month_elem.text.strip().zfill(2))
                if day_elem is not None and day_elem.text:
                    parts.append(day_elem.text.strip().zfill(2))
                else:
                    parts.append("01")
            else:
                parts.extend(["01", "01"])
            result["date_published"] = "-".join(parts)
            break

    # Keywords
    for kwd in front.findall(".//kwd"):
        kwd_text = get_text(kwd).strip()
        if kwd_text:
            result["keywords"].append(kwd_text)

    # Abstract
    abstract_elem = front.find(".//abstract")
    if abstract_elem is not None:
        abstract_parts = []
        for sec in abstract_elem.findall(".//sec"):
            sec_title = sec.find("title")
            if sec_title is not None:
                abstract_parts.append(f"**{get_text(sec_title).strip()}**")
            for p in sec.findall("p"):
                abstract_parts.append(get_text(p).strip())
            abstract_parts.append("")
        if not abstract_parts:
            # No sections, just get paragraphs
            for p in abstract_elem.findall("p"):
                abstract_parts.append(get_text(p).strip())
        result["abstract"] = "\n\n".join(p for p in abstract_parts if p)

    # Body sections
    if body is not None:
        for sec in body.findall("sec"):
            section = parse_section(sec, level=2)
            if section:
                result["body_sections"].append(section)
        # If no <sec> elements, try direct <p> elements
        if not result["body_sections"]:
            paragraphs = []
            for p in body.findall("p"):
                text = get_text(p).strip()
                if text:
                    paragraphs.append(text)
            if paragraphs:
                result["body_sections"].append({
                    "title": "",
                    "level": 2,
                    "paragraphs": paragraphs,
                    "subsections": [],
                })

    # References
    if back is not None:
        ref_list = back.find(".//ref-list")
        if ref_list is not None:
            for ref in ref_list.findall("ref"):
                ref_text = parse_reference(ref)
                if ref_text:
                    result["references"].append(ref_text)

    return result


def parse_section(sec_elem, level=2):
    """Parse a JATS <sec> element into a dict."""
    title_elem = sec_elem.find("title")
    title = get_text(title_elem).strip() if title_elem is not None else ""

    paragraphs = []
    for p in sec_elem.findall("p"):
        text = get_text(p).strip()
        if text:
            paragraphs.append(text)

    # Handle tables (just note them)
    for table_wrap in sec_elem.findall("table-wrap"):
        caption = table_wrap.find(".//caption")
        if caption is not None:
            cap_text = get_text(caption).strip()
            paragraphs.append(f"*[Table: {cap_text}]*")

    # Handle figures (just note them)
    for fig in sec_elem.findall("fig"):
        caption = fig.find(".//caption")
        if caption is not None:
            cap_text = get_text(caption).strip()
            paragraphs.append(f"*[Figure: {cap_text}]*")

    # Handle lists
    for list_elem in sec_elem.findall("list"):
        for item in list_elem.findall("list-item"):
            item_text = get_text(item).strip()
            if item_text:
                paragraphs.append(f"- {item_text}")

    subsections = []
    for child_sec in sec_elem.findall("sec"):
        subsection = parse_section(child_sec, level=level + 1)
        if subsection:
            subsections.append(subsection)

    if title or paragraphs or subsections:
        return {
            "title": title,
            "level": level,
            "paragraphs": paragraphs,
            "subsections": subsections,
        }
    return None


def parse_reference(ref_elem):
    """Parse a JATS <ref> element into a citation string."""
    # Try mixed-citation first, then element-citation
    citation = ref_elem.find("mixed-citation")
    if citation is None:
        citation = ref_elem.find("element-citation")
    if citation is None:
        # Fallback: just get all text
        text = get_text(ref_elem).strip()
        return text if text else None

    # Build citation from structured elements
    parts = []

    # Authors
    person_group = citation.find("person-group")
    if person_group is not None:
        authors = []
        for name_elem in person_group.findall("name"):
            surname = name_elem.find("surname")
            given = name_elem.find("given-names")
            if surname is not None and surname.text:
                if given is not None and given.text:
                    authors.append(f"{surname.text} {given.text}")
                else:
                    authors.append(surname.text)
        if authors:
            if len(authors) > 3:
                parts.append(f"{authors[0]}, et al.")
            else:
                parts.append(", ".join(authors))

    # Year
    year = citation.find("year")
    if year is not None and year.text:
        parts.append(f"({year.text})")

    # Title
    atitle = citation.find("article-title")
    if atitle is not None:
        parts.append(get_text(atitle).strip())

    # Source (journal)
    source = citation.find("source")
    if source is not None and source.text:
        parts.append(f"*{source.text}*")

    # Volume, pages
    vol = citation.find("volume")
    fpage = citation.find("fpage")
    lpage = citation.find("lpage")
    vol_str = ""
    if vol is not None and vol.text:
        vol_str = vol.text
    if fpage is not None and fpage.text:
        if lpage is not None and lpage.text:
            vol_str += f":{fpage.text}-{lpage.text}"
        else:
            vol_str += f":{fpage.text}"
    if vol_str:
        parts.append(vol_str)

    if parts:
        return " ".join(parts)

    # Fallback to full text
    return get_text(citation).strip() or None


def format_author_short(authors):
    """Format author list for frontmatter (first author et al. if >3)."""
    if not authors:
        return "Unknown"
    if len(authors) == 1:
        return authors[0]
    # Get last names
    last_names = []
    for a in authors:
        parts = a.split()
        if parts:
            last_names.append(parts[-1])
    if len(last_names) <= 3:
        return ", ".join(last_names)
    return f"{last_names[0]}, {last_names[1]}, {last_names[2]}, et al."


def escape_yaml(text):
    """Escape text for YAML frontmatter value."""
    if not text:
        return '""'
    # If contains special chars, quote it
    text = text.replace('"', '\\"')
    return f'"{text}"'


def article_to_markdown(article_data, source_category="jissn"):
    """Convert parsed article data to markdown string with YAML frontmatter."""
    pmc_id = article_data["pmc_id"]
    title = article_data["title"]
    authors = article_data["authors"]
    doi = article_data["doi"]

    # Build body content
    body_parts = []

    # Abstract
    if article_data["abstract"]:
        body_parts.append("## Abstract\n")
        body_parts.append(article_data["abstract"])
        body_parts.append("")

    # Body sections
    for section in article_data["body_sections"]:
        body_parts.extend(format_section(section))

    # References
    if article_data["references"]:
        body_parts.append("## References\n")
        for i, ref in enumerate(article_data["references"], 1):
            body_parts.append(f"{i}. {ref}")
        body_parts.append("")

    body = "\n\n".join(body_parts)
    word_count = len(body.split())

    # Build source URL
    pmc_num = pmc_id.replace("PMC", "")
    source_url = f"https://pmc.ncbi.nlm.nih.gov/articles/{pmc_id}/"

    # Tags from keywords
    tags = article_data["keywords"][:10] if article_data["keywords"] else []
    tags_str = json.dumps(tags)

    # Author string
    author_str = format_author_short(authors)

    # Frontmatter
    frontmatter = f"""---
source_id: {escape_yaml(pmc_id)}
source_domain: "pubmed.ncbi.nlm.nih.gov"
source_url: {escape_yaml(source_url)}
title: {escape_yaml(title)}
author: {escape_yaml(author_str)}
date_published: {escape_yaml(article_data['date_published'])}
doi: {escape_yaml(doi)}
tags: {tags_str}
content_type: "science"
source_category: "11_scientific_papers"
source_tier: "tier1"
word_count: {word_count}
has_citations: {str(bool(article_data['references'])).lower()}
journal: {escape_yaml(article_data['journal'])}
---"""

    return f"{frontmatter}\n\n# {title}\n\n{body}"


def format_section(section, depth=0):
    """Format a parsed section dict into markdown lines."""
    lines = []
    level = min(section["level"], 6)
    heading = "#" * level

    if section["title"]:
        lines.append(f"{heading} {section['title']}\n")

    for p in section["paragraphs"]:
        lines.append(p)
        lines.append("")

    for subsec in section["subsections"]:
        lines.extend(format_section(subsec, depth + 1))

    return lines


def save_article(article_data, output_dir, source_category="jissn"):
    """Save article as markdown file. Returns True if saved, False if skipped."""
    pmc_id = article_data["pmc_id"]
    title = article_data["title"]

    # Generate filename
    slug = slugify(title)
    filename = f"{pmc_id.lower()}-{slug}.md"
    filepath = os.path.join(output_dir, filename)

    # Skip if already exists
    if os.path.exists(filepath):
        return False

    markdown = article_to_markdown(article_data, source_category)

    os.makedirs(output_dir, exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(markdown)

    return True


def fetch_and_save_batch(pmc_ids, output_dir, source_category="jissn", label=""):
    """Fetch and save a batch of PMC articles. Returns (saved, skipped, errors)."""
    saved = 0
    skipped = 0
    errors = 0
    total = len(pmc_ids)

    for i, pmc_id in enumerate(pmc_ids):
        # Check if already saved (quick slug check)
        prefix = f"pmc{pmc_id}-"
        existing = [f for f in os.listdir(output_dir) if f.startswith(prefix)] if os.path.exists(output_dir) else []
        if existing:
            skipped += 1
            if (i + 1) % 100 == 0:
                print(f"  [{label}] {i+1}/{total} (saved={saved}, skipped={skipped}, errors={errors})")
            continue

        # Fetch XML
        xml_text = fetch_pmc_xml(pmc_id)
        if xml_text is None:
            errors += 1
            continue

        # Parse
        article_data = parse_jats_xml(xml_text, pmc_id)
        if article_data is None or not article_data["title"]:
            errors += 1
            print(f"    Failed to parse PMC{pmc_id}")
            continue

        # Save
        was_saved = save_article(article_data, output_dir, source_category)
        if was_saved:
            saved += 1
        else:
            skipped += 1

        if (i + 1) % 50 == 0 or (i + 1) == total:
            print(f"  [{label}] {i+1}/{total} (saved={saved}, skipped={skipped}, errors={errors})")

    return saved, skipped, errors


def phase1_jissn():
    """Phase 1: Download ALL JISSN papers from PMC."""
    print("=" * 70)
    print("PHASE 1: JISSN (Journal of the International Society of Sports Nutrition)")
    print("=" * 70)

    # Search for all JISSN papers in PMC
    query = '"Journal of the International Society of Sports Nutrition"[journal]'
    print(f"\nSearching PMC for: {query}")
    pmc_ids = search_pmc(query, retmax=10000)
    STATS["jissn_searched"] = len(pmc_ids)

    if not pmc_ids:
        print("  No results found. Exiting Phase 1.")
        return

    print(f"\nFetching {len(pmc_ids)} JISSN papers from PMC...")
    print(f"  Output: {JISSN_DIR}")
    print(f"  Estimated time: ~{len(pmc_ids) * DELAY / 60:.0f} minutes")
    print()

    saved, skipped, errors = fetch_and_save_batch(
        pmc_ids, JISSN_DIR, source_category="jissn", label="JISSN"
    )

    STATS["jissn_fetched"] = saved + skipped
    STATS["jissn_saved"] = saved
    STATS["jissn_skipped"] = skipped
    STATS["jissn_errors"] = errors

    print(f"\nPhase 1 complete: saved={saved}, skipped={skipped}, errors={errors}")


def phase2_pubmed():
    """Phase 2: Broader PubMed searches for fitness/nutrition topics."""
    print("\n" + "=" * 70)
    print("PHASE 2: Broader PubMed Searches")
    print("=" * 70)

    all_pmc_ids = set()
    # Also track which IDs we already have from JISSN to avoid duplicates
    jissn_ids = set()
    if os.path.exists(JISSN_DIR):
        for f in os.listdir(JISSN_DIR):
            if f.startswith("pmc") and f.endswith(".md"):
                # Extract PMC ID from filename (pmc1234567-slug.md)
                pmc_part = f.split("-")[0]  # "pmc1234567"
                jissn_ids.add(pmc_part.replace("pmc", ""))

    for search_term in PUBMED_SEARCHES:
        print(f"\nSearching PMC for: {search_term}")
        pmc_ids = search_pmc(search_term, retmax=100)

        # Remove IDs we already have from JISSN or previous searches
        new_ids = [pid for pid in pmc_ids if pid not in all_pmc_ids and pid not in jissn_ids]
        all_pmc_ids.update(new_ids)
        print(f"  New unique IDs: {len(new_ids)} (deduped from {len(pmc_ids)})")

    STATS["pubmed_searched"] = len(all_pmc_ids)
    print(f"\nTotal unique PMC IDs for Phase 2: {len(all_pmc_ids)}")

    if not all_pmc_ids:
        print("  No new results. Exiting Phase 2.")
        return

    pmc_id_list = sorted(all_pmc_ids)
    print(f"Fetching {len(pmc_id_list)} papers from PMC...")
    print(f"  Output: {PUBMED_DIR}")
    print(f"  Estimated time: ~{len(pmc_id_list) * DELAY / 60:.0f} minutes")
    print()

    saved, skipped, errors = fetch_and_save_batch(
        pmc_id_list, PUBMED_DIR, source_category="pubmed", label="PubMed"
    )

    STATS["pubmed_fetched"] = saved + skipped
    STATS["pubmed_saved"] = saved
    STATS["pubmed_skipped"] = skipped
    STATS["pubmed_errors"] = errors

    print(f"\nPhase 2 complete: saved={saved}, skipped={skipped}, errors={errors}")


def print_summary():
    """Print final summary."""
    print("\n" + "=" * 70)
    print("FINAL SUMMARY")
    print("=" * 70)
    print(f"\n  JISSN:")
    print(f"    Searched:  {STATS['jissn_searched']}")
    print(f"    Saved:     {STATS['jissn_saved']}")
    print(f"    Skipped:   {STATS['jissn_skipped']}")
    print(f"    Errors:    {STATS['jissn_errors']}")

    print(f"\n  PubMed (broader):")
    print(f"    Searched:  {STATS['pubmed_searched']}")
    print(f"    Saved:     {STATS['pubmed_saved']}")
    print(f"    Skipped:   {STATS['pubmed_skipped']}")
    print(f"    Errors:    {STATS['pubmed_errors']}")

    total_saved = STATS['jissn_saved'] + STATS['pubmed_saved']
    total_skipped = STATS['jissn_skipped'] + STATS['pubmed_skipped']
    print(f"\n  TOTAL PAPERS SAVED: {total_saved}")
    print(f"  TOTAL SKIPPED (already existed): {total_skipped}")

    # Count actual files
    jissn_count = len([f for f in os.listdir(JISSN_DIR) if f.endswith(".md")]) if os.path.exists(JISSN_DIR) else 0
    pubmed_count = len([f for f in os.listdir(PUBMED_DIR) if f.endswith(".md")]) if os.path.exists(PUBMED_DIR) else 0
    print(f"\n  Files on disk:")
    print(f"    {JISSN_DIR}: {jissn_count} files")
    print(f"    {PUBMED_DIR}: {pubmed_count} files")
    print(f"    Total: {jissn_count + pubmed_count} files")


def main():
    os.makedirs(JISSN_DIR, exist_ok=True)
    os.makedirs(PUBMED_DIR, exist_ok=True)

    print("Scientific Papers Scraper (PubMed Central)")
    print(f"Tool: {TOOL}, Email: {EMAIL}")
    print(f"Rate limit delay: {DELAY}s between requests")
    print()

    # Phase 1: JISSN
    phase1_jissn()

    # Phase 2: Broader PubMed
    phase2_pubmed()

    # Summary
    print_summary()


if __name__ == "__main__":
    main()

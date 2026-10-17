#==================================================================================
#Extract and classify character appearances from saved DC Fandom comic pages.
#Reads comic_pages/ and the reference CSVs in data/.
#Writes extraction records, audits and checkpoints to data/scraper_v3/.
#==================================================================================

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit

import pandas as pd
from bs4 import BeautifulSoup, Tag

#=====================================================
# SETTINGS
#=====================================================

ROOT = Path(__file__).resolve().parent
HTML_FOLDER = ROOT / 'comic_pages'
DATA_FOLDER = ROOT / 'data'
OUTPUT_DIR = DATA_FOLDER / 'scraper_v3'
CHARACTER_URL_FILE = DATA_FOLDER / 'dc_characters.csv'
ASSOCIATION_FILE = DATA_FOLDER / 'keywords_associations.csv'
BASE_URL = 'https://dc.fandom.com'
APPEARANCE_LABELS = {
    'Featured Characters', 'Supporting Characters', 'Antagonists',
    'Other Characters', 'Characters', 'Main Characters', 'Guest Characters',
}
RECORD_FIELDS = [
    'record_id', 'source_file', 'comic', 'story', 'entity_display_name',
    'entity_name', 'entity_url', 'listed_as', 'mentioned_only', 'entity_kind',
    'association_category', 'classification_method', 'classification_flags',
    'network_eligible',
]
AUDIT_FIELDS = [
    'source_file', 'comic', 'appearing_headings', 'appearance_lists',
    'items_seen', 'entities_extracted', 'verified_characters',
    'network_eligible', 'association', 'manual_review', 'unknown',
    'external_url', 'invalid', 'status', 'error_type', 'error',
]


def clean_text(value):
    if value is None:
        return ''
    return ' '.join(re.sub(r'\[\d+\]', '', str(value)).split()).strip()


def normalise_url(url):
    value = clean_text(url)
    if not value or value.startswith('#'):
        return ''
    full = urljoin(BASE_URL, value)
    parts = urlsplit(full)
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip('/'), '', ''))


def normalise_name(value):
    # Keep the full display name separately, this is just for my manual inspection I found it easier to look at
    return re.sub(r'\s*\([^()]+\)\s*$', '', clean_text(value)).strip()


def get_column(df, candidates):
    names = {str(c).strip().casefold(): c for c in df.columns}
    for candidate in candidates:
        if candidate in names:
            return names[candidate]
    raise ValueError(f'Expected one of {candidates}, found {list(df.columns)}')


def load_references():
    if not CHARACTER_URL_FILE.is_file() or not ASSOCIATION_FILE.is_file():
        raise FileNotFoundError('Both data/dc_characters.csv and data/keywords_associations.csv are required.')
    chars = pd.read_csv(CHARACTER_URL_FILE, dtype=str, keep_default_na=False)
    character_urls = {
        normalise_url(u) for u in chars[get_column(chars, ['character_url', 'url', 'wiki_url'])]
        if normalise_url(u)
    }
    associations = pd.read_csv(ASSOCIATION_FILE, dtype=str, keep_default_na=False)
    keyword_col = get_column(associations, ['keyword'])
    url_col = get_column(associations, ['url'])
    category_col = get_column(associations, ['category'])
    assoc_by_url = {}
    keywords = set()
    for _, row in associations.iterrows():
        url = normalise_url(row[url_col])
        category = clean_text(row[category_col])
        keyword = normalise_name(row[keyword_col]).casefold()
        if url:
            assoc_by_url.setdefault(url, set()).add(category)
        if keyword:
            keywords.add((keyword, category))
    return character_urls, assoc_by_url, sorted(keywords)


def classify(name, url, mentioned, character_urls, assoc_by_url, keywords):
    stable_url = normalise_url(url)
    if not re.search(r'[a-z0-9]', name, flags=re.I):
        return ('invalid', '', 'invalid_display_name', '', False)
    in_character = stable_url in character_urls
    in_association = stable_url in assoc_by_url
    if in_character and in_association:
        return ('manual_review', '|'.join(sorted(assoc_by_url[stable_url])),
                'conflicting_verified_urls', 'url_in_both_reference_files', False)
    if in_association:
        return ('association', '|'.join(sorted(assoc_by_url[stable_url])),
                'association_url_match', '', False)
    if in_character:
        return ('character', '', 'character_url_match', '', not mentioned)
    name_for_matching = normalise_name(name).casefold()
    matches = [(k, c) for k, c in keywords
               if re.search(r'(?<!\w)' + re.escape(k) + r'(?!\w)', name_for_matching)]
    if matches:
        return ('manual_review', '|'.join(sorted({c for _, c in matches})),
                'association_keyword_candidate',
                'matched_keywords=' + '|'.join(sorted({k for k, _ in matches})), False)
    if stable_url:
        return ('external_url', '', 'url_not_in_reference_lists', '', False)
    return ('unknown', '', 'missing_url', '', False)


def story_title(text):
    value = clean_text(text)
    match = re.search(r'Appearing in\s+["“](.*?)["”]', value, re.I)
    return clean_text(match.group(1)) if match else re.sub(r'^Appearing in\s+', '', value, flags=re.I).strip()


def own_item_details(item):
    #Extract an item without including the contents of nested list, added this when I noticed that I was leaving some characters behind and seems to be working
    text_parts = []
    links = []

    def walk(node):
        if isinstance(node, Tag):
            if node.name in ('ul', 'ol'):
                return
            if node.name == 'sup' and 'reference' in node.get('class', []):
                return
            if 'mw-editsection' in node.get('class', []):
                return
            if node.name == 'a' and node.get('href') and not node['href'].startswith('#'):
                links.append(node)
            for child in node.children:
                walk(child)
        else:
            text_parts.append(str(node))

    for child in item.children:
        walk(child)
    full_text = clean_text(' '.join(text_parts))
    for link in links:
        label = clean_text(link.get_text(' ', strip=True))
        if label:
            return label, normalise_url(link.get('href')), full_text
    return full_text, '', full_text


def extract_file(task):
    path_string, source_file, character_urls, assoc_by_url, keywords = task
    path = Path(path_string)
    audit = dict.fromkeys(AUDIT_FIELDS, '')
    audit.update(source_file=source_file, appearing_headings=0, appearance_lists=0,
                 items_seen=0, entities_extracted=0, verified_characters=0,
                 network_eligible=0, association=0, manual_review=0,
                 unknown=0, external_url=0, invalid=0, status='ok')
    records = []
    try:
        with path.open('r', encoding='utf-8', errors='replace') as fh:
            soup = BeautifulSoup(fh.read(), 'html.parser')
        title_node = soup.find('h1') or soup.find('title')
        comic = clean_text(title_node.get_text(' ', strip=True)) if title_node else path.stem
        audit['comic'] = comic
        headings = soup.find_all(lambda tag: tag.name in ('h2', 'h3')
                                 and 'appearing in' in tag.get_text(' ', strip=True).casefold())
        audit['appearing_headings'] = len(headings)
        for heading in headings:
            story = story_title(heading.get_text(' ', strip=True))
            current_category = None
            element = heading.find_next_sibling()
            while element is not None and element.name not in ('h2', 'h3'):
                if element.name == 'p':
                    paragraph = clean_text(element.get_text(' ', strip=True))
                    # Paragraphs ending in ':' define or reset the current category.
                    if paragraph.endswith(':'):
                        label = paragraph[:-1].strip()
                        current_category = label if label in APPEARANCE_LABELS else None
                elif element.name in ('ul', 'ol') and current_category:
                    audit['appearance_lists'] += 1
                    for item in element.find_all('li'):
                        audit['items_seen'] += 1
                        name, url, full_text = own_item_details(item)
                        if not name:
                            continue
                        mentioned = 'mentioned only' in full_text.casefold()
                        kind, assoc_cat, method, flags, eligible = classify(
                            name, url, mentioned, character_urls, assoc_by_url, keywords
                        )
                        record_key = json.dumps(
                            [source_file, story, current_category, name, url],
                            ensure_ascii=False,
                        )
                        record_id = hashlib.sha256(record_key.encode('utf-8')).hexdigest()[:20]
                        records.append({
                            'record_id': record_id, 'source_file': source_file,
                            'comic': comic, 'story': story, 'entity_display_name': name,
                            'entity_name': normalise_name(name), 'entity_url': url,
                            'listed_as': current_category, 'mentioned_only': mentioned,
                            'entity_kind': kind, 'association_category': assoc_cat,
                            'classification_method': method, 'classification_flags': flags,
                            'network_eligible': eligible,
                        })
                        audit['entities_extracted'] += 1
                        if kind == 'character':
                            audit['verified_characters'] += 1
                        if eligible:
                            audit['network_eligible'] += 1
                        if kind in ('association', 'manual_review', 'unknown', 'external_url', 'invalid'):
                            audit[kind] += 1
                element = element.find_next_sibling()
        if not records:
            audit['status'] = 'zero_entities'
    except Exception as exc:
        audit['status'] = 'error'
        audit['error_type'] = type(exc).__name__
        audit['error'] = str(exc)
        records = []
        audit['entities_extracted'] = 0
    return records, audit


def atomic_csv(df, path):
    tmp = path.with_suffix('.tmp')
    df.to_csv(tmp, index=False, encoding='utf-8')
    os.replace(tmp, path)


def inventory(files):
    # Detect added, removed or modified HTML files before resuming.
    lines = [f'{p.relative_to(HTML_FOLDER).as_posix()}\t{p.stat().st_size}\t{p.stat().st_mtime_ns}'
             for p in files]
    return hashlib.sha256('\n'.join(lines).encode()).hexdigest()


def run(args):
    files = sorted((p for p in HTML_FOLDER.rglob('*')
                    if p.is_file() and p.suffix.casefold() in ('.html', '.htm')),
                   key=lambda p: p.relative_to(HTML_FOLDER).as_posix())
    if not files:
        raise FileNotFoundError(f'No HTML files in {HTML_FOLDER}')
    character_urls, assoc_by_url, keywords = load_references()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    chunks = OUTPUT_DIR / 'chunks'
    chunks.mkdir(exist_ok=True)
    manifest_path = OUTPUT_DIR / 'manifest.json'
    input_hash = inventory(files)
    # Don't reuse batches if the CSVs have changed
    reference_hash = hashlib.sha256(
        CHARACTER_URL_FILE.read_bytes() + b'\0' + ASSOCIATION_FILE.read_bytes()
    ).hexdigest()
    manifest = {'inventory_hash': input_hash, 'reference_hash': reference_hash,
                'total_files': len(files), 'batch_size': args.batch_size}
    if args.fresh:
        for path in chunks.glob('batch_*.csv'):
            path.unlink()
        if manifest_path.exists():
            manifest_path.unlink()
    if manifest_path.exists():
        previous = json.loads(manifest_path.read_text(encoding='utf-8'))
        if previous != manifest:
            raise RuntimeError('Inputs, references, or batch size changed. '
                               'Use --fresh to restart v3 (old v1/v2 outputs are untouched).')
    else:
        if list(chunks.glob('batch_*.csv')):
            raise RuntimeError('Orphan batch files found. Inspect them, or use --fresh.')
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    total_batches = (len(files) + args.batch_size - 1) // args.batch_size
    started = time.monotonic()
    print(f'HTML files: {len(files):,}; verified character URLs: {len(character_urls):,}; '
          f'association URLs: {len(assoc_by_url):,}; workers: {args.workers}')
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        for batch in range(total_batches):
            record_path = chunks / f'batch_{batch:06d}_records.csv'
            audit_path = chunks / f'batch_{batch:06d}_audit.csv'
            if record_path.exists() and audit_path.exists():
                continue
            if record_path.exists() or audit_path.exists():
                raise RuntimeError(f'Incomplete batch {batch}. Remove both batch files and rerun.')
            selection = files[batch * args.batch_size:(batch + 1) * args.batch_size]
            tasks = [(str(p), p.relative_to(HTML_FOLDER).as_posix(),
                      character_urls, assoc_by_url, keywords) for p in selection]
            results = pool.map(extract_file, tasks, chunksize=max(1, len(tasks)//(args.workers*4)))
            rows, audits = [], []
            for records, audit in results:
                rows.extend(records)
                audits.append(audit)
            # A batch is complete only when both CSV exist.
            atomic_csv(pd.DataFrame(audits, columns=AUDIT_FIELDS), audit_path)
            atomic_csv(pd.DataFrame(rows, columns=RECORD_FIELDS), record_path)
            done = min((batch + 1) * args.batch_size, len(files))
            print(f'[{done:,}/{len(files):,}] batch rows: {len(rows):,}; '
                  f'zero files: {sum(a["status"] == "zero_entities" for a in audits)}; '
                  f'errors: {sum(a["status"] == "error" for a in audits)}; '
                  f'elapsed: {time.monotonic()-started:.0f}s', flush=True)

    print('Combining batches...')
    record_frames = [pd.read_csv(chunks / f'batch_{i:06d}_records.csv', dtype=str,
                                 keep_default_na=False)
                     for i in range(total_batches)]
    audit_frames = [pd.read_csv(chunks / f'batch_{i:06d}_audit.csv', dtype=str,
                                keep_default_na=False)
                    for i in range(total_batches)]
    df = pd.concat(record_frames, ignore_index=True)
    audit = pd.concat(audit_frames, ignore_index=True)
    df['is_exact_duplicate'] = df.duplicated(
        subset=['source_file', 'story', 'entity_display_name', 'entity_url', 'listed_as'],
        keep='first')
    df['record_status'] = df['entity_kind'].map(
        lambda kind: 'manual_review' if kind in ('invalid', 'manual_review') else 'retained')
    atomic_csv(df, OUTPUT_DIR / 'comic_characters_cleaned.csv')
    atomic_csv(audit, OUTPUT_DIR / 'comic_extraction_audit.csv')
    errors = audit[audit['status'] == 'error']
    atomic_csv(errors, OUTPUT_DIR / 'character_scraping_errors.csv')
    summary = {
        'html_files': len(files), 'files_with_records': int((pd.to_numeric(audit['entities_extracted']) > 0).sum()),
        'zero_entity_files': int((audit['status'] == 'zero_entities').sum()),
        'error_files': len(errors), 'total_rows': len(df),
        'duplicates_flagged': int(df['is_exact_duplicate'].sum()),
        'classification_counts': dict(Counter(df['entity_kind'])),
    }
    (OUTPUT_DIR / 'summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    print(json.dumps(summary, indent=2))
    print('Outputs:', OUTPUT_DIR)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workers', type=int, default=6)
    parser.add_argument('--batch-size', type=int, default=100)
    parser.add_argument('--fresh', action='store_true', help='Reset v3 chunks and rerun all HTML files')
    args = parser.parse_args()
    if args.workers < 1 or args.batch_size < 1:
        parser.error('workers and batch-size must be positive')
    run(args)

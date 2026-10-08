"""Parse cached dbGaP authorized-request pages into one JSON Lines file per study.

Usage: python parse_requests.py REQ_DIR   (REQ_DIR holds phsNNNNNN/page_*.html from harvest_dbgap.py)
Writes REQ_DIR/<phs>.jsonl with: study, page, in_sample, project_id, surname, given, affiliation, title, approved,
last_approved, status, rus_technical, rus_public.

When a harvest is incomplete, in_sample marks the requests on pages that form an unbroken prefix of the
harvester's seeded random page order (page 1 excluded), so analyses can use a random sample of pages.
"""
import html
import json
import random
import re
import sys
from pathlib import Path

ITEM = re.compile(r"<li>\s*<b>Requestor:</b>(.*?)</li>\s*(?:<br/>|</ol>)", re.S)


def field(block, label):
    m = re.search(rf"<b>{label}:</b>\s*(.*?)<br/>", block, re.S)
    return clean(m.group(1)) if m else ""


def clean(s):
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


def parse_page(text, study):
    out = []
    for m in ITEM.finditer(text):
        b = "<b>Requestor:</b>" + m.group(1)
        name = field(b, "Requestor")
        surname, _, given = name.partition(",")
        pid = re.search(r'id="summary(\d+)"', b)
        tech = re.search(r'-technical"><p>(.*?)</p></div>', b, re.S)
        pub = re.search(r'-non-technical"><p>(.*?)</p></div>', b, re.S)
        out.append({
            "study": study,
            "project_id": pid.group(1) if pid else "",
            "surname": surname.strip(),
            "given": given.strip(),
            "affiliation": field(b, "Affiliation"),
            "title": field(b, "Project"),
            "approved": field(b, "Date of initial approval"),
            "last_approved": field(b, "Date of last approval"),
            "status": field(b, "Request status"),
            "rus_technical": clean(tech.group(1)) if tech else "",
            "rus_public": clean(pub.group(1)) if pub else "",
        })
    return out


def random_prefix(d, n_pages):
    """Pages 2..n in the harvester's random order, up to the first page not yet cached."""
    study_id = re.search(r"study_id=(phs\d+\.v\d+\.p\d+)", (d / "page_0001.html").read_text()).group(1)
    order = list(range(2, n_pages + 1))
    random.Random(study_id).shuffle(order)
    got = []
    for p in order:
        f = d / f"page_{p:04d}.html"
        if not (f.exists() and "Requestor:" in f.read_text(errors="replace")):
            break
        got.append(p)
    return set(got)


def main(req_dir):
    for d in sorted(Path(req_dir).glob("phs*")):
        if not d.is_dir():
            continue
        rows = []
        pages = sorted(d.glob("page_*.html"))
        n_expected = int(re.search(r"There are (\d+) authorized", pages[0].read_text()).group(1))
        n_pages = (n_expected + 9) // 10
        complete = len(pages) >= n_pages
        keep = set(range(1, n_pages + 1)) if complete else random_prefix(d, n_pages)
        for p in pages:
            num = int(p.stem.split("_")[1])
            for r in parse_page(p.read_text(errors="replace"), d.name):
                r = {"study": r.pop("study"), "page": num, "in_sample": num in keep, **r}
                rows.append(r)
        seen, uniq = set(), []
        for r in rows:
            k = (r["project_id"], r["surname"], r["given"])
            if k not in seen:
                seen.add(k)
                uniq.append(r)
        with open(Path(req_dir) / f"{d.name}.jsonl", "w") as f:
            for r in uniq:
                f.write(json.dumps(r) + "\n")
        n_in = sum(r["in_sample"] for r in uniq)
        print(f"{d.name}: {len(pages)}/{n_pages} pages, {len(uniq)} requests parsed, {n_in} in random-page sample, "
              f"{n_expected} listed")


if __name__ == "__main__":
    main(sys.argv[1])

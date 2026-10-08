"""Harvest the public dbGaP "Authorized Data Access Requests" list for one or more studies.

Pages through GetAuthorizedRequest.cgi (10 requests per page), the same endpoint the public study page
calls from JavaScript. No login, no API key. One request every DELAY seconds (robots.txt asks generic
crawlers for Crawl-delay: 5). Raw HTML pages are cached so a rerun only fetches missing pages.

Pages are fetched in a seeded random order, so a harvest cut short is still a random sample of pages, and the
run stops at STOP_AT (NCBI asks for runs of more than 100 requests on weekends or 9 pm to 5 am Eastern on
weekdays). Rerun later to fill in the missing pages.

Usage: python harvest_dbgap.py OUT_DIR phs000178.v11.p8:4344 [phs001287.v23.p7:8003 ...]
       STOP_AT=2026-10-08T05:00-04:00 python harvest_dbgap.py ...
"""
import os
import random
import re
import sys
import time
import urllib.request
from pathlib import Path

BASE = "https://www.ncbi.nlm.nih.gov/projects/gap/cgi-bin/GetAuthorizedRequest.cgi"
UA = "nci-ods-ledger-poc/0.1 (research proof of concept; 5 s between requests)"
DELAY = 5.0
_last = [0.0]
STOP_AT = os.environ.get("STOP_AT")


def get(url):
    wait = DELAY - (time.time() - _last[0])
    if wait > 0:
        time.sleep(wait)
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=90) as r:
                _last[0] = time.time()
                body = r.read().decode("utf-8", "replace")
            if "authorized requesters" not in body:  # the CGI sometimes returns a 200 error page
                raise RuntimeError("error page")
            return body
        except Exception as e:  # transient 5xx or timeout: back off and retry
            _last[0] = time.time()
            print(f"  retry {attempt + 1} after {e}", flush=True)
            time.sleep(30 * (attempt + 1))
    raise RuntimeError(f"failed: {url}")


def harvest(out_dir, study_id, study_key):
    d = Path(out_dir) / study_id.split(".")[0]
    d.mkdir(parents=True, exist_ok=True)
    first = d / "page_0001.html"
    if not first.exists():
        first.write_text(get(f"{BASE}?study_id={study_id}&study_key={study_key}&page_number=1"))
    n = int(re.search(r"There are (\d+) authorized requesters", first.read_text()).group(1))
    pages = (n + 9) // 10
    print(f"{study_id}: {n} requesters, {pages} pages", flush=True)
    order = list(range(2, pages + 1))
    random.Random(study_id).shuffle(order)
    stop = __import__("datetime").datetime.fromisoformat(STOP_AT).timestamp() if STOP_AT else None
    for done, p in enumerate(order, 1):
        if stop and time.time() >= stop:
            print(f"{study_id}: stopped at STOP_AT after {done - 1} of {len(order)} pages", flush=True)
            return
        f = d / f"page_{p:04d}.html"
        if f.exists() and "Requestor:" in f.read_text(errors="replace"):
            continue
        f.write_text(get(f"{BASE}?study_id={study_id}&study_key={study_key}&page_number={p}"))
        if done % 25 == 0:
            print(f"  {study_id} {done}/{len(order)} pages", flush=True)
    print(f"{study_id}: done", flush=True)


if __name__ == "__main__":
    out = sys.argv[1]
    for spec in sys.argv[2:]:
        sid, key = spec.split(":")
        harvest(out, sid, key)

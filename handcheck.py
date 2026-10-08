"""Build the hand-check sheet: accepted links (half drawn at random from all links, half the earliest link of a
random linked request) and rejected candidates whose names agree but lack affiliation or topic support (what the
disambiguation step throws away).

For papers with a PMC full text, the sentences that mention the dataset are pulled from Europe PMC's fullTextXML
endpoint so the reviewer can see how the data were used. The sheet is blind: it does not say which items the
linker accepted, and it shows no scores. The sheet and labels hold names, so they stay under
~/kaggle-data/nci-ods/handcheck/ and only aggregate precision goes into poc/out/results.json.

Usage: python handcheck.py [N_ACCEPTED=40] [N_REJECTED=15]
"""
import json
import random
import re
import sys
import time
import urllib.request
from pathlib import Path

DATA = Path.home() / "kaggle-data/nci-ods"
HC = DATA / "handcheck"
FT = DATA / "fulltext_cache"
SEED = 7
TERMS = {"phs000178": re.compile(r"\bTCGA\b|Cancer Genome Atlas|phs000178", re.I),
         "phs001287": re.compile(r"\bCPTAC\b|Clinical Proteomic Tumou?r Analysis Consortium|phs001287", re.I)}


def fulltext_sentences(pmcid, rx, limit=6):
    if not pmcid:
        return []
    f = FT / f"{pmcid}.xml"
    if not f.exists():
        time.sleep(1.0)
        try:
            url = f"https://www.ebi.ac.uk/europepmc/webservices/rest/{pmcid}/fullTextXML"
            req = urllib.request.Request(url, headers={"User-Agent": "nci-ods-ledger-poc/0.1"})
            with urllib.request.urlopen(req, timeout=60) as r:
                f.write_bytes(r.read())
        except Exception:
            f.write_text("")
    xml = f.read_text(errors="replace")
    body = re.split(r"<ref-list", xml)[0]
    text = re.sub(r"<[^>]+>", " ", body)
    text = re.sub(r"\s+", " ", text)
    sents = re.split(r"(?<=[.;])\s+(?=[A-Z])", text)
    return [s[:300] for s in sents if rx.search(s)][:limit]


def main(n_acc=40, n_rej=15):
    HC.mkdir(parents=True, exist_ok=True)
    FT.mkdir(parents=True, exist_ok=True)
    reqs = {json.loads(l)["req_idx"]: json.loads(l) for l in open(DATA / "links/sample_requests.jsonl")}
    cands = [json.loads(l) for l in open(DATA / "links/candidates.jsonl")]
    for k, c in enumerate(cands):
        c["cand_id"] = k
    rng = random.Random(SEED)
    acc = [c for c in cands if c["linked"]]
    rej = [c for c in cands if not c["linked"] and c["first_name_ok"]]
    # half the accepted picks are random links (link-level precision); half are the earliest link of a random
    # linked request, which is what the share-with-a-paper and time-to-first-paper figures rest on
    rand_links = rng.sample(acc, n_acc // 2)
    firsts = {}
    for c in sorted(acc, key=lambda c: c["first_pub"]):
        firsts.setdefault(c["req_idx"], c)
    pool = [c for c in firsts.values() if c["cand_id"] not in {x["cand_id"] for x in rand_links}]
    first_links = rng.sample(pool, n_acc - n_acc // 2)
    picks = [("accepted_random", c) for c in rand_links] + [("accepted_first", c) for c in first_links] + \
            [("rejected", c) for c in rng.sample(rej, min(n_rej, len(rej)))]
    rng.shuffle(picks)
    lines = []
    for group, c in picks:
        r = reqs[c["req_idx"]]
        sents = fulltext_sentences(c.get("pmcid"), TERMS[c["study"]])
        lines += [f"## cand {c['cand_id']}",
                  f"REQUEST {r['study']} {r['surname']}, {r['given']} | {r['affiliation']} | approved {r['approved']}",
                  f"  project: {r['title']}",
                  f"  use: {r['rus_public'][:500]}",
                  f"PAPER PMID {c['pmid']} {c['first_pub']} {c['journal']}",
                  f"  title: {c['title']}",
                  f"  author first name: {c['author_first']} | orcid {c['orcid']} | aff: {' / '.join(c['author_affs'])[:300]}",
                  f"  abstract: {re.sub(r'<[^>]+>', '', c['abstract'] or '')[:1200]}",
                  "  full-text dataset sentences:"] + [f"    - {s}" for s in sents] + [""]
    (HC / "review.md").write_text("\n".join(lines))
    json.dump([{"cand_id": c["cand_id"], "group": g} for g, c in picks], open(HC / "picks.json", "w"), indent=1)
    print(f"{len(picks)} items -> {HC / 'review.md'}")


if __name__ == "__main__":
    main(*[int(x) for x in sys.argv[1:3]])

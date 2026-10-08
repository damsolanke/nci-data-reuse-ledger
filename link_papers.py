"""Link a random sample of dbGaP authorized requests to later papers by the requester that use the dataset.

For each sampled request:
  1. Candidate papers (Europe PMC REST, PubMed records only): an author matching "Surname F" (first initial; the
     full first name when the initial query would return more than 2,000 records), first published inside the
     request's window, and a dataset mention in a section that signals use: title, abstract, methods, results,
     figures, tables, supplements, data availability, keywords or acknowledgements. Mentions only in the
     introduction, discussion or reference list do not count.
  2. Name check: the matched author's surname must equal the requester's, and the full first name, when the record
     has one, must agree with the requester's first name (equal, a prefix of 4+ letters, or an initial).
  3. Disambiguation, at least one of:
       affiliation: at least half of the distinctive words of the dbGaP affiliation (or of its ROR names) appear in
                    the matched author's affiliation on the paper, or a ROR acronym (NYU, UCSF) appears there;
       topic:       TF-IDF cosine between the request (project title and research use statements) and the paper
                    (title and abstract) above the 99th percentile of cosines between requests and papers found
                    for other requests, provided the author's affiliation does not name a different country.
  4. A second query finds which of the requester's papers mention the accession number anywhere (the naive count).

Window: from the date of initial approval to two years after the date of last approval for closed, expired or
rejected requests, or to the run date for active ones. Europe PMC responses are cached, so reruns make no calls.

Usage: python link_papers.py   (paths below; writes internal files to ~/kaggle-data/nci-ods/links/)
"""
import hashlib
import json
import os
import random
import re
import time
import unicodedata
import urllib.parse
import urllib.request
from datetime import date, timedelta
from pathlib import Path

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer

DATA = Path.home() / "kaggle-data/nci-ods"
CACHE = DATA / "epmc_cache"
OUT = DATA / "links"
ROR_JSON = DATA / "ror/x/v2.14-2026-10-06-ror-data.json"
EPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest/search?"
RUN_DATE = date(2026, 10, 8)
SEED = 20261008
SAMPLE = {"phs000178": 200, "phs001287": 80}
if os.environ.get("LEDGER_SAMPLE"):  # e.g. "phs001287:5" for a quick test run
    SAMPLE = {k: int(v) for k, v in (x.split(":") for x in os.environ["LEDGER_SAMPLE"].split(","))}
OUT = Path(os.environ.get("LEDGER_OUT", str(OUT)))
STUDY_TERMS = {
    "phs000178": '(TCGA OR "Cancer Genome Atlas" OR phs000178)',
    "phs001287": '(CPTAC OR "Clinical Proteomic Tumor Analysis Consortium" OR phs001287)',
}
STUDY_REGEX = {
    "phs000178": re.compile(r"\bTCGA\b|Cancer Genome Atlas|phs000178", re.I),
    "phs001287": re.compile(r"\bCPTAC\b|Clinical Proteomic Tumou?r Analysis Consortium|phs001287", re.I),
}
CONSORTIUM = re.compile(r"Cancer Genome Atlas|TCGA|CPTAC|Clinical Proteomic Tumou?r Analysis", re.I)
USE_SECTIONS = ["TITLE", "ABSTRACT", "METHODS", "RESULTS", "FIG", "TABLE", "SUPPL", "DATA_AVAILABILITY", "KW",
                "ACK_FUND"]
GENERIC = {"university", "medical", "center", "institute", "research", "school", "college", "health", "science",
           "hospital", "national", "department", "faculty", "system", "foundation", "laboratory", "campus",
           "clinical", "biomedical", "technology", "division", "program", "graduate", "academy", "the", "of", "and",
           "at", "in", "for", "inc", "llc", "ltd", "corporation", "corp", "company", "main", "affiliated", "general",
           "children", "childrens", "comprehensive", "sciences", "health", "centre", "usa", "us"}
WEAK = {"cancer", "new", "york", "north", "south", "east", "west", "city", "san", "st", "saint", "state", "county",
        "american", "united", "states", "chinese", "china", "korea", "japan"}
_last = [0.0]
US_STATES = ("Alabama|Alaska|Arizona|Arkansas|California|Colorado|Connecticut|Delaware|Florida|Georgia|Hawaii|Idaho|"
             "Illinois|Indiana|Iowa|Kansas|Kentucky|Louisiana|Maine|Maryland|Massachusetts|Michigan|Minnesota|"
             "Mississippi|Missouri|Montana|Nebraska|Nevada|New Hampshire|New Jersey|New Mexico|New York|"
             "North Carolina|North Dakota|Ohio|Oklahoma|Oregon|Pennsylvania|Rhode Island|South Carolina|"
             "South Dakota|Tennessee|Texas|Utah|Vermont|Virginia|Washington|West Virginia|Wisconsin|Wyoming")
COUNTRY = {
    "US": re.compile(rf"\bUSA\b|United States|U\.S\.A|\b({US_STATES})\b|\b[A-Z]{{2}} \d{{5}}\b"),
    "CN": re.compile(r"\bChina\b|P\.R\.C|PR China"), "KR": re.compile(r"Korea"), "JP": re.compile(r"Japan"),
    "GB": re.compile(r"United Kingdom|\bUK\b|England|Scotland|Wales"), "DE": re.compile(r"Germany"),
    "FR": re.compile(r"France"), "CA": re.compile(r"Canada"), "IT": re.compile(r"Italy"), "ES": re.compile(r"Spain"),
    "NL": re.compile(r"Netherlands"), "AU": re.compile(r"Australia"), "CH": re.compile(r"Switzerland"),
    "SE": re.compile(r"Sweden"), "DK": re.compile(r"Denmark"), "IL": re.compile(r"Israel"),
    "TW": re.compile(r"Taiwan"), "SG": re.compile(r"Singapore"), "IN": re.compile(r"\bIndia\b"),
    "BR": re.compile(r"Brazil"), "BE": re.compile(r"Belgium"), "AT": re.compile(r"Austria"),
    "NO": re.compile(r"Norway"), "FI": re.compile(r"Finland"), "HK": re.compile(r"Hong Kong"),
    "IE": re.compile(r"Ireland"), "PT": re.compile(r"Portugal"), "IR": re.compile(r"\bIran\b"),
    "SA": re.compile(r"Saudi Arabia"), "TR": re.compile(r"Turkey|T\u00fcrkiye"), "MX": re.compile(r"Mexico"),
    "PL": re.compile(r"Poland"), "GR": re.compile(r"Greece"), "EG": re.compile(r"Egypt"),
}


def countries(affs):
    text = " ".join(affs)
    found = {c for c, rx in COUNTRY.items() if rx.search(text)}
    if "CN" in found and ("HK" in found or "TW" in found):
        found.discard("CN")
    return found


def fold(s):
    return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()


def alnum(s):
    return re.sub(r"[^a-z0-9]", "", fold(s))


def tokens(s):
    from classify_institutions import ABBREV
    return [ABBREV.get(t, t) for t in re.findall(r"[a-z0-9]+", fold(s).replace("&", " and "))]


def epmc(query, result_type="lite", page_size=1000, cursor="*"):
    h = hashlib.sha1(f"{query}|{result_type}|{page_size}|{cursor}".encode()).hexdigest()
    f = CACHE / f"{h}.json"
    if f.exists():
        return json.loads(f.read_text())
    wait = 1.0 - (time.time() - _last[0])
    if wait > 0:
        time.sleep(wait)
    url = EPMC + urllib.parse.urlencode({"query": query, "format": "json", "pageSize": page_size,
                                         "resultType": result_type, "cursorMark": cursor})
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "nci-ods-ledger-poc/0.1"})
            with urllib.request.urlopen(req, timeout=120) as r:
                d = json.load(r)
            break
        except Exception as e:
            print("  epmc retry", attempt, e, flush=True)
            time.sleep(10 * (attempt + 1))
    else:
        raise RuntimeError(query)
    _last[0] = time.time()
    f.write_text(json.dumps(d))
    return d


def epmc_all(query, result_type="core", cap=2000):
    out, cursor = [], "*"
    while True:
        d = epmc(query, result_type, 1000, cursor)
        res = d.get("resultList", {}).get("result", [])
        out.extend(res)
        nxt = d.get("nextCursorMark")
        if not res or not nxt or nxt == cursor or len(out) >= min(cap, d.get("hitCount", 0)):
            return out, d.get("hitCount", 0)
        cursor = nxt


def window(r):
    start = date.fromisoformat(r["approved"])
    if r["status"] != "approved" and r["last_approved"]:
        end = min(RUN_DATE, date.fromisoformat(r["last_approved"]) + timedelta(days=730))
    else:
        end = RUN_DATE
    return start, end


def first_name_ok(given, first):
    """Requester's first name vs the paper's: equal, an initial-only record with the same initial, or a prefix
    of at least four letters (Chris / Christopher)."""
    g = alnum((given or "").split()[0] if given else "")
    raw = (first or "").split()[0] if first else ""
    p = alnum(raw)
    if not g or not p:
        return True
    bare = raw.replace(".", "").replace("-", "")
    if len(p) == 1 or (len(bare) <= 2 and bare.isupper()):
        return p[0] == g[0]
    return p == g or (min(len(p), len(g)) >= 4 and (p.startswith(g) or g.startswith(p)))


def aff_tokens(raw):
    return [t for t in dict.fromkeys(tokens(raw)) if t not in GENERIC and not t.isdigit()]


def aff_match(req_aff, alt_names, paper_affs, acronyms=()):
    """Share of the request affiliation's distinctive words found in the author's paper affiliation (1.0 when a
    ROR acronym of the requester's institution, such as NYU or UCSF, appears as a word)."""
    ptoks = set(tokens(" ".join(paper_affs)))
    raw_words = set(re.findall(r"[A-Za-z]+", " ".join(paper_affs)))
    if any(a in raw_words for a in acronyms):
        return 1.0
    best = 0.0
    for name in [req_aff] + alt_names:
        dist = aff_tokens(name)
        if not dist:
            continue
        hit = [t for t in dist if t in ptoks]
        strong = [t for t in hit if t not in WEAK]
        if strong:
            best = max(best, len(hit) / len(dist))
    return best


def author_entries(paper, surname, initial):
    out = []
    for a in paper.get("authorList", {}).get("author", []):
        if alnum(a.get("lastName")) != alnum(surname):
            continue
        ini = alnum(a.get("initials") or a.get("firstName") or "")
        if ini and ini[0] != initial:
            continue
        affs = [x.get("affiliation", "") for x in a.get("authorAffiliationDetailsList", {}).get("authorAffiliation", [])]
        orcid = a.get("authorId", {}).get("value") if a.get("authorId", {}).get("type") == "ORCID" else None
        out.append({"first": a.get("firstName"), "affs": affs, "orcid": orcid})
    return out


def main():
    CACHE.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    affmap = json.load(open(DATA / "affiliations.json"))["affiliations"]
    ror_alt = {}
    sample = []
    frozen = OUT / "sample_requests.jsonl"
    if frozen.exists() and not os.environ.get("LEDGER_RESAMPLE"):
        # keep the sample drawn on the first run, even if more request pages were harvested since
        keep = {"study", "page", "in_sample", "project_id", "surname", "given", "affiliation", "title", "approved",
                "last_approved", "status", "rus_technical", "rus_public"}
        sample = [{k: v for k, v in json.loads(line).items() if k in keep} for line in open(frozen)]
    else:
        for study, n in SAMPLE.items():
            rows = [json.loads(line) for line in open(DATA / "requests" / f"{study}.jsonl")]
            rows = [r for r in rows if r["surname"] and r["given"] and r["approved"] and r.get("in_sample", True)]
            sample.extend(random.Random(f"{SEED}-{study}").sample(rows, n))
    ror_names = {}
    if any(affmap.get(r["affiliation"], {}).get("ror_id") for r in sample):
        want = {affmap[r["affiliation"]]["ror_id"] for r in sample if affmap.get(r["affiliation"], {}).get("ror_id")}
        for o in json.load(open(ROR_JSON)):
            if o["id"] in want:
                ror_names[o["id"]] = o["names"]
    ror_acr, req_country = {}, {}
    for r in sample:
        a = affmap.get(r["affiliation"], {})
        names = ror_names.get(a.get("ror_id"), [])
        ror_alt[r["affiliation"]] = [n["value"] for n in names if "acronym" not in n["types"]]
        ror_acr[r["affiliation"]] = [n["value"] for n in names if "acronym" in n["types"] and len(n["value"]) >= 3
                                     and n["value"].isupper()]
        req_country[r["affiliation"]] = a.get("country")

    cands = []
    for i, r in enumerate(sample):
        start, end = window(r)
        initial = alnum(r["given"])[0]
        auth = f'AUTH:"{r["surname"].replace(chr(34), "")} {initial.upper()}"'
        terms = STUDY_TERMS[r["study"]]
        use = "(" + " OR ".join(f"{s}:{terms}" for s in USE_SECTIONS) + ")"
        dates = f"FIRST_PDATE:[{start.isoformat()} TO {end.isoformat()}]"
        mode = "initial"
        if epmc(f"{auth} AND {use} AND {dates} AND SRC:MED", "lite", 1)["hitCount"] > 2000:
            # very common surname and initial: search the full first name instead
            full = alnum(r["given"].split()[0]).capitalize()
            auth = f'AUTH:"{r["surname"].replace(chr(34), "")} {full}"'
            mode = "full_first_name"
        papers, hits = epmc_all(f"{auth} AND {use} AND {dates} AND SRC:MED")
        acc = r["study"]
        acc_ids = {p.get("pmid") for p in epmc_all(f"{auth} AND {acc} AND {dates} AND SRC:MED", "lite")[0]}
        name_total = epmc(f"{auth} AND {dates} AND SRC:MED", "lite", 1)["hitCount"]
        r.update(window_start=start.isoformat(), window_end=end.isoformat(), n_candidates=hits,
                 name_papers_in_window=name_total, query_auth=auth, query_mode=mode)
        for p in papers:
            ents = author_entries(p, r["surname"], initial)
            if not ents:
                continue
            ok_first = [e for e in ents if first_name_ok(r["given"], e["first"])]
            e = (ok_first or ents)[0]
            text_ta = f'{p.get("title", "")} {p.get("abstractText", "")}'
            cands.append({
                "req_idx": i, "study": r["study"], "project_id": r["project_id"], "pmid": p.get("pmid"),
                "pmcid": p.get("pmcid"), "doi": p.get("doi"), "title": p.get("title"),
                "journal": p.get("journalInfo", {}).get("journal", {}).get("title"),
                "first_pub": p.get("firstPublicationDate"), "abstract": p.get("abstractText", ""),
                "open_access": p.get("isOpenAccess"), "in_epmc_ft": p.get("inEPMC"),
                "author_first": e["first"], "author_affs": e["affs"], "orcid": e["orcid"],
                "first_name_ok": bool(ok_first), "name_has_first": bool(e["first"]),
                "aff_score": aff_match(r["affiliation"], ror_alt[r["affiliation"]], e["affs"],
                                       ror_acr[r["affiliation"]]),
                "paper_countries": sorted(countries(e["affs"])), "req_country": req_country[r["affiliation"]],
                "mention_in_title_abstract": bool(STUDY_REGEX[r["study"]].search(text_ta)),
                "mentions_accession": p.get("pmid") in acc_ids, "n_authors": len(p.get("authorList", {}).get("author", [])),
                "consortium_paper": any(CONSORTIUM.search(a.get("collectiveName", "") or "")
                                        for a in p.get("authorList", {}).get("author", [])),
            })
        print(f"{i + 1}/{len(sample)} {r['study']} hits={hits} kept={sum(c['req_idx'] == i for c in cands)}", flush=True)

    # topic similarity and its null distribution
    req_text = [f'{r["title"]} {r["rus_public"]} {r["rus_technical"]}' for r in sample]
    pap_text = [f'{c["title"]} {c["abstract"]}' for c in cands]
    vec = TfidfVectorizer(stop_words="english", sublinear_tf=True, min_df=2, max_df=0.3,
                          token_pattern=r"(?u)\b[a-zA-Z][a-zA-Z0-9\-]{2,}\b")
    X = vec.fit_transform(req_text + pap_text)
    R, P = X[: len(sample)], X[len(sample):]
    rng2 = np.random.default_rng(SEED)
    for k, c in enumerate(cands):
        c["topic"] = float(R[c["req_idx"]].multiply(P[k]).sum())
    null = []
    for i in range(len(sample)):
        others = [k for k, c in enumerate(cands) if c["req_idx"] != i and c["study"] == sample[i]["study"]]
        if not others:
            continue
        for k in rng2.choice(others, size=min(50, len(others)), replace=False):
            null.append(float(R[i].multiply(P[k]).sum()))
    thr = float(np.percentile(null, 99)) if null else 1.0
    for c in cands:
        c["aff_ok"] = c["aff_score"] >= 0.5
        c["topic_ok"] = c["topic"] >= thr
        c["country_conflict"] = bool(c["req_country"] and c["paper_countries"]
                                     and c["req_country"] not in c["paper_countries"])
        c["linked"] = c["first_name_ok"] and (c["aff_ok"] or (c["topic_ok"] and not c["country_conflict"]))
    with open(OUT / "sample_requests.jsonl", "w") as f:
        for i, r in enumerate(sample):
            r["req_idx"] = i
            f.write(json.dumps(r) + "\n")
    with open(OUT / "candidates.jsonl", "w") as f:
        for c in cands:
            f.write(json.dumps(c) + "\n")
    json.dump({"topic_threshold_p99": thr, "topic_null_n": len(null),
               "topic_null_p95": float(np.percentile(null, 95)) if null else None,
               "n_candidates": len(cands), "n_linked": sum(c["linked"] for c in cands)},
              open(OUT / "link_params.json", "w"), indent=1)
    print("threshold", thr, "candidates", len(cands), "linked", sum(c["linked"] for c in cands))


if __name__ == "__main__":
    main()

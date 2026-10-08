"""Assign each requester affiliation string an institution type, using ROR and the 2025 Carnegie file.

Steps for each distinct affiliation string (as dbGaP prints it, e.g. "UNIVERSITY OF TX MD ANDERSON CAN CTR"):
  1. Expand eRA Commons abbreviations (CAN CTR -> cancer center, UNIV -> university, ...).
  2. Find the ROR organization: exact match on a normalized name in the ROR data dump (CC0, Zenodo), otherwise the
     ROR affiliation-matching API (anonymous, one call every API_DELAY seconds, cached on disk).
  3. For US organizations, find the Carnegie 2025 research designation (R1, R2, other) by name within the same
     state, following ROR "parent" links when the matched unit is a medical school or hospital.
  4. Apply the category rules in CATEGORY_RULES (first rule that fires wins), then manual overrides from
     institution_overrides.csv (raw affiliation -> category), which fix ambiguous strings such as "NIH".

Usage: python classify_institutions.py REQ_DIR ROR_JSON CARNEGIE_XLSX OUT_JSON
"""
import csv
import json
import re
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path

import openpyxl
from rapidfuzz import fuzz

API = "https://api.ror.org/v2/organizations?affiliation="
API_DELAY = 2.0
CACHE = Path.home() / "kaggle-data/nci-ods/ror_api_cache.jsonl"
HERE = Path(__file__).resolve().parent

ABBREV = {
    "univ": "university", "univer": "university", "universi": "university", "uni": "university",
    "ctr": "center", "cntr": "center", "cent": "center", "centre": "center", "inst": "institute", "instit": "institute",
    "can": "cancer", "canc": "cancer", "res": "research", "hosp": "hospital", "hos": "hospital", "med": "medical",
    "medicine": "medical", "sch": "school", "schl": "school", "coll": "college", "col": "college", "hlth": "health",
    "sci": "science", "scis": "science", "sciences": "science", "scien": "science", "tech": "technology",
    "technol": "technology", "natl": "national", "nat": "national", "fdn": "foundation", "fndn": "foundation",
    "fnd": "foundation", "assoc": "association", "lab": "laboratory", "labs": "laboratory", "laboratories": "laboratory",
    "saint": "st", "tx": "texas", "calif": "california", "mass": "massachusetts", "penn": "pennsylvania",
    "hlthcare": "healthcare", "sys": "system", "dept": "department", "biomed": "biomedical", "ctrs": "center",
    "centers": "center", "institutes": "institute", "universitaet": "university", "universitat": "university",
    "universite": "university", "universidad": "university", "universidade": "university", "universita": "university",
}
STOP = {"the", "of", "at", "and", "in", "for", "a", "an", "inc", "llc", "ltd", "corp", "corporation", "co", "company",
        "gmbh", "ag", "sa", "spa", "bv", "nv", "plc", "aps", "asa", "pty", "limited", "incorporated", "s", "de", "la"}
COMPANY = re.compile(r"\b(INC|LLC|L\.L\.C|LTD|CORP|CORPORATION|GMBH|AG|S\.?A\.?S?|SPA|S\.P\.A|B\.?V|N\.?V|PLC|APS|ASA|"
                     r"PTY|LIMITED|INCORPORATED|PHARMACEUTICALS?|THERAPEUTICS|BIOTECH|BIOSCIENCES?|GENOMICS,? INC)\b",
                     re.I)
# NCI-designated centers (freestanding or basic laboratory) whose dbGaP names may lack the word "cancer"
CANCER_CENTER_NAMES = re.compile(r"SLOAN|DANA-FARBER|DANA FARBER|FRED HUTCHINSON|MOFFITT|CITY OF HOPE|BECKMAN RESEARCH|"
                                 r"FOX CHASE|ROSWELL PARK|ST\.? JUDE|JACKSON LAB|SALK INST|WISTAR|COLD SPRING HARBOR|"
                                 r"SANFORD BURNHAM|SANFORD-BURNHAM|MD ANDERSON|HUNTSMAN", re.I)
GOVERNMENT = re.compile(r"^NIH\b|NATIONAL INSTITUTES OF HEALTH|NATIONAL CANCER INSTITUTE|^NCI\b|FOOD AND DRUG|"
                        r"VETERANS|CENTERS FOR DISEASE|NATIONAL LAB|ARMY|NAVY|AIR FORCE|DEPARTMENT OF", re.I)
CATEGORY_RULES = [
    "company (any country): ROR type 'company', or a corporate suffix (INC, LLC, LTD, CORP, GMBH ...) on an "
    "organization ROR does not list as education, healthcare, nonprofit, facility, government or funder",
    "non_us: ROR country is not US",
    "cancer_center: name contains 'cancer' after abbreviation expansion, or names an NCI-designated freestanding or "
    "basic-laboratory center (Sloan Kettering, Dana-Farber, Fred Hutchinson, Moffitt, City of Hope, Fox Chase, "
    "Roswell Park, St. Jude, Jackson Lab, Salk, Wistar, Cold Spring Harbor, Sanford Burnham Prebys)",
    "r1_university: Carnegie 2025 'Research 1' (directly or via the ROR parent organization)",
    "r2_university: Carnegie 2025 'Research 2'",
    "other_us_university: any other Carnegie match, ROR type 'education', or 'university'/'college' in the name",
    "us_government: ROR type 'government' or NIH/FDA/VA/national laboratory names",
    "other_us: hospitals, nonprofit research institutes and anything else in the US",
]
MSI_COLS = ["hbcu", "pbi", "annhsi", "tribal", "aanapisi", "hsi", "nasnti"]


def norm_tokens(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    s = s.replace("&", " and ").replace("'s", "s")
    toks = re.findall(r"[a-z0-9]+", s)
    return [ABBREV.get(t, t) for t in toks]


def key(s):
    toks = [t for t in norm_tokens(s) if t not in STOP]
    return " ".join(dict.fromkeys(toks))


def expand(raw):
    return " ".join(norm_tokens(raw)).title()


def load_ror(path):
    orgs = {o["id"]: o for o in json.load(open(path))}
    idx, tok_idx, df = defaultdict(set), defaultdict(set), defaultdict(int)
    for oid, o in orgs.items():
        if o["status"] == "withdrawn":
            continue
        for n in o["names"]:
            if "acronym" in n["types"]:
                continue
            k = key(n["value"])
            idx[k].add(oid)
            for t in set(k.split()):
                tok_idx[t].add((oid, k))
                df[t] += 1
    return orgs, idx, tok_idx, df


def org_summary(o):
    loc = o["locations"][0]["geonames_details"] if o.get("locations") else {}
    name = next((n["value"] for n in o["names"] if "ror_display" in n["types"]), o["names"][0]["value"])
    return {"ror_id": o["id"], "ror_name": name, "country": loc.get("country_code"),
            "state": loc.get("country_subdivision_name"), "city": loc.get("name"), "ror_types": o.get("types", [])}


def pick(ids, orgs, k):
    """Several ROR orgs share a normalized name: prefer active, then US, then the org whose display name matches."""
    def rank(oid):
        o = orgs[oid]
        s = org_summary(o)
        return (o["status"] == "active", s["country"] == "US", key(s["ror_name"]) == k,
                not any(r["type"] == "parent" for r in o.get("relationships", [])))
    return max(ids, key=rank)


def rare_tokens(k, df):
    toks = [t for t in set(k.split()) if t in df and not t.isdigit()]
    return sorted(toks, key=lambda t: df[t])


def local_fuzzy(k, orgs, tok_idx, df):
    """Best ROR name sharing the two rarest tokens of k; accept close or subset matches only."""
    rare = rare_tokens(k, df)
    if not rare or df[rare[0]] > 5000:
        return None
    cands = tok_idx[rare[0]]
    if len(rare) > 1:
        cands = {c for c in cands if rare[1] in c[1].split()} or cands
    best, best_sc = None, 0
    for oid, nk in cands:
        srt = fuzz.token_sort_ratio(k, nk)
        sub = fuzz.token_set_ratio(k, nk) == 100 and min(len(k.split()), len(nk.split())) >= 2
        sc = srt if srt >= 90 else (srt if sub and srt >= 75 else 0)
        if sc > best_sc or (sc == best_sc and sc and orgs[oid]["status"] == "active"):
            best, best_sc = oid, sc
    return best


def ror_api(text, cache):
    if text in cache:
        return cache[text]
    time.sleep(API_DELAY)
    try:
        req = urllib.request.Request(API + urllib.parse.quote(text), headers={"User-Agent": "nci-ods-ledger-poc/0.1"})
        with urllib.request.urlopen(req, timeout=60) as r:
            items = json.load(r).get("items", [])
    except Exception as e:
        print("ROR API error", text, e, flush=True)
        return None
    hit = next((i for i in items if i.get("chosen")), None)
    res = {"id": hit["organization"]["id"], "score": hit["score"]} if hit else None
    cache[text] = res
    with open(CACHE, "a") as f:
        f.write(json.dumps({"q": text, "res": res}) + "\n")
    return res


def api_plausible(k, o, df):
    """Reject API picks that share none of the query's distinctive tokens (e.g. a Spanish centre for Fred Hutch)."""
    rare = set(rare_tokens(k, df)[:3])
    names = set(" ".join(key(n["value"]) for n in o["names"]).split())
    return bool(rare & names)


def load_carnegie(path):
    ws = openpyxl.load_workbook(path, read_only=True)["data"]
    rows = ws.iter_rows(values_only=True)
    head = next(rows)
    by_state = defaultdict(list)
    for r in rows:
        d = dict(zip(head, r))
        if not d.get("instnm"):
            continue
        k = key(d["instnm"].replace("-Main Campus", "").replace("-", " "))
        by_state[d["stabbr"]].append({"name": d["instnm"], "key": k, "city": (d["city"] or "").lower(),
                                      "research": d["research2025"], "research_name": d["research2025name"],
                                      "msi": any((d.get(c) or 0) == 1 for c in MSI_COLS)})
    return by_state


def carnegie_match(o, orgs, cc):
    """Carnegie row for a US ROR org (or its parent): exact name in the same state, else a close or subset name
    match in the same city."""
    seen = set()
    while o and o["id"] not in seen:
        seen.add(o["id"])
        s = org_summary(o)
        city = (s.get("city") or "").lower()
        names = {key(n["value"]) for n in o["names"] if "acronym" not in n["types"]}
        best, best_sc = None, 0
        for c in cc.get(s["state"], []):
            for nk in names:
                if nk == c["key"]:
                    sc = 300
                else:
                    srt = fuzz.token_sort_ratio(nk, c["key"])
                    sub = fuzz.token_set_ratio(nk, c["key"]) == 100 and len(nk.split()) >= 2
                    same_city = city and city == c["city"]
                    sc = (200 + srt if same_city and (srt >= 85 or sub) else
                          100 + srt if srt >= 95 else 0)
                if sc > best_sc:
                    best, best_sc = c, sc
        if best:
            return best
        parent = next((r["id"] for r in o.get("relationships", []) if r["type"] == "parent"), None)
        o = orgs.get(parent)
    return None


def successor(o, orgs):
    """Superseded ROR records point to the organization that replaced them."""
    for _ in range(3):
        nxt = next((r["id"] for r in o.get("relationships", []) if r["type"] == "successor"), None)
        if nxt not in orgs:
            break
        o = orgs[nxt]
    return o


def categorize(raw, s, cc_row):
    exp = expand(raw)
    corporate = COMPANY.search(raw) and not re.search(r"UNIV|COLLEGE|HOSP", raw, re.I)
    if s is None:
        if CANCER_CENTER_NAMES.search(raw):
            return "cancer_center"
        if GOVERNMENT.search(raw):
            return "us_government"
        return "company" if corporate else "unknown"
    nonprofit_like = {"education", "healthcare", "nonprofit", "facility", "government", "funder"} & set(s["ror_types"])
    if "company" in s["ror_types"] or (corporate and not nonprofit_like):
        return "company"
    if s.get("country") != "US":
        return "non_us"
    if "Cancer" in exp or CANCER_CENTER_NAMES.search(raw) or CANCER_CENTER_NAMES.search(s["ror_name"]):
        return "cancer_center"
    if cc_row and cc_row["research"] == 1:
        return "r1_university"
    if cc_row and cc_row["research"] == 2:
        return "r2_university"
    if cc_row or "education" in s["ror_types"] or re.search(r"UNIV|COLLEGE|COLL\b", raw, re.I):
        return "other_us_university"
    if "government" in s["ror_types"] or GOVERNMENT.search(raw):
        return "us_government"
    return "other_us"


def main(req_dir, ror_json, cc_xlsx, out_json):
    affs = defaultdict(int)
    for f in Path(req_dir).glob("phs*.jsonl"):
        for line in open(f):
            affs[json.loads(line)["affiliation"]] += 1
    orgs, idx, tok_idx, df = load_ror(ror_json)
    cc = load_carnegie(cc_xlsx)
    cache = {}
    if CACHE.exists():
        for line in open(CACHE):
            d = json.loads(line)
            cache[d["q"]] = d["res"]
    overrides = {}
    ov = HERE / "institution_overrides.csv"
    if ov.exists():
        for r in csv.DictReader(open(ov)):
            overrides[r["affiliation"]] = r
    out = {}
    for i, (raw, n) in enumerate(sorted(affs.items(), key=lambda kv: -kv[1])):
        rec = {"n_requests": n, "expanded": expand(raw)}
        if not raw.strip():
            rec.update(category="unknown", method="blank")
            out[raw] = rec
            continue
        s, method, k = None, None, key(raw)
        ids = idx.get(k)
        if ids:
            s, method = org_summary(orgs[pick(ids, orgs, k)]), "ror_exact"
        elif (oid := local_fuzzy(k, orgs, tok_idx, df)):
            s, method = org_summary(orgs[oid]), "ror_fuzzy"
        else:
            res = ror_api(rec["expanded"], cache)
            if res and res["id"] in orgs and api_plausible(k, orgs[res["id"]], df):
                s, method = org_summary(orgs[res["id"]]), "ror_api"
        if s:
            s = org_summary(successor(orgs[s["ror_id"]], orgs))
        cc_row = carnegie_match(orgs[s["ror_id"]], orgs, cc) if s and s["country"] == "US" else None
        rec.update(s or {}, method=method or "unmatched")
        if cc_row:
            rec.update(carnegie_name=cc_row["name"], carnegie_research=cc_row["research_name"], msi=cc_row["msi"])
        rec["category"] = categorize(raw, s, cc_row)
        if raw in overrides:
            rec["category"] = overrides[raw]["category"]
            rec["method"] += "+override"
        out[raw] = rec
        if i % 100 == 0:
            print(f"{i}/{len(affs)} affiliations", flush=True)
    json.dump({"rules": CATEGORY_RULES, "affiliations": out}, open(out_json, "w"), indent=1)
    print("done", len(out))


if __name__ == "__main__":
    main(*sys.argv[1:5])

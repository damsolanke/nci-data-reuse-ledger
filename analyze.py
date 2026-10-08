"""Aggregate the ledger proof of concept into poc/out/results.json and two figures.

Only aggregates leave ~/kaggle-data/nci-ods: no requester names, no per-request rows.
Usage: python analyze.py
"""
import csv
import json
import math
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

import matplotlib
import numpy as np
import openpyxl

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from link_papers import DATA, RUN_DATE, SAMPLE, SEED, STUDY_TERMS, USE_SECTIONS, epmc  # noqa: E402

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"
FIG = OUT / "figures"
STUDY_NAME = {"phs000178": "TCGA", "phs001287": "CPTAC", "phs000218": "TARGET"}
CAT_ORDER = ["r1_university", "r2_university", "other_us_university", "cancer_center", "us_government", "other_us",
             "company", "non_us", "unknown"]
CAT_LABEL = {"r1_university": "US R1 university", "r2_university": "US R2 university",
             "other_us_university": "Other US university or college", "cancer_center": "US cancer center",
             "us_government": "US government", "other_us": "Other US (hospital, nonprofit)",
             "company": "Company (any country)", "non_us": "Non-US academic or nonprofit", "unknown": "Unresolved"}
COLORS = {"phs000178": "#2a78d6", "phs001287": "#eb6834"}
INK, INK2, GRID, SURFACE = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"


def wilson(k, n, z=1.96):
    if n == 0:
        return (None, None)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (round(c - h, 3), round(c + h, 3))


def km(times, events):
    """Kaplan-Meier: returns (t, cumulative incidence) step points for time to first linked paper."""
    order = sorted(zip(times, events))
    n_at_risk, s, pts = len(order), 1.0, [(0, 0.0)]
    i = 0
    while i < len(order):
        t = order[i][0]
        d = sum(1 for tt, e in order if tt == t and e)
        c = sum(1 for tt, e in order if tt == t)
        if d:
            s *= 1 - d / n_at_risk
            pts.append((t, 1 - s))
        n_at_risk -= c
        i += c
    return pts


def km_at(pts, t):
    v = 0.0
    for tt, ci in pts:
        if tt <= t:
            v = ci
    return round(v, 3)


def km_median(pts):
    for t, ci in pts:
        if ci >= 0.5:
            return t
    return None


def load_jsonl(p):
    return [json.loads(line) for line in open(p)]


def main():
    FIG.mkdir(parents=True, exist_ok=True)
    affmap = json.load(open(DATA / "affiliations.json"))
    aff = affmap["affiliations"]
    cited = np.load(Path.home() / "kaggle-data/spark/icite/cited_by.npy", mmap_mode="r")
    res = {"run_date": RUN_DATE.isoformat(), "studies": {}, "institution_rules": affmap["rules"]}
    ws = openpyxl.load_workbook(DATA / "carnegie/cc2025.xlsx", read_only=True)["data"]
    rows_cc = ws.iter_rows(values_only=True)
    head = next(rows_cc)
    hbcu = {d["instnm"] for d in (dict(zip(head, r)) for r in rows_cc) if d.get("hbcu") == 1}

    # A. who receives access: every harvested request
    for f in sorted((DATA / "requests").glob("phs*.jsonl")):
        study = f.stem
        harvested = load_jsonl(f)
        rows = [r for r in harvested if r.get("in_sample", True)]
        cats = Counter(aff.get(r["affiliation"], {}).get("category", "unknown") for r in rows)
        us_acad = [r for r in rows if aff.get(r["affiliation"], {}).get("category") in
                   ("r1_university", "r2_university", "other_us_university")]
        years = Counter(r["approved"][:4] for r in rows if r["approved"])
        res["studies"][study] = {
            "name": STUDY_NAME.get(study, study),
            "approved_requests_listed_on_dbgap": int(__import__("re").search(
                r"There are (\d+) authorized", (DATA / "requests" / study / "page_0001.html").read_text()).group(1)),
            "requests_in_random_page_sample": len(rows),
            "distinct_requesters": len({(r["surname"].lower(), r["given"].lower()) for r in rows}),
            "distinct_institution_strings": len({r["affiliation"] for r in rows}),
            "first_approval": min(r["approved"] for r in rows if r["approved"]),
            "latest_approval": max(r["approved"] for r in rows if r["approved"]),
            "status": dict(Counter(r["status"] for r in rows)),
            "approvals_by_year": dict(sorted(years.items())),
            "institution_split": {c: {"n": cats.get(c, 0), "share": round(cats.get(c, 0) / len(rows), 3)}
                                  for c in CAT_ORDER},
            "hbcu_requests": sum(aff.get(r["affiliation"], {}).get("carnegie_name") in hbcu for r in rows),
            "msi_share_of_us_university_requests": round(
                sum(bool(aff[r["affiliation"]].get("msi")) for r in us_acad) / max(1, len(us_acad)), 3),
        }

    # B. outcomes for the linked sample
    sample = load_jsonl(DATA / "links/sample_requests.jsonl")
    cands = load_jsonl(DATA / "links/candidates.jsonl")
    for k, c in enumerate(cands):
        c["cand_id"] = k
    params = json.load(open(DATA / "links/link_params.json"))
    all_reqs = {s: load_jsonl(DATA / "requests" / f"{s}.jsonl") for s in SAMPLE}
    by_pi = defaultdict(list)
    for s, rows in all_reqs.items():
        for r in rows:
            by_pi[(s, r["surname"].lower(), r["given"].lower())].append(r["approved"])
    linked = defaultdict(list)
    for c in cands:
        if c["linked"]:
            linked[c["req_idx"]].append(c)

    labels = {}
    lab_file = DATA / "handcheck/labels.csv"
    if lab_file.exists():
        for r in csv.DictReader(open(lab_file)):
            labels[int(r["cand_id"])] = r
    groups = {p["cand_id"]: p["group"] for p in json.load(open(DATA / "handcheck/picks.json"))}

    def prec(group_names):
        rows = [labels[c] for c, g in groups.items() if g in group_names]
        ok = sum(r["verdict"] == "correct" for r in rows)
        bad = sum(r["verdict"] == "incorrect" for r in rows)
        unsure = sum(r["verdict"] == "unsure" for r in rows)
        return {"checked": len(rows), "correct": ok, "incorrect": bad, "unsure": unsure,
                "precision_excluding_unsure": round(ok / (ok + bad), 3) if ok + bad else None,
                "wilson_95ci": wilson(ok, ok + bad),
                "precision_if_unsure_counted_wrong": round(ok / len(rows), 3) if rows else None,
                "wrong_person": sum(r["verdict"] == "incorrect" and r["person_ok"] == "no" for r in rows),
                "right_person_no_data_use": sum(r["verdict"] == "incorrect" and r["person_ok"] == "yes"
                                                for r in rows)}

    p_links = prec({"accepted_random"})
    p_first = prec({"accepted_first"})
    precision = p_links["precision_excluding_unsure"]
    precision_first = p_first["precision_excluding_unsure"]
    rej = prec({"rejected"})
    res["precision_check"] = {
        "random_links": p_links,
        "first_link_of_random_linked_request": p_first,
        "all_accepted": prec({"accepted_random", "accepted_first"}),
        "rejected_name_compatible_candidates": {"checked": rej["checked"], "true_links_found": rej["correct"],
                                                "unsure": rej["unsure"]},
        "method": "Blind review (the sheet did not say which items the linker accepted): 20 random accepted links, "
                  "the earliest accepted link of 20 random linked requests, and 15 random rejected candidates with "
                  "compatible names. Each was judged on the request's project title and research use statement "
                  "against the paper's title, abstract, author first name and affiliation, the full-text sentences "
                  "that mention the dataset (PMC open access), and the Europe PMC sections that mention it. "
                  "Correct = same person and the paper analyzes the dataset. Labels are kept for re-review.",
    }

    out_s = {}
    gap_rows = {}
    km_curves = {}
    for study in SAMPLE:
        reqs = [r for r in sample if r["study"] == study]
        times, events, firsts, per_req, attributed = [], [], [], [], 0
        attributed_by_req = []
        cats = defaultdict(lambda: [0, 0])
        pmids = set()
        for r in reqs:
            start = date.fromisoformat(r["window_start"])
            end = date.fromisoformat(r["window_end"])
            ls = linked.get(r["req_idx"], [])
            uniq = {c["pmid"]: c for c in ls}
            pmids |= set(uniq)
            cat = aff.get(r["affiliation"], {}).get("category", "unknown")
            cats[cat][0] += 1
            if uniq:
                cats[cat][1] += 1
                first = min(date.fromisoformat(c["first_pub"]) for c in uniq.values())
                times.append((first - start).days)
                events.append(True)
                firsts.append((first - start).days)
                per_req.append(len(uniq))
                prior = sorted(by_pi[(study, r["surname"].lower(), r["given"].lower())])
                own = sum(max(a for a in prior if a <= c["first_pub"]) == r["approved"] for c in uniq.values())
                attributed += own
                attributed_by_req.append(own)
            else:
                times.append((end - start).days)
                events.append(False)
                attributed_by_req.append(0)
        first_time = [r for r in reqs if min(by_pi[(study, r["surname"].lower(), r["given"].lower())]) == r["approved"]]
        ft_days = []
        for r in first_time:
            ls = linked.get(r["req_idx"], [])
            if ls:
                ft_days.append((min(date.fromisoformat(c["first_pub"]) for c in ls) -
                                date.fromisoformat(r["window_start"])).days)
        long = [r for r in reqs if (date.fromisoformat(r["window_end"]) -
                                    date.fromisoformat(r["window_start"])).days >= 3 * 365]
        long_with = sum(bool(linked.get(r["req_idx"])) for r in long)
        pts = km(times, events)
        km_curves[study] = (pts, sorted(times)[-5])  # draw the curve while at least 5 requests are still at risk
        cites = [int(cited[int(p)]) for p in pmids if p and int(p) < len(cited)]
        old = [int(cited[int(c["pmid"])]) for c in {c["pmid"]: c for r in reqs for c in linked.get(r["req_idx"], [])}.values()
               if c["pmid"] and c["first_pub"] <= "2023-12-31"]
        lpapers = {c["pmid"]: c for r in reqs for c in linked.get(r["req_idx"], [])}
        with_acc = sum(c["mentions_accession"] for c in lpapers.values())
        in_ta = sum(c["mention_in_title_abstract"] for c in lpapers.values())
        n_with = sum(1 for e in events if e)
        n_total = res["studies"][study]["approved_requests_listed_on_dbgap"]
        est = attributed / len(reqs) * n_total
        boot_rng = np.random.default_rng(SEED)
        arr = np.array(attributed_by_req)
        boot = [boot_rng.choice(arr, len(arr)).mean() * n_total for _ in range(2000)]
        est_ci = [round(float(np.percentile(boot, 2.5))), round(float(np.percentile(boot, 97.5)))]
        out_s[study] = {
            "name": STUDY_NAME[study], "sampled_requests": len(reqs),
            "requests_with_linked_paper": n_with, "share_with_linked_paper": round(n_with / len(reqs), 3),
            "share_with_linked_paper_95ci": wilson(n_with, len(reqs)),
            "share_with_paper_precision_adjusted": round(n_with / len(reqs) * precision_first, 3) if precision_first else None,
            "requests_with_3y_window": len(long), "share_with_paper_among_3y_window": round(long_with / max(1, len(long)), 3),
            "km_cumulative_share_with_paper": {f"{y}y": km_at(pts, 365 * y) for y in (1, 2, 3, 5)},
            "km_median_days_to_first_paper": km_median(pts),
            "median_days_to_first_paper_among_linked": float(np.median(firsts)) if firsts else None,
            "iqr_days_to_first_paper_among_linked": [float(np.percentile(firsts, 25)), float(np.percentile(firsts, 75))] if firsts else None,
            "first_time_requests": {"n": len(first_time), "with_paper": len(ft_days),
                                    "median_days_to_first_paper": float(np.median(ft_days)) if ft_days else None},
            "median_linked_papers_per_linked_request": float(np.median(per_req)) if per_req else None,
            "distinct_linked_papers": len(lpapers),
            "linked_papers_citing_accession_anywhere": with_acc,
            "share_linked_papers_citing_accession": round(with_acc / max(1, len(lpapers)), 3),
            "linked_papers_naming_dataset_in_title_or_abstract": in_ta,
            "linked_papers_with_consortium_author": sum(c["consortium_paper"] for c in lpapers.values()),
            "citations_icite": {"papers": len(cites), "total": int(sum(cites)),
                                "median": float(np.median(cites)) if cites else None,
                                "mean": round(float(np.mean(cites)), 1) if cites else None,
                                "median_for_papers_published_by_2023": float(np.median(old)) if old else None,
                                "share_with_10plus": round(sum(x >= 10 for x in cites) / max(1, len(cites)), 3)},
            "by_institution_type": {c: {"sampled": cats[c][0], "with_paper": cats[c][1],
                                        "share": round(cats[c][1] / cats[c][0], 3) if cats[c][0] else None}
                                    for c in CAT_ORDER if cats[c][0]},
            "attributed_papers_in_sample": attributed,
            "rough_extrapolated_reuse_papers_all_requests": round(est),
            "rough_extrapolated_precision_adjusted": round(est * precision) if precision else None,
            "rough_extrapolated_bootstrap_95ci_unadjusted": est_ci,
        }
        terms = STUDY_TERMS[study]
        use = "(" + " OR ".join(f"{s}:{terms}" for s in USE_SECTIONS) + ")"
        gap_rows[study] = {
            "epmc_records_with_accession_string": epmc(f'"{study}"', "lite", 1)["hitCount"],
            "epmc_records_with_accession_annotation": epmc(f"ACCESSION_ID:{study}", "lite", 1)["hitCount"],
            "epmc_records_naming_dataset_in_use_sections": epmc(use, "lite", 1)["hitCount"],
            "epmc_records_naming_dataset_anywhere": epmc(terms, "lite", 1)["hitCount"],
        }
    res["sample"] = {"seed": SEED, "per_study": SAMPLE, "topic_threshold_p99": round(params["topic_threshold_p99"], 4),
                     "candidates_screened": params["n_candidates"], "links_accepted": params["n_linked"],
                     "window_rule": "initial approval date to last approval + 2 years (closed, expired or rejected requests) or "
                                    "to the run date (active requests)",
                     "tcga_sample_frame": "requests on the first 58 pages of a seeded random page order (580 requests)"}
    res["outcomes"] = out_s
    res["naive_counts_europe_pmc"] = gap_rows
    res["terms_of_use"] = {
        "dbgap_authorized_requests": {
            "robots_txt": "www.ncbi.nlm.nih.gov/robots.txt (fetched 2026-10-08): /projects/gap/ is not disallowed; "
                          "generic crawlers get Crawl-delay: 5",
            "ncbi_policy": "NCBI website policies: run scripts with more than 100 requests on weekends or 9 pm to "
                           "5 am Eastern on weekdays; NCBI-created content is public domain, NLM acknowledgment "
                           "requested",
            "requester_consent": "Data use agreements state that the requester's name, institution, project name "
                                 "and research use description may be posted publicly; NOT-OD-25-159 requires "
                                 "repositories to post them",
            "practice": "5 s between pages, no login, no API key; 2026-10-08 03:33 to 05:00 ET plus two re-fetches; "
                        "bulk TSV endpoint returned HTTP 502 for TCGA; outputs hold aggregates only"},
        "europe_pmc": "anonymous REST API, at most 1 call per second, responses cached; terms page not reachable "
                      "by script (HTTP 403)",
        "ror": "CC0 data dump; anonymous API for unmatched strings at 1 call per 2 s",
    }
    json.dump(res, open(OUT / "results.json", "w"), indent=1)

    # Figure 1: what accession counting sees
    plt.rcParams.update({"font.family": "Helvetica Neue", "font.size": 10, "axes.edgecolor": GRID,
                         "axes.labelcolor": INK2, "xtick.color": INK2, "ytick.color": INK2})
    fig, ax = plt.subplots(figsize=(6.4, 2.3), dpi=200)
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)
    names = [STUDY_NAME[s] for s in SAMPLE][::-1]
    for y, s in enumerate(list(SAMPLE)[::-1]):
        o = out_s[s]
        a, b = o["linked_papers_citing_accession_anywhere"], o["distinct_linked_papers"] - o["linked_papers_citing_accession_anywhere"]
        ax.barh(y, a, height=0.5, color=COLORS[s], edgecolor=SURFACE, linewidth=2)
        ax.barh(y, b, left=a, height=0.5, color=COLORS[s], alpha=0.35, edgecolor=SURFACE, linewidth=2)
        ax.text(a + b + 8, y, f"{a + b} papers, {a} mention {s}", va="center", color=INK, fontsize=9)
    ax.set_yticks(range(len(names)), names)
    ax.set_xlabel("Papers linked to sampled requests (dark: also found by searching the accession number)")
    ax.set_title("Accession search finds few of the papers linked to approved requests", loc="left", fontsize=10.5,
                 color=INK)
    ax.spines[["top", "right", "left"]].set_visible(False)
    ax.tick_params(axis="y", length=0)
    ax.grid(axis="x", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    ax.set_xlim(0, max(o["distinct_linked_papers"] for o in out_s.values()) * 1.4)
    fig.tight_layout()
    fig.savefig(FIG / "fig1_accession_gap.png", facecolor=SURFACE)
    plt.close(fig)

    # Figure 2: time from approval to first linked paper (Kaplan-Meier)
    fig, ax = plt.subplots(figsize=(6.4, 3.0), dpi=200)
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)
    xmax = 0
    for s, (pts, tmax) in km_curves.items():
        pts = [(t, ci) for t, ci in pts if t <= tmax]
        xs = [t / 365.25 for t, _ in pts] + [tmax / 365.25]
        ys = [ci * 100 for _, ci in pts] + [pts[-1][1] * 100]
        xmax = max(xmax, xs[-1])
        ax.step(xs, ys, where="post", color=COLORS[s], linewidth=2)
        ax.text(xs[-1] + 0.1, ys[-1], f"{STUDY_NAME[s]} {ys[-1]:.0f}%", color=INK, va="center", fontsize=9)
    ax.set_xlim(0, math.ceil(xmax) + 1)
    ax.set_title("Time from access approval to the requester's first linked paper (Kaplan-Meier)", loc="left",
                 fontsize=10.5, color=INK)
    ax.set_ylim(0, 100)
    ax.set_xlabel("Years since data access was approved")
    ax.set_ylabel("Requests with a linked paper (%)")
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    fig.tight_layout()
    fig.savefig(FIG / "fig2_time_to_first_paper.png", facecolor=SURFACE)
    plt.close(fig)
    print(json.dumps({s: {k: v for k, v in o.items() if k != "by_institution_type"} for s, o in out_s.items()}, indent=1))
    print(json.dumps(res["precision_check"], indent=1))


if __name__ == "__main__":
    main()

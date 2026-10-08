# Data reuse ledger: proof of concept

This folder links approved dbGaP data access requests for two NCI datasets, TCGA (phs000178) and CPTAC (phs001287), to the papers the requesters later published with those data. It also collects the TARGET (phs000218) request list for the institution-type counts. It supports the Track 1 entry in `../docs/research_2026-10-08.md`.

## What it does

| Step | Script | Input | Output |
|---|---|---|---|
| 1. Harvest request lists | `harvest_dbgap.py` | public dbGaP "Authorized Requests" pages | cached HTML, `~/kaggle-data/nci-ods/requests/phs*/` |
| 2. Parse | `parse_requests.py` | cached HTML | one JSON Lines file per study |
| 3. Institution types | `classify_institutions.py` | affiliations, ROR data dump, Carnegie 2025 file, `institution_overrides.csv` | `~/kaggle-data/nci-ods/affiliations.json` |
| 4. Link requests to papers | `link_papers.py` | random sample of 200 TCGA and 80 CPTAC requests | candidates and links, `~/kaggle-data/nci-ods/links/` |
| 5. Hand-check sheet | `handcheck.py` | random accepted and rejected candidates | `~/kaggle-data/nci-ods/handcheck/review.md` (labels go in `labels.csv` there) |
| 6. Aggregate | `analyze.py` | all of the above, iCite citation counts | `out/results.json`, `out/figures/*.png` |

`out/evidence_page.md` is the draft one-page "Supporting Evidence" text. The TCGA linking sample (200 requests) was drawn from the first 58 random pages harvested; the population counts in `out/results.json` use all 218 random pages harvested so far.

## Run it

```bash
cd poc
uv venv --python 3.13 .venv
uv pip install --python .venv/bin/python rapidfuzz numpy pandas matplotlib openpyxl scikit-learn

D=~/kaggle-data/nci-ods
# 1-2: one page every 5 seconds, and the TCGA pages take about 20 seconds each to serve, so TCGA alone
# needs about 3.5 hours. Run on a weekend or 9 pm to 5 am Eastern; STOP_AT ends the run on time, pages come in
# a seeded random order, and a rerun fetches only the missing pages.
STOP_AT=2026-10-09T05:00:00-04:00 nohup .venv/bin/python -u harvest_dbgap.py $D/requests \
  phs001287.v23.p7:8003 phs000218.v26.p8:6521 phs000178.v11.p8:4344 > $D/harvest.log 2>&1 &
.venv/bin/python parse_requests.py $D/requests

# 3: needs the ROR dump (Zenodo, CC0) and the Carnegie 2025 public data file
.venv/bin/python classify_institutions.py $D/requests $D/ror/x/v2.14-2026-10-06-ror-data.json \
  $D/carnegie/cc2025.xlsx $D/affiliations.json

# 4-6: about 50 minutes of Europe PMC calls on a cold cache; the sample is frozen after the first run
# (LEDGER_RESAMPLE=1 draws a new one)
.venv/bin/python link_papers.py
.venv/bin/python handcheck.py 40 15        # then label review.md into labels.csv
.venv/bin/python analyze.py
```

Inputs that are not fetched by the scripts:

| Input | Where | Notes |
|---|---|---|
| ROR data dump v2.14 (2026-10-06) | https://zenodo.org/records/23187250 | CC0 |
| Carnegie Classifications 2025 public data file | https://carnegieclassifications.acenet.edu/wp-content/uploads/2025/04/2025-Public-Data-File.xlsx | research designation and MSI flags |
| iCite citation counts | `~/kaggle-data/spark/icite/cited_by.npy` | built from the NIH Open Citation Collection snapshot of August 2026 |

## Method in brief

- **Candidates.** For each sampled request, Europe PMC returns PubMed records with an author "Surname F" (first initial), first published between the initial approval date and two years after the last approval (or today for active requests), and a mention of the dataset (name, acronym or accession) in a section that signals use: title, abstract, methods, results, figures, tables, supplements, data availability, keywords or acknowledgements. Introduction, discussion and reference-list mentions do not count.
- **Name check.** The matched author's surname must equal the requester's, and the full first name, when the record has one, must agree.
- **Disambiguation.** A candidate becomes a link if the author's affiliation on the paper contains at least half of the distinctive words of the requester's dbGaP affiliation or of its ROR names, or a ROR acronym such as NYU. Failing that, it becomes a link if the TF-IDF similarity between the request (project title and research use statements) and the paper (title and abstract) is above the 99th percentile of similarities between requests and papers found for other requests, and the author's affiliation does not name a different country.
- **Naive count.** A second query per request finds which papers mention the accession number anywhere in Europe PMC, which is what accession-based counting would see.
- **Precision.** `handcheck.py` writes a blind sheet (no scores, no accept or reject flag) with 20 random accepted links, the earliest accepted link of 20 random linked requests, and 15 random rejected candidates whose names agree. Each item shows the request's project title and research use statement next to the paper's title, abstract, author first name and affiliation, and the full-text sentences that mention the dataset when PMC has an open-access copy. The labels sit in `~/kaggle-data/nci-ods/handcheck/labels.csv` for re-review.
- **Institution types.** Each affiliation string is matched to ROR (exact name, then fuzzy, then the ROR affiliation API), US universities to the Carnegie 2025 research designation, then the first matching rule applies: company (any country), non-US, US cancer center, R1, R2, other US university or college, US government, other US. The rules are listed in `out/results.json` under `institution_rules`; `institution_overrides.csv` fixes strings the matcher gets wrong.

## Terms of use and data handling

| Source | What applies | What this code does |
|---|---|---|
| dbGaP "Authorized Requests" pages | NCBI robots.txt (fetched 2026-10-08) does not disallow `/projects/gap/` and sets `Crawl-delay: 5` for generic crawlers. NCBI's website policies ask scripts to "Run retrieval scripts on weekends or between 9 pm and 5 am Eastern Time weekdays for any series of more than 100 requests"; NCBI-created content is public domain, with NLM acknowledgment requested. Requesters agree in the data use agreement that their name, institution, project name and research use description "may be posted publicly on the repository website", and NOT-OD-25-159 requires repositories to post them. | One page every 5 seconds, no login, no API key, a descriptive User-Agent. On 2026-10-08 (a Thursday) the harvest ran from 3:33 to 5:00 AM Eastern: CPTAC and TARGET in full, and 218 of TCGA's 581 later pages in random order (2,180 of 5,818 requests). Two pages that came back as server-error pages were fetched again at about 5:10 AM. The bulk download endpoint returned HTTP 502 for TCGA twice. `~/kaggle-data/nci-ods/resume_harvest_tonight.sh` fetches the remaining TCGA pages from 9:05 PM Eastern on 2026-10-08 and stops at 5:00 AM. |
| Europe PMC REST API | Free and anonymous. The developer and terms pages returned HTTP 403 to scripted fetches, so no published rate limit was checked. | At most one call per second; every response is cached so reruns make no calls. |
| ROR | Data dump is CC0. The API is anonymous; client-ID registration is paused and ROR says no rate limits are enforced in the meantime. | Dump first; API only for strings the dump cannot match, one call every 2 seconds, cached. |
| Personal data | Requester names are public, but the ledger does not need to republish them. | Names are kept only under `~/kaggle-data/nci-ods/` to make the links. `out/` holds aggregates only: no names, no per-request rows. |

## License

Code: MIT, see [LICENSE](LICENSE). Aggregate tables and figures in `out/`: CC0.

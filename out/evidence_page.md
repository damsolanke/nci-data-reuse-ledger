# Supporting Evidence

**NCI Data Reuse Ledger: a working proof of concept on TCGA and CPTAC, built October 8, 2026.** Code (MIT) and aggregate outputs: [repository link to add]. No requester names appear in the outputs.

**Method.** From the public dbGaP "Authorized Requests" lists I drew a random sample of 200 TCGA (phs000178) and 80 CPTAC (phs001287) requests, then searched Europe PMC for later papers by an author with the requester's surname and first initial (first name checked when recorded) that name the dataset in the title, abstract, methods, results, figures, tables or data statement. A paper counted as a link only when the author's affiliation matched the requesting institution or its abstract closely matched the request's research use statement.

| Measure | TCGA | CPTAC |
|---|---|---|
| Approved requests listed on dbGaP | 5,818 | 487 |
| Sampled requests with at least one linked paper | 62% (123 of 200) | 24% (19 of 80) |
| Same, for requests with 3 or more years to publish | 74% (95 of 129) | 48% (13 of 27) |
| Requests with a linked paper within one year (Kaplan-Meier) | 41% | 13% |
| Median days from approval to first linked paper | 271 | 423 |
| Distinct linked papers | 736 | 81 |
| Linked papers that mention the accession number anywhere | 51 (7%) | 14 (17%) |
| Median citations per linked paper (NIH iCite) | 24 | 23 |

**Precision.** A blind check of 40 accepted links found 33 correct, 4 wrong and 3 undecidable: 89% of decided links (95% CI 75 to 96%). For the first link of each request, which drives the percentages above, 18 of 19 decided links were correct. Three of the four errors named the right person but showed no real use of the data; one was "ExAC non-TCGA" in a methods section.

**The counting gap.** Europe PMC holds 876 records that mention phs000178. It holds 140,439 that name TCGA in a section that signals use. Scaled up from the sample, approved TCGA requesters alone account for about 15,000 papers that use the data after the precision correction (rough range 11,000 to 20,000).

**Who gets access.** In a random 2,180 of the 5,818 TCGA requests, 36% came from non-US institutions, 28% from US R1 universities, 12% from companies, 9% from US cancer centers, 3% from R2 universities and 1% from other US colleges. Six requests came from historically Black colleges and universities. CPTAC had one such request in 487 and TARGET none in 676. Of the sampled TCGA requests, 79% from R1 universities (45 of 57) and 76% from cancer centers (16 of 21) led to a linked paper, compared with 17% from companies (4 of 24).

Figure: `figures/fig1_accession_gap.png` (papers found by the ledger versus an accession search).

**Limits.** The counts probably run low. Name search misses some papers, full text covers mostly open-access articles, and 2 of the 15 rejected candidates in the check were real links. Each link shows that an approved requester used TCGA or CPTAC, not that the paper used the controlled tier. Repeat requesters may publish from earlier access; for first-time requests the TCGA median is 296 days. Because NCBI asks that large scripted jobs run overnight, the TCGA list was sampled by page; the remaining pages are scheduled. Institution types come from ROR and the 2025 Carnegie Classification, with 77 manual corrections.

Sources: dbGaP Authorized Requests for phs000178, phs001287 and phs000218; Europe PMC REST API; NIH Open Citation Collection (August 2026); ROR data v2.14 (CC0); Carnegie Classifications 2025 public data file.

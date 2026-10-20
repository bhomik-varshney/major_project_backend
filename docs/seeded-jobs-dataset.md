# Reproducible 1,000-job demonstration corpus

The job fixture now contains exactly 1,000 listings across 46 career categories. This extends the existing seeded provider without adding a job SQL table, external requests, matching scores or agents. Every company/vacancy is fictional, every record is labeled `seeded_mock`/`generated_data=true`, and every source/application URL uses the inactive `example.invalid` domain.

## Generation and safe reset

`app/providers/job_catalog.py` contains coherent role recipes: titles, industry, department, domain skills, tools and specific work descriptions. `job_dataset.py` chooses variations using SHA-256 of the fixed seed, record index and field name. This avoids global PRNG state, randomized Python hashes, wall-clock dates and network dependencies. The ten original stable IDs remain compatibility anchors in `data/job_anchors.json`; they are generator inputs, not a separate active dataset.

`python -m scripts.seed_jobs` generates and validates 1,000 records and atomically replaces `app/providers/data/jobs.json`. A repeat run restores the same bytes. Optional `--seed` and `--output` support isolated experiments; validation must use the same seed. The seeder rejects database extensions, anchor replacement and non-job JSON. It imports no workspace database service and opens no SQL connection. Tests snapshot every workspace table around reseeding and confirm no changes to profiles, documents, activities, extraction/agent runs or verification receipts. Applications are not implemented; the seeder has no mechanism to access any future application table either.

`SeededJobProvider.reset()` invalidates only its local cache. File size/mtime/inode detect atomically replaced fixtures on subsequent requests. A lock protects cold parsing/cache replacement. Warm requests reuse the validated snapshot and a bounded normalized-text cache. Corrupt/unreadable datasets yield a safe 503; invalid/duplicate records are excluded with an explicit warning, rather than silently accepted. Production fixture validation is stricter and requires all 1,000 records to pass.

## Coverage and record fields

The 46 recipes cover software/frontend/backend/full-stack/mobile/web, analysis/data science/ML/AI/NLP/vision, security/cloud/DevOps/SRE/database/testing, design/product/business/project management/writing/research, embedded/IoT/robotics/networking/support/systems, fintech/martech/marketing/BI/operations/HR/health/education/e-commerce/games/Web3/GIS/energy and developer education. Each category has 21–25 listings.

The default seed `20261019`, version `2026-10-demo-v2` and reference date `2026-10-19` produce:

| Dimension | Counts |
| --- | --- |
| Experience | 243 student, 194 fresher, 207 graduate, 176 entry level, 144 junior, 36 limited experience |
| Employment | 249 internship, 580 full time, 79 contract, 92 apprenticeship |
| Work mode | 237 remote, 250 hybrid, 513 onsite |
| Availability at reference date | 838 active, 135 expired, 27 future-dated |
| Countries/currencies | India/INR, US/USD, UK/GBP, Germany/EUR, Canada/CAD, Singapore/SGD |

Fields include stable ID/title/company/industry/department/location/country, work mode plus compatible `remote` boolean, employment/experience level and years, description/responsibilities, required/preferred skills/tools, education/languages, optional salary and currency/period, dates, source labels/URLs/generated flag, canonical skill tokens/category and dataset/company-size/evaluation metadata. Skill wording can retain JS versus JavaScript, while explicit aliases normalize search tokens. Multi-word concepts remain complete canonical phrases. Salaries and dates are illustrative values, not current market estimates.

## Controlled evaluation cases

Labeled cases include 23 empty descriptions, 247 missing salaries, 58 absent preferred-skill lists, 34 overlapping-skill cases, 12 unrelated-skill requirements, 10 incomplete-metadata examples, seven intentionally unusual/conflicting requirements and three near-duplicate pairs with distinct IDs and requisition wording. Expired/future and remote/location-specific examples are also present. Empty/unknown fields remain present with empty values or nulls; these cases are valid records, unlike corrupt schemas or invalid date ranges.

`python -m scripts.job_evaluation_cases` exports 16 deterministic candidate/job examples for exact/partial/missing skills, explicit synonyms, multi-word skills, semantic review, experience ranges, location/remote/employment/domain preferences, required-versus-preferred overlap, duplicates and availability. Factual checks are tested. The semantic example is a human-review prompt for later experiments; no unimplemented semantic matcher or ranking score is asserted.

## Search contract and UI

The existing session-protected `GET /api/v1/jobs` supports:

| Filter | Meaning |
| --- | --- |
| `keyword` | AND of normalized tokens over title/company/industry/department/description/responsibilities/skills; includes back-end/backend and explicit skill aliases. |
| Repeated `skill` | All canonical skill names must be present in required or preferred skills; JS/JavaScript, ML/machine learning and NLP/natural language processing are equivalent. |
| `category`, `country` | Case-insensitive exact normalized value. |
| `location`, `company`, `industry` | Case-insensitive substring. |
| `work_mode` | `remote`, `hybrid` or `onsite`. Legacy `remote=true` means fully remote; false includes onsite/hybrid. |
| `employment_type` | Internship, full time, contract or apprenticeship slugs. |
| `experience` | Student, fresher, graduate, entry level, junior or limited experience slugs. |
| `posted_from`, `posted_to` | Inclusive ISO calendar-date range; reversed/invalid ranges return 422. |
| `availability`, `as_of` | All/active/expired/future relative to an explicit date; default fixed reference date, never an implicit moving clock. Closing date remains active through that date. |
| `page`, `page_size`, `max_results` | One-based stable-ID pagination, 1–50 items/page, complete result cap 1–1000. |

Filters combine with AND. Responses retain `items,total,page,page_size,source,dataset_version,warnings,filters` and add `matched_total` (before cap) and `reference_date`. Out-of-range pages return empty items. `GET /api/v1/jobs/options` provides actual categories/industries/countries/modes/employment/experience choices and dataset metadata. No search history is written.

The frontend keeps the existing panels/navigation and adds category/experience/work-mode choices plus collapsible advanced filters and role details. It renders per-record currency and monthly/annual salary, absent-description/salary states, labeled expired/future examples and safe ISO dates. No active application links or fabricated ranking scores are shown.

A future provider can implement `JobProvider.snapshot()` and normalize its records to this business shape, allowing the same filtering/pagination service to remain. Before enabling live Adzuna/Arbeitnow records, their real-source validation and UI labels must be deliberately added; the current seeded schema requires demonstration flags and invalid-domain URLs, preventing real and synthetic records from being confused. No live integration is included here.

## Validation and measured performance

```sh
python -m scripts.seed_jobs
python -m scripts.validate_jobs
python -m scripts.job_evaluation_cases
python -m pytest
```

The validator checks count, full field presence, schema/ranges/dates/canonical tokens, IDs, repeated reads, common credential patterns, unintentional identical records, domain/evaluation coverage, seed/version/reference date and exact generator bytes. It reports only aggregate counts and a SHA-256 digest. Malformed input errors omit record contents so an accidental credential is not printed. Pattern scanning is a defense against common credential formats, not a universal secret detector.

Measured locally with the generated 1,000-record fixture: cold validation/load about 28.6 ms; 30 warm service searches for Python with 50-item pages had a median of 1.71 ms and maximum of 1.82 ms. These are local service measurements, not a network/SLA guarantee. Tests check bounded warm service and HTTP response time, deterministic concurrent reads, generator equality across a fresh process/hash seed, reseeding/cache reset, malformed records, all filters, date states, aliases, pagination/caps, database preservation and the labeled evaluation examples.

Frontend validation includes Chromium and WebKit, responsive layouts, loading/empty/error recovery, expanded filters, role details and all existing resume/profile workflows. Production build, TypeScript and lint are run separately from browser tests to avoid development-server rebuilds interrupting navigation.

## Delivery checks

| Check | Result |
| --- | --- |
| Dataset validation | Exactly 1,000 valid/reproducible records, 46 categories, all intended edge-case groups present. |
| Default fixture SHA-256 | `e69af5cb3f5d8abb6208bee9fbd032cf4b3d03d3c35f8e75a2d7f38f699390d1` |
| Backend suite | 168 passed, including fixture generation, HTTP/service performance and database-preservation tests. |
| Chromium browser suite | 22 passed. |
| WebKit browser suite | 22 passed. |
| Main frontend | Port 5173 verified in WebKit: 1,000 results, 46 categories, desktop/mobile search and no browser exceptions. |
| API smoke | Port 8000: 1,000 total, 46 categories, 135 expired, 27 future, capped pagination correct. Warm local HTTP median 4.13 ms across 11 measured requests after warmup. |
| Alternate seed | Documented seed-123 generation/validation example passed in an isolated temporary directory. |
| Other checks | TypeScript, production build, Python compilation, dependency compatibility, credential-pattern scans and diff whitespace checks passed. Lint has zero errors and ten existing fast-refresh warnings. |

The landing page was independently updated to Ascendra during this work. Browser expectations now match those existing labels; branding and landing-page changes were preserved. Navigation tests wait for the workspace connection indicator before moving to another route, avoiding premature navigation during hydration. No commit, push or deployment was performed as part of this resumed validation work.

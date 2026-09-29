<!-- Append this section to stock-intel's existing CLAUDE.md. Do not replace the file. -->

## Event-Study & Swing Research Module — Non-Negotiables

This module exists to answer one question honestly: *how has this stock, and its peers, reacted to this kind of event, and is that pattern still alive?* It feeds the `swing-strategy-builder` skill through a read-only MCP server. It is advisory and structural, not predictive.

### 1. LLM as extractor, not computer
- Models may extract structured facts from text (8-K item classification, guidance direction from a press release). Every extraction stores the source accession number and the extracted span.
- All metrics, returns, estimates, rankings, and tests are deterministic Python. No model computes a number that gets stored.
- No price prediction, no sentiment scores.

### 2. Point-in-time or it doesn't exist
- Every row that could inform a decision has a `knowledge_time`: the earliest moment the information was public. For SEC data this is the EDGAR `acceptanceDateTime` (from the submissions API, joined by accession number), not the `filed` date and not the period end.
- A computation dated T may only read rows with `knowledge_time <= T`. There is a test for this, and it must fail loudly on violation.
- Session classification comes from knowledge_time in America/New_York. Before 09:30 → before-open, and the reaction day is the same session. After 16:00 → after-close, and the reaction day is the next session. Between those → intraday, flagged. If a press release timestamp is known and earlier than acceptance, use it and record which was used.
- Amendments (8-K/A, 10-Q/A) are new events with their own knowledge_time. They never overwrite the original.
- Prices store raw and adjusted values, plus `source` and `ingested_at`. Revisions are appended, never updated in place.

### 3. Reaction measurement (fixed definitions; change only with a migration + changelog entry)
- Windows: `gap` = prior close → reaction-day open; `d0` = prior close → reaction-day close; `drift` = reaction-day close → close of trading day +20. Drift is the swing-relevant window.
- Abnormal return = stock minus its mapped sector ETF over the same window. Secondary: beta-adjusted vs SPY, with beta estimated on trading days [-250, -30] before the event.
- Every reaction row stores `code_version` (git SHA) and `computed_at`.

### 4. Estimation discipline
- Shrinkage is mandatory. Report own-history, peer-pool, and shrunk estimates side by side. The shrinkage weight is `n_eff / (n_eff + k)`, with `k` estimated from the peer cross-section, never hand-set.
- Recency weighting: exponential, default half-life 3 years. Always report the effective sample size, `n_eff`.
- Sample labels follow the skill: `n_eff < 30` is anecdotal, 30–100 is exploratory.
- Walk-forward only. Refit quarterly, and score only on events after the fit's cutoff. No full-sample fit is ever reported as a result.
- Structural-break check: compare the most recent 12 events with the earlier history using a permutation test. Flag a break; never auto-drop history.
- Condition on surprise when the data exists (EPS/revenue vs consensus, guidance direction). Where consensus data is unavailable, the field is NULL and the profile says so. Never proxy it silently.
- Parameters are round and fixed. Change one parameter per iteration, logged in `docs/event-study-changelog.md` with the before/after walk-forward numbers.
- Overfitting is detected by the gap between in-sample and out-of-sample numbers, not by how a chart looks.

### 5. Tests and golden set
- Red tests first: write the failing test before the fix or feature.
- The golden set must include adversarial cases before any profile is exposed over MCP:
  - splits and reverse splits
  - ticker changes
  - delisted names
  - 8-K/A amendments
  - null-body or exhibit-only 8-Ks (the MATINS over-merge failure mode)
  - filings accepted after 17:30 ET, which EDGAR dates to the next business day
  - Friday after-close reports
  - market holidays and half-days
  - halted sessions
  - multiple 8-Ks on the same day
- Clean-data precision and recall of 1.0 is not evidence. The adversarial cases are the evidence.

### 6. Data sources and etiquette
- SEC EDGAR: send a declared `User-Agent` with a contact email, stay under 10 requests/sec, and cache raw JSON in MinIO before parsing.
- Prices come through a `PriceSource` interface. The vendor is configured in `.env`. Any free or scraped fallback is marked `source='dev_fallback'`, and profiles built on it are flagged as such.
- Never scrape Fidelity. Holdings come only from the SnapTrade MCP connector, which lives in Claude Code's config, not in this repo.

### 7. MCP server rules
- Read-only. Connect through a Postgres role with `SELECT` on the module's tables only.
- Every tool response includes `as_of`, `source`, `code_version`, `n`, `n_eff`, and a `sample_label`, so the skill can label each number as Data or Calc.
- A missing value is returned as `null` with a reason string. The server never fills gaps.

### 8. Jobs
- Scheduled jobs are plain Python in the Compose stack. No model sits in the ingest or compute path.
- Jobs are idempotent and write to a `job_runs` table (start, end, status, rows touched, error).
- Before any rebase or cleanup, commit working state and tag it.

# Kickoff prompt: swing research stack in Claude Code

Paste everything below the line into Claude Code, started from the root of the stock-intel repo. Keep `swing-strategy-builder.skill` and `CLAUDE.md-event-study-section.md` in the repo root, or give their paths when asked.

---

You're helping me build a research stack for systematic swing trading (1–6 week holds). The stack connects three things:

1. My `swing-strategy-builder` skill, which writes regime, ticker, and strategy briefs.
2. My Fidelity holdings, read-only through the SnapTrade MCP connector.
3. A new event-study module inside this repo (stock-intel). It measures how stocks and their peers have historically reacted to earnings and 8-K events, and whether those patterns are still alive. A local read-only MCP server exposes the results.

This is advisory infrastructure. Nothing in it places orders or predicts prices.

## How to work

- Work phase by phase. At the start of each phase, use plan mode: show the plan, the files you'll touch, and the tests you'll write, then wait for my OK. At the end of each phase, stop, summarize what changed, and show the test output.
- Write failing tests first, then make them pass.
- Commit at the end of each phase and tag it `event-study-p<N>`.
- Don't invent structure. Read the repo before proposing anything.
- If a decision isn't covered here, ask. Don't guess.

## Phase 0: Orient (read-only, no code)

1. Read `CLAUDE.md`, the README, `docker-compose*.yml`, the Alembic migrations, existing models, and any roadmap docs for M1–M8.
2. Append the contents of `CLAUDE.md-event-study-section.md` to the end of the existing `CLAUDE.md`. Don't rewrite anything already there. Show me the diff.
3. Report back:
   - how the current stack is organized
   - where the event-study module should live
   - which M1–M8 milestone it fits under
   - any conflicts between the new section and existing non-negotiables
4. Ask me these questions in one message, then wait:
   - Price data vendor, and whether I have an API key yet. If not, we build against the `PriceSource` interface with a clearly flagged dev fallback.
   - The starting universe (tickers and sectors), and the sector ETF mapping I want (default: SPDR sector ETFs).
   - Whether I have any consensus-estimate source. If not, surprise fields stay NULL.
   - Whether I have any options data for pre-event implied moves. If not, that column stays NULL and I may enter it by hand.
   - Which machine runs the nightly jobs, and whether it's always on.

## Phase 1: Claude Code setup (outside the repo)

1. Install the skill for all my projects. Unzip `swing-strategy-builder.skill`, which is a zip archive, into `~/.claude/skills/`. The result should be `~/.claude/skills/swing-strategy-builder/SKILL.md` plus `scripts/swing_metrics.py`.
2. Patch the skill's Section 3. Replace the paragraph that starts "**The code sandbox usually has no internet access.**" with:

   > **Data access depends on the environment.** In Claude Code, if the `stock-intel` MCP tools are available, use them first for price-derived metrics, event profiles, and regime snapshots. They return `as_of`, `source`, `n`, and `n_eff`, so label their numbers Calc and cite the underlying source as Data. If network access is available, you may fetch SEC EDGAR and quote pages directly. In environments without network access (e.g., claude.ai), current values come from web search and multi-year history from CSVs the user attaches. Holdings come only from the SnapTrade connector or a positions CSV the user exports from Fidelity Trader+.

3. Add a subsection **"D. Event Profile (used inside Ticker Briefs)"** after Section 4C. It applies when a hold window crosses an earnings date or a material 8-K is pending. In that case, call `event_profile` and `event_history`, and report in one table row group:
   - shrunk abnormal drift estimate with its range
   - own-history vs peer-pool estimate
   - `n` and `n_eff` with the sample label
   - the structural-break flag
   - historical median absolute d0 move vs the current implied move, if available
   - whether surprise conditioning was available

   Keep the anti-bloat rule: this adds at most 3 table rows and one sentence of context.

4. Add SnapTrade: `claude mcp add --transport http snaptrade https://mcp.snaptrade.com/mcp`. Then tell me to run `/mcp` and authenticate. After I confirm, test it by listing my accounts and positions. Don't print full account numbers.
5. Confirm the skill loads by asking which skills are available.

## Phase 2: Schema and ingest

Build Alembic migrations and models. Adapt the names to the repo's conventions and show me the final schema before migrating.

- `securities`: ticker history with valid-from/to dates, CIK, sector, mapped sector ETF, active/delisted.
- `daily_bars` (Timescale hypertable): raw OHLCV, adjusted OHLCV, `source`, `ingested_at`. Revisions are appended.
- `sec_filings`: accession, CIK, form, `acceptance_datetime` (from the submissions API), filed date, 8-K item codes, amendment-of link, raw JSON pointer in MinIO.
- `fundamentals_pit`: CIK, XBRL concept, value, unit, period start/end, accession, `knowledge_time`. Knowledge time comes from joining on accession to get `acceptance_datetime`.
- `events`: event_id, security, `event_type` (earnings, or an 8-K item such as 1.01, 2.01, 5.02, 7.01, 8.01), `knowledge_time`, which timestamp was used, session (before-open / intraday / after-close), reaction date, source accession, and these nullable fields: EPS/revenue surprise, guidance direction plus its extraction span, pre-event implied move.
- `job_runs`.

Ingest jobs, all idempotent:

- EDGAR submissions and company facts for the universe. Declared User-Agent, rate-limited under 10 requests/sec, raw JSON cached to MinIO first.
- Daily bars through `PriceSource`.
- Event builder: earnings events from 8-K item 2.02, plus the other 8-K items listed above.
- If MATINS extraction is usable here, call it for guidance direction and store the span. Otherwise leave the field NULL and add a TODO. Don't build a second extractor.

The golden set comes first. Build it from the adversarial cases in the CLAUDE.md section before writing the ingest code, and make the point-in-time leak test part of it.

## Phase 3: Reactions and profiles

- `event_reactions`: gap, d0, and drift (day +1 through day +20) windows. For each: raw return, abnormal return vs the sector ETF, beta-adjusted return vs SPY, `code_version`, `computed_at`.
- `event_profiles`, per ticker × event_type × conditioning bucket, as of a date:
  - own estimate, peer-pool estimate, and the shrunk estimate using the weight rule in CLAUDE.md
  - `n` and `n_eff` under a 3-year half-life
  - the structural-break test on the last 12 events vs earlier ones
  - walk-forward stats: quarterly refits, scored only on later events, compared with a zero-drift baseline
- `predictions_log`: before each upcoming event, store the profile's stated expectation and its range. After the event, score it. This is the only true out-of-sample record, so it must be written before the event, never backfilled.
- Deliver a short report on the starting universe: how many profiles are anecdotal, exploratory, or have more than 100 events; how many show breaks; and the walk-forward result vs the baseline. No charts needed.

## Phase 4: Local read-only MCP server

- Use the Python MCP SDK (FastMCP) inside this repo. It runs locally over stdio and connects through a new Postgres role that has `SELECT` access only.
- Tools, each returning `as_of`, `source`, `code_version`, `n`, `n_eff`, `sample_label`, and `null` plus a reason for anything missing:
  - `event_profile(ticker, event_type, as_of=None)`
  - `event_history(ticker, event_type, limit=20)`
  - `upcoming_events(tickers, days=21)`
  - `price_metrics(ticker, bench=None)`: reuse the logic from the skill's `swing_metrics.py` against `daily_bars`, and import it rather than copying it.
  - `regime_snapshot()`: SPY vs its 50- and 200-day averages, plus VIX level and 1-year percentile if we store them. Anything not stored comes back null with a reason.
- Register it with `claude mcp add stock-intel -- <command that starts the server>`. Show me the command before running it.
- Test it end to end. Ask for a ticker brief on one holding that has earnings inside 3 weeks, and confirm the brief uses SnapTrade for the position, stock-intel for the history, and web search only for current values.

## Phase 5: Scheduling

- Add a scheduler service to Docker Compose, using cron or APScheduler, whichever fits the repo:
  - nightly after the close: EDGAR poll, bars, event builder, reactions for newly completed windows
  - before each upcoming event: `predictions_log` entries
  - quarterly: profile refits
- Every job writes to `job_runs`. Add a simple "last successful run per job" query that `regime_snapshot` can report, so briefs flag stale data.
- Optional, and only after I confirm: a weekday cron job that runs Claude Code headless (`claude -p`), restricted to read-only tools. It asks for a regime brief plus a check of my holdings for events in the next 3 weeks, and writes the result to `briefs/YYYY-MM-DD.md`. It never feeds anything back into the database.

## Done means

- Every phase has a tag.
- The golden set, including the adversarial cases, passes.
- The point-in-time leak test passes.
- `/mcp` shows both `snaptrade` and `stock-intel` connected.
- A real ticker brief comes out in the skill's format with every number sourced and dated.

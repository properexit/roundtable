# Build log & decisions

Running log of what was decided and why, kept so the reasoning survives past
the code itself (and doubles as interview prep material).

## LLM backend
**Decided: Azure OpenAI, gpt-4.1-mini.**

Chose 4.1-mini over gpt-4.1 and gpt-5-mini: cheap enough for a multi-agent
system firing several calls per run, and the more recognizable, defensible
choice in an interview versus explaining why a research demo needed a
frontier-class model.

Notable friction getting here, worth remembering for interviews as a real
cloud-operations story: the Azure for Students subscription's region-
allowlist policy rejected the first deployment attempt (auto-routed to
eastus2 under 'Global Standard', which the subscription disallows).
Fixed by switching the deployment type to 'Standard' (regional, pinned to
the resource's own region, Germany West Central) and picking gpt-4.1-mini,
which had availability there. gpt-35-turbo was not available in that
region/subscription at all.

## Why MCP instead of calling APIs directly from agent code
- Tools become a versioned, inspectable contract instead of ad-hoc function
  calls buried in agent prompts.
- The one state-changing tool (execute_simulated_trade) can be gated and
  audit-logged in one place, regardless of which agent or graph path reaches it.
- Directly demonstrates a protocol that's new enough most candidates haven't
  touched it yet.

## Why LangGraph instead of a single ReAct agent
- The roundtable structure (three independent analysts → one synthesizer) is
  a real state machine, not a single loop — LangGraph makes the control flow
  explicit and inspectable rather than implicit in a prompt.
- Makes the human-approval gate a first-class node in the graph, not a bolt-on
  check.

## Why "simulated" trading only
- Avoids real financial/regulatory exposure.
- The interesting engineering problem (multi-agent synthesis, tool-gating,
  audit trail, eval) doesn't require real order execution to be genuine.

## Known tech debt (tracked, not urgent)
- `create_react_agent` (langgraph.prebuilt) is deprecated in favor of
  `langchain.agents.create_agent`. Left as-is for now since it still works
  and I can't test the newer API from a sandboxed shell without live model
  access -- revisit if time allows, otherwise mention in the writeup as a
  known upgrade path rather than something missed.

## Milestone: full core system verified end-to-end (2026-08-29)
Confirmed live and working, in this order: yfinance price/fundamentals,
NewsAPI, Azure AI Language sentiment, Azure OpenAI (gpt-4.1-mini) reasoning
through a real MCP client/server round trip, the LangGraph Roundtable graph
(parallel Fundamentals+News -> draft -> Risk -> final), and the human-
approval interrupt/resume mechanism in both branches (reject never touches
state; approve executes with a real live price and updates the audit log).

This is the core "does the architecture actually work" question answered
yes. What remains is productionization: a UI, the eval/backtest harness,
CI/CD, and the Azure deployment (swapping local JSON state for Table
Storage, adding Key Vault + Container Apps + App Insights).

## Known tech debt: MCP client instantiation per tool-fetch
`get_mcp_tools()` spawns a fresh `MultiServerMCPClient` (and therefore a new
Python subprocess running the MCP server) on essentially every call --
across one /analyze run this means 4-5 separate subprocess spawns
(fundamentals, news, risk, propose, execute), each paying the full import
cost of langchain/azure SDKs. This is almost certainly what caused OOM
crashes on the Container App's default 0.25 CPU / 0.5Gi allocation.

Fast fix applied: bumped the Container App to 1.0 CPU / 2Gi.
Real fix (not done yet, time-permitting): hold one long-lived MCP client/
session for the duration of a graph run (or for the process lifetime) and
reuse it across nodes instead of reconnecting per tool-fetch. Documented
here rather than silently patched over with more resources and left
unexplained.

## Bug: MCP subprocess env vars worked locally, failed in the container
Root cause: `news.py`/`sentiment.py` call `load_dotenv()` at import time,
which reads `.env` directly off disk relative to CWD. Locally, the spawned
MCP server subprocess's CWD is the project root where `.env` lives, so it
silently loaded secrets straight from the file every time, regardless of
whether env vars were actually inherited from the parent process. In the
container there's deliberately no `.env` file (secrets come from Azure
Container App env vars instead) -- so the subprocess fell through to
relying on inherited environment, which wasn't being passed explicitly by
langchain-mcp-adapters' stdio transport, and every news/sentiment tool call
failed with a KeyError. market_data.py never surfaced this since it needs
no credentials. Fixed by explicitly passing `env=dict(os.environ)` when
spawning the MCP server subprocess (src/agents/mcp_client.py).

## Feature: real Upstox account integration (read-only) (2026-08-30)
Added a second, explicitly separate surface: my *real* Upstox
holdings/positions/funds, alongside (not mixed into) the simulated $100k
paper portfolio the agents actually trade against. Nothing about the
agents' trade proposal/approval/execution path changed -- this is purely a
new read-only view.

### Why not Upstox's hosted MCP server
Upstox exposes a hosted, remote MCP server (`https://mcp.upstox.com/mcp`)
built for interactive chat clients (Claude Desktop, Cursor, etc.) where a
human is present to click through an OAuth screen. `langchain-mcp-adapters`
doesn't have a clean story for carrying a per-user bearer token through a
remote MCP connection from an unattended backend, and remote-MCP-with-OAuth
is generally still an unsettled pattern industry-wide. Rather than fight
that, this calls Upstox's own REST API directly (same underlying data, a
normal documented OAuth2 flow) and exposes it as ordinary FastAPI routes --
keeping the MCP boundary at the one server this project actually controls
(src/mcp_server/server.py), not extending it to a third party's.

### Why OAuth (daily login) instead of Upstox's Analytics Token
Upstox has a second token type, the "Analytics Token": 1-year validity,
generated directly from the developer dashboard, no daily re-auth. It looks
like the obvious answer for an always-on service -- except it requires
calling from a pre-registered **static outbound IP** for any account-level
endpoint (holdings/positions/funds/orders/profile). Azure Container Apps on
the Consumption plan (what `roundtable-api` runs on) does not offer a
static outbound IP without upgrading to a paid Workload Profile + NAT
Gateway environment. That's new, ongoing infrastructure cost and complexity
to take on for a personal project on student credits, for a benefit (no
daily click) that isn't worth it here. Went with the standard OAuth
authorization-code flow instead: I click a "Connect Upstox" link roughly
once a day, the resulting access token is valid until 3:30 AM IST the next
day (a SEBI-driven policy applying to all Indian brokers, not Upstox-
specific), and the system just treats "no token issued today" as
disconnected and asks for a fresh login. A real production version serving
many users, or one where daily manual login was unacceptable, would justify
the static-IP infrastructure; for one personal account it doesn't.

### Session design (src/api/auth.py)
This project has exactly one real user. Rather than build a real
auth/user system, added the smallest thing that's still honest about being
a security boundary: one shared password (env var, compared with
`secrets.compare_digest` to avoid timing leaks) issues a signed,
time-limited token (`itsdangerous.URLSafeTimedSerializer`, 12h expiry) that
every protected route checks via a FastAPI dependency. No database, no
password hashing library, no session store -- correctly scoped to "keep my
real financial data off a page anyone with the URL can hit," not scoped to
be a general-purpose auth system.

### OAuth CSRF via `state`
`/auth/upstox/login` can only be reached with a valid site session (token
passed as a query param, since a full-page redirect can't carry an
Authorization header). That same token is threaded through as Upstox's
`state` parameter and re-validated in `/auth/upstox/callback` -- standard
OAuth CSRF protection, and it happens to reuse the exact same session-check
logic as every other protected route, so there's no separate mechanism to
keep in sync.

### Storage (src/tools/upstox_auth_store.py)
The OAuth token is stored in Azure Table Storage, not a local file --
Container Apps instances are ephemeral, and a restart or new revision would
silently wipe a local file. `AZURE_STORAGE_CONNECTION_STRING` had been
sitting in `.env` unused since the very first setup; this is the first
thing that actually uses it. Token freshness is checked by comparing the
stored "issued on" date (IST) against today, rather than tracking the exact
3:30 AM cutoff -- simpler, and the cost (occasionally asking for a fresh
login a little earlier than strictly required) is negligible.

### Verification
Full auth/session/OAuth-redirect flow verified with FastAPI's TestClient
(no real network calls to Upstox needed for this): unauthenticated access
to `/portfolio/real/*` correctly 401s; wrong site password 401s; correct
password issues a working session token; a protected route with a valid
session but no Upstox connection returns `connected: false` instead of an
error; a garbage/tampered session token 401s on both the data routes and
`/auth/upstox/login`; the Upstox authorize-dialog redirect URL is built
correctly (client_id, redirect_uri, state all present); and
`/auth/upstox/callback` correctly rejects a bad `state` before ever
attempting a token exchange. `src/tools/upstox_account.py`'s three
response shapes (no token stored / valid token + 200 / valid-looking token
but Upstox returns 401) were each verified in isolation with a mocked
`requests.get`. What's *not* yet verified is the real end-to-end path
against Upstox's live API and a real Azure Table -- that needs your actual
login and gets checked once this is deployed.

## Bug: real-Upstox login gave a generic "Failed to fetch" in the browser
Root cause: shipped `src/api/auth.py` and the Upstox routes reading
`SITE_LOGIN_PASSWORD`, `SITE_SESSION_SECRET`, `UPSTOX_CLIENT_ID`,
`UPSTOX_CLIENT_SECRET`, `UPSTOX_REDIRECT_URI`, and (for the first time)
`AZURE_STORAGE_CONNECTION_STRING` from `os.environ`, and had these added to
the *local* `.env` -- but never pushed them to the actual Azure Container
App as secrets/env vars. Local `.env` and the deployed container's
environment are entirely separate; adding a key locally does nothing to
what's running in Azure. `/auth/login` hitting `os.environ["SITE_LOGIN_PASSWORD"]`
therefore raised an unhandled `KeyError` -> FastAPI 500 -> the response came
back without CORS headers (a well-known FastAPI/Starlette behavior:
CORSMiddleware only adds its headers to responses that complete normally,
not to ones short-circuited by an unhandled exception) -> the browser
reported this as a generic "Failed to fetch" instead of a readable error,
which is what made this confusing to diagnose from the frontend alone.

Fixed by extending `scripts/deploy_env_vars.sh` to also push the six new
values as Container App secrets + env vars. Worth remembering for next
time: "add to .env" and "deploy" are two separate steps, and skipping the
second one fails silently from the browser's point of view rather than
with an obviously-related error message.

## Bug: storage connection string silently truncated at the first semicolon
`scripts/deploy_env_vars.sh` loads `.env` via plain `source .env`. Every value
in that file had been simple enough (API keys, URLs) that this never
mattered -- but an Azure Storage connection string is delimited by `;`
(`DefaultEndpointsProtocol=https;AccountName=...;AccountKey=...;...`), and
in bash, an unquoted `;` in a sourced file ends one statement and starts a
new one. `source` silently accepted `AZURE_STORAGE_CONNECTION_STRING=DefaultEndpointsProtocol=https`
as the full assignment and treated `AccountName=...`, `AccountKey=...` etc.
as separate (harmless, no-op) statements afterward -- no error, no warning,
just an incomplete value quietly making it all the way to
`az containerapp secret set`, then failing much later, deep in the Azure
Table SDK, as "Connection string missing required connection details."

Fixed by quoting the value in `.env` (`KEY="value;with;semicolons"`) so
bash's `source` treats it as one literal string. Confirmed via
`az containerapp logs show` that the traceback pointed exactly at
`TableServiceClient.from_connection_string` -- diagnosed the same way the
earlier OOM and env-inheritance bugs were: read the actual container logs
rather than guess from the browser's generic "Failed to fetch".

## Milestone: real Upstox account integration verified end-to-end (2026-08-30)
Confirmed live: site login gate, OAuth handshake with Upstox (authorization
code flow + CSRF-safe `state`), token persisted in Azure Table Storage, and
real holdings/positions/funds rendering on daycandle.org behind the login.
Positions and funds matched the field names assumed from Upstox's public
docs exactly (`trading_symbol`, `quantity`, `average_price`, `pnl`,
`product`; `equity`/`commodity` segments with `available_margin` etc.) --
no frontend changes needed once the data actually flowed.

Three real bugs surfaced and fixed getting here, all logged above as they
happened rather than smoothed over: secrets added to local `.env` but never
pushed to the Container App (client-visible as a generic "Failed to fetch"
masking a server-side 500); a nonexistent Storage Account plus a stale
Azure CLI token queued up two more layers to peel back; and a connection
string silently truncated at its first semicolon by naive `bash source`.
Every one of them was found by reading the actual container logs
(`az containerapp logs show`) rather than guessing from symptoms -- the
same diagnostic discipline used earlier for the OOM and env-inheritance
bugs.

## Feature: real Upstox position as context for the Portfolio Manager (2026-08-30)
When you're logged in (site session token present) and run the roundtable
on a ticker, `/analyze` now checks your real Upstox holdings/positions for
that exact ticker and, if found, folds a one-line summary into both the
draft and final Portfolio Manager prompts -- e.g. "the user already holds
15 shares of X at an average price of Y" -- so the agent can reason about
concentration or whether adding to / trimming a real position makes sense.

### Why this stayed gated behind login, not just "always on"
`/analyze` is the public, unauthenticated demo -- deliberately so, it's the
thing anyone visiting the site can try. If real-position lookup ran
unconditionally, anyone could learn my real holdings just by running an
analysis on a ticker I own. Scoped instead to: real context only gets
computed when the request carries a valid site session token (checked the
same way as every other protected route). An anonymous request gets
`real_position_context: None` threaded through the graph, which is a no-op
for the prompts -- byte-for-byte the same analysis as before this feature
existed. Verified both paths (with and without a valid session, plus a
garbage/invalid token, which is silently treated as anonymous rather than
erroring) with FastAPI's TestClient against a stubbed graph.

### Known limitation: ticker matching is exact-string against NSE/BSE symbols
Real position lookup matches the entered ticker against Upstox's own
`trading_symbol` field exactly (case-insensitive). My real holdings are
Indian-market symbols (e.g. "PNB", "SAGILITY"); the demo mostly analyzes
US tickers via yfinance (AAPL, NVDA, ...). A match only happens when what's
typed into "Run Roundtable" is also my exact real trading symbol -- not
attempting fuzzy/cross-market matching between two genuinely different
ticker namespaces. Distinguished "not connected to Upstox" (adds nothing
to the prompt) from "connected, checked, confirmed no position" (adds an
explicit "no position" line) rather than collapsing both into silence --
the second case is real information the agent should have.

## Feature: eval harness -- forward-tracking + rule-based historical backtest (2026-08-30)
Two deliberately separate pieces, not one, because they're evaluating two
different things and mixing them would misrepresent both:

### A. Forward-tracking (eval/tracker.py)
Runs a new `build_analysis_graph()` (src/agents/graph.py -- the same four
analyst nodes as the real pipeline, reusing the exact node functions, just
stopped before propose/human_approval/execute since this runs unattended
with no human to approve a trade) against a small fixed watchlist
(AAPL, MSFT, NVDA), and logs each decision + the price at that moment to a
new `EvalSnapshots` Table Storage table. `eval.compute_agent_performance()`
replays the recorded decisions sequentially (buy spends notional cash,
sell converts back, hold no-ops) into a simple equity curve, compared
against `eval.baselines.all_baselines()` (buy-hold / hold-cash / sell-short)
computed from the same first/last recorded price.

Triggered weekly by `.github/workflows/eval-snapshot.yml` (GitHub Actions'
own cron scheduler) calling `POST /eval/record-snapshot`, gated by a shared
secret (`EVAL_TRIGGER_SECRET`, constant-time compared) rather than the
site-login auth -- this is a scheduled job, not a person with a session.
Chose GitHub Actions over provisioning a dedicated Azure Container App Job
for this: it's free, cron-native, and this repo's CI/CD already lives
there -- no new Azure resource needed for a once-a-week HTTP call.

### B. Rule-based historical backtest (eval/backtest.py)
NOT the LLM agents -- a moving-average-crossover strategy (golden
cross / death cross) backtested over real historical prices (yfinance
genuinely has years of OHLCV, unlike news or fundamentals). Exists as a
second, clearly-separate reference point: does a real systematic rule beat
buy-hold/hold-cash/sell-short over a period the agents themselves cannot
be honestly evaluated on?

### Why not backtest the actual agents historically
Two independent data walls, not one: NewsAPI's free tier only returns
articles from the past ~30 days, and yfinance's fundamentals
(`get_fundamentals`) are always *current* company data, not a point-in-time
historical snapshot -- there's no free source for "what was AAPL's P/E on
2023-06-01." Either constraint alone rules out a full historical backtest
of the real system; feeding today's fundamentals or today's news into a
decision dated years ago and calling it a "backtest" would be quietly
wrong, not a shortcut. Chose to keep A and B honest about what data each
one actually has, rather than build one backtest that fudges either gap.

### Verification
`eval/baselines.py` and `eval/backtest.py` are pure functions, unit tested
directly (including an independent trade-log replay that reconstructs
`sma_crossover_backtest`'s own ending value from its returned trades list
and confirms they match, catching arithmetic bugs the return-value
assertion alone wouldn't). `eval/tracker.py`'s Table Storage and graph
calls verified with mocks (record_snapshot writes the right entity shape
once per watchlist ticker; get_snapshots sorts chronologically regardless
of query order; compute_agent_performance's buy/sell/hold replay, the
insufficient-cash skip, and the sell-capped-at-held-shares cases each
checked directly). The three new `/eval/*` routes verified with FastAPI's
TestClient: the trigger secret gates record-snapshot correctly (missing,
wrong, and correct cases), and both GET routes degrade cleanly (empty
history, insufficient backtest data) rather than 500ing.

## Bug: eval harness 404'd in production -- Dockerfile never shipped eval/ (2026-08-31)
After pushing the eval harness commit, GitHub Actions' auto-deploy reported
success and built a new image, but `/eval/performance` and `/eval/backtest`
both 404'd on the live API. `az containerapp revision list` showed the new
revision as Unhealthy with 0 replicas while an older revision kept serving
traffic -- so the build succeeded but the container crash-looped on start.

Root cause: `Dockerfile` only had `COPY src/ ./src/`. The `eval/` package
(tracker.py, backtest.py, baselines.py) lives at the repo root as a sibling
of `src/`, not inside it, so it was never copied into the image.
`src/api/main.py` imports it as `from eval import tracker as eval_tracker`
at module load time, so the missing package raised `ModuleNotFoundError`
on every startup attempt, before the app could ever pass its readiness
probe. The container app's ingress falling back to the last healthy
revision (pre-eval-harness code) is what made the symptom look like a
routing problem rather than a crash -- `/openapi.json` on the live URL kept
showing the old route list, which pointed straight at "the new code isn't
actually running" once compared against what GitHub Actions said it built.
Fixed with one line: `COPY eval/ ./eval/`, matching how `src/` is already
copied and how `eval/tracker.py` itself imports back into `src.*` (both
packages need to be siblings importable from `/app`, which uvicorn's
default working-directory-on-sys.path behavior makes possible).

Diagnostic path worth keeping: `az containerapp revision list -o table`
found the unhealthy revision; the GitHub Actions run's `conclusion` (via
the GitHub API, since `gh`/browser weren't in hand yet) confirmed the build
itself didn't fail, narrowing it to "runtime crash in a build Actions
thinks succeeded" rather than "deploy never ran" -- which is what pointed
at the Dockerfile's COPY list instead of, say, a missing secret.

### Frontend hardening from the same bug
`loadTrackRecord()` in the frontend was parsing `/eval/performance`'s
response body as ticker data without checking `res.ok` first. Against a
404 body of `{"detail": "Not Found"}`, `Object.keys(...)` on that returned
`["detail"]`, which the UI then rendered as if "detail" were a ticker
symbol -- a confusing phantom tab that obscured the real backend problem.
Fixed to check `res.ok` and show an honest status message instead, so a
future backend regression fails visibly rather than silently as fake data.

## Feature: drop the company-name field, resolve it server-side (2026-08-31)
UI feedback: typing both a ticker and its company name on every run was
redundant and made the nav-bar search bar cramped. Dropped `company_name`
from the frontend entirely -- the analyze bar is ticker-only now.

Not just a UI simplification, though: `company_name` is load-bearing, not
decorative -- `src/agents/news_analyst.py` forwards it verbatim into
`get_news_sentiment`, which becomes NewsAPI's `q` search parameter
(`src/tools/news.py`). A bare ticker like "AAPL" is a poor NewsAPI query
(noisy/irrelevant matches), so simply stopping sending it would have
quietly degraded the News & Sentiment analyst.

Fix: `AnalyzeRequest.company_name` is now `str | None = None`
(`src/api/main.py`), and when omitted the `/analyze` handler resolves it
server-side via a new `market_data.resolve_company_name(ticker)` (falls
back through yfinance's `longName` -> `shortName` -> the ticker itself,
and never raises -- a bad symbol just degrades to a ticker-based search
rather than a 500). Added as its own small function rather than reusing
`get_fundamentals` (which already reads the same `longName` field) because
`get_fundamentals` runs concurrently inside the fundamentals node -- the
news node can't read its result mid-graph, and resolving once in the
endpoint, before `_graph.ainvoke(...)`, keeps the parallel fan-out
structure untouched. `company_name` stays accepted (optional) for
backwards compatibility with `scripts/run_roundtable.py` and
`eval/tracker.py`'s fixed watchlist, which both still pass it explicitly.

Verified with a mocked yfinance client (present/missing `longName`,
missing everything, and a raised exception all resolve to a sane string)
and FastAPI's TestClient with the graph itself mocked out (ticker-only
request resolves and forwards the mocked name into graph state; a request
that explicitly supplies `company_name` skips the resolver entirely and
uses the caller's value unchanged).

### Ticker suggestions
The nav's ticker input also gained a native `<datalist>` (`#ticker-suggestions`),
populated from the user's own real Upstox holdings + positions once they're
logged in and connected (`populateTickerSuggestions()` in web/index_new.html,
called after `loadRealData()` succeeds). Kept to a plain `<datalist>` rather
than a hand-rolled dropdown -- free browser-native autocomplete, no extra
JS/CSS for something this small, and it degrades to nothing (no
suggestions, no error) for a logged-out or not-yet-connected visitor.

## Feature: Indian-stock news routing -- Marketaux + Economic Times RSS fallback (2026-08-31)
Prompted by a direct question: why does Track Record show AAPL/MSFT/NVDA
instead of the user's real (Indian, NSE-listed) holdings? Answer at the
time was that NewsAPI's free tier has thin, unconfirmed coverage of Indian
financial press, so evaluating the agents against Indian tickers would be
noisier and less trustworthy than the fixed US-large-cap watchlist. That's
a real constraint, not just an assumption -- confirmed by research before
building anything: NewsAPI.org's docs don't name a single Indian outlet as
an indexed source, and its free "Developer" tier is explicitly documented
as not for production use (24h-delayed articles, localhost-only CORS).

Rather than accept that ceiling, added a second, ticker-routed news path
so Indian stocks get real coverage too, without touching NewsAPI's
existing (working) path for everything else:

- **Detection**: `market_data.is_indian_ticker(ticker)` -- true for a
  ".NS"/".BO" suffix, yfinance's own NSE/BSE exchange-suffix convention.
  Chosen over "is it in the user's real Upstox holdings" so it also works
  for any Indian ticker someone types in, not just their current positions.
- **Primary source**: Marketaux (`src/tools/news.py::_get_marketaux_news`).
  Researched and confirmed (via its own docs and sample data) to carry
  real Indian-publisher coverage -- Economic Times, Business Standard,
  Times of India, LiveMint, Hindu BusinessLine all present, 12,584 Indian
  financial entities mapped. Free tier: 100 requests/day, workable for a
  personal-scale watchlist. Query params: `search` (company name),
  `countries=in`, `published_after` (Y-m-d), `api_token`.
- **Fallback**: Economic Times' free Markets RSS feed
  (`_get_et_rss_news`), no API key, always available. Not company-filtered
  by the publisher (it's a general Markets feed), so filtered client-side
  by a case-insensitive substring match against the company name in each
  entry's title/summary -- an unmatched company returns `[]`, not the
  unfiltered feed, because substituting unrelated market news would
  corrupt the sentiment signal rather than honestly report thin coverage
  (same principle as the "explicit no-position line" decision earlier in
  this doc).
- **Never raises**: a missing `MARKETAUX_API_KEY`, a Marketaux error, or
  an RSS parse failure all just fall through to the next source or an
  empty list -- one flaky data source degrades the News Analyst's
  confidence, it doesn't 500 the whole `/analyze` call.
- Considered and rejected for now: the unofficial NSE India API (real
  corporate announcements, but no official ToS, requires session-cookie
  handling NSE actively tries to block, and is a different kind of signal
  -- disclosures, not news coverage). Revisit only if the agent specifically
  needs authoritative filings a news search can't provide.

Threaded `ticker` through the previously company-name-only news path:
`get_news_sentiment` (MCP tool, `src/mcp_server/server.py`) gained an
optional `ticker` param; `news_analyst.analyze()` now takes `ticker` too
and includes it in the prompt so the tool-calling LLM actually passes it;
`graph.py`'s `_news_node` forwards `state.get("ticker")` -- the same
`ticker` already in `RoundtableState` for the fundamentals node, just not
previously read by the news branch.

### Frontend: suggestions get a correct suffix, not just a symbol
`web/index_new.html`'s ticker `<datalist>` (populated from the user's real
Upstox holdings) now suffixes holdings with ".NS" before suggesting them
-- Upstox's `trading_symbol` field is a bare NSE/BSE symbol ("PNB", not
"PNB.NS"), and without the suffix yfinance would silently resolve to the
wrong ticker (or nothing) for both fundamentals/price *and* this news
routing. Positions are suggested unsuffixed on purpose: unlike holdings,
a position can be a derivatives contract (e.g. "NIFTY24AUGFUT") and there's
no reliable field here to tell an equity intraday position from an F&O one,
so guessing a suffix that might be wrong was judged worse than leaving it
alone.

### Verification
`is_indian_ticker` (suffix match, case-insensitivity, non-Indian and
derivatives-symbol negatives); `get_news`'s routing (Indian ticker ->
`get_indian_news`, everything else -> the original NewsAPI path, unchanged
behavior when no ticker is passed at all); the full Marketaux ->
RSS-fallback chain (success skips RSS, empty result falls through, a
raised exception falls through, both failing returns `[]` without raising);
`_get_marketaux_news`'s exact request params and response shaping with a
mocked `requests.get`; `_get_et_rss_news`'s company-name filtering against
a mocked feed (matches by title *or* summary, a company with zero matches
returns `[]` rather than the unfiltered feed); and `_news_node` forwarding
`state["ticker"]` into `news_analyst.analyze()` (including the
no-ticker-in-state case, so this doesn't KeyError on older call sites).

### Follow-up: Marketaux's own relevance ranking wasn't enough (2026-08-31)
The routing above shipped, but real validation against a live Marketaux key
(not mocks) showed the "Indian, routed" results for "Punjab National Bank"
included articles that were not actually about PNB -- a bank-holiday
notice, an unrelated Reliance Communications legal story, a generic
home-loan-rates roundup. Two fixes, both driven by real returned data
rather than guessing at the API's behavior:

**1. Quote the `search` param for exact-phrase matching.** Marketaux
appears to OR-match an unquoted multi-word query word by word, so
`search=Punjab National Bank` matched any article containing the very
common word "bank". Wrapping the query in quotes (`search='"Punjab
National Bank"'`, per Marketaux's documented `""` operator) noticeably
improved results but didn't fully fix it.

**2. Filter by entity match_score, not API result order.** A diagnostic
script (`_debug_marketaux.py`, scratch, not committed) dumped the raw
`entities` array Marketaux returns per article. The evidence was
unambiguous: the one genuinely on-topic article had only 2 entities, with
PNB clearly highest (match_score 38.14 vs. 18.07). The two noisy hits that
survived the quoted search were both broad multi-stock roundups (20+
entities each) where PNB was present but not the top-scored company --
a "home loan rates" article scored PNB at 36.0 against Karur Vysya Bank's
47.98, and a 25-company margin bulletin scored PNB at 43.4 against
Crompton Greaves' 103.78. Marketaux's own relevance/result ordering did
not reflect this -- both noisy articles were returned as if relevant.

Added `_is_primary_entity(article, ticker)`: an article is kept only if
the target ticker is its single highest-match_score entity. Since this
filter runs client-side after the API call, `_get_marketaux_news` now
requests a larger raw pool (`min(page_size * 3, 25)`) before filtering
down to `page_size`, so the filter doesn't starve legitimate results down
to fewer than requested. `ticker` became a required parameter on
`_get_marketaux_news` and `get_indian_news` (previously `get_indian_news`
didn't take a ticker at all) -- `get_news`'s call site was updated to
forward it, which was also where a real bug was caught and fixed: the
first version of this signature change didn't actually pass `ticker`
through from `get_news`, which would have raised `TypeError` on every
Indian-routed call.

Verified with mocked unit tests in `tests/test_news.py`, using the real
match_score numbers above as fixtures (not synthetic ones) so the test
actually exercises the boundary the live data revealed: `_is_primary_entity`
true/false cases for all three real articles plus edge cases (no entities,
target absent); `_get_marketaux_news`'s quoted search param and raw-limit
sizing against a mocked response; the full Marketaux -> RSS fallback chain
with the new required `ticker` param; and `get_news`'s routing correctly
forwarding `ticker` into `get_indian_news` (the exact call that was
silently broken before this pass).

Re-validated live against the real Marketaux key after the fix: the same
"Punjab National Bank" query that previously returned 3 articles (1
relevant, 2 noisy) now returns exactly 1 -- a CBI court case tied to the
PNB scam, genuinely about the company, no roundup articles slipping
through.

## Feature: track record watchlist is now dynamic, not hardcoded (2026-08-31)
The original WATCHLIST (AAPL, MSFT, NVDA) was a fixed constant chosen
before real-portfolio integration existed. Once the user could actually
see their own Upstox holdings elsewhere in the app, a hardcoded watchlist
unrelated to those holdings stopped making sense -- the ask was
specifically "populate track record with our portfolio stocks, dont
hardcode it, if i buy something then we should see that as well."

Kept AAPL alone as `BASE_WATCHLIST` -- a fixed, always-tracked dummy
entry, not a portfolio holding. This still matters: the weekly snapshot
job (`.github/workflows/eval-snapshot.yml`, Monday 06:00 UTC) runs
unattended via a shared secret, with no interactive session, but the
Upstox access token expires daily at 3:30 AM IST and is only refreshed by
the user clicking through Upstox's login page (see
`upstox_auth_store.py`). Most Mondays the token will not be valid at
snapshot time. AAPL guarantees a continuous track record regardless;
real holdings are additive on top of it, appearing whenever a run happens
to land on a day the token is still fresh.

`eval/tracker._real_holdings_watchlist()` fetches
`upstox_account.get_real_holdings()` at snapshot time (long-term holdings
only, not short-term/derivatives positions -- those can be F&O contracts
that don't price the same way through yfinance), appends ".NS" to match
yfinance's convention (same suffixing the frontend's ticker-suggestions
datalist already does), and resolves a display name via
`market_data.resolve_company_name` (added earlier this session for the
same reason: Upstox's holdings response has no company-name field, only
`trading_symbol`). Capped at `MAX_REAL_HOLDINGS_TRACKED = 10` -- each
extra ticker costs a news-API call and an Azure OpenAI round trip per
agent, and an unbounded real portfolio could quietly exceed the shared
free-tier quota already budgeted tightly for a small fixed watchlist.

`/eval/performance` used to iterate the fixed `WATCHLIST` constant; it
now calls a new `eval_tracker.get_all_tracked_tickers()`, which scans
`EvalSnapshots` for every distinct PartitionKey ever recorded (Table
Storage has no cheap distinct-keys query at this scale, so a full scan is
fine for a handful of tickers). This means a ticker that was tracked once
and later drops out of the dynamic watchlist (a holding since sold, or
the original MSFT/NVDA entries from before this change) still shows its
existing history -- it just stops growing new snapshots. The original
single MSFT/NVDA snapshots recorded under the old fixed watchlist are
still sitting in the table as one-entry, permanently-flat tabs; left in
place rather than deleted, since deleting production table data wasn't
asked for and this session has no route to Azure Table Storage from here.

Verified with mocked unit tests in `tests/test_tracker.py`:
`_real_holdings_watchlist`'s not-connected/empty case, the ".NS"
suffixing and name resolution, blank-symbol skipping, and the
`MAX_REAL_HOLDINGS_TRACKED` cap; `record_snapshot` always including AAPL
whether or not Upstox is connected, and adding real-holdings tickers on
top when it is; and `get_all_tracked_tickers` returning distinct, sorted
tickers from a table scan with duplicate PartitionKeys.

### Also: info-icon popovers caused a horizontal scrollbar
Real in-browser testing (not just a visual check) showed that revealing
an info-icon popover -- next to "Track record" and "Historical rule
check" -- forced the whole page into horizontal scroll on a browser
window that wasn't maximized. The earlier display:none/block fix (see
the entry above) only addressed the *hidden* state's contribution to
scrollable overflow; once actually shown, the bubble's `left: 0`
anchoring relative to a mid-page icon plus a 300px width could still push
its right edge past the viewport on a narrower window. Fixed two ways:
centered the bubble under its icon (`left: 50%; transform:
translateX(-50%)`) instead of anchoring purely to the left, roughly
halving the odds of overflowing either edge; and added `overflow-x:
hidden` on `html`/`body` as a hard backstop, so nothing on the page --
this popover or otherwise -- can ever force the window itself to scroll
horizontally.

## Follow-up: dropped the AAPL fallback entirely, fixed a real popover-clipping bug (2026-08-31)
Kept from the entry above for exactly one day: seeing AAPL/MSFT/NVDA
(the original fixed watchlist, still sitting as one-snapshot rows from
before the dynamic-watchlist change) next to "Track record" read as
confusing noise once real portfolio tracking existed -- "why does my
track record show stocks I don't own." Removed `BASE_WATCHLIST` down to
an empty list; the watchlist is now 100% the user's real Upstox holdings,
with no fixed/dummy fallback of any kind. The accepted tradeoff, stated
plainly rather than papered over: the Upstox token expires daily and the
snapshot job runs unattended on a schedule, so a week where the token
isn't fresh at snapshot time now records nothing at all, for any ticker
-- there is no longer a guaranteed-continuous baseline series. The user
chose this explicitly, understanding the tradeoff (buying a real holding
specifically to seed the track record with something trackable).

### Also: the popover-positioning fix from the previous entry was wrong
Real in-browser testing again (a screenshot, not just a description) 
showed the info-icon popover getting half-clipped and unreadable -- text
cut off, left edge flush against the viewport with no margin. Root
cause: the previous fix centered the bubble under its icon via
`left: 50%; transform: translateX(-50%)`, but the icon sits near the
left edge of its row (right after a short heading), so centering pushed
the bubble's computed left edge into negative viewport coordinates --
territory that `overflow-x: hidden` on `body` then clipped, hiding
roughly the left half of the bubble instead of just preventing a
scrollbar. A pure CSS anchor cannot know where the icon actually sits
relative to the viewport; there's no CSS-only way to express "keep this
box's edges at least 12px from either side of the window" when the
anchor point can be anywhere.

Replaced the CSS `:hover`-driven show/hide with a small amount of JS
(`positionInfoBubble`/`hideInfoBubble`, wired to mouseenter/mouseleave/
focus/blur on each `.info-icon`): on show, it measures the icon's and
bubble's actual bounding rects and sets an explicit `left` (in pixels,
relative to the icon) clamped so the bubble's edges never come within
12px of either side of `window.innerWidth`. This is a page with exactly
two of these popovers, so the added JS is small and low-risk, and it's
the only correct fix for an anchor whose on-page position isn't fixed.
`overflow-x: hidden` on `html`/`body` stays in place as a defensive
backstop for anything else that might overflow, but the popover itself
no longer depends on it to avoid a scrollbar -- it's now guaranteed to
fit inside the viewport by construction.

Tests: `tests/test_tracker.py` updated -- `test_record_snapshot_records_
nothing_when_not_connected` (replaces the old always-includes-AAPL test)
asserts an empty watchlist run touches the graph zero times and records
nothing; `test_record_snapshot_records_only_real_holdings_when_connected`
asserts the recorded set is exactly the real holdings, no fixed entry
mixed in.

## Follow-up: replaced the info-icon popover with a modal (2026-08-31)
The measured-and-clamped JS positioner from the previous entry still
truncated the popover in real testing (a second screenshot). Rather than
chase a third positioning fix for a tooltip anchored to a 16px icon --
which will always have some edge case depending on where that icon lands
in a responsive layout -- switched to the pattern already used elsewhere
on this page: a centered modal. `track-record-help-modal` and
`historical-rule-help-modal` reuse the existing `.modal-overlay`/
`.modal-box`/`openModal`/`closeModal`/`wireModal` machinery (the same
one welcome-modal, track-record-info-modal, and architecture-modal use),
opened by a click on the info icon instead of hover/focus. A modal
centered in the viewport has no anchor-point edge case to get wrong --
it doesn't matter where the triggering icon sits in the layout. Removed
the popover CSS/JS entirely (`.info-bubble`, `positionInfoBubble`,
`hideInfoBubble`) rather than leaving dead code behind.

## Follow-up: weekly to daily snapshot cadence (2026-08-31)
Fair pushback: weekly was too coarse to mean anything from an investor's
point of view -- news genuinely moves prices day to day, and a single
data point a week can't reflect that. The original weekly cadence was
chosen before real-portfolio tracking existed, specifically to protect a
free-tier API quota against a fixed 3-ticker watchlist (see the
WATCHLIST entry from earlier today); now that the watchlist is real
holdings (capped at `MAX_REAL_HOLDINGS_TRACKED = 10`), the quota math for
daily still comfortably holds at personal-portfolio scale: NewsAPI and
Marketaux's free tiers are both 100 requests/day, so even at the full
10-ticker cap this uses at most 10 of that, shared with whatever public
demo traffic the site gets that day. Azure OpenAI cost scales the same
way (roughly 7x versus weekly, since it's 4 agent calls per ticker per
run) -- worth knowing, not a hard blocker at this portfolio size, but the
`MAX_REAL_HOLDINGS_TRACKED` cap is the lever to pull if either quota
starts running tight.

Genuine side benefit beyond the investor-relevance argument: the Upstox
token expires daily, so weekly cadence meant missing the single Monday
run skipped real-holdings tracking for a full week. Daily gives many more
chances to land on a day the token happens to be fresh.

Changed: `.github/workflows/eval-snapshot.yml`'s cron from
`0 6 * * 1` (Monday only) to `0 6 * * *` (every day, still 06:00 UTC /
11:30 AM IST), renamed the workflow to "Daily Eval Snapshot", and updated
every "weekly" reference in `eval/tracker.py`'s comments and
`web/index.html`'s copy to match. No code-logic changes were needed
beyond the cron expression and comments -- `record_snapshot()` and the
watchlist-discovery logic were already cadence-agnostic.

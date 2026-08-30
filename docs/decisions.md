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

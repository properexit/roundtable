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

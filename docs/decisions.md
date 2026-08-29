# Build log & decisions

Running log of what was decided and why, kept so the reasoning survives past
the code itself (and doubles as interview prep material).

## LLM backend
Decision pending — checking Azure OpenAI access on student subscription first.
If blocked/needs approval, falling back to Groq rather than waiting, since the
Azure story is already carried by AI Language (sentiment), Table Storage
(portfolio/audit), Key Vault (secrets), Container Apps (hosting), and App
Insights (tracing) — the LLM vendor is a swappable detail, not the point.

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

"""
FastAPI backend wrapping the Roundtable graph -- this is what the web
frontend calls. The graph (with its MemorySaver checkpointer) is built once
at process startup and reused across requests: the checkpointer keeps each
run's paused state in memory, keyed by thread_id, so /analyze and /approve
can be two separate HTTP requests that resume the same paused graph.

Note: MemorySaver is in-process only -- if this container restarts between
/analyze and /approve, the pending approval is lost. Fine for a portfolio
demo with a single instance; a real production version would swap in a
persistent checkpointer (e.g. backed by the same Table Storage used for
the portfolio). Documented here rather than silently glossed over.

Also serves a small set of /auth and /portfolio/real/* routes that read
your actual Upstox account (holdings/positions/funds, read-only) -- gated
behind the single-user session auth in src/api/auth.py. See docs/decisions.md
for why this exists as a separate, explicitly-gated surface rather than
just another open endpoint.
"""
from __future__ import annotations

import os
import secrets
import uuid
from urllib.parse import quote

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from langgraph.types import Command
from pydantic import BaseModel

from src.agents.graph import build_graph
from src.api import auth
from src.tools import market_data, portfolio, upstox_account, upstox_auth_store
from eval import tracker as eval_tracker
from eval.backtest import sma_crossover_backtest
from eval.baselines import all_baselines

app = FastAPI(title="Roundtable API", version="0.1.0")

# Tightened to the actual frontend origin via ALLOWED_ORIGINS env var in
# production -- see docs/decisions.md. Wide open here during local dev.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_graph = build_graph()

FRONTEND_URL = "https://daycandle.org"


class AnalyzeRequest(BaseModel):
    ticker: str
    # Optional: the frontend only sends a ticker now. When omitted, the
    # News & Sentiment analyst's search query is auto-resolved server-side
    # via yfinance (src/tools/market_data.resolve_company_name) rather than
    # relying on the user to type an exact company name. Still accepted
    # explicitly for backwards compatibility (scripts/run_roundtable.py,
    # eval/tracker.py's fixed watchlist both pass it directly).
    company_name: str | None = None


class ApproveRequest(BaseModel):
    thread_id: str
    approved: bool
    approved_by: str = ""


class LoginRequest(BaseModel):
    password: str


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/portfolio")
async def get_portfolio():
    return portfolio.get_state()


@app.post("/analyze")
async def analyze(req: AnalyzeRequest, authorization: str | None = Header(default=None)):
    """
    Public and unauthenticated by design -- this is the open demo. Real
    Upstox holdings only ever get folded into the analysis when the caller
    also sends a valid site session token (i.e. you're logged in when you
    click "Run Roundtable"); an anonymous visitor gets exactly the same
    analysis as before, with nothing about your real account touched.
    """
    real_position_context = None
    if authorization and authorization.startswith("Bearer "):
        token = authorization.removeprefix("Bearer ")
        if auth.verify_session_token(token):
            real_position_context = upstox_account.real_position_context_line(req.ticker.upper())

    company_name = req.company_name or market_data.resolve_company_name(req.ticker)

    thread_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}

    result = await _graph.ainvoke(
        {
            "ticker": req.ticker.upper(),
            "company_name": company_name,
            "real_position_context": real_position_context,
        },
        config=config,
    )

    if "__interrupt__" not in result:
        return {
            "status": "no_trade",
            "thread_id": thread_id,
            "fundamentals": result.get("fundamentals"),
            "news": result.get("news"),
            "action": result.get("final_action", "hold"),
            "rationale": result.get("final_rationale"),
            "used_real_context": real_position_context is not None,
        }

    payload = result["__interrupt__"][0].value
    return {
        "status": "pending_approval",
        "thread_id": thread_id,
        "fundamentals": result.get("fundamentals"),
        "news": result.get("news"),
        "risk": result.get("risk"),
        "recommendation": payload,
        "used_real_context": real_position_context is not None,
    }


@app.post("/approve")
async def approve(req: ApproveRequest):
    config = {"configurable": {"thread_id": req.thread_id}}
    try:
        result = await _graph.ainvoke(
            Command(resume={"approved": req.approved, "approved_by": req.approved_by}),
            config=config,
        )
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"No pending approval for thread_id {req.thread_id}: {e}")

    return {"execution_result": result.get("execution_result")}


# --- Site auth -------------------------------------------------------------


@app.post("/auth/login")
async def login(req: LoginRequest):
    if not auth.check_password(req.password):
        raise HTTPException(status_code=401, detail="Incorrect password")
    return {"token": auth.issue_session_token()}


# --- Upstox OAuth (real account, read-only) --------------------------------


@app.get("/auth/upstox/login")
async def upstox_login(session: str):
    """
    The browser navigates here directly as a full-page redirect, so the
    site session token can't travel as an Authorization header the way it
    does on every other protected route -- it comes as a query param
    instead, then gets threaded through Upstox's OAuth `state` param so
    /auth/upstox/callback can check it again on the way back.
    """
    if not auth.verify_session_token(session):
        raise HTTPException(status_code=401, detail="Invalid or expired session")

    client_id = os.environ["UPSTOX_CLIENT_ID"]
    redirect_uri = os.environ["UPSTOX_REDIRECT_URI"]
    dialog_url = (
        "https://api.upstox.com/v2/login/authorization/dialog"
        f"?response_type=code&client_id={quote(client_id)}"
        f"&redirect_uri={quote(redirect_uri, safe='')}"
        f"&state={quote(session)}"
    )
    return RedirectResponse(dialog_url)


@app.get("/auth/upstox/callback")
async def upstox_callback(code: str, state: str):
    """
    `state` round-trips the site session token that started this flow.
    Checking it here is standard OAuth CSRF protection (stops a stranger
    from hitting this callback directly and linking their own Upstox login
    to this service's stored token), and it reuses the same session check
    every other protected route uses.
    """
    if not auth.verify_session_token(state):
        raise HTTPException(status_code=401, detail="Invalid or expired session")

    upstox_auth_store.exchange_code_for_token(code)
    return RedirectResponse(f"{FRONTEND_URL}/?upstox=connected")


# --- Real account data (read-only, session-gated) ---------------------------


@app.get("/portfolio/real/holdings", dependencies=[Depends(auth.require_session)])
async def real_holdings():
    return upstox_account.get_real_holdings()


@app.get("/portfolio/real/positions", dependencies=[Depends(auth.require_session)])
async def real_positions():
    return upstox_account.get_real_positions()


@app.get("/portfolio/real/funds", dependencies=[Depends(auth.require_session)])
async def real_funds():
    return upstox_account.get_real_funds()


# --- Eval: forward-tracking + historical rule-based backtest ---------------


def _check_eval_secret(x_eval_secret: str | None) -> None:
    """
    Separate from the site-login auth on purpose: this isn't a person
    browsing the site, it's a scheduled job (see .github/workflows/
    eval-snapshot.yml) with no session to log into. A shared secret,
    constant-time compared, is the right amount of ceremony for "only my
    own scheduler should be able to trigger this" -- each call costs a
    real NewsAPI request and several Azure OpenAI calls, so it isn't left
    open to anyone who finds the URL.
    """
    expected = os.environ["EVAL_TRIGGER_SECRET"]
    if not x_eval_secret or not secrets.compare_digest(x_eval_secret, expected):
        raise HTTPException(status_code=401, detail="Invalid or missing eval trigger secret")


@app.post("/eval/record-snapshot")
async def eval_record_snapshot(x_eval_secret: str | None = Header(default=None)):
    _check_eval_secret(x_eval_secret)
    recorded = await eval_tracker.record_snapshot()
    return {"recorded": recorded}


@app.delete("/eval/snapshots/{ticker}")
async def eval_delete_snapshots(ticker: str, x_eval_secret: str | None = Header(default=None)):
    """
    Deletes every recorded snapshot for `ticker`. Secret-protected like
    /eval/record-snapshot -- destructive, not the public read-only
    surface. Exists to clear stale watchlist entries that no longer
    belong (e.g. AAPL/MSFT/NVDA, recorded before the watchlist became
    dynamic/real-holdings-only) -- the dynamic-watchlist logic only ever
    adds tickers, it has no way to prune an old one on its own.
    """
    _check_eval_secret(x_eval_secret)
    deleted = eval_tracker.delete_snapshots(ticker)
    return {"ticker": ticker.upper(), "deleted": deleted}


@app.get("/eval/performance")
async def eval_performance():
    """
    Public and read-only -- this is aggregate track-record data for the
    watchlist (what the system called, and how that's played out against
    buy-hold/hold-cash/sell-short), not anything account-specific.
    """
    out = {}
    for ticker in eval_tracker.get_all_tracked_tickers():
        snapshots = eval_tracker.get_snapshots(ticker)
        if not snapshots:
            out[ticker] = {"snapshot_count": 0}
            continue
        agent_perf = eval_tracker.compute_agent_performance(snapshots)
        baselines = all_baselines(snapshots[0]["price"], snapshots[-1]["price"])
        out[ticker] = {
            "snapshots": snapshots,
            "agent_performance": agent_perf,
            "baselines": baselines,
        }
    return out


@app.get("/eval/backtest")
async def eval_backtest(ticker: str, period: str = "2y", short_window: int = 20, long_window: int = 50):
    try:
        return sma_crossover_backtest(ticker.upper(), period, short_window, long_window)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

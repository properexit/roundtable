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
"""
from __future__ import annotations

import uuid

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from langgraph.types import Command
from pydantic import BaseModel

from src.agents.graph import build_graph
from src.tools import portfolio

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


class AnalyzeRequest(BaseModel):
    ticker: str
    company_name: str


class ApproveRequest(BaseModel):
    thread_id: str
    approved: bool
    approved_by: str = ""


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/portfolio")
async def get_portfolio():
    return portfolio.get_state()


@app.post("/analyze")
async def analyze(req: AnalyzeRequest):
    thread_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}

    result = await _graph.ainvoke(
        {"ticker": req.ticker.upper(), "company_name": req.company_name}, config=config
    )

    if "__interrupt__" not in result:
        return {
            "status": "no_trade",
            "thread_id": thread_id,
            "fundamentals": result.get("fundamentals"),
            "news": result.get("news"),
            "action": result.get("final_action", "hold"),
            "rationale": result.get("final_rationale"),
        }

    payload = result["__interrupt__"][0].value
    return {
        "status": "pending_approval",
        "thread_id": thread_id,
        "fundamentals": result.get("fundamentals"),
        "news": result.get("news"),
        "risk": result.get("risk"),
        "recommendation": payload,
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

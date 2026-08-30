"""
Storage for the Upstox OAuth access token this service uses to read your
real holdings/positions/funds.

Why a token store at all, and not a value baked in at deploy time: the
token comes from an interactive OAuth login (you clicking through Upstox's
own login page in a browser), not something we control. And it expires at
3:30 AM IST the day after it's issued, no matter when it was issued -- a
standard SEBI-driven policy for Indian brokers, not an Upstox quirk.
(Upstox does offer a separate long-lived "Analytics Token" that skips this
daily-expiry, but only for requests from a pre-registered static IP -- the
Container Apps Consumption plan this service runs on doesn't offer a
static outbound IP without upgrading to a paid Workload Profile + NAT
Gateway. Traded "click a login link once a day" for "don't take on new
paid infrastructure" -- see docs/decisions.md.)

Why Table Storage and not a local file: Container Apps instances are
ephemeral -- any restart or new revision wipes local disk. This table was
already provisioned (AZURE_STORAGE_CONNECTION_STRING has been sitting in
.env unused since the very first setup) and is exactly the right shape for
one small piece of durable state.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import requests
from azure.data.tables import TableServiceClient
from dotenv import load_dotenv

load_dotenv()

TABLE_NAME = "UpstoxToken"
PARTITION_KEY = "upstox"
ROW_KEY = "current"  # single user -- this row is just overwritten on every login

UPSTOX_TOKEN_URL = "https://api.upstox.com/v2/login/authorization/token"
IST = timezone(timedelta(hours=5, minutes=30))


def _table_client():
    conn_str = os.environ["AZURE_STORAGE_CONNECTION_STRING"]
    service = TableServiceClient.from_connection_string(conn_str)
    try:
        service.create_table(TABLE_NAME)
    except Exception:
        pass  # already exists -- fine, this runs on every call
    return service.get_table_client(TABLE_NAME)


def exchange_code_for_token(code: str) -> str:
    """
    Trades the one-time authorization code Upstox redirected back with
    (see /auth/upstox/callback in src/api/main.py) for an access token,
    stores it, and returns it.
    """
    resp = requests.post(
        UPSTOX_TOKEN_URL,
        headers={
            "accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={
            "code": code,
            "client_id": os.environ["UPSTOX_CLIENT_ID"],
            "client_secret": os.environ["UPSTOX_CLIENT_SECRET"],
            "redirect_uri": os.environ["UPSTOX_REDIRECT_URI"],
            "grant_type": "authorization_code",
        },
        timeout=15,
    )
    resp.raise_for_status()
    access_token = resp.json()["access_token"]
    _save_token(access_token)
    return access_token


def _save_token(access_token: str) -> None:
    table = _table_client()
    entity = {
        "PartitionKey": PARTITION_KEY,
        "RowKey": ROW_KEY,
        "access_token": access_token,
        "issued_date_ist": datetime.now(IST).date().isoformat(),
    }
    table.upsert_entity(entity)


def get_valid_token() -> str | None:
    """
    Returns the stored access token if it was issued today (IST); None
    otherwise. Being conservative and treating "not issued today" as
    expired -- rather than tracking the exact 3:30 AM cutoff -- is simpler
    and safer, at the cost of occasionally asking for a fresh login a
    little earlier than strictly necessary.
    """
    table = _table_client()
    try:
        entity = table.get_entity(PARTITION_KEY, ROW_KEY)
    except Exception:
        return None

    issued_date = entity.get("issued_date_ist")
    today = datetime.now(IST).date().isoformat()
    if issued_date != today:
        return None
    return entity.get("access_token")

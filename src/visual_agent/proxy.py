"""Synthetic target only. Runner code must never import this module."""

import argparse
import html
import secrets

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse

SCENARIOS = {
    "normal",
    "not_found",
    "validation",
    "permission",
    "slow",
    "notice",
    "session_expired",
    "unknown_dialog",
    "oversized_dialog",
    "app_error",
}

STYLE = """
*{box-sizing:border-box}body{margin:0;background:#dce2e7;color:#182c3d;font:18px Arial,sans-serif}
header{background:#18394c;color:#fff;padding:18px 28px;height:78px}header small{float:right;font-size:14px;margin-top:12px}
.layout{display:flex;height:calc(100vh - 78px)}nav{width:190px;padding:28px 18px;background:#becbd4;border-right:2px solid #7c909e;font-size:16px;line-height:2.4}
iframe{border:0;flex:1;background:#edf0f2}.page{padding:28px 36px}h1{font-size:28px;margin:0 0 22px;color:#17394c}
.panel{background:#fff;border:2px solid #718897;padding:24px;max-width:890px}table{width:100%;border-collapse:collapse}
td,th{padding:14px 12px;text-align:left;border-bottom:1px solid #c8d2d9}th{background:#dce5eb;font-size:16px}
label{display:inline-block;width:240px;font-weight:bold}input{width:310px;height:42px;border:2px solid #456274;font:20px Arial;padding:5px 9px;background:white}
.row{margin:18px 0}button{background:#24546b;color:white;border:2px solid #173f53;padding:12px 24px;font:18px Arial;cursor:pointer;margin:10px 12px 0 0}
.subtle{background:#5f707b}.danger{background:#8a3434;border-color:#722929}.hint{font-size:15px;color:#516875;margin-top:22px}
.overlay{position:fixed;inset:0;background:#101d2cb0;display:flex;align-items:center;justify-content:center}.dialog{padding:30px;background:#fff;border:3px solid #c1963d;width:640px;box-shadow:0 10px 35px #0005}
.error{border-left:6px solid #a83c3c;padding:18px;background:#fff1ef}.badge{font-size:13px;letter-spacing:1px;color:#476172;margin-bottom:10px}
@media (max-width:700px){header{height:58px;padding:17px 20px}header small,nav{display:none}.layout{height:calc(100vh - 58px)}.page{padding:20px}.panel{padding:18px}label{display:block;width:auto;margin-bottom:7px}input{width:min(100%,310px)}td,th{padding:10px 8px}.dialog{width:min(94vw,640px);padding:20px}}
"""


def create_app(scenario="normal"):
    if scenario not in SCENARIOS:
        raise ValueError("Unknown scenario")
    app = FastAPI(docs_url=None, redoc_url=None)
    sessions = {}

    def state(req):
        key = req.cookies.get("proxy_session")
        return sessions.get(key)

    def page(title, content, overlay=""):
        return HTMLResponse(
            f'<!doctype html><html><head><style>{STYLE}</style></head><body><main class="page"><div class="badge">MEMBER SERVICES / V1 / SYNTHETIC DATA</div><h1>{title}</h1><section class="panel">{content}</section></main>{overlay}</body></html>'
        )

    def form(action, fields, label):
        return f'<form method="get" action="{action}">{fields}<button>{label}</button></form>'

    def field(label, name, value=""):
        return f'<div class="row"><label>{label}</label><input name="{name}" value="{html.escape(value, quote=True)}" autocomplete="off"></div>'

    @app.get("/", response_class=HTMLResponse)
    async def root():
        key = secrets.token_urlsafe(24)
        sessions[key] = {"stage": "search", "resolved": False}
        response = HTMLResponse(
            f'<!doctype html><html><head><title>Member Services — Local Training</title><style>{STYLE}</style></head><body><header><strong>MEMBER SERVICES</strong><small>LOCAL TRAINING • FICTIONAL RECORDS ONLY</small></header><div class="layout"><nav>Operations<br>Member lookup<br>Account servicing<br>Statement requests<hr>Operator: DEMO</nav><iframe title="Work area" src="/screen"></iframe></div></body></html>'
        )
        response.set_cookie("proxy_session", key, httponly=True, samesite="strict")
        return response

    @app.get("/screen")
    async def screen(req: Request):
        s = state(req)
        if s is None:
            return page("Session expired", "Reload the application entry point.")
        stage = s["stage"]
        if stage == "search":
            return page(
                "Member Search",
                form("/search", field("Member ID", "member_id"), "Search")
                + '<p class="hint">Enter a five digit member identifier. Training records: 10001, 10002.</p>',
            )
        if stage == "not_found":
            return page("Search Result", '<div class="error">Member not found</div>')
        if stage == "permission":
            return page("Access Restricted", '<div class="error">Permission denied</div>')
        if stage == "app_error":
            return page("System Message", '<div class="error">Application unavailable</div>')
        if stage == "detail":
            overlay = ""
            if not s["resolved"] and scenario in {
                "notice",
                "session_expired",
                "unknown_dialog",
                "oversized_dialog",
            }:
                title, action = {
                    "notice": ("Service notice", "Dismiss notice"),
                    "session_expired": ("Session expired", "Restore training session"),
                    "unknown_dialog": (
                        "Supervisor acknowledgement",
                        "Acknowledge and continue",
                    ),
                    "oversized_dialog": (
                        "Supervisor acknowledgement",
                        "Acknowledge and continue",
                    ),
                }[scenario]
                extra = ' style="width:96vw;height:88vh"' if scenario == "oversized_dialog" else ""
                overlay = f'<div class="overlay"><div class="dialog"{extra}><h1>{title}</h1><p>Training intervention. No credentials are required.</p>{form("/resolve", "", action)}</div></div>'
            return page(
                "Member Details",
                f"<table><tr><th>Member</th><th>Status</th></tr><tr><td>{s['member_id']}</td><td>Active training member</td></tr></table>"
                + form("/account", "", "Open savings"),
                overlay,
            )
        if stage == "account":
            return page(
                "Savings Account",
                f"<table><tr><th>Account</th><th>Type</th></tr><tr><td>****{s['member_id'][-4:]}</td><td>Savings</td></tr></table>"
                + form("/request", "", "Prepare statement"),
            )
        if stage == "request":
            return page(
                "Statement Request",
                form(
                    "/review",
                    field("Statement month", "statement_month")
                    + field("Delivery method", "delivery_method"),
                    "Review request",
                )
                + '<p class="hint">Month: YYYY-MM. Delivery: postal or electronic.</p>',
            )
        if stage == "validation":
            return page(
                "Statement Request",
                '<div class="error">Validation rejected</div><p>Choose a supported statement month and delivery method.</p>',
            )
        if stage == "review":
            rows = [
                ("Masked account", "****" + s["member_id"][-4:]),
                ("Statement month", s["statement_month"]),
                ("Delivery method", s["delivery_method"]),
            ]
            return page(
                "Statement Review",
                "<table>"
                + "".join(f"<tr><th>{k}</th><td>{html.escape(v)}</td></tr>" for k, v in rows)
                + '</table><p>Review ready. No request has been submitted.</p><form action="/submit" method="post"><button class="danger">Submit request</button></form>',
            )
        return page("System Message", "Application unavailable")

    def advance(req, stage):
        s = state(req)
        if s is not None:
            s["stage"] = stage
        return RedirectResponse("/screen", status_code=303)

    @app.get("/search")
    async def search(req: Request, member_id: str = ""):
        s = state(req)
        if s is None:
            return advance(req, "search")
        s["member_id"] = member_id
        stage = (
            "not_found"
            if scenario == "not_found" or member_id not in {"10001", "10002"}
            else "detail"
        )
        if scenario in {"permission", "app_error"}:
            stage = scenario
        s["stage"] = stage
        if scenario == "slow":
            return page(
                "Loading",
                '<p>Loading member record</p><script>setTimeout(()=>location.replace("/screen"),3500)</script>',
            )
        return RedirectResponse("/screen", 303)

    @app.get("/account")
    async def account(req: Request):
        return advance(req, "account")

    @app.get("/request")
    async def request_form(req: Request):
        return advance(req, "request")

    @app.get("/resolve")
    async def resolve(req: Request):
        if scenario == "notice":
            import asyncio

            await asyncio.sleep(0.5)
        if state(req):
            state(req)["resolved"] = True
        return RedirectResponse("/screen", 303)

    @app.get("/review")
    async def review(req: Request, statement_month: str = "", delivery_method: str = ""):
        s = state(req)
        if s:
            s.update(statement_month=statement_month, delivery_method=delivery_method)
        import re

        valid = re.fullmatch(r"2026-(0[1-9]|1[0-2])", statement_month) and delivery_method in {
            "postal",
            "electronic",
        }
        return advance(req, "validation" if scenario == "validation" or not valid else "review")

    @app.post("/submit")
    async def submit():
        return page("Training only", "Submission is not implemented.")

    return app


def main():
    import uvicorn

    p = argparse.ArgumentParser()
    p.add_argument("--scenario", choices=sorted(SCENARIOS), default="normal")
    p.add_argument("--port", type=int, default=8765)
    a = p.parse_args()
    uvicorn.run(create_app(a.scenario), host="127.0.0.1", port=a.port, access_log=False)


if __name__ == "__main__":
    main()

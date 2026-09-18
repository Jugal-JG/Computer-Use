import secrets

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, Response

from .policy import PolicyDenied

HTML = """<!doctype html><html><head><title>Visual Agent · Operator</title><style>
body{font:16px Arial;background:#101e2a;color:#e6edf2;margin:24px}h1{font-size:24px}button,input{font:16px Arial;padding:10px;margin:5px}button{cursor:pointer;background:#d5ebdc;border:0;border-radius:4px}#screen{width:100%;height:auto;border:2px solid #6b8597;cursor:crosshair}#status{white-space:pre-wrap;padding:14px;background:#243a4b}p{max-width:1000px;color:#c1d0dc}</style></head>
<body><h1>Visual capability · Human intervention</h1><p>This panel forwards input to the same live browser session. Take control before clicking the image. Typing replaces the selected field. Resume verifies the paused checkpoint. Final submission remains blocked.</p><div id="status"></div>
<button onclick="send('take',{})">Take control</button><button onclick="send('resume',{})">Resume automation</button><button onclick="send('abort',{})">Abort run</button>
<input id="entry" type="password" placeholder="Replacement field value" autocomplete="off"><button onclick="send('action',{kind:'type_text',text:document.querySelector('#entry').value});document.querySelector('#entry').value=''">Type into selected field</button>
<p id="message"></p><img id="screen" alt="Live target screenshot"><script>
const token=location.hash.slice(1);let state={},busy=false;
async function call(path,opts={}){opts.headers={...(opts.headers||{}),'Authorization':'Bearer '+token};let r=await fetch(path,opts);if(!r.ok)throw Error((await r.json()).detail);return r}
async function refresh(){if(busy)return;busy=true;let next;try{state=await(await call('/state')).json();document.querySelector('#status').textContent=JSON.stringify(state,null,2);let b=await(await call('/screen')).blob();let im=document.querySelector('#screen'),old=im.src;next=URL.createObjectURL(b);let ready=new Image();ready.src=next;await ready.decode();im.src=next;next=null;if(old.startsWith('blob:'))URL.revokeObjectURL(old)}catch(e){document.querySelector('#message').textContent=e.message}finally{if(next)URL.revokeObjectURL(next);busy=false}}
async function send(path,data){try{await call('/'+path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...data,generation:state.generation})});document.querySelector('#message').textContent='Accepted';await refresh()}catch(e){document.querySelector('#message').textContent=e.message;await refresh()}}
document.querySelector('#screen').onclick=e=>{let r=e.target.getBoundingClientRect();send('action',{kind:'click',x:(e.clientX-r.left)*e.target.naturalWidth/r.width,y:(e.clientY-r.top)*e.target.naturalHeight/r.height})};
document.querySelector('#screen').onwheel=e=>{e.preventDefault();send('action',{kind:'scroll',delta:e.deltaY})};setInterval(refresh,1500);refresh();
</script></body></html>"""


def create_operator(run):
    app = FastAPI(docs_url=None, redoc_url=None)
    token = secrets.token_urlsafe(32)

    def auth(req):
        if req.headers.get("authorization") != "Bearer " + token:
            raise HTTPException(401, "Unauthorized")

    @app.get("/")
    async def index():
        return HTMLResponse(HTML)

    @app.get("/state")
    async def state(req: Request):
        auth(req)
        return {
            "run_id": run.id,
            "session_id": run.surface.session_id,
            "state": run.state,
            "owner": run.owner,
            "generation": run.generation,
            "step": run.step,
            "reason": run.reason,
            "expected": run.expected,
        }

    @app.get("/screen")
    async def screen(req: Request):
        auth(req)
        async with run.lock:
            png = await run.surface.observe(max_age=0.75)
        return Response(png, media_type="image/png", headers={"Cache-Control": "no-store"})

    @app.post("/{operation}")
    async def operation(operation: str, req: Request):
        auth(req)
        data = await req.json()
        try:
            if operation == "take":
                await run.take_control(data["generation"])
            elif operation == "resume":
                await run.resume(data["generation"])
            elif operation == "action":
                await run.human_action(data)
            elif operation == "abort":
                async with run.lock:
                    if data["generation"] != run.generation or run.state not in {
                        "WAITING_FOR_HUMAN",
                        "HUMAN_CONTROL",
                    }:
                        raise PolicyDenied("STALE_CONTROL")
                    run.abort = True
                    run.owner = "none"
                    run.generation += 1
                    run.resume_event.set()
                    run.evidence.event("operator_aborted")
            else:
                raise HTTPException(404, "Unknown operation")
        except (PolicyDenied, KeyError, ValueError) as exc:
            raise HTTPException(409, str(exc))
        return {"ok": True}

    return app, token

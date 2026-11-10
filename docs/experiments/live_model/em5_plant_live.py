import sys, json, os
from pathlib import Path
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, "scripts")  # run from the league directory with PYTHONPATH=src
import challenge_live as cl
from hoshitori import deployment, scheduler, model_client, arm_l1
from hoshitori import snapshot as _snap
from hoshitori.snapshot import Reader
RUNS=int(sys.argv[1]); CYCLE="cyzqwmvt7wzt654dizalk4h5ssep"
settings=deployment.load_settings(Path("SETTINGS.json"))
l1=cl.the_arm(settings)
with deployment.assemble(settings) as running:
    at=cl.locate(scheduler.read_all(running.services), CYCLE, "ETH", 24, "ryo")
    snap=at.frozen
    target=[i for i,_ in snap.items if i.evidence_id.startswith("ev54iy66")][0]
    new=tuple((k, ("3165.56" if k=="market.price_usd" else v)) for k,v in target.fields)
    items=tuple((replace(i, fields=new) if i.evidence_id==target.evidence_id else i, d) for i,d in snap.items)
    planted=replace(snap, items=items)
    fetcher=model_client.GatewayFetcher(running.services.env.runner, running.captures, running.services.env.clock, running.model_provider or "")
    client=model_client.ModelClient(fetcher)
    def ask(which, i):
        s=snap if which=="control" else planted
        cfg=replace(l1, seed=5000+i)
        d=arm_l1.decide(client, Reader(s,_snap.Access.INDEPENDENT), at.candidate, "ETH", 24*3_600_000, snap.snapshot_at_ms, cfg)
        return {"verdict":str(d.verdict),"p":None if d.p is None else str(d.p),"reason":None if d.reason is None else str(d.reason)}
    jobs=[(w,i) for w in ("control","planted") for i in range(RUNS)]
    with ThreadPoolExecutor(4) as ex: res=list(ex.map(lambda j: (j,ask(*j)), jobs))
out={"control":[r for (w,i),r in res if w=="control"],"planted":[r for (w,i),r in res if w=="planted"]}
Path("plant_live.json").write_text(json.dumps(out,indent=1))
for k,v in out.items(): print(k,[(x["verdict"].split(".")[-1], x["p"]) for x in v])

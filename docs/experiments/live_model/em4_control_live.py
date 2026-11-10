import sys, json, os
from pathlib import Path
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
AL=None  # set to the league directory
sys.path.insert(0, "scripts")
import challenge_live as cl
from hoshitori import deployment, scheduler, model_client, arm_l1, model_challenge, model_challenge_file
PAIRS=int(sys.argv[1]); CYCLE="cyzqwmvt7wzt654dizalk4h5ssep"; SYM="ETH"; H=24
settings=deployment.load_settings(Path("SETTINGS.json"))
l1=cl.the_arm(settings)
seeds=model_challenge.seeds(l1.seed, PAIRS)
with deployment.assemble(settings) as running:
    at=cl.locate(scheduler.read_all(running.services), CYCLE, SYM, H, "ryo")
    snap=at.frozen
    shared_ryo=tuple(sorted(i.evidence_id for i,_ in snap.items if i.source=="ryo" and i.evidence_id in snap.shared))
    print("shared ryo items:", len(shared_ryo), file=sys.stderr)
    at=replace(at, removed=shared_ryo)
    fetcher=model_client.GatewayFetcher(running.services.env.runner, running.captures, running.services.env.clock, running.model_provider or "")
    ask=cl.asker(model_client.ModelClient(fetcher), at, l1, seeds, SYM, H)
    done={}
    def lookup(_r,i,arm):
        if (arm,i) not in done:
            if (arm,i)==(model_challenge.WITHHELD,0): done[(arm,i)]=ask(arm,i)
            else:
                rest=[j for j in seeds if j not in done]
                with ThreadPoolExecutor(4) as pool: done.update(zip(rest, pool.map(lambda j: ask(*j), rest), strict=True))
        return done[(arm,i)]
    result=model_challenge.challenge(at.frozen, at.store, at.removed, lookup, at.original, PAIRS)
    data=model_challenge_file.record(cl.asked_of(at,l1,CYCLE,SYM,H,"ryo-shared",PAIRS), result, done)
Path("placebo_shared_ryo.json").write_bytes(data)
print(json.dumps(cl.summary(model_challenge_file.read(data)), indent=1))

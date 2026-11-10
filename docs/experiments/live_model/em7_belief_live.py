import json, os, sys, re, time, urllib.request, threading
from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor
# run with PYTHONPATH set to the league's src directory and LLM_API_KEY in the environment
from hoshitori import belief_revision as br
URL = "https://integrate.api.nvidia.com/v1/chat/completions"
KEY = os.environ["LLM_API_KEY"]
MODEL = "nvidia/nemotron-3.5-super-vl-120b-a12b"
RUNS = int(sys.argv[1])
calls = 0; fails = 0; lock = threading.Lock()
def render(cards):
    if not cards: return "No evidence has been collected."
    lines = []
    by = {c.card_id: c for c in cards}
    for c in cards:
        stance = {1: "points to a rise", -1: "points to a fall", 0: "neither a rise nor a fall"}[c.stance]
        q = {"PRIMARY": "primary source", "SECONDARY": "secondary source", "LOW": "low-quality source"}[c.quality]
        t = f"- [{c.card_id}] from {c.origin} ({q}): {stance}."
        if c.retracts: t += f" It withdraws item {c.retracts}."
        lines.append(t)
    return "\n".join(lines)
cache = {}
def answer(cards, run):
    global calls, fails
    key = (cards, run)
    prompt = ("You are a careful market researcher. Below is the evidence collected about whether one token's price will be higher in 24 hours. "
              "Weigh sources by quality, count repeats from one origin once, and ignore withdrawn items.\n\nEvidence:\n" + render(cards) +
              "\n\nReply with only a number between 0.02 and 0.98: your probability that the price is higher in 24 hours.")
    body = json.dumps({"model": MODEL, "messages": [{"role": "user", "content": prompt}], "max_tokens": 200, "temperature": 0.7, "thinking": False}).encode()
    for attempt in range(3):
        try:
            req = urllib.request.Request(URL, body, {"Authorization": "Bearer " + KEY, "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=120) as r:
                txt = json.loads(r.read())["choices"][0]["message"]["content"]
            with lock: calls += 1
            m = re.search(r"0?\.\d+|1\.0|0|1", txt)
            v = Decimal(m.group(0)); v = min(max(v, br.FLOOR), br.CEILING)
            return v
        except Exception as e:
            with lock: fails += 1
            time.sleep(2)
    return Decimal("0.5")
# memoise per (cards, run) so identical asks inside evaluate are asked once each
memo = {}
def memo_answer(cards, run):
    k = (cards, run)
    if k not in memo: memo[k] = answer(cards, run)
    return memo[k]
# pre-fill in parallel: every ask evaluate will make
jobs = []
for case in br.CASES:
    for i in range(RUNS):
        jobs += [(case.base, i), (case.base + case.update, i), (case.base, i + RUNS)]
uniq = list(dict.fromkeys(jobs))
with ThreadPoolExecutor(4) as ex: res = list(ex.map(lambda j: (j, answer(*j)), uniq))
memo.update(dict(res))
ev = br.evaluate(memo_answer, RUNS)
out = {"runs_per_case": ev.runs_per_case, "spearman": ev.correlation,
       "model_calls_ok": calls, "model_call_failures": fails, "distinct_asks": len(uniq),
       "classes": [{"class": str(r.update_class), "total": r.runs, "revised": r.revised, "right_way": r.right_way, "under": r.under, "over": r.over, "labels": list(r.labels), "consistency": r.consistency, "verdict": r.verdict} for r in ev.classes]}
print(json.dumps(out, indent=1, default=str))

import json, os, subprocess, sys, time, glob, shutil
HERE = os.path.dirname(os.path.abspath(__file__))
PY = os.environ["E15_PYTHON"]  # a Python with inspect_ai installed, in its own environment
logs = os.path.join(os.environ.get("CLAUDE_JOB_DIR", "/tmp") + "/tmp", "e15logs"); shutil.rmtree(logs, ignore_errors=True); os.makedirs(logs)
env = {"PATH": "/usr/bin:/bin", "HOME": os.environ["HOME"], "INSPECT_LOG_DIR": logs, "INSPECT_DISPLAY": "none"}
t = time.time()
p = subprocess.run([PY, "-I", "-m", "inspect_ai", "eval", "abstention_task.py", "--model", "mockllm/model", "--log-dir", logs, "--no-fail-on-error", "--display", "none"],
                   cwd=HERE, env=env, capture_output=True, text=True)
out = {"returncode": p.returncode, "seconds": round(time.time() - t, 1), "stdout_tail": p.stdout[-600:], "stderr_tail": p.stderr[-600:]}
files = glob.glob(logs + "/*.eval") + glob.glob(logs + "/*.json")
out["log_files"] = [os.path.basename(f) for f in files]
code = f'''
import json, glob
from inspect_ai.log import read_eval_log
f = sorted(glob.glob("{logs}/*.eval") + glob.glob("{logs}/*.json"))[-1]
log = read_eval_log(f)
r = {{"status": log.status, "n_samples": len(log.samples or []), "results_total": log.results.total_samples if log.results else None,
     "completed": log.results.completed_samples if log.results else None,
     "scores": {{s.name: {{k: v.value for k, v in s.metrics.items()}} for s in (log.results.scores if log.results else [])}},
     "coverage_from_log": (log.results.completed_samples / log.results.total_samples) if log.results else None,
     "errors": [ (s.id, (s.error.message[:80] if s.error else None)) for s in (log.samples or []) if s.error ],
     "sample_metadata_kept": (log.samples[0].scores["action_match"].metadata if log.samples and log.samples[0].scores else None),
     "eval_has_task_args_and_model": [log.eval.task, log.eval.model]}}
print(json.dumps(r))
'''
q = subprocess.run([PY, "-I", "-c", code], env=env, capture_output=True, text=True)
out["log_read"] = json.loads(q.stdout.strip().splitlines()[-1]) if q.stdout.strip() else {"error": q.stderr[-500:]}
json.dump(out, open(HERE + "/E15_results.json", "w"), indent=1); print(json.dumps(out, indent=1))

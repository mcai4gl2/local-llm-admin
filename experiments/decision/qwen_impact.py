import json, os, threading, time
import bench
os.environ["DJ_UNIQUE"] = "1"
def hammer(stop):
    n = 0
    while not stop.is_set():
        body = {"state": dict(bench.STATE, ticket=f"[{time.time_ns()}] " + bench.STATE["ticket"]),
                "model": "openjev-latest", "questions": bench.questions(3)}
        bench.post("/v1/systemone", body); n += 1
    print(json.dumps({"sidecar_reads_during": n}))
o = {}; bench.qwen_long(30000, 20, o); print(json.dumps({"test": "qwen-30k-sidecar-idle", **o}))
for label, load in (("decode-sidecar-idle", False), ("decode-sidecar-busy", True)):
    stop = threading.Event(); th = threading.Thread(target=hammer, args=(stop,))
    if load: th.start(); time.sleep(1)
    r = [bench.qwen_decode_rate(512) for _ in range(3)]
    if load: stop.set(); th.join()
    print(json.dumps({"test": label, "runs": r}))
stop = threading.Event(); th = threading.Thread(target=hammer, args=(stop,)); th.start(); time.sleep(1)
o = {}; bench.qwen_long(30000, 20, o); stop.set(); th.join(); print(json.dumps({"test": "qwen-30k-sidecar-busy", **o}))
print("DONE")

import json, threading, time
import bench
def paced(rate, stop, lat):
    nxt = time.perf_counter()
    while not stop.is_set():
        body = {"state": dict(bench.STATE, ticket=f"[{time.time_ns()}] " + bench.STATE["ticket"]),
                "model": "openjev-latest", "questions": bench.questions(3), "samples": 1}
        _c, _r, _h, dt = bench.post("/v1/systemone", body); lat.append(dt * 1000)
        nxt += 1 / rate; time.sleep(max(0, nxt - time.perf_counter()))
for rate in (0.5, 1.0, 2.0):
    stop, lat = threading.Event(), []
    th = threading.Thread(target=paced, args=(rate, stop, lat)); th.start(); time.sleep(1)
    r = [bench.qwen_decode_rate(512) for _ in range(2)]
    stop.set(); th.join()
    lat.sort()
    print(json.dumps({"rate_per_s": rate, "reads": len(lat), "read_p50_ms": round(lat[len(lat)//2]), "read_max_ms": round(lat[-1]),
                      "qwen_wall_s": [x["wall_s"] for x in r]}))
print("DONE")

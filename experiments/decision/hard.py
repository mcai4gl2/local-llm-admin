import json, statistics, bench
HARD = [
 ("Plot a histogram of these sales numbers with matplotlib.", "code"),
 ("Draw the graph of y = x^2 - 4 and mark its roots.", "math"),
 ("Write a Python script that generates fractal images.", "code"),
 ("How many ways can I arrange 5 books on a shelf? Write the code too.", "math"),
 ("Make a meme picture of a cat saying 'deploy on Friday'.", "image"),
 ("What's a good name for my Kubernetes cluster? Something fun.", "chat"),
 ("Compute the SHA-256 of 'hello' and explain how hashing works.", "code"),
 ("Describe what a sunset over Mount Fuji looks like.", "chat"),
 ("Calculate my monthly mortgage payment: 500k at 4% over 30 years.", "math"),
 ("Translate this regex into plain English: ^\\d{3}-\\d{4}$", "code"),
 ("Sketch a diagram of a neural network with two hidden layers.", "image"),
 ("Is 2^31 - 1 prime? Just yes or no and why.", "math"),
 ("Write a haiku about a segmentation fault.", "chat"),
 ("Convert this CSV to JSON: name,age\\nBob,4", "code"),
 ("Give me an illustration for my blog post about gardening.", "image"),
 ("What's 15% tip on $86.40?", "math"),
]
q = {"route": {"type": "choice", "instructions": "Which backend should handle this user request?",
     "criteria": {"code": "Programming, debugging, shell, SQL", "image": "Generate or draw an image",
                  "math": "Calculations, equations, proofs, probability",
                  "chat": "General conversation, writing, translation, advice"}}}
rows = []
for p, want in HARD:
    _c, r, _h, _dt = bench.post("/v1/systemone", {"state": {"request": p}, "model": "openjev-latest", "questions": q})
    a = r["answers"]["route"]; rows.append((want, a["choice"], round(a["confidence"], 3), p[:50]))
ok = [c for w, g, c, _ in rows if w == g]; bad = [c for w, g, c, _ in rows if w != g]
print(json.dumps({"accuracy": f"{len(ok)}/{len(rows)}", "conf_right_mean": round(statistics.mean(ok), 3) if ok else None,
                  "conf_wrong_mean": round(statistics.mean(bad), 3) if bad else None}))
for row in rows: print(row)

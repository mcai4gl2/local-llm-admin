# Hypotheses

A: Exit between ps and lsof makes _exe_matches return false; _member raises even if
the process is now absent. stop catches nothing and leaves splash.json. Confirm by
injecting a disappearing native after the controller receives TERM in real stop.

B: Other process/listener inspection failure. Live redaction prevents disproof; keep
unknown/error refusals and do not label the exact live failure proven.

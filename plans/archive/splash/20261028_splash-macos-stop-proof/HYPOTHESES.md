# Hypotheses

1. Process exits between ps fields and executable inspection, including truncated zombie
   output before arguments exist. Confirm by exact exception/field capture; do not infer.
2. Transient child/listener identity changes during controller termination. Confirm with
   recorded generation and successive ps/lsof observations.
3. Stop unlink or command failure unrelated to the fixed executable race. Confirm traceback.

The leading explanation remains unproven until real exception evidence is captured.


Confirmed: Mac P_WEXIT (?E) outlives executable mappings. The executable mismatch's
second process sample was still present and equally exiting; the earlier absence-only
race fix therefore correctly refused it, leaving receipt cleanup unfinished. The new
bounded wait retains independent absence proof and lets this expected teardown phase
settle instead of immediately failing. Stuck/reused/denied cases still refuse.

# Reproduction

Base: c25078c plus the staged Phase3 readiness correction. Inject native exit between
process sampling and executable lookup after controller TERM inside standalone stop.
Run uv run pytest -q tests/test_splash_host_failures.py -k disappearing.
The exact redacted live exception remains unknown; this establishes a faithful race
boundary and receipt leak, not a claim that every live stop failure has this cause.

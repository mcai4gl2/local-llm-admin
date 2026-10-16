#!/bin/zsh
# Strata Phase 0: record the server's endpoints as test fixtures, scrubbed for the public repo.
# usage: capture.sh <sandbox-dir> [base-url]      (STRATA_API_KEY from the environment)
# Writes <sandbox>/results/fixtures/*.json|txt. Paths under the sandbox and the home folder
# become /Volumes/Models/strata and /Users/you; IPv4 addresses other than 127.0.0.1 become
# 192.0.2.1 (a dotted version such as 0.1.44.1-metal is kept). Check the files by eye
# before copying them into the repository.
set -eu
SANDBOX=${1:A}
BASE=${2:-http://127.0.0.1:8086}
OUT=$SANDBOX/results/fixtures
mkdir -p $OUT
auth=(-H "Authorization: Bearer ${STRATA_API_KEY:-}")

scrub() {
  /usr/bin/perl -pe "s#\Q${SANDBOX}\E#/Volumes/Models/strata#g; s#/Users/[^/\"' ]+#/Users/you#g;" \
    -e 's/\b(?!127\.0\.0\.1\b)\d{1,3}(?:\.\d{1,3}){3}\b(?![-.\w])/192.0.2.1/g'
}

get() {  # name path [accept]
  local -a accept=()
  [[ -n ${3:-} ]] && accept=(-H "Accept: $3")
  curl -fsS --max-time 10 "${auth[@]}" "${accept[@]}" "$BASE$2" | scrub >$OUT/$1 \
    || print -u2 "capture: $2 failed"
}

get health.json /health
get models.json /v1/models
get props.json /props
get status.json /status
get slots.json /slots
get metrics.json /metrics
get metrics.prom "/metrics?format=prometheus" "text/plain"
log=$SANDBOX/logs/strata.log
[[ -f $log ]] && tail -n 200 $log | scrub >$OUT/strata.log
ls -l $OUT

#!/usr/bin/env bash
# Score finished demo sessions with a one-off ("past traces") monitor on each agent.
#
# Why: each chat session is ONE trace (support-agent/session_trace.py), and a continuous monitor picks traces by their
# start time. A session that is still going when the monitor runs is scored with only the turns it had at that moment.
# Run this after a rehearsal, once the sessions are done, to score every turn of every session in the window.
#
#   ./deploy/rescore-sessions.sh            # sessions that started in the last 60 minutes
#   MINUTES=20 ./deploy/rescore-sessions.sh
set -uo pipefail
cd "$(dirname "$0")"
ORG="${ORG:-default}"; PROJECT="${PROJECT:-support-demo}"; ENVIRONMENT="${ENVIRONMENT:-default}"
PROVIDER="${PROVIDER:-shared-openai}"; MINUTES="${MINUTES:-60}"
GOVERNED="${GOVERNED:-support-agent}"; UNGOVERNED="${UNGOVERNED:-support-agent-ungoverned}"
command -v amctl >/dev/null && command -v jq >/dev/null || { echo "needs amctl and jq"; exit 1; }

iso() { date -u -d "@$1" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -r "$1" +%Y-%m-%dT%H:%M:%SZ; }
NOW=$(date +%s); START="$(iso $((NOW - MINUTES * 60)))"; END="$(iso "$NOW")"
STAMP="$(date -u +%H%M%S)"
echo "scoring sessions that started between $START and $END"
for a in "$UNGOVERNED" "$GOVERNED"; do
  name="refund-rescore-$STAMP"
  jq -nc --arg n "$name" --arg env "$ENVIRONMENT" --arg p "$PROVIDER" --arg s "$START" --arg e "$END" '{
    name:$n, displayName:("Refund quality, sessions " + $s[11:16] + "-" + $e[11:16] + " UTC"), environmentName:$env,
    type:"past", traceStart:$s, traceEnd:$e, samplingRate:1,
    evaluators:[
      {identifier:"refund-policy-compliance", displayName:"Refund policy compliance", config:{auto_refund_limit:100}},
      {identifier:"groundedness", displayName:"Groundedness"},
      {identifier:"tone", displayName:"Tone", config:{context:"customer support chat about orders and refunds"}},
      {identifier:"instruction_following", displayName:"Instruction Following"}],
    llmProvider:{providerName:$p}}' \
    | amctl api "/orgs/$ORG/projects/$PROJECT/agents/$a/monitors" -X POST --input - >/dev/null \
    && echo "  $a: monitor '$name' created, it runs once now (Console: $a > Evaluation)" \
    || echo "  $a: could not create the monitor (see the error above)"
done

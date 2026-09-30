#!/usr/bin/env bash
# Build the Salesforce MCP server and the console, load them into the Agent Manager k3d cluster and deploy them.
set -euo pipefail
cd "$(dirname "$0")/.."

CLUSTER="${CLUSTER:-amp-local}"

echo "==> Building images"
docker build -t salesforce-mcp:demo salesforce-mcp
docker build -t governance-console:demo governance-console

echo "==> Loading images into k3d cluster '$CLUSTER'"
k3d image import salesforce-mcp:demo governance-console:demo -c "$CLUSTER"

echo "==> Applying manifests"
kubectl apply -f deploy/k8s/salesforce-mcp.yaml -f deploy/k8s/governance-console.yaml
kubectl -n sales-demo rollout status deploy/salesforce-mcp --timeout=120s
kubectl -n sales-demo rollout status deploy/governance-console --timeout=120s

cat <<MSG

Done. In-cluster MCP URL (use it for the MCP proxy and the ungoverned agent):
  http://salesforce-mcp.sales-demo.svc.cluster.local:8080/mcp

To open the demo on this machine, keep these two running:
  kubectl -n sales-demo port-forward svc/salesforce-mcp 8090:8080 &
  kubectl -n sales-demo port-forward svc/governance-console 3000:80 &
Then browse to http://localhost:3000
MSG

#!/usr/bin/env bash
# Build the Salesforce MCP server and the console, load them into the Agent Manager k3d cluster and deploy them.
set -euo pipefail
cd "$(dirname "$0")/.."

CLUSTER="${CLUSTER:-amp-local}"
NS=sales-demo

echo "==> Building images"
docker build -t salesforce-mcp:demo salesforce-mcp
docker build -t governance-console:demo governance-console

echo "==> Loading images into k3d cluster '$CLUSTER'"
k3d image import salesforce-mcp:demo governance-console:demo -c "$CLUSTER"

echo "==> Namespace and API keys"
kubectl create namespace "$NS" --dry-run=client -o yaml | kubectl apply -f - >/dev/null
if kubectl -n "$NS" get secret salesforce-mcp-keys >/dev/null 2>&1; then
  echo "keys already exist, keeping them"
else
  # direct = shared key the ungoverned agent holds. gateway = credential the MCP proxy attaches upstream.
  kubectl -n "$NS" create secret generic salesforce-mcp-keys \
    --from-literal=SF_API_KEYS="direct=$(openssl rand -hex 16),gateway=$(openssl rand -hex 16)" >/dev/null
  echo "generated random keys"
fi

echo "==> Applying manifests"
kubectl apply -f deploy/k8s/salesforce-mcp.yaml -f deploy/k8s/governance-console.yaml
kubectl -n "$NS" rollout restart deploy/salesforce-mcp deploy/governance-console >/dev/null
kubectl -n "$NS" rollout status deploy/salesforce-mcp --timeout=120s
kubectl -n "$NS" rollout status deploy/governance-console --timeout=120s

cat <<MSG

Done.
  Ungoverned agent reaches MCP in-cluster: http://salesforce-mcp.$NS.svc.cluster.local:8080/mcp

Agent Manager only accepts a PUBLIC upstream URL for an MCP proxy, so publish the MCP port next:
  ./deploy/expose-mcp.sh          (keep it running, it also opens the dashboard's audit port on localhost:8090)
Then open the demo UI:
  kubectl -n $NS port-forward svc/governance-console 3000:80 &
  open http://localhost:3000
MSG

#!/usr/bin/env bash
# Build the Orders & Payments MCP server and the console, load them into the Agent Manager k3d cluster and deploy them.
set -euo pipefail
cd "$(dirname "$0")/.."

CLUSTER="${CLUSTER:-amp-local}"
NS=support-demo

# kubectl must reach the cluster before anything is built. A cluster that was recreated keeps its name but gets a new
# certificate authority, and the old kubeconfig then fails with "x509: certificate signed by unknown authority".
if ! KERR="$(kubectl --context "k3d-$CLUSTER" get namespace default -o name 2>&1)"; then
  echo "kubectl cannot reach the k3d cluster '$CLUSTER': $KERR"
  case "$KERR" in
    *x509*|*certificate*|*"context was not found"*|*"no context exists"*)
      echo "The kubeconfig entry for '$CLUSTER' is stale or missing (the cluster was probably recreated). Refresh it with:"
      echo "  k3d kubeconfig merge $CLUSTER --kubeconfig-merge-default --kubeconfig-switch-context"
      echo "then check: kubectl get nodes" ;;
    *) echo "Check: k3d cluster list, and kubectl config current-context (should be k3d-$CLUSTER)." ;;
  esac
  exit 1
fi
kubectl config use-context "k3d-$CLUSTER" >/dev/null

echo "==> Building images"
docker build -t commerce-mcp:demo commerce-mcp
docker build -t governance-console:demo governance-console

echo "==> Loading images into k3d cluster '$CLUSTER'"
k3d image import commerce-mcp:demo governance-console:demo -c "$CLUSTER"

echo "==> Namespace and API keys"
kubectl create namespace "$NS" --dry-run=client -o yaml | kubectl apply -f - >/dev/null
if kubectl -n "$NS" get secret commerce-mcp-keys >/dev/null 2>&1; then
  echo "keys already exist, keeping them"
else
  # direct = shared key the ungoverned agent holds. gateway = credential the MCP proxy attaches upstream.
  kubectl -n "$NS" create secret generic commerce-mcp-keys \
    --from-literal=COMMERCE_API_KEYS="direct=$(openssl rand -hex 16),gateway=$(openssl rand -hex 16)" >/dev/null
  echo "generated random keys"
fi

echo "==> Applying manifests"
kubectl apply -f deploy/k8s/commerce-mcp.yaml -f deploy/k8s/governance-console.yaml
kubectl -n "$NS" rollout restart deploy/commerce-mcp deploy/governance-console >/dev/null
kubectl -n "$NS" rollout status deploy/commerce-mcp --timeout=120s
kubectl -n "$NS" rollout status deploy/governance-console --timeout=120s

cat <<MSG

Done. (In-cluster URL, only if you set MCP_DIRECT_URL yourself: http://commerce-mcp.$NS.svc.cluster.local:8080/mcp)

Agent Manager only accepts a PUBLIC upstream URL for an MCP proxy, so publish the MCP port next:
  ./deploy/expose-mcp.sh          (keep it running: it also serves the demo UI on localhost:3000 and the audit feed on localhost:8090)
MSG

# MLflow on Kubernetes (Sonador SSO)
Production-style deployment of the MLflow tracking server with Sonador Data Service mediated authentication. Kubernetes counterpart to `compose/mlflow-tracking.yaml`; the SSO integration itself is documented in `docker/mlflow/README.md`.

Components:

| File | Contents |
| --- | --- |
| `mlflow-05.database.yaml` | CloudNativePG `Cluster` (backend store) + superuser secret |
| `mlflow-secrets.yaml` | All environment configuration (DSN, object storage, Sonador, session keys) |
| `mlflow-10.deployment.yaml` | MLflow server `Deployment` + `Service` (port 5000) |
| `mlflow-20.ingress.yaml` | NGINX `Ingress` with cert-manager TLS |

The tracked files are samples with placeholder hosts and credentials. Copy each to a `*.local.yaml` (gitignored via `k8s/**/*.local.yaml`), fill in real values, and apply the local copies.

## Prerequisites

* [CloudNativePG operator](https://cloudnative-pg.io/) (provides `postgresql.cnpg.io/v1`)
* NGINX ingress controller and cert-manager. The ingress references a **namespace-scoped** `Issuer` named `letsencrypt-prod` — a fresh `sonador-ai` namespace will not have one. Either replicate an existing Issuer from another namespace:

  ```bash
  kubectl -n imaging get issuer letsencrypt-prod -o yaml \
    | sed 's/namespace: imaging/namespace: sonador-ai/' | kubectl apply -f -
  ```

  or switch the ingress annotation to `cert-manager.io/cluster-issuer` if the cluster provides a `ClusterIssuer`.
* A GCS bucket for artifacts with an [HMAC interoperability key pair](https://cloud.google.com/storage/docs/interoperability) (Settings → Interoperability). The deployment uses `s3://mlflow-experiments` — change the `--default-artifact-root` argument if the bucket is named differently.
* The `oaktreetech/mlflow` image (2026-08 or later), which ships the `sonador-auth` app

## Sonador Data Service
Create and configure the data service as described in `docker/mlflow/README.md`:

1. `python3 manage.py data-service create --service mlflow ... --set-acl-allow-staff --set-active` (or via the Admin Panel, which assigns a random service ID — that ID becomes `SONADOR_SERVICE_CLIENT_ID`)
2. Enable "Allow OIDC Auth" on the service
3. Register the callback URL **exactly** as it will be sent: `https://<mlflow-host>/oauth-authorized/sonador`

Because TLS terminates at the ingress, the app cannot derive the `https://` callback from the proxied request — `SONADOR_SERVICE_REDIRECT_URL` in the secret pins it explicitly and must match the registered value byte-for-byte.

## Deploy

```bash
kubectl create namespace sonador-ai   # if it does not already exist

cp mlflow-05.database.yaml   mlflow-05.database.local.yaml
cp mlflow-secrets.yaml       mlflow-secrets.local.yaml
cp mlflow-10.deployment.yaml mlflow-10.deployment.local.yaml
cp mlflow-20.ingress.yaml    mlflow-20.ingress.local.yaml
# edit the .local.yaml files: hostnames, passwords, HMAC keys, Sonador token/service id,
# and the ingress controller ClusterIP for the hostAliases entry (see below)

kubectl apply -f mlflow-05.database.local.yaml
kubectl wait --for=condition=Ready cluster/mlflow-postgres -n sonador-ai --timeout=300s

kubectl apply -f mlflow-secrets.local.yaml
kubectl apply -f mlflow-10.deployment.local.yaml
kubectl apply -f mlflow-20.ingress.local.yaml
```

First boot runs Alembic migrations against the fresh database; the startup probe allows ~3 minutes.

## In-Cluster Connectivity to Sonador
When MLflow runs in the same cluster as Sonador, the public Sonador hostname is often not reachable from inside pods (LoadBalancer hairpin), and GKE's kube-dns cannot rewrite external names the way a CoreDNS rule could. The deployment solves this with a `hostAliases` entry that points the public hostname (e.g. `imaging.gke.oak-tree.tech`) at the **ClusterIP of the ingress-nginx controller service**:

```bash
kubectl -n ingress-nginx get svc ingress-nginx-controller -o jsonpath='{.spec.clusterIP}'
```

Why this construct:

* Requests keep the public hostname, so SNI/Host are correct, the ingress serves the real certificate, TLS terminates at the controller, and certificate validation passes (`SONADOR_VERIFY_SSL=true`).
* `SONADOR_URL` is then identical inside and outside the cluster, so the browser-facing OIDC redirect flow needs no internal/external split (`SONADOR_PUBLIC_URL` stays unset).
* An `ExternalName` service cannot do this for HTTPS: it only mints a new cluster-local DNS name, so the client would present the wrong SNI and land on the ingress default backend with a certificate mismatch.
* Target the controller **data-path** service, not `ingress-nginx-controller-admission` (that service fronts the validating admission webhook, not proxied traffic).

The ClusterIP is stable for the life of the Service but must be re-checked if the ingress controller is ever reinstalled.

**Alternative (no fixed IP):** set `SONADOR_URL=http://sonador-ha.imaging:8070` (direct service, plain HTTP inside the cluster) and `SONADOR_PUBLIC_URL=https://<public-sonador-host>`. The auth plugin rewrites the browser-facing authorize redirect to the public URL while server-side calls stay internal — SSO remains intact. Use this if pinning the controller IP is undesirable; the trade-off is unencrypted in-cluster API traffic and a split URL configuration.

## Verify

* `https://<mlflow-host>/health` returns 200 without authentication
* `https://<mlflow-host>/` redirects an unauthenticated browser into the Sonador login and back to the MLflow dashboard
* API access with a Sonador token:

  ```bash
  export MLFLOW_TRACKING_URI="https://<mlflow-host>"
  export MLFLOW_TRACKING_TOKEN="<sonador-api-token>"
  python3 -c "import mlflow; print(mlflow.search_experiments())"
  ```

## Notes

* **Password encoding**: reserved characters in the PostgreSQL password must be percent-encoded in `MLFLOW_BACKEND_STORE_URI` (`@` → `%40`), but appear literally in the `mlflow-postgres-superuser` secret.
* **Scaling**: the server is stateless (PostgreSQL + object storage + signed session cookies); `replicas` may be raised without further changes since all replicas share `MLFLOW_FLASK_SERVER_SECRET_KEY`.
* **Workers and memory**: `mlflow server` defaults to 4 uvicorn workers, each a full MLflow+genai import (~500MB RSS). The deployment pins `--workers 2` with a 3Gi limit; scale both together.
* **CNPG endpoints**: the deployment targets `mlflow-postgres-rw`; `-ro`/`-r` services exist for read-only consumers.
* **Body size**: `proxy-body-size: 1024m` bounds artifact upload size through the ingress.

## Troubleshooting

**Pod crashloops with clean logs (healthy startup, then the log just stops).** OOM kill — no traceback and no graceful-shutdown lines is the signature. Confirm with `kubectl describe pod` (`Last State: Terminated, Reason: OOMKilled, Exit Code: 137`). Reduce `--workers` or raise the memory limit; see the sizing note above.

**503 from the ingress while the pod is Ready.** The controller is either ignoring the Ingress or cannot reach the pod:
1. *Ingress ignored (also blocks certificate issuance)* — older nginx controllers only watch the `kubernetes.io/ingress.class` annotation, not `spec.ingressClassName`. The manifest sets both; verify the annotation survived any edits. Discriminate with `curl -vk https://<host>/health`: the controller "Fake Certificate" plus `default backend - 404` means the Ingress is not being served.
2. *No endpoints* — `kubectl -n sonador-ai get endpoints mlflow` must list the pod IP.
3. *Blocked path* — controller log (`kubectl -n ingress-nginx logs deploy/ingress-nginx-controller | grep mlflow`) distinguishes `"no endpoints available"` from `connect() failed` (the latter with endpoints present usually means a NetworkPolicy is not admitting the `ingress-nginx` namespace).

**Certificate never issues.** Usually one of: the namespace-scoped Issuer is missing (see Prerequisites), or the Ingress itself is not being served so the HTTP-01 challenge can never complete (fix the ingress-class problem first). Inspect with `kubectl -n sonador-ai describe certificate mlflow-tls` and `kubectl -n sonador-ai get order,challenge`.

**`InsecureRequestWarning` in the pod log.** `SONADOR_VERIFY_SSL` is not active in the running pod. `envFrom` snapshots the secret at container start: re-apply `mlflow-secrets.local.yaml`, then `kubectl -n sonador-ai rollout restart deployment/mlflow`.

**SSO redirect loops or `Invalid OpenID connect state`.** Verify `SONADOR_SERVICE_REDIRECT_URL` matches the registered data service callback byte-for-byte, and that the browser reaches MLflow over the same hostname the redirect URL declares.

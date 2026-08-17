# Sonador Airflow on Kubernetes

A production-shaped Airflow 3 deployment with Sonador single sign-on, Celery workers, a
RabbitMQ broker, and the TotalSegmentator AI pipeline. This is the Kubernetes counterpart to
`compose/airflow-ai.yaml` and `compose/message-broker.yaml`.

**These manifests are a reference environment, not a deployable configuration.** Like the
files under `compose/`, every hostname is `example.com` and every credential is a published
development placeholder. Copy the directory, replace the marked values, and deploy from
somewhere outside this repository. Anything still reading `REPLACE-` will fail loudly rather
than quietly doing the wrong thing.

There is no Helm chart. These are plain manifests, applied in order, with the reasoning kept
next to the YAML.

| | |
|---|---|
| Image | `oaktreetech/sonador-airflow.ai` (Airflow 3.1.5 / Python 3.10) |
| Executor | `CeleryExecutor` |
| Namespace | `airflow-ai` — created and managed outside this set |
| Metadata DB | External PostgreSQL; the sample targets a CloudNativePG cluster at `airflow-postgres-rw:5432` |
| Broker | RabbitMQ 4.1, single node, in-namespace, PVC-backed |
| Task logs | S3-compatible object storage (MinIO in the development environment) |
| DAG delivery | ConfigMap generated from `data/airflow/dags/` |
| Ingress | Traefik + cert-manager |
| Auth | Sonador OpenID Connect via a Sonador data service |

**Contents** — [Landscape](#landscape) · [Container image](#the-container-image) ·
[Airflow configuration](#airflow-configuration) · [The manifests](#the-manifests) ·
[gen-configmaps.py](#gen-configmapspy) · [Adapting this](#adapting-this-for-a-real-deployment) ·
[Apply](#apply) · [Verifying](#verifying) · [Image defects](#known-image-defects-worked-around-here) ·
[Differences from compose](#deliberate-differences-from-the-compose-stack) ·
[Troubleshooting](#troubleshooting)

## Landscape

Nine workloads. The API server is the only one exposed publicly.

```
                    Traefik ingress ── https://airflow.example.com
                            │
                    ┌───────▼────────┐         ┌──────────────────────┐
                    │  api-server ×2 │  OIDC   │  Sonador             │
                    │  UI, REST API, │◄───────►│  imaging.example.com │
                    │  /auth callback│         │  data service        │
                    └───────┬────────┘         └──────────────────────┘
                            │ execution API
      ┌─────────────┬───────┴───────┬──────────────┐
      │             │               │              │
┌─────▼─────┐ ┌─────▼──────┐ ┌──────▼─────┐ ┌──────▼──────┐
│ scheduler │ │ dag-       │ │ triggerer  │ │ flower      │
│           │ │ processor  │ │            │ │ (port-fwd)  │
└─────┬─────┘ └─────┬──────┘ └──────┬─────┘ └──────┬──────┘
      │             │               │              │
      │      ┌──────▼───────────────▼──────────────▼─────┐
      │      │  PostgreSQL (metadata, external)          │
      │      └───────────────────────────────────────────┘
      │
      │ queue      ┌──────────────┐        ┌──────────────────┐
      └───────────►│ RabbitMQ     │◄───────┤  worker ×2       │
                   │ (StatefulSet)│        │  (StatefulSet)   │
                   └──────────────┘        └────────┬─────────┘
                                                    │ task logs, AI artifacts
                                           ┌────────▼─────────┐
                                           │ object storage   │
                                           │ (S3 / MinIO)     │
                                           └──────────────────┘
```

Four relationships that are not obvious from the diagram:

* **Every component talks to Sonador at startup**, not just the API server. The auth manager
  (`sonador_auth`) is imported whenever the Airflow CLI starts, and it calls Sonador before
  doing any work. An unreachable Sonador or a bad API token is a deployment-wide crash loop,
  and it makes every `airflow` invocation several seconds slower — which is why the exec
  probes here carry explicit, generous `timeoutSeconds`.
* **Remote logging is not optional under CeleryExecutor.** Worker pods are ephemeral, so the
  API server cannot read task logs off a worker's local disk once it has been rescheduled.
  Object storage is what makes logs outlive the pod that wrote them.
* **Workers are a StatefulSet**, so each gets its own PVC for the TotalSegmentator model
  cache (several GB, otherwise re-downloaded on every restart). This avoids requiring
  ReadWriteMany storage, at the cost of one copy of the weights per worker.
* **The database is external.** Nothing here provisions PostgreSQL, so deleting the whole
  namespace loses no history.

## The container image

All Airflow components run one image, `oaktreetech/sonador-airflow.ai`, built in three layers
from `docker/airflow/`. Knowing what is in each layer explains most of the manifest choices.

| Layer | Dockerfile | Adds |
|---|---|---|
| `apache/airflow:3.1.5-python3.10` | upstream | Airflow 3 core, FAB auth manager, Celery and Amazon providers |
| `oaktreetech/sonador-airflow` | `Dockerfile` | `requirements/etl.txt` — `sonador`, `sonadoretl`, `client`, `boto3`, `s3fs`, `pandas`, `openpyxl`, `requests[_ntlm]`, `PyYAML`; plus the Sonador auth and hook modules |
| `oaktreetech/sonador-airflow.ai` | `Dockerfile.sonador-ai` | `requirements/ai.txt` — `sonador3d`, `pyvista`, `meshio`, `pymeshfix`; offscreen-rendering system libraries (`libgl1`, `libxrender`, `xvfb`); `airflow-ai-sdk[openai]`; and a **separate TotalSegmentator virtualenv** |

### TotalSegmentator

[TotalSegmentator](https://github.com/wasserth/TotalSegmentator) performs semantic
segmentation of CT and MR volumes. It is installed into its own virtualenv at
`/home/airflow/env/totalsegmentator`, **not** alongside Airflow, because its pinned
`torch`/nnU-Net dependency tree conflicts with Airflow's. That venv gets
`totalsegmentator==2.12.0`, `pydicom`, `dicom2nifti==2.5.1`, `fury`, plus its own copies of
`sonador` and `sonadoretl` so it can publish results directly.

The `Sonador-TotalSegmentator` DAG therefore does not import TotalSegmentator. It shells into
that interpreter with a `BashOperator`:

```
/home/airflow/env/totalsegmentator/bin/python3 \
/home/airflow/env/totalsegmentator/bin/airflow-totalsegmentator.execute.py ...
```

The pipeline runs in three phases, over five tasks:

| Phase | Tasks | What happens |
|---|---|---|
| Preparation | `verify-env` → `prepare-seg-data` | Pull the DICOM series from Sonador/Orthanc, convert to NIfTI, stage to object storage |
| Segmentation | `map-inference-options` → `execute` | One dynamically-mapped `execute` task per requested model/ROI set, each running inference in the TotalSegmentator venv |
| Mesh + encode | `m3d-series` | Labelmaps → STL → M3D meshes, DICOM-encoded and uploaded back to Sonador |

What this means for the deployment:

* **Model weights** are downloaded on first inference into `~/.totalsegmentator`, which is why
  `airflow-24.worker.yaml` gives each worker a 20 Gi `volumeClaimTemplate`. Without it every
  pod restart re-downloads several GB.
* **`ai_inference_pool`** is declared by the DAG (`pool='ai_inference_pool'`, `pool_slots=1`)
  and bounds how many inference tasks run at once. `airflow-10.init.yaml` creates it; without
  it those tasks queue forever with no error surfaced in the UI.
* **CPU by default.** Inference works on CPU but is slow. GPU scheduling is a commented
  `nvidia.com/gpu` limit and node selector in the worker manifest, and needs the NVIDIA
  device plugin — see `docs/nvida-runtime.md`.
* **Object storage is on the data path**, not just for logs: the intermediate NIfTI and STL
  artifacts move between phases through the bucket in `AIRFLOW_CONN_S3_DEFAULT`.
* **The runner script is mounted, not baked** — see [image defects](#known-image-defects-worked-around-here).

The `io` DAGs (`SonadorExample01-HelloWorld`, `SonadorExample02-Index`) use only the base
layer and are the right thing to trigger first when validating a deployment.

## Airflow configuration

Configuration is split by sensitivity, not by topic. `airflow-config.yaml` holds everything
publishable; `airflow-secrets.yaml` holds everything else. Both are consumed wholesale:

```yaml
envFrom:
- configMapRef: {name: airflow-config}
- secretRef:    {name: airflow-secrets}
```

Every Secret key is named exactly as the environment variable it becomes — that is what makes
a single `secretRef` work, so renaming a key means updating whatever reads it. Component
overrides (worker concurrency, the init job's auth manager) use an explicit `env:` entry,
which takes precedence over `envFrom`.

### airflow-config.yaml

| Group | Settings | Notes |
|---|---|---|
| Core | `EXECUTOR`, `LOAD_EXAMPLES`, `DAGS_ARE_PAUSED_AT_CREATION`, `EXECUTION_API_SERVER_URL` | Workers and scheduler call back into the API server; the trailing `/execution/` is required |
| Sonador SSO | `AUTH_MANAGER`, `FAB__CONFIG_FILE` | Set on *every* component, not just the web tier — the auth manager loads during CLI startup |
| API / proxy | `API__BASE_URL`, `API__EXPOSE_CONFIG`, `FAB__ENABLE_PROXY_FIX`, `FAB__COOKIE_SECURE` | `ENABLE_PROXY_FIX` is what makes the OIDC `redirect_uri` come out as `https://` behind the ingress; `BASE_URL` does **not** drive it |
| Remote logging | `REMOTE_LOGGING`, `REMOTE_BASE_LOG_FOLDER`, `REMOTE_LOG_CONN_ID`, `ENCRYPT_S3_LOGS` | Credentials come from `AIRFLOW_CONN_S3_DEFAULT`; the bucket must already exist |
| Celery | `WORKER_CONCURRENCY` | Broker URL and result backend carry credentials, so they live in the Secret |
| Scheduling | `HOSTNAME_CALLABLE`, `DAG_DIR_LIST_INTERVAL`, `DAG_PROCESSOR__REFRESH_INTERVAL` | `hostname_callable` is pinned to `socket.gethostname`; Airflow's `getfqdn` default breaks probes on StatefulSet pods |
| Image workaround | `PYTHONPATH` | Makes the Sonador libraries importable — see [image defects](#known-image-defects-worked-around-here) |
| Sonador API | `SONADOR_URL`, `SONADOR_SERVICE_CLIENT_ID`, `SONADOR_SERVICE_OPENID_SCOPE` | The service id is what `manage.py data-service create --service airflow` produced; admin-panel services get a random id |
| Connection discovery | `S3_CONNECTIONS`, `S3_DEFAULT_CONN`, `AIRFLOW_VAR_SONADOR_CONNECTIONS`, `AIRFLOW_VAR_SONADOR_DEFAULT_CONN` | Populates the AI DAG's connection dropdowns without a database read |

That last group is worth expanding. Airflow 3 forbids database access during DAG parsing, so
the Sonador hooks fall back to configuration instead of querying the connection table. The two
hooks are asymmetric — that is in the hook source, not a mistake here:

* `object_storage_hook.available_connections()` reads the **environment variable**
  `S3_CONNECTIONS`
* `sonador_hook.available_connections()` reads the **Airflow Variable** `SONADOR_CONNECTIONS`,
  which is what `AIRFLOW_VAR_SONADOR_CONNECTIONS` sets

### airflow-secrets.yaml

| Group | Keys | Notes |
|---|---|---|
| Metadata database | `AIRFLOW__DATABASE__SQL_ALCHEMY_CONN`, `AIRFLOW__CELERY__RESULT_BACKEND` | Same database, different URL prefixes. **Percent-encode reserved characters in the password** — a literal `@` parses as part of the host and fails as a DNS error, not an auth error |
| Broker | `AIRFLOW__CELERY__BROKER_URL` | Must match the RabbitMQ credentials below; trailing `/` selects the default vhost |
| Sonador | `SONADOR_APITOKEN`, `AIRFLOW_CONN_SONADOR` | The token authenticates the platform; the connection is what the AI DAG reads as `conn[params.conn_id]` |
| Object storage | `AIRFLOW_CONN_S3_DEFAULT` | Backs both remote logging and AI artifacts. Note the extra key is `endpoint_url`, not the `host` shown in the older UI recipe |
| Instance secrets | `AIRFLOW__CORE__FERNET_KEY`, `AIRFLOW__API__SECRET_KEY`, `AIRFLOW__API_AUTH__JWT_SECRET` | Must be byte-identical across components; the JWT secret is how workers authenticate to the execution API |
| Broker credentials | `RABBITMQ_DEFAULT_USER`, `RABBITMQ_DEFAULT_PASS`, `RABBITMQ_ERLANG_COOKIE` | Applied **only on first boot**, while mnesia is empty |

Both Airflow Connections are supplied as environment variables rather than created through the
UI (as `docs/env-prod.airflow-remote-logging.md` describes). That keeps the deployment
reproducible and makes them resolvable at DAG parse time.

## The manifests

Configuration and data carry no numeric prefix; workloads are numbered in apply order.

### Configuration and data

**`airflow-secrets.yaml`** — two Secrets: `airflow-secrets` for Airflow, and
`airflow-rabbitmq-secrets`, kept separate because the broker image consumes
`RABBITMQ_DEFAULT_*` directly and has no business seeing Sonador credentials.

**`airflow-config.yaml`** — the single `airflow-config` ConfigMap described above.

**`airflow-dags.yaml`** *(generated — `gen-configmaps.py --check` verifies)* — two ConfigMaps, `airflow-dags-io` and
`airflow-dags-ai`, mounted as **directories** at `/opt/airflow/dags/sonador` and
`/opt/airflow/dags/ai`, mirroring the compose bind-mount layout. Directory mounts mean DAG
edits reach running pods without a restart.

**`airflow-modules.yaml`** *(generated — `gen-configmaps.py --check` verifies)* — the Sonador integration modules, mounted
individually with `subPath` over paths the image either misses or bakes in the wrong place.
`subPath` mounts do **not** receive updates, so changes here need a restart.

### Workloads

**`rabbit-mq.yaml`** — a Service, a headless Service, and a single-replica StatefulSet with a
5 Gi PVC. A StatefulSet rather than a Deployment because RabbitMQ writes its node identity
(`rabbit@<hostname>`) into mnesia; a changing pod name leaves it unable to recognise its own
database. The management UI is exposed on the Service but not through the ingress.

**`airflow-10.init.yaml`** — a Job running three steps: `airflow db migrate` (core schema,
63 tables), `airflow fab-db migrate` (the FAB user/role tables Sonador SSO writes into on
first login), and `airflow pools set ai_inference_pool`. It overrides `AUTH_MANAGER` to the
stock FAB manager, since migration has no reason to depend on Sonador being reachable, and it
loops until the database answers so it can be applied before PostgreSQL is ready. Re-run it
after any image upgrade that bumps the Airflow version.

**`airflow-21.api-server.yaml`** — the API server, two replicas behind a Service. Serves the
UI, `/api/v2`, the `/auth` FAB views that carry the SSO callback, and the execution API the
other components call. Runs with `--proxy-headers`. The only component with HTTP probes
(`/api/v2/monitor/health`, which needs no authentication and stays 200 even when the rest of
the stack is down).

Note the filename says `api-server` — Airflow 3's name for the component — but the Deployment
and Service are still named `airflow-webserver`, carried over from Airflow 2. Both names are
therefore in play: `kubectl -n airflow-ai rollout restart deploy/airflow-webserver`, but
`airflow-21.api-server.yaml` on disk. Renaming the objects would mean deleting the Deployment
(its selector is immutable) and updating `AIRFLOW__CORE__EXECUTION_API_SERVER_URL` and the
ingress backend, so it is left alone deliberately.

**`airflow-22.scheduler.yaml`** — one replica, `strategy: Recreate` to avoid two schedulers
overlapping during a rollout. Airflow 3 supports active-active scheduling, but a second
replica buys availability rather than throughput and doubles database load.

**`airflow-23.triggerer.yaml`** — the asyncio loop that services deferrable operators. Neither
DAG defers today, so it is idle; it is included because adding it later means first
discovering tasks stuck in `deferred` with nothing to service them.

**`airflow-24.dag-processor.yaml`** — parses the DAG folder and writes serialised DAGs to the
database. The component most sensitive to the `PYTHONPATH` workaround, since both DAGs import
`client.*` and `sonador.*` at module scope. When a DAG goes missing from the UI, the traceback
is in this pod's log.

**`airflow-24.worker.yaml`** — Celery workers, two replicas, as a StatefulSet with a per-worker
20 Gi `volumeClaimTemplate` for the TotalSegmentator model cache. `terminationGracePeriodSeconds:
600` allows a warm shutdown rather than killing a task mid-inference. Also mounts the
TotalSegmentator runner script onto the path the AI DAG invokes. Scaling means raising
`replicas`; the per-worker volumes are not deleted on scale-down.

**`airflow-25.flower.yaml`** — Celery's monitoring UI, the fastest way to tell "the scheduler
is not dispatching" from "the workers are not consuming". Deliberately not exposed through the
ingress: it has no authentication here and would publish task arguments, which for these DAGs
include Sonador series identifiers. Reach it with a port-forward.

**`airflow-30.ingress.yaml`** — a Traefik Ingress with one `/` Prefix rule; Airflow serves the
UI, API, and auth views from one backend. Do not add a path-rewriting middleware — the OIDC
callback depends on the `/auth` prefix surviving. Give each hostname its own `tls` entry and
secret; several hosts under one `secretName` become one certificate with several SANs, and ACME
fails the whole order if any one cannot be validated.

## gen-configmaps.py

Two of the ConfigMaps are generated rather than hand-written, because they inline ~60 KB of
Python that already exists in the repository. Hand-copying it into YAML guarantees drift.

```bash
python3 k8s/airflow-ai/gen-configmaps.py           # regenerate
python3 k8s/airflow-ai/gen-configmaps.py --check   # verify only, exit 1 if stale
```

The generated files are committed so this directory reads as a complete blueprint, which means
they *can* fall behind their sources. `--check` renders them and compares against what is on
disk without writing, naming any stale file and exiting non-zero — suitable for a pre-commit
hook or CI job:

```
ok       k8s/airflow-ai/airflow-dags.yaml             (5 keys, 31912 bytes)
STALE    k8s/airflow-ai/airflow-modules.yaml -- regenerate with: python3 k8s/airflow-ai/gen-configmaps.py
```

Note that `airflow-config.yaml` is **not** generated and has no upstream copy — it is the only
definition of the Airflow settings for Kubernetes. Compose carries an equivalent environment
block, but neither is derived from the other.

| Reads | Writes | ConfigMap | Mount |
|---|---|---|---|
| `data/airflow/dags/io/*.py` | `airflow-dags.yaml` | `airflow-dags-io` | directory → `/opt/airflow/dags/sonador` |
| `data/airflow/dags/ai/*.py` | `airflow-dags.yaml` | `airflow-dags-ai` | directory → `/opt/airflow/dags/ai` |
| `docker/airflow/src/*.py` | `airflow-modules.yaml` | `airflow-modules` | `subPath`, per file |

Each source file becomes one ConfigMap key named for the file the pod should see, which is
where the renaming happens — `sonador-sso.webserver_config.py` in the repository is mounted as
`sonador_sso.py`, and `airflow-api.sonador_auth.py` as `sonador_auth.py`.

Beyond copying, the script enforces two things:

1. **The 1 MiB ConfigMap limit.** It exits with an error rather than emitting a manifest the
   API server will reject. Today the DAGs total ~31 KB. Past that ceiling, DAG delivery has to
   move to git-sync or a shared volume.
2. **An `.airflowignore` in every DAG ConfigMap.** Kubernetes surfaces a ConfigMap volume as a
   symlink farm — `..data` pointing at a timestamped directory — and Airflow walks the DAG
   folder following symlinks, sees the same real directory twice, and kills the whole
   DagProcessorJob with `Detected recursive loop when walking DAG directory`. In
   `airflow/utils/file.py` the ignore patterns are applied immediately *before* that check, so
   pruning the `..*` entries prevents the error rather than hiding it. Do not drop that key.

Run it after changing a DAG or any module under `docker/airflow/src`, then apply:

```bash
kubectl apply -f airflow-dags.yaml -f airflow-modules.yaml
```

DAG changes propagate to running pods within about a minute. Module changes need a restart,
because `subPath` mounts are not updated:

```bash
kubectl -n airflow-ai rollout restart deploy/airflow-webserver deploy/airflow-scheduler \
    deploy/airflow-dag-processor deploy/airflow-triggerer deploy/airflow-flower
kubectl -n airflow-ai rollout restart statefulset/airflow-worker
```

## Adapting this for a real deployment

Replace, at minimum:

| Where | What |
|---|---|
| `airflow-secrets.yaml` | Every `REPLACE-` value |
| `airflow-secrets.yaml` | The Fernet/API/JWT keys — those are published compose defaults, not secrets |
| `airflow-secrets.yaml` | Object storage endpoint and credentials, and the Sonador connection host |
| `airflow-config.yaml` | `SONADOR_URL`, `SONADOR_SERVICE_CLIENT_ID`, `AIRFLOW__API__BASE_URL` |
| `airflow-30.ingress.yaml` | Host, TLS secret name, cert-manager issuer |

Then, outside these files:

1. **Create the Sonador data service.** From the Sonador application container:

   ```bash
   python3 manage.py data-service create --service airflow \
       --service-description "Sonador Airflow" --set-acl-allow-staff --set-active
   ```

   `manage.py` lets you choose the id; services created from the admin panel get a random
   string instead, which then belongs in `SONADOR_SERVICE_CLIENT_ID`.

2. **Enable OIDC and register the callback**, exactly:

   ```
   https://airflow.example.com/auth/oauth-authorized/sonador
   ```

   `sonador_auth.py` refuses to import unless the service has `openid_allow_auth` set, so the
   pods crash-loop until this is done.

3. **Create the database and the log bucket.** Neither is provisioned here, and object storage
   will not create a bucket on demand.

Keep real credentials out of this repository. `*.local.yaml` under `k8s/` is gitignored for
exactly that purpose:

```bash
cp airflow-secrets.yaml airflow-secrets.local.yaml   # edit, then apply this instead
```

## Apply

`kubectl apply -f k8s/airflow-ai/` will **not** work: kubectl applies a directory in filename
order, which sorts `airflow-10.init.yaml` ahead of the config and secrets it depends on. Use
the explicit sequence:

```bash
# The airflow-ai namespace is managed outside this set and must already exist.
kubectl apply -f airflow-secrets.yaml
kubectl apply -f airflow-config.yaml
kubectl apply -f airflow-dags.yaml
kubectl apply -f airflow-modules.yaml
kubectl apply -f rabbit-mq.yaml
kubectl -n airflow-ai rollout status statefulset/airflow-rabbitmq

kubectl apply -f airflow-10.init.yaml
kubectl -n airflow-ai wait --for=condition=complete job/airflow-init --timeout=10m

kubectl apply -f airflow-21.api-server.yaml \
              -f airflow-22.scheduler.yaml \
              -f airflow-23.triggerer.yaml \
              -f airflow-24.dag-processor.yaml \
              -f airflow-24.worker.yaml \
              -f airflow-25.flower.yaml \
              -f airflow-30.ingress.yaml
```

Steady state is nine pods: 2 api-server, 2 worker, one each of scheduler, dag-processor,
triggerer, flower, and `airflow-rabbitmq-0`.

## Verifying

```bash
kubectl -n airflow-ai get pods
kubectl -n airflow-ai logs deploy/airflow-dag-processor              # DAG parse errors
kubectl -n airflow-ai port-forward svc/airflow-flower 5555:5555      # workers registered?
kubectl -n airflow-ai port-forward svc/airflow-rabbitmq 15672:15672  # queue depth
```

The login page should offer a single **Sonador SSO** button — `AUTH_TYPE = AUTH_OAUTH` means
there is no local password form. A successful login creates the account in the FAB tables and
maps it to `Admin` when the Sonador user is staff or superuser, otherwise `User`.

Trigger `SonadorExample01-HelloWorld` first. It exercises scheduler → broker → worker → remote
logging without touching Sonador or object storage as a data source, so a failure isolates the
plumbing. If the task succeeds but its log will not display, the object storage endpoint is
wrong or the bucket is missing. Only then try `Sonador-TotalSegmentator`, which additionally
needs the model cache, the inference pool, and both connections.

## Known image defects worked around here

Three bugs in `docker/airflow/Dockerfile` and `Dockerfile.sonador-ai`, all verified against the
published image. Compose hides all three behind bind mounts, which is why they are easy to miss.

| Defect | Effect | Workaround |
|---|---|---|
| `pip3 install -r etl.txt` runs as root, installing `sonador`, `sonadoretl`, `sonador3d`, and `client` into `/usr/python/lib/python3.10/site-packages` rather than the venv Airflow runs from | `ModuleNotFoundError: No module named 'client'` in every component, including the dag-processor — both DAGs import `client.*` at parse time | `PYTHONPATH` in `airflow-config.yaml` |
| `Dockerfile:15` copies `object_storage_hook.py` to `python.10` (missing the `3`) | Module absent; the AI DAG imports it | mounted from `airflow-modules.yaml` |
| `Dockerfile.sonador-ai` copies the TotalSegmentator runner to a relative destination, landing it at `/opt/airflow/home/airflow/env/...` | The AI DAG's `BashOperator` invokes `/home/airflow/env/totalsegmentator/bin/airflow-totalsegmentator.execute.py` and fails | mounted onto the expected path in `airflow-24.worker.yaml` |

`sonador_sso.py` is not in the image at all — it only ever existed as a compose bind mount — so
it comes from the same ConfigMap. Fixing the image would let you drop the `PYTHONPATH` entry and
reduce `airflow-modules.yaml` to `sonador_sso.py` alone.

## Deliberate differences from the compose stack

* **Named broker credentials.** Compose uses the built-in `guest` account; the stock RabbitMQ
  image ships `loopback_users.guest = false`, so guest is reachable from any pod. Setting
  `RABBITMQ_DEFAULT_USER` replaces that account rather than adding to it.
* **Broker persistence and a pinned node name.** Compose keeps mnesia in the container's
  writable layer, so a restart drops queued messages. Here it is a PVC, with `RABBITMQ_NODENAME`
  pinned so the node recognises its own database after rescheduling.
* **Pinned image tags.** Compose floats on `rabbitmq:management-alpine`.
* **Corrected worker health check.** Compose probes `airflow.executors.celery_executor`, which
  does not exist in Airflow 3; the provider path is
  `airflow.providers.celery.executors.celery_executor`.
* **Explicit probe timeouts.** An exec probe defaults to `timeoutSeconds: 1`. Because every
  Airflow CLI call pays for the Sonador round trip, `airflow jobs check` takes 5–8 s here and
  `celery inspect ping` 13–15 s. Left at the default, pods sit at 0/1 Running with nothing in
  their logs.
* **`hostname_callable` pinned to `socket.gethostname`.** Airflow defaults to `getfqdn`, which
  for StatefulSet pods returns `<pod>.<svc>.<ns>.svc.cluster.local` and breaks both
  `airflow jobs check --local` and Celery's `-d celery@$(hostname)` target.
* **`AIRFLOW__API__APP_MIDDLEWARE` dropped.** Airflow 3.1.5 has no `[api] app_middleware`
  option and `sonador_auth.py` defines no `SonadorHeaderMiddleware`; the compose setting is a
  no-op.
* **Proxy awareness added.** `AIRFLOW__FAB__ENABLE_PROXY_FIX` plus `api-server --proxy-headers`.
  TLS terminates at the ingress, so without them FAB builds an `http://` `redirect_uri` pointing
  at localhost and the OIDC callback is rejected.
* **Connections supplied as environment variables** rather than created in the UI.
* **`ai_inference_pool` created by the init job.**

## Notes on the SSO callback

The `redirect_uri` Airflow sends carries a query string:

```
https://airflow.example.com/auth/oauth-authorized/sonador?provider=sonador
```

`SonadorAuthOAuthView.oauth_authorized` is exposed at a static path rather than stock FAB's
`/oauth-authorized/<provider>`, so `url_for` appends the provider as a query argument. Despite
the "must match exactly" wording in `docker/airflow/README.md`, Sonador accepts this: its
authorize endpoint returns `302` for both the bare and the `?provider=` form, and `403` for a
host that is not registered.

If Airflow is reachable on more than one hostname, each one needs its own callback entry — the
`redirect_uri` is built from the incoming `X-Forwarded-Host`.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Every pod crash-loops, `ModuleNotFoundError: No module named 'client'` | `PYTHONPATH` removed from `airflow-config.yaml` |
| Every pod crash-loops, `ClientOperationError ... invalid-access-token` | `SONADOR_APITOKEN` wrong or rotated |
| Pods crash-loop, `... does not have OpenID Connect authentication enabled` | *Allow OIDC Auth* not set on the Sonador data service |
| Pods sit at 0/1 Running with empty logs | An exec probe is timing out — check `timeoutSeconds` |
| `sqlalchemy.exc.OperationalError`, host looks like `pass@airflow-postgres-rw` | A reserved character in the database password was not percent-encoded |
| dag-processor dies with `Detected recursive loop when walking DAG directory` | The `.airflowignore` key is missing from the DAG ConfigMaps; regenerate |
| DAG missing from the UI | Parse error — `kubectl -n airflow-ai logs deploy/airflow-dag-processor` |
| Tasks stay `queued` forever | Workers not registered (check Flower), or `ai_inference_pool` missing |
| Segmentation tasks queue but never start | `ai_inference_pool` has no slots, or no worker has the model cache volume |
| TotalSegmentator task fails with "No such file or directory" | The runner script mount is missing from the worker manifest |
| Task logs show "log file does not exist" | Object storage endpoint or credentials wrong, or the bucket does not exist |
| SSO redirect rejected by Sonador | Callback not registered for the hostname actually used |

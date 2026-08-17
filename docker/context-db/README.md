# Sonador Context Augmentation Database

ContextDB is a vector store for **AI task context** within the Sonador platform: a general
purpose structure for capturing model perception and elements of clinical decision making
without requiring models to be retrained.

Context augmentation allows operational and clinical experience to scale. Clinical expertise
is traditionally locked in people, workflows, and retrospective reviews; context augmentation
encodes prior decisions, outcomes, and expert judgement — rather than hard-coded rules or
static models — so agentic AI can make informed, repeatable decisions while continuously
improving over time. ContextDB captures what mattered in prior decisions:

* Accepted versus rejected cases
* Edge conditions, failure modes, and exceptions
* Protocol deviations and expert overrides

Text, metadata, measurements, and annotations are transformed into embeddings. Embeddings
encode semantic meaning and qualitative assessment, not just keywords: similar cases cluster
together and patterns become discoverable. The context database becomes a living memory of
operational experience.

### Use-Cases

ContextDB stores embeddings from any model, and every record carries an arbitrary JSON
payload (`misc`) for custom attributes — the tables are deliberately model-agnostic rather
than bound to a specific task. **Segmentation quality assessment** is one use-case the
server covers, supported at two granularities:

* **Instance segmentation embeddings** (2D) — what a model perceived about a single imaging
  slice, paired with Dice and Hausdorff scores computed against a known ground truth.
* **Series segmentation embeddings** (3D) — the same record shape for a whole imaging series.

When a new segmentation is evaluated, a similarity search returns prior cases that *looked*
like the current one together with how those cases actually scored, grounding a model's
qualitative judgement in evidence rather than a single observation.

**Contents** — [Architecture](#architecture) · [Data Model](#data-model) ·
[Quickstart](#quickstart) · [API Reference](#api-reference) · [Client Usage](#client-usage) ·
[SSO setup](#openid-connect-single-sign-on-and-api-authentication) · [Database](#database) ·
[Configuration](#configuration-reference) · [Troubleshooting](#troubleshooting)

## Architecture

```
   Notebook / Airflow task / agent
              │  Bearer token (Sonador session or api-token)
              ▼
   ┌──────────────────────────────┐        ┌────────────────────────┐
   │  ContextDB  (FastAPI :8072)  │───────►│  Sonador               │
   │  • OIDC via data service     │  authz │  • token introspection │
   │  • group membership checks   │        │  • data service groups │
   │  • resource validation       │───────►│  Orthanc               │
   └───────────────┬──────────────┘ verify │  • series / instances  │
                   │                       └────────────────────────┘
                   ▼
   ┌──────────────────────────────┐
   │  PostgreSQL + PgVector       │
   │  • embeddings (vector)       │
   │  • dice / hausdorff / quality│
   │  • L2 similarity search      │
   └──────────────────────────────┘
```

Authentication and authorization are provided by Sonador and mediated by the
[Sonador FastAPI](https://code.oak-tree.tech/oak-tree/medical-imaging/packages/sonador-fastapi)
package. ContextDB is the reference implementation of that integration: OpenID Connect
authentication against a Sonador data service, group-scoped authorization, and SQLAlchemy
session management inside a FastAPI application.

Deployments should account for:

* **Authorization is delegated to Sonador.** Every request carries a Sonador token. The
  service checks that the requested `group` is attached to its data service (404 if not) and
  that the calling user is a member of that group (403 if not). Groups are re-read from
  Sonador per request, so membership changes take effect immediately.
* **Referenced imaging is validated on write.** Creating an embedding verifies that the
  source instance, the segmentation resource, and the ground-truth resource all exist in
  Orthanc and that the latter two are `SEG` or `M3D` modality. Bad references are rejected at
  the API rather than discovered later during a search.
* **Records are scoped by model.** Every embedding carries `model_label` and `model_version`,
  and similarity search is filtered by both. Embeddings from different models are not
  comparable, and keeping them in one table without that filter would silently return
  nonsense.

## Data Model

ContextDB provides two tables to aid with segmentation tasks:

| Table | Granularity | `source` |
|---|---|---|
| `sonador_contextdb__instance_embedding_segmentation` | 2D — one slice | Orthanc **instance** UID |
| `sonador_contextdb__embedding_segmentation` | 3D — whole series | Orthanc **series** UID |

| Field | Type | Notes |
|---|---|---|
| `uid` | string | Primary key |
| `group` | int | Sonador group; every query is scoped to it |
| `model_label`, `model_version` | string | Which model produced the embedding |
| `embedding` | `vector` | PgVector column, dimension set by the model |
| `segmentation_label` | string | Anatomical label, e.g. `femur` |
| `source` | string | Imaging the segmentation was derived from |
| `resource` | string | The segmentation being assessed (SEG or M3D) |
| `ground_truth` | string | The reference segmentation (SEG or M3D) |
| `quality` | int | Derived score |
| `dice`, `hausdorff` | float | Similarity metrics against ground truth |
| `notes` | text | Free-text — typically the VLM's qualitative assessment |
| `misc` | JSONB | Ad-hoc attributes; used to tag records (e.g. `{"seg-model": "TotalSegmentator"}`) |

`quality`, `dice`, and `hausdorff` are nullable in storage but **required on create** — the
request schema enforces them, so a record cannot be written without the metrics that make it
useful for grounding.

## Quickstart

From the [development environment](https://code.oak-tree.tech/oak-tree/medical-imaging/imaging-development-env):

```bash
docker-compose -f compose/core.yaml -f compose/sonador.yaml \
    -f compose/pacs-secure.yaml -f compose/context-db.yaml up
```

This starts PgVector and the API together. The service listens on **8072**; interactive
documentation is at `http://localhost:8072/docs` and requires a Sonador login.

Before first use you need a Sonador data service with OIDC enabled (see
[SSO setup](#openid-connect-single-sign-on-and-api-authentication)) and the schema created
(see [Database](#database)).

To run the API directly during development:

```bash
uvicorn --reload --host 0.0.0.0 --port 8072 --log-level info main:app
```

## API Reference

All routes are prefixed `/embeddings/{group}` and require a `Bearer` token. `{granularity}`
is `instance` (2D) or `series` (3D); both families expose the same operations.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/seg/{granularity}/{model_label}/{model_version}` | List embeddings, filtered and paginated |
| `POST` | `/seg/{granularity}` | Create an embedding |
| `GET` | `/seg/{granularity}/{uid}` | Retrieve one embedding |
| `PUT` | `/seg/{granularity}/{uid}` | Update an embedding |
| `DELETE` | `/seg/{granularity}/{uid}` | Delete an embedding |
| `POST` | `/seg/{granularity}/{model_label}/{model_version}/search` | Vector similarity search |

List filters — all optional, combinable: `segmentation_label`, `source`, `ground_truth`,
`resource`, `page`, `items` (max 1000).

The `resource` + `items` combination is what makes bulk embedding runs resumable: fetch the
records already written for a segmentation series, skip those slices, and continue.

Search accepts an embedding vector and returns matches ordered by **L2 distance**, each
carrying its `distance` alongside the stored metrics. Results are always constrained to the
same `model_label` and `model_version`, and optionally to a `segmentation_label`.

## Client Usage

The [`oai`](https://code.oak-tree.tech/oak-tree/medical-imaging/oai) package wraps these
endpoints for notebook and pipeline use:

```python
from oai.contextdb import seg2d_instance_vlm_embedding_qa, \
    create_seg2d_instance_vlm_embedding_qa, fetch_seg2d_instance_vlm_embedding_qa, \
    seg2d_instance_similarity_search

# Build a record from the DICOM instance, the candidate segmentation, and ground truth
record = seg2d_instance_vlm_embedding_qa(dcm_instance, sx_segmentation, sx_ground_truth,
    model_label, model_version, 'femur', dice, hausdorff, embedding,
    seg_notes=vlm_assessment, seg_misc={'seg-model': 'TotalSegmentator'})

# Persist it
create_seg2d_instance_vlm_embedding_qa(iserver, CONTEXTDB_URL, group, record)

# Retrieve comparable prior cases
for match in seg2d_instance_similarity_search(iserver, CONTEXTDB_URL, group,
        model_label, model_version, query_embedding, segmentation_label='femur'):
    print(match['distance'], match['quality'], match['dice'], match['hausdorff'])
```

A complete worked example — building the corpus slice by slice from OAI knee MR data,
scoring against radiologist ground truth, and running retrieval against it — is in
`sonador-ai.agentic-ai-segmentation-qa.ipynb` in the
[Sonador examples](https://code.oak-tree.tech/oak-tree/medical-imaging/sonador-examples)
repository.

## OpenID Connect Single Sign-On and API Authentication
The Context Augmentation Database and OpenAPI documentation (`/docs`) utilize OpenID connect (via a Sonador Data Service) to authenticate users and issue API tokens. _Context Data API endpoints will accept any of [Sonador's token types](https://code.oak-tree.tech/oak-tree/medical-imaging/imaging-development-env/-/wikis/dev.credentials-management) including session, permament, and upstream IdP (remote validated) tokens. Users authenticated via the Context Database `/auth` API will receive session tokens._


### Auth and SSO Setup
Authentication setup is a three-step process:
1. Create and configure a data service for the deployment
2. Configure environment variables
3. Test single-on integration


#### Create and Configure Data Service 
Data services can be setup and configured via the [Sonador web application](https://code.oak-tree.tech/oak-tree/medical-imaging/sonador) `manage.py data-service` command or from the Sonador Administrative Panel. _**IMPORTANT**: `manage.py data-service` allows for a known service ID to be set, services created via the Admin Panel will use a randomly generated string for the service ID._

The callback URL for ContextDB will have the form: `{scheme}://{domain}:{port}/auth/token`. Examples URLs:
* local deployment: `http://localhost:8072/auth/token`
* production deployment
  - standard port (443): `https://contextdb.example.com/auth/token`
  - custom port (8072): `https://contextdb.example.com:8072/auth/token`

_The groups attached to this data service determine which `{group}` values the API will
accept. A user must be a member of the group to read or write its embeddings._


#### Configure Environment Variables
For the API server to start, it is necessary to provide the Sonador URL associated with the deployment, the imaging server which will be used for validating imaging references, the API token which will be used by the application for integration, the Data Service ID, and an "app encryption secret" which is used for creating signatures and encrypting sensitive data for application/client hand-off.

```bash
# Sample Deployment Configuration -- replace every value below
export SONADOR_URL=http://imaging:8070
export SONADOR_IMAGING_SERVER=dev01
export SONADOR_SERVICE_CLIENT_ID=contextdb
export SONADOR_APITOKEN=REPLACE-SONADOR-API-TOKEN
export FASTAPI_APP_ENCRYPTION_SECRET=REPLACE-APP-ENCRYPTION-SECRET
export DATABASE_URL=postgresql://contextdb:REPLACE-DB-PASSWORD@contextdb-pgvector:5432/contextdb
```

Deployment notes:
* **IMPORTANT**: the application user must be an administrator/superuser.
* It is recommended to use standing/permanent API tokens.
* For development deployments, it is recommended to put the configuration into a `postactivate` file which can be sourced as part of activating a virtual environment.
* Generate an encryption secret with `python3 -c "import secrets; print(secrets.token_urlsafe(64))"`.


#### Test Single Sign-On Integration
Once configured, you can test the SSO integration by attempting to access the `/docs` URL available at `{scheme}://{domain}:{port}/docs`. If working properly, you will be redirected to Sonador for login. Once the auth flow finishes, the docs page will load.


### Authentication
The Context-Augmentation Database API uses Sonador API tokens for authentication with both permanent (`api-token`) and HMAC-SHA256 session tokens supported as options. Tokens should be attached to the request as `Bearer` tokens using the `Authentication` header. Examples:

* session token: `Authorization: Bearer InBnZHJ0bzNkYXlzcG1uZWJxdDh6Z28zMzY0eGQ0bTR1Ig:1pkn9X:8_3LjBTDUAbWe-LrrWxgQ-Cm14RnXl6KSq7vmuXMmGs`
* permanent API token: `Authorization: Bearer api-token x73Gqshay2NVZH7SD1xNN2wgt4Vh8B5rRuwrPW5LL0upkAE4UgEf06u6Gqp2ZKxJ`



## Database
The Context-Augmentation API uses PostgreSQL as its primary relational datastore and the PgVector extension to support high-performance vector similarity search. PgVector adds a native vector column type and efficient distance operators that allow ContextDB to perform embedding proximity searches directly within PostgreSQL.

The database schema is managed using Alembic migrations, which create and maintain the required tables and indexes. The following steps will guide you through preparing the database and initializing the schema.


#### Deploy PostgreSQL with PgVector
The Context Augmentation Database API requires PostgreSQL with the PgVector extension installed. You can either:

* Install PgVector manually on an existing PostgreSQL instance, or
* Use the official PgVector container image (`pgvector/pgvector`), which includes the extension pre-installed.

The simplest approach is to run the container image:

```bash
docker run -d \
  --name contextdb-postgres \
  -p 5432:5432 \
  -e POSTGRES_USER=contextdb \
  -e POSTGRES_PASSWORD=contextdb \
  -e POSTGRES_DB=contextdb \
  pgvector/pgvector:pg16
```

This image provides PostgreSQL 16 with PgVector already available. _The [Oak-Tree Development Environment](https://code.oak-tree.tech/oak-tree/medical-imaging/imaging-development-env) includes a [Docker Compose manifest](https://code.oak-tree.tech/oak-tree/medical-imaging/imaging-development-env/-/tree/master/compose?ref_type=heads) (`compose/context-db.yaml`) which shows how the database can be deployed alongside the Context-Augmentation FastAPI application._


#### Enable the PgVector Extension
After the database is running, enable the vector extension within the target database.
First, connect to the container:

```bash
docker exec -it contextdb-postgres psql -U contextdb -d contextdb
```

Then run the following SQL command:

```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

This registers the PgVector data type and vector similarity operators within the database.


#### Initialize the Database Schema
Context Augmentation Database uses Alembic to manage database migrations.

Once the database and PgVector extension are available, run the migrations to create the required schema.

From the project root:

```bash
alembic upgrade head
```

This will create all tables and indexes required for operation.

_The application also calls `create_all(checkfirst=True)` on startup, so a fresh deployment
will come up against an empty database. Alembic remains the source of truth for schema
changes; use it rather than relying on the startup hook._


#### Verify Installation
After migrations complete, the database should contain the tables and be ready for use. You can verify the PgVector extension is active by running:

```sql
SELECT * FROM pg_extension WHERE extname = 'vector';
```

You should see a row indicating the vector extension is installed. At this point, the database is fully configured and ready to store embeddings and perform vector similarity searches.

## Configuration Reference

| Variable | Required | Purpose |
|---|---|---|
| `DATABASE_URL` | yes | PostgreSQL connection string; the instance must have PgVector |
| `SONADOR_URL` | yes | Sonador instance used for token validation and group lookup |
| `SONADOR_APITOKEN` | yes | Token the application uses to reach Sonador; the account must be a superuser |
| `SONADOR_IMAGING_SERVER` | yes | Imaging server used to validate segmentation references |
| `SONADOR_SERVICE_CLIENT_ID` | yes | Data service id providing OIDC and the group list |
| `FASTAPI_APP_ENCRYPTION_SECRET` | yes | Session signing and client hand-off encryption |
| `FASTAPI_SAME_SITE` | no | Session cookie policy, default `lax` |
| `FASTAPI_HTTPS_ONLY` | no | Set `True` when served over TLS |
| `FASTAPI_BACKGROUND_WORKERS` | no | Thread pool size for background work |

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Container exits at startup with a configuration error | A required variable above is unset; `DATABASE_URL` is checked explicitly |
| `404` on every request | The `{group}` is not attached to the data service named by `SONADOR_SERVICE_CLIENT_ID` |
| `403` on every request | The authenticated user is not a member of that group |
| `404 Source imaging ... does not exist` | The `source`/`resource`/`ground_truth` UID is not present on `SONADOR_IMAGING_SERVER` |
| `400 Invalid segmentation series ... modality` | `resource` or `ground_truth` is not a `SEG` or `M3D` series |
| `400 Unable to create similarity operation` | The query vector's dimension does not match the stored embeddings for that model |
| Similarity search returns unrelated records | `model_label`/`model_version` differ from the records you expect; vectors from different models are not comparable |
| `/docs` redirects in a loop | Callback URL registered on the data service does not match the deployed scheme, host, and port |

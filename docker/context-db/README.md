# Sonador Context Augmentation Database
Start API server:

```bash
uvicorn --reload --host 0.0.0.0 --port 8071 --log-level info main:app
```

## OpenID Connect Single Sign-On and API Authentication
The Context Augmentation Database and OpenAPI documentation (`/docs`) utilize OpenID connect (via a Sonador Data Service) to authenticate users and issue API tokens. _Context Data API endpoints will accept any of [Sonador's token types](https://code.oak-tree.tech/oak-tree/medical-imaging/imaging-development-env/-/wikis/dev.credentials-management) including session, permament, and upstream IdP (remote validated) tokens. Users authenticated via the Context Database `/auth` API will receive session tokens._


### Auth and SSO Setup
Authentication setup is a three-step process:
1. Create and configure a data service for the deployment
2. Configure environment variables
3. Test single-on integration

#### Create and Configure Data Service 
Data services can be setup and configured via the [Sonador web application](https://code.oak-tree.tech/oak-tree/medical-imaging/sonador) `manage.py data-service` command or from the Sonador Administrative Panel. _**IMPORTANT**: `manage.py data-service` allows for a known service ID to be set, services created via the Admin Panel will use a randomly generated string for the service ID._

**Step 1: `exec` to the Sonador web application container instance.** _The instructions for accessing `manage.py` within the container instance depend on whether the deployment is running in Docker Compose or Kubernetes._

**Step 2: Create a data service instance for the deployment.**

The command in the listing below creates a service with ID `contextdb`.

```bash
python3 manage.py data-service create --service contextdb \
	--service-description "Agentic AI Context Augmentation Database" \
	--set-acl-allow-staff --set-active
```

**Step 3: Enable OpenID authentication and add callback URLs.**

Because OpenID Connect SSO cannot be enabled from `manage.py data-service`, after creating the service log into the Data Service admin and select the service created in Step 2. Click on the "Allow OIDC Auth" and then add the service's token URL to the "Callback URL" box. _The callback URL used for redirect must match exactly. For deployments with multiple fully qualified DNS (FQDN), multiple callback URLs can be added (one per line)._

The callback URL for Context DB will have the form: `{scheme}://{domain}:{port}/auth/token`. Examples URLs:
* local deployment: `http://localhost:8071/auth/token`
* production deployment
  - standard port (443): `https://contextdb.example.com/auth/token`
  - custom port (8071): `https://contextdb.example.com:8071/auth/token`

#### Configure Environment Variables for Sentido Cloud Deployment
For the API server to start, it is necessary to provide the Sonador URL associated with the deployment, the imaging server which will be used for persisting hardware recordings, the API token which will be used by the application for integration, the Data Service ID, and an "app encryption secret" which is used for creating signatures and encrypting sensitive data for application/client hand-off.

```bash
# Sample Deployment Configuration
export SONADOR_URL=http://imaging:8070
export SONADOR_IMAGING_SERVER=dev01
export SONADOR_SERVICE_CLIENT_ID=contextdb
export SONADOR_APITOKEN=mnOP5Tp6QAFK50nKPD0KPx4WipWEKYKAzGCAVY650yqGccHIeGNV2rUtAA8YJlh4
export FASTAPI_APP_ENCRYPTION_SECRET=S8FxeSUoqibl8i7m7+mh3iBd0ZHnrFArV2MspswYkIeYuqAZ4w8FlQcH60DhREctTPMIbvdJ8M5DSsEzft9SFw
```

Deployment notes:
* **IMPORTANT**: the application user must be an administrator/superuser.
* It is recommended to use standing/permanent API tokens.
* For development deployments, it is recommended to put the configuration into a `postactivate` file which can be sourced as part of activating a virtual environment.

#### Test Single Sign-On Integration
Once configured, you can test the SSO integration by attempting to access the `/docs` URL available at `{scheme}://{domain}:{port}/docs`. If working properly, you will be redirected to Sonador for login. Once the auth flow finishes, the docs page will load.


### Authentication
The Context-Augmentation Database API uses Sonador API tokens for authentication with both permanent (`api-token`) and HMAC-SHA256 session tokens supported as options. Tokens should be attached to the request as `Bearer` tokens using the `Authentication` header. Examples:

* session token: `Authorization: Bearer  InBnZHJ0bzNkYXlzcG1uZWJxdDh6Z28zMzY0eGQ0bTR1Ig:1pkn9X:8_3LjBTDUAbWe-LrrWxgQ-Cm14RnXl6KSq7vmuXMmGs`
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

This image provides PostgreSQL 16 with PgVector already available. _The [Oak-Tree Development Environment](https://code.oak-tree.tech/oak-tree/medical-imaging/imaging-development-env) includes a [Docker Compose manifest](https://code.oak-tree.tech/oak-tree/medical-imaging/imaging-development-env/-/tree/master/compose?ref_type=heads) which shows how the database can be deployed alongside the Context-Augmentation FastAPI application._

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

#### Verify Installation
After migrations complete, the database should contain the tables and be ready for use. You can verify the PgVector extension is active by running:

```sql
SELECT * FROM pg_extension WHERE extname = 'vector';
```

You should see a row indicating the vector extension is installed. At this point, the database is fully configured and ready to store embeddings and perform vector similarity searches.

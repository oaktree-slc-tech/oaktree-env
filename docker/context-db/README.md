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
The Context Augmentation Database API uses Sonador API tokens for authentication with both permanent (`api-token`) and HMAC-SHA256 session tokens supported as options. Tokens should be attached to the request as `Bearer` tokens using the `Authentication` header. Examples:

* session token: `Authorization: Bearer  InBnZHJ0bzNkYXlzcG1uZWJxdDh6Z28zMzY0eGQ0bTR1Ig:1pkn9X:8_3LjBTDUAbWe-LrrWxgQ-Cm14RnXl6KSq7vmuXMmGs`
* permanent API token: `Authorization: Bearer api-token x73Gqshay2NVZH7SD1xNN2wgt4Vh8B5rRuwrPW5LL0upkAE4UgEf06u6Gqp2ZKxJ`

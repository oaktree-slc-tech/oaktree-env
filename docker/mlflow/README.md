# Sonador MLflow Container
This folder contains the Docker build files for the Sonador MLflow image (`oaktreetech/mlflow`). The image packages MLflow (with the GenAI extras), the Sonador client library, and the Sonador authentication app for the MLflow tracking server.

* `Dockerfile`: MLflow tracking server with Sonador Data Service authentication.
    - Tagged as `oaktreetech/mlflow`
    - Parent: `ubuntu:24.04`
* `src/sonador-mlflow-auth`: Python package which provides Single Sign On (via OpenID Connect) and API token validation for MLflow, mediated by a Sonador Data Service. The package registers a `sonador-auth` application under the `mlflow.app` entry point. _The integration follows the same pattern as the Sonador Airflow integration (see `docker/airflow`)._


## Single Sign-On
When integrated with Sonador, MLflow is able to use OpenID Connect (via a Sonador Data Service) to authenticate users via single sign-on (SSO). In addition to browser SSO, the same data service validates Sonador API tokens for programmatic access to the MLflow REST API. Authentication setup is a three-step process:

1. Create and configure a data service for the deployment
2. Configure environment variables and enable the auth app
3. Test single sign-on and API token integration


### Create and Configure Data Service
Data services can be setup and configured via the [Sonador web application](https://code.oak-tree.tech/oak-tree/medical-imaging/sonador) `manage.py data-service` command or from the Sonador Administrative Panel.

**Step 1: `exec` to the Sonador web application container instance.**

**Step 2: Create a data service instance for the deployment.**

The command in the listing below creates a service with ID `mlflow`.

```bash
python3 manage.py data-service create --service mlflow --service-description "Sonador MLflow" \
    --set-acl-allow-staff --set-active
```

**Step 3: Enable OpenID authentication and add callback URLs.**

After creating the service, log into the Data Service admin and select the service created in Step 2. Click on "Allow OIDC Auth" and add the deployment's callback URL to the "Callback URL" box. _The callback URL used for redirect must match exactly. Multiple callback URLs can be added (one per line)._

The callback URL for MLflow has the form: `{scheme}://{domain}:{port}/oauth-authorized/sonador`. Example URLs:
* local deployment: `http://localhost:5000/oauth-authorized/sonador`
* production deployment
  - standard port (443): `https://mlflow.example.com/oauth-authorized/sonador`
  - custom port (5000): `https://mlflow.example.com:5000/oauth-authorized/sonador`


### Configure Environment Variables
The Sonador integration is managed using environment variables. It is necessary to provide the Sonador URL associated with the deployment, the API token used by the application, and the Data Service ID. To enable the integration, the tracking server must be launched with `--app-name sonador-auth`.

```bash
# Sonador Connection
export SONADOR_URL="http://imaging:8070"
export SONADOR_APITOKEN="secure-api@sonador-dev"
export SONADOR_SERVICE_CLIENT_ID="mlflow"

# Session secret: used to sign the MLflow session cookie which stores the
# Sonador auth token after SSO login. Set to a value unique for the deployment.
export MLFLOW_FLASK_SERVER_SECRET_KEY="<sonador.mlflow-session-secret>"

# Launch the tracking server with the Sonador auth app
mlflow server --app-name sonador-auth --host 0.0.0.0 --port 5000 ...
```

Optional variables:

* `SONADOR_SERVICE_OPENID_SCOPE`: OpenID scope requested during login (default: `openid email profile`).
* `SONADOR_PUBLIC_URL`: browser-facing URL of the Sonador instance, used for the login redirect when `SONADOR_URL` is an internal address not reachable from the user's browser.
* `SONADOR_SERVICE_REDIRECT_URL`: explicit OpenID callback URL. Defaults to `{scheme}://{host}/oauth-authorized/sonador` derived from the request.
* `SONADOR_TOKEN_CACHE_TTL`: seconds introspection responses are cached before credentials are re-validated with Sonador (default: 60).

_Refer to `compose/mlflow-tracking.yaml` for the complete configuration._


### Test Single Sign-On
Once configured, navigate to the MLflow UI (for a development deployment, `http://localhost:5000`). If not authenticated, the browser is redirected to Sonador to sign in; after providing credentials you will be returned to the MLflow dashboard. `/sonador/logout` ends the session.

Any user with access to the data service is able to use MLflow after introspection succeeds. _Role mapping (e.g. read-only access for non-staff users) is planned for a future release, mirroring the Airflow integration._


### Programmatic Access (API Tokens)
The MLflow REST API validates Sonador API tokens via data service introspection. Configure clients with either of the following:

```bash
# Bearer token (preferred)
export MLFLOW_TRACKING_URI="http://localhost:5000"
export MLFLOW_TRACKING_TOKEN="<sonador-api-token>"

# Token type as username/password (mirrors the Airflow API auth backend)
export MLFLOW_TRACKING_USERNAME="api-token"
export MLFLOW_TRACKING_PASSWORD="<sonador-api-token>"
```

Requests may also send the token directly via the `Api-Token` header (Sonador request header convention). Unauthenticated API requests receive `401`. Health (`/health`) and version (`/version`) endpoints remain unauthenticated for orchestration liveness checks.

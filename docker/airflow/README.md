# Sonador AirFlow Containers
This repository contains the Docker build files for the Sonador Airflow images. The container images package dependencies and tools for running data preparation and ETL jobs which involve medical imaging data. The Sonador project maintains two flavors of images:

* [Sonador IO](https://sonador.oak-tree.tech/io): Sonador IO Airflow images includes the Sonador IO utilities for working with medical imaging data and can be used for general workflows.
* [Sonador AI](https://sonador.oak-tree.tech/ai): Sonador AI AirFlow images include additional tools for medical imaging segmentation and AI driven tasks. [Total Segmentator](https://github.com/wasserth/TotalSegmentator) is included within a separate virtual environment for semantic segmentation of MRI and CT data.

The images are structured in a series of layers. Images at the bottom provide a light-weight general environment, while images higher up in the stack provide more specialized tools and niceties such as Single Sign On (SSO) to help with their deployment to production environments. _The images in this repository use Sonador for Single Sign On (via Sonador's Data Services API). Refer to "Single Sign On" below for additional information._ 

Core:

* `Dockerfile`: core image for the Sonador environment. Includes the Sonador client, dependencies .
    - Tagged as `oaktreetech/sonador-airflow`
    - Parent: `apache/airflow`
* `Dockerfile.sonador-ai`: Sonador AI tools. Includes Sonador 3D, PyVista, and VTK. Total Segmentator is installed within a separate virtual environment at `/home/airflow/env/totalsegmentator` to allow for the creation of anatomic .
    - Tagged as `oaktreetech/sonador-airflow.ai`
    - Parent: `oaktreetech/sonador-airflow`


## Single Sign-On
When integrated with Sonador, Airflow is able to use OpenID connect (via a Sonador Data Service) to authenticate users via single sign-on (SSO). Authentication setup is a three-step process:

1. Create and configure a data service for the deployment
2. Configure environment variables
3. Test single-son integration


### Create and Configure Data Service
Data services can be setup and configured via the [Sonador web application](https://code.oak-tree.tech/oak-tree/medical-imaging/sonador) `manage.py data-service` command or from the Sonador Administrative Panel. _**IMPORTANT**: `manage.py data-service` allows for a known service ID to be set, services created via the Admin Panel will use a randomly generated string for service ID._

**Step 1: `exec` to the Sonador web application container instance.** _The instructions for accessing `manage.py` within the container instance depend on whether the deployment is running in Docker Compose or Kubernetes._

**Step 2: Create a data service instance for the deployment.**

The command in the listing below creates a service with ID `airflow`.

```bash
python3 manage.py data-service create --service airflow --service-description "Sonador Airflow" \
    --set-acl-allow-staff --set-active
```

**Step 3: Enable OpenID authentication and add callback URLs.**

Because OpenID Connect SSO cannot be enabled from `manage.py data-service`, after creating the service log into the Data Service admin and select the service created in Step 2. Click on the "Allow OIDC Auth" and then add the service's token URL to the "Callback URL" box. _The callback URL used for redirect must match exactly. For deployments with multiple fully qualified DNS (FQDN), multiple callback URLs can be added (one per line)._

The callback URL for Sentido will have the form: `{scheme}://{domain}:{port}/auth/oauth-authorized/sonador`. Examples URLs:
* local deployment: `http://localhost:8060/auth/oauth-authorized/sonador`
* production deployment
  - standard port (443): `https://airflow.example.com/auth/oauth-authorized/sonador`
  - custom port (8060): `https://airflow.example.com:8060/auth/oauth-authorized/sonador`


### Configure Environment Variables
The Sonador Airflow integration within Airflow is managed using environment variables. It is necessary to provide the Sonador URL associated with the deployment, the Data Service which will be used for authentication and user-account validation, the API token used by the application, and the data Data Service ID. To enable the integration, additional variables are necessary specifying the auth manager and SSO config file.

The listing below shows the variables used for providing this information to the container. _A complete configuration Airflow configuration showing environment and services can be found in the `compose` sub-folder of this repository._

```bash
# Sonador Connection
export SONADOR_URL="http://imaging:8070"
export SONADOR_APITOKEN="secure-api@sonador-dev"
export SONADOR_SERVICE_CLIENT_ID="airflow"

# Airflow COnfiguration
export AIRFLOW__CORE__AUTH_MANAGER="airflow.providers.fab.auth_manager.fab_auth_manager.FabAuthManager"
export AIRFLOW__FAB__CONFIG_FILE="/home/airflow/.local/lib/python3.10/site-packages/sonador_sso.py"
export AIRFLOW__API__AUTH_BACKENDS="sonador_auth"

# Airflow API Configuration. AIRFLOW__CORE__FERNET_KEY, AIRFLOW__API__SECRET_KEY,
# and AIRFLOW__API_AUTH__JWT_SECRET should be set to values unique
# for the deployment.
export AIRFLOW__API_AUTH__JWT_SECRET="<sonador.airflow-api-secret>"
export AIRFLOW__CORE__FERNET_KEY="ZmDfcTF7_60GrrY167zsiPd67pEvs0aGOv2oasOM1Pg="
export AIRFLOW__API__SECRET_KEY="<sonador.airflow-api-secret>"
```

_Refer to `compose/airflow-etl.yaml` and `compose/airflow-ai.yaml` for the complete configuration._


### Test single-sign on
Once configured, you can test the SSO integration by navigating to the frontend application for Airflow in your browser. For a development deployment of the resources in this repository, that will be at `http://localhost:8060`. If working properly, there will be a "Sign in with sonador" button.

Clicking this button should begin the login workflow for the application. After providing credentials for a user, you will be directed back to the Airflow dashboard. Permissions are taken from the `is_staff` and `is_superuser` attributes of the user account.
* If the user is staff or admin, they will receive an "Admin" role in Airflow.
* Otherwise the account is assigned a "User" role.

_Adding role mapping is planned for a future release._

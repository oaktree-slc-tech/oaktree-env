# Oak-Tree Development Environment
This repository contains configuration, development, and deployment resources to support the development of the Oak-Tree technical systems. It provides a set of container images designed to provide a development environment for [data systems](https://www.oak-tree.tech/blog/big-data-lab-environment) and [MedTech applications](https://sonador.oak-tree.tech) applications.

<img src="https://d33idl3etu5qjr.cloudfront.net/acorn/documents/sonador.platform-components-long.svg" alt="Oak-Tree development environment." width="50%" align="right" />

* **[MinIO](https://min.io/)**: S3 compatible storage
* **[PostgreSQL](https://www.postgresql.org/)**: Highly performant and scalable storage for relational data
* **[Orthanc](https://www.orthanc-server.com/)**: light-weight and extensible PACS server which provides a REST and DICOMweb interface for medical image management.
	- [S3 Storage Plugin](https://code.oak-tree.tech/oak-tree/medical-imaging/orthanc-s3)
	- PostgreSQL Plugin
	- (optional) [Advanced Authentication Plugin](https://orthanc.uclouvain.be/book/plugins/authorization.html)
* **[Kafka](https://kafka.apache.org/)**: a distributed streaming platform
* [RabbitMQ](https://www.rabbitmq.com/): an AMQP messaging broker and its management console
* [etcd](https://etcd.io/): distributed key-value store used by cluster computing systems to manage and coordinate state
* **[Sonador](https://sonador.oak-tree.tech)**: open source cloud platform for medical imaging visualization and research
	- OHIF: extensible DICOM viewer platform written in JavaScript
* **[Airflow](https://airflow.apache.org/)**: open source ETL platform for managing data

Repository contents:

* Docker Compose manifests that can be used to deploy some (or all) of the components for a development environment can be found with the `compose` folder. 
* Kubernetes manifests that are appropriate for the deploymenmt of the pieces to staging or production clusters can be found in the `k8s` folder.
* Sample configuration for Orthanc, Sonador, Airflow, and NGINX are available in the `config` folder. _Note: This environment includes CORS options for Sonador and NGINX._


## Getting Started
A tutorial which describes the capabilities of the environment, architecture, and how to get started with general "data" development as well as more specific "medical imaging" configurations are available from the [Oak-Tree website](https://www.oak-tree.tech/blog).

* [Part 1: A Docker Based Lab for Data Development](https://www.oak-tree.tech/blog/big-data-lab-environment). Introduces the components of the environment, and how it is able to support the development of data driven applications.
* [Part 2: Hello Kafka](https://www.oak-tree.tech/blog/big-data-kafka). Introduces Kafka and how to interface with it from the JupyterLab application.
* [Part 3: First Steps with JupyterLab](https://www.oak-tree.tech/blog/first-steps-jupyter). Showcases the capabilities of JupyterLab and describes how it can be used within the Oak-Tree environment to interact with other services in the environment.


### Docker Compose Manifests
Multiple `docker-compose` scripts are included so that it is possible to deploy the components of the environment in different configurations. All manifests can be found within the the `compose` subfolder of the repository.

* `core.yaml`. Core services of the environment: MinIO, ZooKepper, and Kafka.
* `pacs-secure.yaml`. Orthanc with Sonador security/authorization layer enabled. Includes NGINX proxy for Orthanc which injects CORS headers. _This is required if you will be using the OHIF viewer instance provided by Sonador._
* `sonador.yaml`. Sonador and the PostgreSQL database instance.
* `message-broker.yaml`. RabbitMQ message broker (with management plugin).
* `airflow-etl.yaml`. Airflow with Sonador/Orthanc client libraries installed. _Requires `message-broker.yaml`, `core.yaml`, and `pacs-secure.yaml` to be active. Username/password for environment is `airflow:airflow`._
  - `airflow-etl.gitlab-sso.yaml`: specialized Airflow configuration with dependencies needed to support GitLab as an SSO Identity Provider. _Refer to notes below for configuration instructions and to ["Using GitLab as an Identity Provider for Apache Airflow 2"](https://www.oak-tree.tech/blog/k8s-airflow-oauth2-gitlab) for implementation details._
* `analytics.yaml`. Jupyter with Sonador/Orthanc client libraries installed.

#### DNS
The following lines need to be added to the `/etc/hosts` file of the development machine to allow for traffic to resolve to the correct containers:

*Windows 10 `/etc/hosts` file is located at `C:\Windows\System32\drivers\etc\hosts`*

```text
# Sonador DNS for ipv4 & ipv6
::1 object-storage orthanc imaging
::1 imaging.local
127.0.0.1 object-storage orthanc imaging
127.0.0.1 imaging.local
```

The Sonador and OHIF viewer instance should be accessed using the `imaging.local` domain.

#### Quickstart
A minimal container environment which includes MinIO, Orthanc, Sonador, Kafka, and a web proxy can be started using the command below (run from the root of the repository):

```bash
docker-compose -f compose/core.yaml -f compose/pacs-secure.yaml -f compose/sonador.yaml up -d
```

The `up` command will download the images (if not already present), create, and start container instances for each of the required components of the environment.

In order to access the web interface of Sonador, you need to add the following lines to the `/etc/hosts` configuration of the machine.

```text
# Sonador DNS for ipv4 & ipv6
::1 object-storage orthanc imaging
::1 imaging.local
127.0.0.1 object-storage orthanc imaging
127.0.0.1 imaging.local
```

The Sonador web application can be accessed via `http://imaging.local:8070`. The username/password for the environment are:

```yaml
url: "http://imaging.local:8070"
username: "dev01"
password: "sonador@development-env"
```

#### Manage Environment
Once running, the containers can be managed individually (or as smaller groups). The command belows, for example, could be used to manage the Sonador web application:

```bash
# Stop the Sonador web application
docker-compose -f compose/sonador.yaml stop

# Start the Sonador web application
docker-compose -f compose/sonador.yaml start

# Reload the Sonador web application
docker-compose -f compose/sonador.yaml restart
```


### GPU 
The analytics container has GPU support included in the runtime, which can be used through the `compose/analytics-gpu.yaml` manifest. In order for the GPU accelerated to be recognized, the `nvidia-docker2` host must have the `nvidia-docker2` package installed and an `nvidia` runtime must be defined in `/etc/docker/daemon.json` similar to the example shown in the listing below:

```json
{
  "runtimes": {
  "nvidia": {
      "path": "/usr/bin/nvidia-container-runtime",
      "runtimeArgs": []
    }
  }
}
```


### Sonador Web Application Development Environment
If you need to make changes to the Sonador web application, it is often easier to create a local development instance which runs under the Django development server. For instructions on how to obtain the sources and configure the web application to run locally but use backing services hosted in the containers, [follow this guide](https://code.oak-tree.tech/oak-tree/medical-imaging/imaging-development-env/-/wikis/Environment-Setup).


### GitLab SSO
The Sonador platform includes support for Single Sign On via the OpenID Connect protocol, and can be used for authentication for Sonador (and by extension Orthanc) and Airflow.

#### Airflow
In the `compose` subfolder of the repository, there is an example manifest that shows the configuration required to enable GitLab SSO for Airflow. Before you can use the manifest, however, you first need to setup a file that provides the OpenID connect configuration for your GitLab instance.

An example file called `client_secret.json.sample` showing how this can be done is found in the `config/airflow` subfolder. Create a copy of the file in the same directory, calling it `client_secret.json` and provide the OIDC `client_id`, `client_secret`, GitLab URL, and Airflow URL.

_For additional detail about how the GitLab SSO has been implemented within Airflow, refer to ["Using Gitlab as an Identity Provider for Apache Airflow 2"](https://www.oak-tree.tech/blog/k8s-airflow-oauth2-gitlab)._

##### Assigning an Airflow Role
Depending on the default role that is specified within the environment, new Airflow users may not be granted any permissions. Before they are able to access the web interface or interface with the system via the API, they need to be granted an expanded role. This can be done using the `users` command of the the `airflow` CLI utility.

You can access the `airflow` via one of the container instances:

```bash
# docker exec to one of the container instances to access the Airflow CLI tools.
# The sample command below uses the webserver container instance.
docker exec -it compose_airflow-webserver_1 bash
```

From there you can use `airflow users --add-role` to allocate expanded permissions. The example below grants `Admin` permissions to a user called `username`:

```bash
# Grant Admin permissions to the "username" user.
airflow add-role -u username -r Admin
```

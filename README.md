# Oak-Tree Imaging Development Environment
This repository contains configuration, development, and deployment resources for the Oak-Tree medical imaging technical stack. Oak-Tree uses the following components for research and visualization projects:

* MinIO: S3 compatible storage
* PostgreSQL: Highly performant and scalable storage for imaging metadata
* Orthanc: light-weight and extensible PACS server which provides a REST and DICOMweb interface for medical image management.
	- S3 Storage Plugin
	- PostgreSQL Plugin
	- (optional) Advanced Authentication Plugin
* Kafka: a distributed streaming platform
* RabbitMQ: an AMQP messaging broker and its management console
* etcd: distributed key-value store used by cluster computing systems to manage and coordinate state
* Sonador: open source cloud platform for medical imaging visualization and research
	- OHIF: extensible DICOM viewer platform written in JavaScript
* AirFlow: open source ETL platform for managing data

Repository contents:

* Docker Compose manifests that can be used to deploy some (or all) of the components for a development environment can be found with the `compose` folder. 
* Kubernetes manifests that are appropriate for the deploymenmt of the pieces to staging or production clusters can be found in the `k8s` folder.
* Sample configuration for Orthanc, Sonador, AirFlow, and NGINX are available in the `config` folder. _Note: This environment includes CORS options for Sonador and NGINX._


### Docker Compose Manifests
Multiple `docker-compose` scripts are included so that it is possible to deploy the components of the environment in different configurations. All manifests can be found within the the `compose` subfolder of the repository.

* `core.yaml`. Core services of the environment: MinIO, ZooKepper, and Kafka.
* `pacs.yaml`. Orthanc without the Sonador security/authorization layer enabled.
* `pacs-secure.yaml`. Orthanc with Sonador security/authorization layer enabled.
* `web-proxy.yaml`: NGINX proxy for Orthanc which injects CORS headers. _This is required if you will be using the OHIF viewer instance provided by Sonador._
* `sonador.yaml`. Sonador and the PostgreSQL database instance.
* `airflow-etl.yaml`. AirFlow with Sonador/Orthanc client libraries installed.
* `analytics.yaml`. Jupyter with Sonador/Orthanc client libraries installed.
* `message-broker.yaml`. RabbitMQ message broker (with management plugin).

**Important**: `pacs.yaml` and `pacs-secure.yaml` cannot be used at the same time as they will cause port and hostname conflicts.

#### DNS
The following lines need to be added to the `/etc/hosts` file of the development machine to allow for traffic to resolve to the correct containers:

```
::1     object-storage orthanc imaging kafka
::1     imaging.local
```

The Sonador and OHIF viewer instance should be accessed using the `imaging.local` domain.

#### Quickstart
A minimal container environment which includes MinIO, Orthanc, Sonador, Kafka, and a web proxy can be started using the command below (run from the root of the repository):

```bash
docker-compose -f compose/core.yaml -f compose/pacs-secure.yaml -f compose/web-proxy.yaml -f \
  -f compose/sonador.yaml up -d
```

The `up` command will download the images (if not already present), create, and start container instances for each of the required components of the environment.

In order to access the web interface of Sonador, you need to add the following lines to the `/etc/hosts` configuration of the machine.

```text
::1     object-storage orthanc imaging
::1     imaging.local
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

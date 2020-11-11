# Oak-Tree Imaging Development Environment
This repository contains configuration, development, and deployment resources for the Oak-Tree medical imaging technical stack.  Oak-Tree uses the following components for medical imaging research and visualization projects:

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

This repository includes Docker Compose manifests that can be used to deploy some (or all) of the components for a development environment, and Kubernetes manifests that are appropriate for the deploymenmt of the pieces to staging or production clusters.


### Docker Compose Manifests
Multiple `docker-compose` scripts are included so that the components that be deployed in different configurations.

* `docker-compose.core.yaml`. Core services of the environment: MinIO, ZooKepper, and Kafka.
* `docker-compose.pacs.yaml`. Orthanc without the Sonador security/authorization layer enabled.
* `docker-compose.pacs-secure.yaml`. Orthanc with Sonador security/authorization layer enabled.
* `docker-compose.web-proxy.yaml`: NGINX proxy for Orthanc which injects CORS headers. _This is required if you will be using the OHIF viewer instance provided by Sonador._
* `docker-compose.sonador.yaml`. Sonador and the PostgreSQL database instance.
* `docker-compose.airflow-etl.yaml`. AirFlow with Sonador/Orthanc client libraries installed.
* `docker-compose.analytics.yaml`. Jupyter with Sonador/Orthanc client libraries installed.
* `docker-compose.message-broker.yaml`. RabbitMQ message broker (with management plugin).

**Important**: `docker-compose.pacs.yaml` and `docker-compose.pacs-secure.yaml` cannot be used at the same time as they will cause port and hostname conflicts.


### GPU 
The analytics container has GPU support included in the runtime, which can be used through the `docker-compose.analytics-gpu.yaml`. In order for the GPU accelerated to be recognized, the `nvidia-docker2` host must have the `nvidia-docker2` package installed and an `nvidia` runtime must be defined in `/etc/docker/daemon.json` similar to the example shown in the listing below:

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

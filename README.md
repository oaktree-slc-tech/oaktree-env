# Oak-Tree Imaging Development Environment
This repository contains configuration, development, and deployment resources for the Oak-Tree medical imaging technical stack.  Oak-Tree uses the following components for medical imaging research and visualization projects:

* MinIO: S3 compatible storage
* PostgreSQL: Highly performant and scalable storage for imaging metadata
* Orthanc: light-weight and extensible PACS server which provides a REST and DICOMweb interface for medical image management.
	- S3 Storage Plugin
	- PostgreSQL Plugin
	- (optional) Advanced Authentication Plugin
* Kafka: a distributed streaming platform
* etcd: distributed key-value store used by cluster computing systems to manage and coordinate state
* Sonador: open source cloud platform for medical imaging visualization and research
	- OHIF: extensible DICOM viewer platform written in JavaScript

This repository includes Docker Compose manifests that can be used to deploy some (or all) of the components for a development environment, and Kubernetes manifests that are appropriate for the deploymenmt of the pieces to staging or production clusters.


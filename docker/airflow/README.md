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
# Sonador AirFlow Containers
This repository contains the Docker build files for the Airflow images used as part of the Sonador Medical Imaging platform. The container images package dependencies and tools for running data preparation and ETL jobs which involve medical imaging data.

The images are structured in a series of layers. Images at the bottom provide a light-weight general environment, while images higher up in the stack provide more specialized tools and niceties such as Single Sign On (SSO).

Core:

* `Dockerfile`: core image for the Sonador environment. Includes the Sonador client and dependencies.
    - Tagged as `oaktreetech/sonador-airflow`
    - Parent: `apache/airflow`

Production images:

* `Dockerfile.gitlab-sso`: extension of the Sonador image to include dependencies that allow for GitLab to be used as a Single Sign On provider. _Refer to ["Using GItLab as an Identity Provider for Apache Airflow 2"](https://www.oak-tree.tech/blog/k8s-airflow-oauth2-gitlab) for details._
    - Tagged as `oaktreetech/sonador-airflow.gitlab-sso`
    - Parent: `oaktreetech/sonador-airflow`

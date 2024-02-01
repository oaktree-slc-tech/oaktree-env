FROM ubuntu:20.04
RUN apt-get update && apt-get install -y python3 python3-pip \
  && pip3 install --upgrade pip \
  && pip3 install mlflow scikit-learn psycopg2-binary boto3
RUN useradd -m -s /bin/bash mlflow
RUN mkdir -p /home/mlflow && chown -R mlflow:mlflow /home/mlflow

USER mlflow
WORKDIR /home/mlflow

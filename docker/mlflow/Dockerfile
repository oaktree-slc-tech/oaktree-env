FROM ubuntu:24.04
RUN apt-get update && apt-get install -y python3 python3-pip \
  && pip3 install --break-system-packages mlflow "mlflow[genai]" scikit-learn psycopg2-binary boto3 vertexai
RUN useradd -m -s /bin/bash mlflow
RUN mkdir -p /home/mlflow && chown -R mlflow:mlflow /home/mlflow

USER mlflow
WORKDIR /home/mlflow

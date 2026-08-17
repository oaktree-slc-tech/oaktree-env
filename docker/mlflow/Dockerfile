FROM ubuntu:24.04
RUN apt-get update && apt-get install -y python3 python3-pip \
  && pip3 install --break-system-packages \
     --extra-index-url https://code.oak-tree.tech/api/v4/projects/335/packages/pypi/simple \
     "mlflow[genai]==3.15.1" scikit-learn psycopg2-binary boto3 vertexai "sonador>=0.4.1"

# Sonador Data Service authentication (SSO + API token validation). Registered
# under the `mlflow.app` entry point and enabled via `--app-name sonador-auth`.
COPY src/sonador-mlflow-auth /opt/sonador-mlflow-auth
RUN pip3 install --break-system-packages --no-deps /opt/sonador-mlflow-auth

RUN useradd -m -s /bin/bash mlflow
RUN mkdir -p /home/mlflow && chown -R mlflow:mlflow /home/mlflow

USER mlflow
WORKDIR /home/mlflow

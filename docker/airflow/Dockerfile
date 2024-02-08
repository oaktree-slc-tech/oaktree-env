FROM apache/airflow:2.7.3-python3.10
USER root

# Install Sonador Client Library Dependencies
RUN apt-get update && apt-get install -y git \
  && pip3 install --upgrade pip \
  && pip3 install requests requests_ntlm boto3 sonador>=0.3.0b1 sonadoretl \
      s3fs openpyxl pandas PyYAML \
    --extra-index-url https://code.oak-tree.tech/api/v4/projects/335/packages/pypi/simple --no-user

USER airflow
COPY airflow-api.sonador_auth.py /home/airflow/.local/lib/python3.10/site-packages/sonador_auth.py


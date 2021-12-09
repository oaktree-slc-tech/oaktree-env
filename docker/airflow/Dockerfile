FROM apache/airflow:2.2.1-python3.8
USER root

# Install Sonador Client Library Dependencies
RUN apt-get update && apt-get install -y git \
  && pip3 install requests client sonador --extra-index-url https://code.oak-tree.tech/api/v4/projects/335/packages/pypi/simple --no-user
USER airflow
COPY airflow-api.sonador_auth.py /home/airflow/.local/lib/python3.8/site-packages/sonador_auth.py

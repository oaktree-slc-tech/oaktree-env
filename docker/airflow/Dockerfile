FROM apache/airflow:3.1.5-python3.10
USER root

# Install Sonador Client Library Dependencies
RUN mkdir -p /opt/root/requirements/
COPY requirements/etl.txt /opt/root/requirements/
RUN apt-get update && apt-get install -y git \
  && pip3 install --upgrade pip \
  && pip3 install -r /opt/root/requirements/etl.txt
    

USER airflow
COPY src/airflow-api.sonador_auth.py /home/airflow/.local/lib/python3.10/site-packages/sonador_auth.py
COPY src/airflow-api.sonador_hook.py /home/airflow/.local/lib/python3.10/site-packages/sonador_hook.py
COPY src/airflow-api.object_storage_hook.py /home/airflow/.local/lib/python.10/site-packages/object_storage_hook.py

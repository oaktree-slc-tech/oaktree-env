FROM puckel/docker-airflow
USER root

# Install Sonador Client Library Dependencies
RUN apt-get update && apt-get install -y git \
	&& pip3 install requests \
	&& pip3 install git+https://code.oak-tree.tech/guru-labs/packages/guru-client.git \
	&& pip3 install git+https://code.oak-tree.tech/oak-tree/medical-imaging/packages/sonador.git
USER airflow

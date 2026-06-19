FROM ubuntu:24.04

WORKDIR /app

# Install system dependencies
RUN mkdir -p /app/requirements/ \
	&& apt-get update && apt-get install -y python3-pip \
	&& rm -rf /var/lib/apt/lists/*

# Install application dependencies
COPY --chown=1000:1000 requirements.txt /app/requirements/app.txt
RUN pip3 install --break-system-packages --no-cache-dir -r /app/requirements/app.txt

# Copy MCP application code
COPY --chown=1000:1000 README.md server.py /app/

# Application runtime settings
USER 1000
EXPOSE 6767
CMD ["python3", "server.py"]
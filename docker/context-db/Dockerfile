FROM ubuntu:24.04

WORKDIR /app

# Install system dependencies
RUN mkdir -p /app/requirements/ \
	&& apt-get update && apt-get install -y gcc postgresql-client python3-pip \
	&& rm -rf /var/lib/apt/lists/*

# Install application dependencies
COPY --chown=1000:1000 requirements.txt /app/requirements/app.txt
RUN pip3 install --break-system-packages --no-cache-dir -r /app/requirements/app.txt

# Copy application code and runtime files
COPY --chown=1000:1000 README.md main.py /app/
COPY --chown=1000:1000 contextdb /app/contextdb

# Application runtime settings
USER 1000
EXPOSE 8072
CMD ["uvicorn", "--host", "0.0.0.0", "--port", "8072", "main:app"]

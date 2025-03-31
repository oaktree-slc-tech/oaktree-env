# Kaitara


### Quickstart
Start container instance (command run from the root of the environment repository):

```bash
# Start core services, database, and web application
docker-compose -f compose/core.yaml -f compose/kaitara.yaml up
```

The first time that the application launches, the database needs to be configured, SCSS styles compiled, and static resources deployed to MinIO.

**Step 0**: `exec` into the Kaitara container image.

```bash
docker exec -it compose_kaitara_1
```

**All subsequent commands need to be run from the command prompt within the container. (default runs automatically by 'entrypoint.sh'**


**Step 1**: Create and apply database migrations

```bash

# Create database migrations for wgtauth
python3 /srv/www/kaitara/kaitara/manage.py makemigrations wgtauth

# Create database migrations for wgtbase
python3 /srv/www/kaitara/kaitara/manage.py makemigrations wgtbase

# Create database migrations for wgtcontent
python3 /srv/www/kaitara/kaitara/manage.py makemigrations wgtcontent

# Creatae database migrations for other applications
python3 /srv/www/kaitara/kaitara/manage.py makemigrations

# Apply migrations
python3 /srv/www/kaitara/kaitara/manage.py migrate
```

**Step 2**: Compile CSS stylesheets

```bash
python3 /srv/www/kaitara/kaitara/manage.py compile-scss
```

**Step 3**: Collect static assets and deploy to MinIO

```bash
python3 /srv/www/kaitara/kaitara/manage.py collectstatic
```

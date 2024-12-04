# Oak-Tree Acorn


### Quickstart
Start container instance (command run from the root of the environment repository):

```bash
# Start core services, database, and web application
docker-compose -f compose/core.yaml -f compose/acorn.yaml up
```

The first time that the application launches, the database needs to be configured, SCSS styles compiled, and static resources deployed to MinIO.

**Step 0**: `exec` into the Acorn container image.

```bash
docker exec -it compose_acorn_1
```

**All subsequent commands need to be run from the command prompt within the container.**


**Step 1**: Create and apply database migrations

```bash
# Create database migrations for wgtcontent
python3 /srv/www/acorn/acorn/manage.py makemigrations wgtcontent

# Creatae database migrations for other applications
python3 /srv/www/acorn/acorn/manage.py makemigrations

# Apply migrations
python3 /srv/www/acorn/acorn/manage.py migrate
```

**Step 2**: Compile CSS stylesheets

```bash
python3 /srv/www/acorn/acorn/manage.py compile-scss
```

**Step 3**: Collect static assets and deploy to MinIO

```bash
python3 /srv/www/acorn/acorn/manage.py collectstatic
```

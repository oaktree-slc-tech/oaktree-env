#!/bin/bash

echo "🛠 Making migrations wgtbase"
python3 manage.py makemigrations wgtbase
python3 manage.py migrate wgtbase

echo "🛠 Making migrations wgtcontent"
python3 manage.py makemigrations wgtcontent
python3 manage.py migrate wgtcontent

echo "🛠 Making migrations kaitara"
python3 manage.py makemigrations
python3 manage.py migrate

echo "🎨 Compiling SCSS..."
python3 manage.py compile-scss

echo "📁 Collecting static files..."
python3 /srv/www/kaitara/kaitara/manage.py collectstatic --noinput

echo "▶️ Running server..."
python3 manage.py runserver 0.0.0.0:8000

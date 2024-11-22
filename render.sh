#!/bin/sh
python manage.py makemigrations api
python manage.py migrate
python manage.py collectstatic --no-input
daphne server.asgi:application --bind 0.0.0.0 --port "$PORT"
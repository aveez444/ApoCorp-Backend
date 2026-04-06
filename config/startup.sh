#!/bin/bash

# Install Playwright Chromium browser if not already present
# This runs after every deploy but skips the download if binary exists
if [ ! -d "/home/.cache/ms-playwright" ]; then
    echo "Playwright browsers not found — installing Chromium..."
    playwright install chromium
    playwright install-deps chromium
else
    echo "Playwright browsers already present — skipping install."
fi

python manage.py migrate
gunicorn config.wsgi:application --bind 0.0.0.0:8000
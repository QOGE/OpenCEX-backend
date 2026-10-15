# Python 3.8 is EOL; stay on 3.8.18 until a dedicated Django 4 / Python 3.12 upgrade.
FROM python:3.8.18-bookworm
ENV PYTHONUNBUFFERED 1
WORKDIR /app
COPY requirements.txt /tmp/
RUN python -m pip install --upgrade pip
RUN pip install  --no-cache-dir -r /tmp/requirements.txt
RUN apt-get update && apt-get install systemd gettext -y
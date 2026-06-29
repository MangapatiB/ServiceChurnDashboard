FROM image-registry.openshift-image-registry.svc:5000/openshift/python:3.11-ubi9

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080 \
    HOME=/tmp \
    LOG_DIR=/tmp/logs

WORKDIR /app

USER 0

RUN microdnf -y update \
    && microdnf -y install curl gnupg2 ca-certificates gcc gcc-c++ make unixODBC unixODBC-devel \
    && curl -fsSL https://packages.microsoft.com/config/rhel/9/prod.repo -o /etc/yum.repos.d/microsoft-prod.repo \
    && ACCEPT_EULA=Y microdnf -y install msodbcsql18 \
    && microdnf clean all

COPY requirements.txt ./

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir gunicorn

COPY . .

RUN mkdir -p /tmp/logs /app/logs \
    && chgrp -R 0 /app /tmp \
    && chmod -R g=u /app /tmp

USER 1001

EXPOSE 8080

CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:${PORT} --workers 2 --threads 4 --timeout 120 run:app"]
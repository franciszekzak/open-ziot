FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY server/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt \
    && useradd --uid 10001 --create-home ziot \
    && mkdir /data && chown ziot:ziot /data
COPY server/ /app/
USER 10001:10001
CMD ["python", "-u", "/app/camera_service.py", "--config", "/config/config.json"]

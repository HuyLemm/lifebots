FROM python:3.12-slim
ENV TZ=Asia/Ho_Chi_Minh PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
RUN pip install --no-cache-dir httpx truststore
COPY shared ./shared
COPY price ./price
COPY news ./news
COPY weather ./weather
COPY stuff ./stuff

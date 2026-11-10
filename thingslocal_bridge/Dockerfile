FROM python:3.14-alpine
RUN apk add --no-cache openssl
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
WORKDIR /app
COPY src ./src
COPY LICENSE UPSTREAM.md ./
ENV PYTHONPATH=/app/src
CMD ["python", "-m", "thingslocal", "app"]

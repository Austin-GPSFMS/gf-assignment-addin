# Alternative to lightsail-setup.sh: run the API as a container
# (Lightsail Container Service, ECS, or any Docker host behind HTTPS).
#
#   docker build -f deploy/Dockerfile -t gf-assignment-api .
#   docker run --env-file backend/.env -p 8000:8000 gf-assignment-api
#
# The emailer is a separate scheduled run of the same image:
#   docker run --env-file backend/.env gf-assignment-api python pretrip_email.py
FROM python:3.12-slim
WORKDIR /app
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ .
RUN rm -rf tests out .env databases.json recipients.json recipients.*.json
EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]

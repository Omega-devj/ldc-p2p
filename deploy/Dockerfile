# Serveur de rendez-vous / relais LDC P2P (aucune dépendance Qt)
FROM python:3.12-slim
RUN useradd -r -u 10001 ldc && mkdir /data && chown ldc /data
WORKDIR /app
RUN pip install --no-cache-dir PyNaCl cryptography
COPY src/ldc_p2p/__init__.py src/ldc_p2p/__init__.py
COPY src/ldc_p2p/crypto src/ldc_p2p/crypto
COPY src/ldc_p2p/network src/ldc_p2p/network
COPY src/ldc_p2p/server src/ldc_p2p/server
ENV PYTHONPATH=/app/src
USER ldc
EXPOSE 9443
VOLUME /data
CMD ["python", "-m", "ldc_p2p.server", "--host", "0.0.0.0", "--port", "9443", "--db", "/data/relay.db", "--tls-cert", "/certs/fullchain.pem", "--tls-key", "/certs/privkey.pem"]

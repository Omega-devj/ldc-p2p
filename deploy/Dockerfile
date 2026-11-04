# Serveur de rendez-vous / relais LDC P2P — image minimale, sans Qt
FROM python:3.12-slim
RUN useradd -r -u 10001 ldc && mkdir /data && chown ldc /data
WORKDIR /app
RUN pip install --no-cache-dir PyNaCl cryptography
COPY src/ldc_p2p/__init__.py src/ldc_p2p/__init__.py
COPY src/ldc_p2p/crypto src/ldc_p2p/crypto
COPY src/ldc_p2p/network src/ldc_p2p/network
COPY src/ldc_p2p/server src/ldc_p2p/server
ENV PYTHONPATH=/app/src LDC_DB=/data/relay.db LDC_STATUS_PORT=9444 LDC_STATUS_HOST=0.0.0.0
USER ldc
EXPOSE 9443
VOLUME /data
HEALTHCHECK --interval=30s --timeout=4s CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:9444/healthz', timeout=3)" || exit 1
CMD ["python", "-m", "ldc_p2p.server"]

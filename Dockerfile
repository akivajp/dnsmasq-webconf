# Runtime image for dnsmasq-webconf
FROM python:3.12-slim

# Resolve dependencies first to take advantage of the layer cache
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY dnsmasq_webconf ./dnsmasq_webconf
RUN pip install --no-cache-dir ".[server]"

# Intended to run as the host-side uid, since it rewrites the config file
# (e.g. docker run --user "$(id -u):$(id -g)")
EXPOSE 8080

# Default to 0.0.0.0, since it must be reachable from outside the container.
# Authentication is required (pass it via the DNSMASQ_WEBCONF_AUTH environment variable).
ENTRYPOINT ["dnsmasq-webconf"]
CMD ["8080", "--host", "0.0.0.0"]

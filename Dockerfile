FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY monitor/ ./monitor/

# Snapshot volume mount point (root-owned, 0755). The monitor runs as root because it
# needs docker.sock + /rootfs (root-equivalent anyway); homeserver compose hardens it
# with cap_drop: ALL + DAC_READ_SEARCH + no-new-privileges. Snapshot files are 0644 so
# the unprivileged garminbot container can read them.
RUN mkdir -p /app/status

CMD ["python", "-m", "monitor.main"]

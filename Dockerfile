# syntax=docker/dockerfile:1

# ---- stage 1: build the Olaf fingerprinter with Zig ----
FROM debian:trixie-slim AS olaf
ARG ZIG_VERSION=0.16.0
RUN apt-get update && apt-get install -y --no-install-recommends \
      git curl ca-certificates xz-utils && rm -rf /var/lib/apt/lists/*
RUN set -eux; \
    case "$(uname -m)" in \
      x86_64)  ZARCH=x86_64 ;; \
      aarch64) ZARCH=aarch64 ;; \
      armv7l)  ZARCH=arm ;; \
      *) echo "unsupported arch $(uname -m)"; exit 1 ;; \
    esac; \
    curl -fsSL "https://ziglang.org/download/${ZIG_VERSION}/zig-${ZARCH}-linux-${ZIG_VERSION}.tar.xz" \
      | tar -xJ -C /opt; \
    ln -s "/opt/zig-${ZARCH}-linux-${ZIG_VERSION}/zig" /usr/local/bin/zig
RUN git clone --depth 1 https://github.com/JorenSix/Olaf /src/Olaf
WORKDIR /src/Olaf
RUN zig build -Doptimize=ReleaseFast && cp zig-out/bin/olaf /usr/local/bin/olaf

# ---- stage 2: build the React frontend ----
FROM node:22-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---- stage 3: slim Python runtime (no audio capture) ----
FROM python:3.13-slim-trixie
RUN apt-get update && apt-get install -y --no-install-recommends \
      ffmpeg libsndfile1 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
RUN pip install --no-cache-dir \
      "fastapi>=0.110" "uvicorn[standard]>=0.29" "httpx>=0.27" \
      "PyYAML>=6.0" "numpy>=1.24" "soundfile>=0.12"
COPY --from=olaf /usr/local/bin/olaf /usr/local/bin/olaf
COPY backend/ ./backend/
COPY --from=web /web/dist ./frontend/dist
ENV DATA_DIR=/data \
    FRONTEND_DIST=/app/frontend/dist \
    PYTHONUNBUFFERED=1
EXPOSE 8080
CMD ["uvicorn", "backend.asgi:app", "--host", "0.0.0.0", "--port", "8080"]

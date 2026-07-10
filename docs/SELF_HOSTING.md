# Self-hosting GPTHub Tools

This repository contains the complete public application: the React frontend, the FastAPI job API and workers, plus optional compatible AI services for upscaling and background removal. Production domains, server addresses, certificates, runtime `.env` files, upload data and model binaries are intentionally not part of the repository.

## 1. Base stack

Install Docker Engine with the Compose plugin, then create a runtime configuration outside version control:

```bash
cp .env.example .env
chmod 600 .env
# Replace APP_SECRET with a long random value before exposing the service.
docker compose up -d --build
```

The base stack includes Nginx, FastAPI, Redis, ClamAV and four isolated workers. By default it calls the URLs in `.env.example` for the two AI operations. All image conversion, document conversion and PDF work stays inside this stack.

For a public host, put a TLS reverse proxy in front of the loopback-only gateway (`127.0.0.1:9080`). Do not expose Redis, ClamAV or worker ports.

## 2. Self-host the AI services

The optional `compose.ai.yml` connects two compatible services to the same private Compose network. They do not publish host ports and workers wait for their health checks before starting.

The Real-ESRGAN service requires an NVIDIA GPU, a compatible driver and NVIDIA Container Toolkit. It can run on CPU if `gpus: all` is removed from `compose.ai.yml`, but it will be substantially slower.

Create the model directories. On Linux, make them writable by the unprivileged container account where noted:

```bash
mkdir -p data/ai-models/realesrgan data/ai-models/rembg
sudo chown -R 10001:10001 data/ai-models
```

Download the two Real-ESRGAN weight files from the upstream project. They are not committed to this repository:

```bash
curl --fail --location \
  -o data/ai-models/realesrgan/RealESRGAN_x2plus.pth \
  https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.1/RealESRGAN_x2plus.pth
curl --fail --location \
  -o data/ai-models/realesrgan/RealESRGAN_x4plus.pth \
  https://github.com/xinntao/Real-ESRGAN/releases/download/v0.1.0/RealESRGAN_x4plus.pth
```

Start the complete self-hosted deployment:

```bash
docker compose -f compose.yml -f compose.ai.yml --profile ai up -d --build
curl -fsS http://127.0.0.1:9080/api/health
```

On its first start, the background-removal service downloads the approximately 224 MB `birefnet-general-lite` model into `data/ai-models/rembg`. Keep that directory persistent and out of Git; the health check becomes ready only after this first download completes.

## 3. Run AI services on another private host

Do not expose the AI services to the public internet. Connect the hosts with a private network or VPN and set only these two values in the Tools host's private `.env`:

```dotenv
UPSCALE_URL=http://ai-upscaler.internal:5011/upscale
BACKGROUND_URL=http://ai-background-remover.internal:5010/process
```

If a reverse proxy is required between the hosts, preserve the endpoint contracts:

```nginx
location /upscale {
    proxy_pass http://ai-upscaler.internal:5011;
    client_max_body_size 50m;
}

location /bgr1 {
    proxy_pass http://ai-background-remover.internal:5010/;
    client_max_body_size 50m;
}
```

The trailing slash in the second `proxy_pass` intentionally maps `/bgr1/process` to the background service's `/process` route.

## 4. Operations and updates

- Keep `.env`, model directories, logs, uploaded files and TLS certificates off GitHub.
- Restrict the AI host firewall so that only the Tools host or private overlay can reach it.
- Keep one AI worker per GPU-heavy service instance unless you have measured safe VRAM headroom.
- Build and test a new image before changing the public reverse proxy; retain a previous release for rollback.
- The model weights and their upstream licenses are separate from this AGPL application. Review the upstream terms before redistributing weights.

Upstream model projects: [Real-ESRGAN](https://github.com/xinntao/Real-ESRGAN) and [rembg](https://github.com/danielgatis/rembg).

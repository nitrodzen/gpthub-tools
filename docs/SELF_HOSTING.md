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

The base stack includes Nginx, FastAPI, Redis, ClamAV and four isolated workers. The backend image includes headless LibreOffice Writer and Calc plus fonts used for document and spreadsheet conversion. By default it calls the URLs in `.env.example` for the two AI operations. All image conversion, document and spreadsheet conversion, and PDF work stays inside this stack.

The local-operation workers and cleanup service attach only to the `local-no-egress` Docker network, which is declared with `internal: true`. Redis and ClamAV attach to both networks so the isolated services can reach their queue and scanner dependencies without gaining an internet route. The API, AI workers and optional AI services remain on the existing egress-capable network because configured AI endpoints and model downloads may require outbound access. ClamAV also retains that network for signature updates.

For a public host, put a TLS reverse proxy in front of the loopback-only gateway (`127.0.0.1:9080`). Do not expose Redis, ClamAV or worker ports.

## 2. Self-host the AI services

The optional `compose.ai.yml` connects two compatible services to the same private Compose network. They do not publish host ports and workers wait for their health checks before starting.

The upscaler requires an NVIDIA GPU, a compatible driver and NVIDIA Container Toolkit. CPU execution is opt-in: remove `gpus: all` and set `REQUIRE_CUDA=false`; it will be substantially slower. FP32 is the default and is appropriate for older Pascal GPUs. The service loads every upscale model once at startup.

Create the model directories. On Linux, make them writable by the unprivileged container account where noted:

```bash
mkdir -p data/ai-models/realesrgan data/ai-models/rembg data/ai-cache
sudo chown -R 10001:10001 data/ai-models
sudo chown 10001:10001 data/ai-cache
```

Provision model files using Python 3.11 or newer. The downloader verifies pinned SHA-256 values and skips already verified files. Model binaries are not committed to this repository:

```bash
python3 ai-backends/download_models.py data/ai-models/realesrgan --group upscale
python3 ai-backends/download_models.py data/ai-models/rembg --group cpu
sudo chown -R 10001:10001 data/ai-models
```

Start the complete self-hosted deployment:

```bash
docker compose -f compose.yml -f compose.ai.yml --profile ai up -d --build
curl -fsS http://127.0.0.1:9080/api/health
```

Keep model directories persistent and out of Git. The upscale set is approximately 0.5 GB; CPU models take approximately 2.3 GB. Missing BiRefNet models can also be downloaded by rembg when first selected. Provisioning ahead of time avoids that delay. Full BiRefNet processing can peak near 10 GiB RAM, so the full CPU service has a 12 GiB memory limit with swap disabled. Only the active background session remains in RAM.

### CPU work on the application host, GPU on a separate host

Use `deploy/compose.cpu.yml` independently of the main release. It connects to the existing Tools Docker network and exposes only a loopback port for an optional host proxy. Configure `BACKGROUND_URL=http://gpthub-image-cpu:5010/process` and `ENHANCE_URL=http://gpthub-image-cpu:5010/enhance` in the application's private `.env`, then recreate its API/AI workers. OCR already runs in the isolated local workers and needs no external model service.

```bash
CPU_IMAGE_VERSION=<version> CPU_MODELS_DIR=/absolute/path/to/models \
  docker compose -f deploy/compose.cpu.yml up -d --build
```

For a smaller application host, set `CPU_MEMORY_LIMIT=7g` and `REMOTE_BACKGROUND_URL=http://private-large-memory-host:5010/process`. Fast background removal and 1× enhancement then run locally; quality/portrait requests are forwarded to the larger CPU host. The CPU service unloads the previous operation's model before switching between restoration and background removal. Configure that larger host without `REMOTE_BACKGROUND_URL` to avoid forwarding loops. Do not expose a new public model endpoint just for this arrangement. The existing background endpoint's JSON result contract is preserved.

## 3. Run AI services on another private host

Do not expose the AI services to the public internet. Connect the hosts with a private network or VPN and set only these two values in the Tools host's private `.env`:

```dotenv
UPSCALE_URL=http://ai-upscaler.internal:5011/upscale
BACKGROUND_URL=http://ai-background-remover.internal:5010/process
ENHANCE_URL=http://ai-background-remover.internal:5010/enhance
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

The release helper expects immutable releases under `/opt/gpthub-tools/releases`, persistent state under `/opt/gpthub-tools/shared`, and a server-only `/opt/gpthub-tools/shared/.env`. Run it from the new release directory:

```bash
cd /opt/gpthub-tools/releases/<release-id>
APP_ROOT=/opt/gpthub-tools \
  DEPLOY_BACKEND_IMAGE_BYTES=<measured-image-size-in-bytes> \
  bash deploy/activate-release.sh <release-id>
```

Build and verify the backend image on a trusted workstation first, then record its logical byte size:

```bash
docker build --pull -t gpthub-tools-backend:release-check backend
docker image inspect gpthub-tools-backend:release-check --format '{{.Size}}'
```

`DEPLOY_BACKEND_IMAGE_BYTES` is mandatory. Before building on the server, activation requires free space equal to `max(5 GiB, 2 x measured backend image size + 1 GiB)`. `DEPLOY_MIN_FREE_MIB` can raise this guard for a host-specific reserve, but cannot lower the image-derived requirement:

```bash
DEPLOY_MIN_FREE_MIB=8192 \
  DEPLOY_BACKEND_IMAGE_BYTES=<measured-image-size-in-bytes> \
  APP_ROOT=/opt/gpthub-tools \
  bash deploy/activate-release.sh <release-id>
```

After the new stack passes its health check, the helper atomically records the old `current` target as `previous` and switches `current` to the new release. It then removes only unused, older `gpthub-tools-backend:<release>` and `gpthub-tools-gateway:<release>` image tags. Images for the active and immediately previous releases are retained, and any image still referenced by a container is skipped. The helper never runs a global Docker prune and does not delete BuildKit cache, release directories, logs or shared job/metrics data.

Roll back to a retained release by ID:

```bash
APP_ROOT=/opt/gpthub-tools \
  bash /opt/gpthub-tools/current/deploy/rollback.sh <release-id>
```

Office conversions are CPU-, memory- and temporary-storage-intensive. Keep the default local-worker concurrency at one per container until real workloads show safe headroom, and monitor the worker memory limits and `/tmp` tmpfs. Password-protected and macro-bearing Office inputs are rejected; conversion is best effort and is not a substitute for opening untrusted files in an isolated document-review workflow. See [Word and spreadsheet conversions](OFFICE_CONVERSIONS.md) for formats, options, limits and stable warning/error codes.

Upscale output is limited to 200 million pixels by default through `MAX_UPSCALE_OUTPUT_PIXELS`. The API, workers, browser preflight and optional self-hosted upscaler must use the same value. AI workers run one job at a time with a 4 GiB memory limit so oversized images are rejected before queueing instead of exhausting a worker cgroup.

- Keep `.env`, model directories, logs, uploaded files and TLS certificates off GitHub.
- Restrict the AI host firewall so that only the Tools host or private overlay can reach it.
- Keep Office conversion on the no-egress local workers; do not attach `worker-local-1`, `worker-local-2` or `cleanup` to the egress-capable network.
- Keep one AI worker per GPU-heavy service instance unless you have measured safe VRAM headroom.
- Build and test a new image before changing the public reverse proxy; retain a previous release for rollback.
- The model weights and their upstream licenses are separate from this AGPL application. Review the upstream terms before redistributing weights.

Upstream model projects: [Real-ESRGAN](https://github.com/xinntao/Real-ESRGAN) and [rembg](https://github.com/danielgatis/rembg).

All model credits, new operation options, preview behavior and OCR limitations are documented in [Image Lab](IMAGE_LAB.md).

# Metrics API

`GET /api/metrics` returns privacy-preserving product and operational metrics as JSON. It is intended for a private dashboard and begins collecting data when the metrics release is installed; older Redis job data cannot be reconstructed.

## Access

Set a server-side key in `.env`:

```dotenv
METRICS_API_KEY=replace-with-your-key
METRICS_RETENTION_DAYS=365
```

The production key is never committed. Open the endpoint manually in a browser or retrieve it from a server-side dashboard:

```text
https://tools.gpthub.ru/api/metrics?key=<METRICS_API_KEY>&from=2026-07-01&to=2026-07-07&bucket=day
```

```bash
curl --fail --get 'https://tools.gpthub.ru/api/metrics' \
  --data-urlencode 'key=<METRICS_API_KEY>' \
  --data-urlencode 'from=2026-07-01' \
  --data-urlencode 'to=2026-07-07' \
  --data-urlencode 'bucket=day'
```

The endpoint has no CORS policy for third-party browser applications and sends `Cache-Control: no-store`. Keep the key out of public JavaScript, shared links, and screenshots. A missing or wrong key returns `404`.

## Query parameters

| Parameter | Default | Meaning |
| --- | --- | --- |
| `key` | required | Value of `METRICS_API_KEY`. |
| `from` | seven days before `to` | Inclusive UTC calendar date, `YYYY-MM-DD`. |
| `to` | current UTC date | Inclusive UTC calendar date, `YYYY-MM-DD`. |
| `bucket` | `day` | `hour` for a maximum 31-day period, or `day` for a maximum 365-day period. |

The API stores the last 365 days. Timestamps in `generatedAt` and `series[].start` are UTC.

## Response

`summary` describes the requested period. `operations` has the same aggregate values by operation. `series` is the requested time series and includes per-operation values only when that operation has activity.

- `jobs.accepted` is bucketed by accepted upload time.
- `jobs.succeeded`, `failed`, and `cancelled` are bucketed by terminal time.
- `jobs.rejected` counts requests rejected before they entered the queue.
- `filesAccepted`, `inputBytes`, and `resultBytes` are aggregate numbers only.
- `timing.queueSeconds` and `timing.processingSeconds` provide `count`, `average`, `p50`, and `p95` in seconds.
- `errors` contains stable error codes and aggregate counts.
- `live` is a current snapshot of API health, Redis availability, worker count, expected worker count, and free temporary-storage bytes.

The database deliberately excludes filenames, original files, result paths, IP addresses, capability tokens, and conversion options.

To replace the key later, update `METRICS_API_KEY` in the server `.env` and redeploy or restart the project containers. No source-code change is necessary.

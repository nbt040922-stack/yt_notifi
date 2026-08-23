# ContentOps Local Architecture Design

## Goal

Make automatic YT_NOTIFI jobs and manual LAN recovery jobs enter through separate APIs but converge on one durable Silence Scheduler at `127.0.0.1:8791`, with Qwen `127.0.0.1:8792` owned only by that scheduler.

## Fixed contracts

- YT_NOTIFI: `127.0.0.1:8787`
- Manual LAN API: configured LAN bind, port `8780`
- YTDOWNLOAD: `127.0.0.1:8790`
- Silence Scheduler/ContentOps bridge: `127.0.0.1:8791`
- Qwen Worker: `127.0.0.1:8792`

## Data flow

Automatic flow: YT_NOTIFI creates provenance `AUTO_YT_NOTIFI`, asks YTDOWNLOAD for a finalized local file, then submits that path to the local Silence Scheduler. It never calls the manual LAN API and never calls Qwen.

Manual flow: the LAN API accepts a YouTube URL, creates provenance `MANUAL_LAN`, asks YTDOWNLOAD for the finalized local file, then submits the path to the same Silence Scheduler. The LAN API never calls Qwen directly and exposes a polling status view over the scheduler job.

## Scheduler authority

The scheduler owns one durable FIFO queue and one processing slot. Queue records contain stable job identity, origin, source path, video metadata, state, timestamps, queue position, and failure reason. Restart recovery re-reads the queue and does not re-enqueue a job already terminal or already represented by its stable idempotency key.

## Readiness and failure

Only a verified finalized YTDOWNLOAD path may be submitted. If the scheduler is unavailable or not ready, the caller keeps the job pending/retryable. Qwen readiness is reported by the scheduler; clients do not probe or invoke Qwen.

## Security

Ports `8787`, `8790`, `8791`, and `8792` remain loopback-only. Only `8780` may bind to LAN and it retains its existing authentication/token requirement.

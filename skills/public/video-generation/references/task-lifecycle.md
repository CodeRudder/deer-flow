# Task lifecycle: sidecar record, duplicate check, timeout, cancel, query

Loaded on demand — read this file after a polling timeout, before cancelling,
or when a sidecar `.task.json` needs interpretation.

## Sidecar record

Each run writes `outputs/{name}.task.json` next to the video (provider, task
id, prompt file, parameters, status). The `status` field holds the latest
observed state; a record without a confirmed terminal status (`succeeded` /
`failed` / `cancelled`) may still be in flight.

## Duplicate check before dispatch

If `outputs/{name}.task.json` exists without a confirmed upstream terminal
status, a task for this output may still be in flight — report its task id and
elapsed time instead of submitting a duplicate.

## Local polling timeout

A local polling `timeout` is not an upstream terminal status — the task was
created upstream and may still finish (and was billed). Treat any
non-terminal upstream state (queued / processing / pending / running) as
still active, and report the provider's returned status verbatim. Recovery:
query the task with the read-only `--query` and branch on the result:

- `succeeded` → use the returned video URL directly; do NOT call the
  generation endpoint again. Download to a NEW output path (curl — the
  generation run already ended), verify the file exists and is usable, then
  run the Step 5 delivery checks and iteration exits.
- `failed` → report and offer a fresh plan.
- still active → report and wait; re-query later.

Never auto-resubmit without the user's explicit go-ahead.

## Cancel

`--cancel` cancels only tasks still QUEUED. Running tasks cannot be cancelled;
finished tasks are never touched (the upstream endpoint would delete their
records — the script checks state first and refuses). Cancelling a queued task
is not billed.

```bash
python /mnt/skills/public/video-generation/scripts/generate.py \
  --cancel {task_id} --model MiniMax-H3 \
  --output-file /mnt/user-data/outputs/{name}.mp4
```

`--output-file` is optional; when given it also marks that run's `.task.json`
as cancelled.

## Query

`--query {task_id}` is a read-only status lookup — safe any time, never
cancels, works on every provider. Prints the status plus the video URL on
`succeeded`. `--output-file` in query mode only locates and refreshes that
run's sidecar record — it NEVER downloads the video.

```bash
python /mnt/skills/public/video-generation/scripts/generate.py \
  --query {task_id} --model MiniMax-H3
```

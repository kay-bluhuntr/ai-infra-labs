# Lab 2 · Train a small GPT and watch it in Grafana

**Tracker milestones (Wk 2):**
- Character-level GPT trained on CPU. Evidence: sample output.
- Training loop emits OTel metrics: step time, tokens/sec, loss. Evidence: Grafana panel.

**Time:** about 2 hours.

## What you will build

You run `trainer/train_gpt.py`, a working GPT that you do not need to edit. It trains on the complete works of Shakespeare and reports metrics over OTLP to the collector from Lab 1. Prometheus scrapes those metrics and Grafana graphs them.

```
train_gpt.py ──OTLP gRPC──▶ localhost:4317 ══port-forward══▶ otel-collector ──▶ :8889 ──scrape──▶ Prometheus ──▶ Grafana
```

| Metric in Prometheus | Meaning |
|---|---|
| `train_loss` | How wrong the model's next-character guesses are. Starts around 4.2 and should fall below 2. |
| `train_val_loss` | The same, but on text it never trains on. |
| `train_tokens_per_second` | Throughput. Here 1 token = 1 character. |
| `train_tokens_total` | Characters processed so far. |
| `train_step_duration_seconds_bucket` | A histogram of step times, used for p50 / p95. |

Every series carries a `run_id` label, so you can compare runs side by side.

---

## Step 1 · Python environment

From the repo root:

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows/WSL: same command
pip install -r trainer/requirements.txt
```

PyTorch is a large download (up to ~1 GB), so give it a few minutes.

## Step 2 · Connect the trainer to the collector

In a separate terminal, leave this running:

```bash
kubectl -n monitoring port-forward svc/otel-collector 4317:4317
```

## Step 3 · Train

```bash
python trainer/train_gpt.py
```

Every 100 steps you will see a line like this:

```
INFO run 3f9a1c2b step 300: train loss 2.210, val loss 2.236, 14512 tokens/s
```

On a laptop CPU, 2,000 steps take roughly 5–15 minutes. On an Apple Silicon Mac the script uses the GPU (`mps`) automatically and runs faster. At the end it prints 300 characters of generated "Shakespeare".

**Evidence:** copy the sample text into a file in your repo, for example `trainer/samples.md`.

> Press Ctrl+C to stop early. You will still get a sample.

## Step 4 · Find your metrics in Prometheus

With the Prometheus port-forward from Lab 1 running, open http://localhost:9090 and run each query:

```promql
train_loss
train_tokens_per_second
histogram_quantile(0.95, sum by (le) (rate(train_step_duration_seconds_bucket[1m])))
```

Look at the labels on `train_loss`. Next to `run_id` you will see `model_layers`, `train_batch_size` and `train_device`. The script sets these as OTel *resource attributes*, and the collector turns them into labels (`resource_to_telemetry_conversion` in `values/otel-collector.yaml`).

## Step 5 · Build the panel in Grafana

1. In Grafana, go to **Dashboards → New → New dashboard → Add visualisation** and choose **Prometheus**.
2. Build three panels:
   - **Loss:** queries `train_loss` and `train_val_loss`, with legend `{{run_id}}`.
   - **Tokens per second:** `train_tokens_per_second`.
   - **Step time p50 / p95:** the `histogram_quantile` query from Step 4, once with 0.5 and once with 0.95. Set the unit to *seconds*.
3. Save the dashboard as *Training run (manual)*.

**Evidence:** take a screenshot of the dashboard while a run is in progress.

Lab 4 replaces this hand-made dashboard with one stored in Git. Building it by hand first shows you what the JSON describes.

## Step 6 · Run two experiments and compare

Each run gets a new `run_id`, so both appear on the same graphs.

```bash
python trainer/train_gpt.py --steps 800 --batch-size 64     # bigger batches
python trainer/train_gpt.py --steps 800 --n-layer 8         # twice as many layers
```

Answer these in your notes, because they are good post and interview material:

- Did doubling the batch roughly double tokens/s, or did step time grow too? This is the batching trade-off from the course's roofline lesson, seen on your own machine.
- How much slower is each step with 8 layers? Compute grows roughly in proportion to layers.
- Does the gap between p50 and p95 step time change? A wide gap means uneven steps, usually caused by other work on the machine.

---

## Done when

- [ ] One full run finished and its sample text saved
- [ ] `train_loss` and `train_tokens_per_second` visible in Prometheus
- [ ] Grafana screenshot with loss, tokens/s and step-time panels
- [ ] Two runs compared, with notes written

Mark both Wk 2 milestones **Complete** in the tracker.

## Talking points for interviews

- "My training job doesn't talk to Prometheus directly. It sends OTLP to a collector, so the same code would work with Datadog or Grafana Cloud."
- "I measure step time as a histogram, not an average, because p95 is what exposes stragglers. In distributed training the slowest worker sets the pace."
- "Tokens per second is the number that turns into cost: GPU-hours = tokens ÷ throughput."

## If something goes wrong

| Symptom | Likely cause and fix |
|---|---|
| `StatusCode.UNAVAILABLE` warnings in the trainer | The port-forward isn't running. Start it (Step 2); training continues either way. |
| No `train_*` metrics in Prometheus | Wait 30 s: metrics are exported every 5 s and scraped every 15 s. Then check the `prom-exporter` target is UP. |
| `pip install` fails on torch | Use Python 3.10–3.13 and upgrade pip with `pip install -U pip`. |
| Very slow on Windows | Run it inside WSL2, not native Windows Python. |

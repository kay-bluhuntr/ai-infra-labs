# Lab 3 · Add logs and traces, then link all three signals

**Tracker milestone (Wk 3):** Loki + Tempo added; one training run correlated across metrics, logs and traces.
**Evidence:** screenshot of the linked signals.
**Time:** about 2 hours.

## What you will build

The trainer has been sending traces and logs since Lab 2, but the collector only printed them to its own log. In this lab you install Loki (logs) and Tempo (traces), point the collector at them, and use Grafana to jump between the three signals.

```
                       ┌─ metrics ─▶ Prometheus ─┐
train_gpt.py ─OTLP─▶ collector ─ traces ──▶ Tempo ──────┼─▶ Grafana: log line ⇄ trace ⇄ metrics
                       └─ logs ────▶ Loki ───────┘
```

How the trainer shapes its telemetry:

- **One trace per 100 steps**, named `train_interval`. It contains 100 `train_step` spans and one `evaluate` span. A single trace for a whole run would stay incomplete until the end.
- **One log line per evaluation**, written inside the `evaluate` span. That stamps it with the trace's `trace_id`, which is what lets Grafana link a log line to its trace.

---

## Step 1 · Install Loki and Tempo via Git

```bash
cp labs/apps/loki.yaml labs/apps/tempo.yaml platform/
git add platform/
git commit -m "Lab 3: add Loki and Tempo"
git push
```

Watch Argo CD create both applications, or check from the terminal:

```bash
kubectl -n argocd get applications
kubectl -n monitoring get pods -l 'app.kubernetes.io/name in (loki,tempo)'
```

**Check:** `loki-0` and `tempo-0` are `Running`. In Grafana, under **Connections → Data sources**, click **Loki** then **Test**, and do the same for **Tempo**. Both should now pass.

## Step 2 · Route traces and logs in the collector

Edit `values/otel-collector.yaml` and change two lines in `service.pipelines`:

```yaml
      traces:
        ...
        exporters: [otlp/tempo]        # was [debug]
      logs:
        ...
        exporters: [otlphttp/loki]     # was [debug]
```

Then push:

```bash
git commit -am "Lab 3: send traces to Tempo and logs to Loki"
git push
```

**Check:** once Argo CD has synced, the collector pod restarts with the new config. Check it's clean:

```bash
kubectl -n monitoring logs deploy/otel-collector | grep -iE "error|tempo|loki" | tail
```

There should be no repeating `Exporting failed` lines.

> You only changed *where* telemetry goes, not the trainer code. Re-routing signals without touching applications is the main reason to run a collector.

## Step 3 · Run a training job

Restart the collector port-forward first, because the pod was replaced in Step 2:

```bash
kubectl -n monitoring port-forward svc/otel-collector 4317:4317
```

In another terminal:

```bash
source .venv/bin/activate
python trainer/train_gpt.py --steps 1000
```

Note the `run_id` printed on the first line. You will filter on it.

## Step 4 · Start from a log line

1. In Grafana, open **Explore** and choose the **Loki** data source.
2. Switch to **Code** mode and run:

```logql
{service_name="nanogpt-trainer"} |= "val loss"
```

3. Expand one line. Under **Fields** you will see `trace_id` and `span_id`, and a **View trace** button next to `trace_id`.
4. Click **View trace**. Grafana splits the screen and opens that interval's trace from Tempo.

## Step 5 · Read the trace

In the trace view:

- `train_interval` is the root span and covers 100 steps.
- The 100 `train_step` spans should be roughly the same length. A wide one is a straggler.
- `evaluate` is at the end. Compare its duration with a single step: evaluation runs 20 forward passes and no backward pass.
- Click a span, then **Logs for this span**. This jumps back to the Loki line, closing the loop.

Find slow steps across all runs with TraceQL. On the **Tempo** data source, choose the **TraceQL** tab:

```traceql
{ resource.service.name = "nanogpt-trainer" && name = "train_step" && duration > 100ms }
```

## Step 6 · Add the metrics for the same run

1. With the split view still open, set the left pane to **Prometheus**.
2. Run this query, using your own run ID:

```promql
train_tokens_per_second{run_id="<your run_id>"}
```

3. Click the **link** icon between the panes to sync their time ranges.

You now have metrics, logs and a trace for one run, on one screen and lined up in time.

**Evidence:** take a screenshot of this three-signal view.

## Step 7 · Make a problem and find it

While a run is going, start something CPU-heavy for about a minute, for example a second trainer with `python trainer/train_gpt.py --no-otel`. Then use your three signals to answer:

1. **Metrics:** when did tokens/s drop?
2. **Traces:** which `train_interval` traces got longer? Did every step slow down, or only some?
3. **Logs:** does the val-loss line for that interval show the lower tokens/s?

That is the incident workflow you will use on vLLM in Month 3: metrics tell you *when*, traces tell you *where*, logs tell you *what*.

---

## Done when

- [ ] Loki and Tempo installed by `git push`, and their data sources pass the test
- [ ] Collector routes traces to Tempo and logs to Loki
- [ ] Jumped log → trace → log and found the run's metrics
- [ ] Three-signal screenshot saved
- [ ] Step 7 notes written

Mark the Wk 3 observability milestone **Complete**.

## Talking points for interviews

- "I correlate signals by trace ID. Logs carry it as structured metadata in Loki, and Grafana turns it into a link to Tempo."
- "I chose the trace boundary deliberately: one trace per 100 steps, so traces land while the job is still running."
- "Loki and Tempo use local disk here. In production they'd sit on S3 or Azure Blob, which is a values-file change."

## If something goes wrong

| Symptom | Likely cause and fix |
|---|---|
| Loki data source test fails | Check `kubectl -n monitoring get svc loki`. The data source expects `http://loki.monitoring.svc:3100`. |
| Collector log: `Exporting failed ... tempo` | Tempo isn't ready yet. The collector retries for 5 minutes, so wait for `tempo-0` to be Running. |
| No `View trace` button on log lines | Expand the line and check that `trace_id` is listed under Fields. Only the per-evaluation lines carry one; the first "parameters" line doesn't. |
| Trace says "root span not yet received" | That interval is still running. Pick an earlier one. |
| Collector won't start after the edit | Check the YAML indentation in `values/otel-collector.yaml`, then run `kubectl -n monitoring logs deploy/otel-collector`. |

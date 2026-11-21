# Lab 4 · Dashboards and SLOs as code

**Tracker milestone (Wk 4):** First dashboard as code + SLOs defined in a repo (Sloth).
**Evidence:** repo link showing `dashboards/` and `slos/`.
**Time:** 2–3 hours.

## What you will build

- **Dashboards as code.** Grafana dashboards live as JSON in `dashboards/`. Argo CD turns them into ConfigMaps, and Grafana's sidecar loads them. No more hand-made dashboards that vanish with the cluster.
- **SLOs as code.** One YAML file in `slos/` describes two service level objectives for the telemetry pipeline. Sloth turns it into Prometheus recording rules and multi-window burn-rate alerts. These are the same alerts as in the course's *Observing* lesson.

The SLO is: **"99.5% of the spans and log records the collector sends to Tempo and Loki are delivered."** An observability platform needs an SLO of its own; if it loses data, every other team flies blind.

---

## Part A · Dashboard as code

### Step 1 · Deploy the dashboards folder

```bash
cp labs/apps/dashboards.yaml platform/
git add platform/
git commit -m "Lab 4: dashboards as code"
git push
```

**Check:** in Grafana, open **Dashboards** and search for **Training run**. It has stat tiles, loss, throughput and step-time panels, and a **Run** dropdown that filters by `run_id`. Start a trainer run (Lab 2, steps 2–3) to fill it.

### Step 2 · Learn the edit loop

Dashboards loaded from ConfigMaps are *provisioned*, so Grafana won't let you save changes in the UI. That's deliberate: Git is the source of truth. The loop is:

1. Change the dashboard in the UI. For example, add a panel with `train_tokens_total` shown as a rate.
2. Choose **Export → Export as JSON** and copy it.
3. Paste it over `dashboards/training-run.json`, keeping `"uid": "training-run"`.
4. Commit, push, and watch Argo CD sync. Reload Grafana to see your change.

To add a *new* dashboard, drop its JSON in `dashboards/` and add the file name under `configMapGenerator` in `dashboards/kustomization.yaml`.

Also convert your hand-made *Training run (manual)* dashboard from Lab 2 this way. Then delete the manual one.

## Part B · SLOs as code with Sloth

### Step 3 · Check the metric names first

Collector metric names change between versions, so check them before you trust an SLO built on them. In Prometheus (http://localhost:9090), run:

```promql
{__name__=~"otelcol_exporter_sent_(spans|log_records).*"}
```

You should see `otelcol_exporter_sent_spans_total` with `exporter="otlp/tempo"`, and `otelcol_exporter_sent_log_records_total` with `exporter="otlphttp/loki"`.

- **Names match:** go on to Step 4.
- **They differ** (for example, no `_total`, or a different `exporter` label): edit the queries in `slos/telemetry-pipeline.yaml` to match exactly what you see.

### Step 4 · Install Sloth and the SLOs

```bash
cp labs/apps/sloth.yaml labs/apps/slos.yaml platform/
git add platform/ slos/
git commit -m "Lab 4: SLOs as code with Sloth"
git push
```

**Check** that Sloth generated the rules:

```bash
kubectl -n monitoring get prometheusservicelevels           # telemetry-pipeline
kubectl -n monitoring get prometheusrules telemetry-pipeline  # the rules Sloth generated
kubectl -n monitoring logs deploy/sloth | tail               # no errors
```

> If the `slos` app shows an error about an unknown kind, it synced before Sloth's CRD existed. It retries automatically; give it a minute.

### Step 5 · Read what Sloth made

Open `slos/telemetry-pipeline.yaml` and compare it with what appeared in Prometheus.

1. **Rules:** go to **Status → Rules** and search for `sloth-slo`. One 20-line file became dozens of recording rules, one per time window.
2. **Alerts:** search the **Alerts** page for `TracesNotDelivered` and `LogsNotDelivered`. Each has a `severity=page` and a `severity=ticket` version.
3. **Queries:** with a trainer running, try:

```promql
slo:sli_error:ratio_rate5m{sloth_service="telemetry-pipeline"}     # error ratio, last 5 min
slo:current_burn_rate:ratio{sloth_service="telemetry-pipeline"}    # 1 = spending budget exactly on schedule
slo:period_error_budget_remaining:ratio{sloth_service="telemetry-pipeline"}  # 1 = full budget left
```

Write down what each page alert means in plain English. For example: "page if more than 7.2% of spans fail over the last hour and the last 5 minutes". That's 14.4 × the 0.5% allowance.

### Step 6 · Break it on purpose

Prove the alert works by breaking delivery through Git, the same way a bad deploy would.

1. In `values/otel-collector.yaml`, change the Tempo endpoint to a host that doesn't exist:

```yaml
    otlp/tempo:
      endpoint: tempo-typo.monitoring.svc:4317
```

2. Commit and push, then run the trainer for about 10 minutes.
3. The collector retries for up to 5 minutes before it counts spans as failed. After that, watch three things:
   - `slo:sli_error:ratio_rate5m` climbs towards 1.
   - `slo:current_burn_rate:ratio` jumps to around 200.
   - `TracesNotDelivered` with `severity=page` goes **Pending**, then **Firing**, on the Alerts page.
4. Fix the endpoint, push, and watch the alert resolve.

**Evidence:** take a screenshot of the firing alert. Your post can tell this story: "I broke my own pipeline, and the SLO caught it."

> Why not just `kubectl scale` Tempo to zero? Try it. Argo CD's self-heal puts it back within seconds. Breaking things through Git is the honest test.

---

## Done when

- [ ] *Training run* dashboard deployed from `dashboards/`, with one change made through the export loop
- [ ] Metric names checked; SLO file matches your collector
- [ ] Sloth installed; rules and alerts visible in Prometheus
- [ ] Alert fired on purpose and then resolved
- [ ] Repo pushed with `dashboards/` and `slos/`

Mark the Wk 4 observability milestone **Complete**. Together with Lab 2 (GPT trained) and your memory note and post, this is the Month 1 gate.

## Talking points for interviews

- "Dashboards and SLOs are in Git and deployed by Argo CD, so a new cluster comes up with its monitoring already in place."
- "I use multi-window, multi-burn-rate alerts. A page needs a fast burn over both 1 hour and 5 minutes, so it fires quickly on real problems and ignores brief blips."
- "The observability platform has its own SLO. When I broke Tempo delivery on purpose, it paged within minutes of the retries running out."

## If something goes wrong

| Symptom | Likely cause and fix |
|---|---|
| Dashboard doesn't appear | Check `kubectl -n monitoring get cm -l grafana_dashboard=1`. If it's there, check the Grafana sidecar log: `kubectl -n monitoring logs deploy/kube-prometheus-stack-grafana -c grafana-sc-dashboard`. |
| Sloth rules missing in Prometheus | Prometheus must select every PrometheusRule. Check `ruleSelectorNilUsesHelmValues: false` in `values/kube-prometheus-stack.yaml`. |
| SLO queries show no data | No spans have been sent recently, so 0 ÷ 0 = no data. Run the trainer. |
| Alert never fires | Retries delay failures by about 5 minutes; keep the trainer running for 10. Also check the metric names (Step 3). |

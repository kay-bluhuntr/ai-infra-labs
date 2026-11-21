# Lab 1 · Local observability cluster with Helm and Argo CD

**Tracker milestone (Wk 1):** Local cluster (kind) with kube-prometheus-stack, Grafana and an OTel Collector, deployed via Helm + Argo CD.
**Evidence:** repo link + Grafana screenshot.
**Time:** 2–3 hours, much of it waiting for images to download.

## What you will build

A one-node Kubernetes cluster where Argo CD installs Prometheus, Grafana and an OpenTelemetry Collector from this Git repo. Helm is only used once, by hand, to install Argo CD itself. Every component after that, and every change to one, arrives through a `git push`.

```
GitHub repo ──▶ Argo CD ──▶ root app ──▶ platform/kube-prometheus-stack.yaml ──▶ Prometheus, Grafana, Alertmanager
                                     └─▶ platform/otel-collector.yaml       ──▶ OTel Collector (+ ServiceMonitor)
```

---

## Step 1 · Make the repo yours

1. On GitHub, create a new **public** repository called `ai-infra-labs`, with no README.
2. Unzip this folder, then in a terminal:

```bash
cd ai-infra-labs
./scripts/set-repo.sh <your-github-username>    # rewrites every repoURL to point at your repo
git init -b main
git add .
git commit -m "Lab 1: bootstrap observability platform"
git remote add origin https://github.com/<your-github-username>/ai-infra-labs.git
git push -u origin main
```

**Check:** your repo on GitHub shows `platform/`, `values/` and `bootstrap/`.

## Step 2 · Create the cluster

```bash
kind create cluster --config bootstrap/kind-config.yaml
kubectl get nodes
```

**Check:** one node, `ai-infra-control-plane`, with status `Ready`.

## Step 3 · Install Argo CD with Helm

This is the one manual Helm install.

```bash
helm repo add argo https://argoproj.github.io/argo-helm
helm repo update
helm install argocd argo/argo-cd --version 10.18.2 \
  --namespace argocd --create-namespace \
  -f bootstrap/argocd-values.yaml --wait
```

Get the admin password and open the UI:

```bash
kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d; echo
kubectl -n argocd port-forward svc/argocd-server 8080:80
```

Open http://localhost:8080 and log in as `admin` with that password. It shows no applications yet.

## Step 4 · Hand over to GitOps

```bash
kubectl apply -f bootstrap/root-app.yaml
```

In the Argo CD UI you will see `root` appear, followed by `kube-prometheus-stack` and `otel-collector`. The first sync downloads several images, so allow 5–10 minutes. You can also watch it from the terminal:

```bash
kubectl -n argocd get applications -w
kubectl -n monitoring get pods
```

**Check:** all three applications show `Synced` and `Healthy`, and every pod in `monitoring` is `Running`.

> `otel-collector` may retry a few times at first. Its ServiceMonitor needs a CRD that `kube-prometheus-stack` installs. The retry and sync-wave settings in `platform/otel-collector.yaml` handle this, so leave it alone.

## Step 5 · Open Grafana

```bash
kubectl -n monitoring port-forward svc/kube-prometheus-stack-grafana 3000:80
```

Open http://localhost:3000 and log in as `admin` / `labs-admin`. Then open **Dashboards → Kubernetes / Compute Resources / Cluster**.

**Evidence:** take a screenshot of this dashboard for the tracker.

The **Loki** and **Tempo** data sources will show errors under Connections until Lab 3 installs them. That is expected.

## Step 6 · Confirm Prometheus is scraping the collector

```bash
kubectl -n monitoring port-forward svc/kube-prometheus-stack-prometheus 9090:9090
```

1. Open http://localhost:9090/targets and search for `otel-collector`. You should see **two endpoints, both UP**:
   - `metrics` (:8888) is the collector's health.
   - `prom-exporter` (:8889) is where your app metrics will appear in Lab 2. It is empty for now.
2. On the Query page, type `otelcol_` and browse the autocomplete list. These are the collector's own metrics: received, refused, sent and failed. Lab 4 builds an SLO from them.

## Step 7 · Prove the GitOps loop

Make a change through Git only:

1. Edit `values/kube-prometheus-stack.yaml` and change `retention: 3d` to `retention: 2d`.
2. Commit and push:

```bash
git commit -am "Prometheus retention 2d"
git push
```

3. In Argo CD, click **Refresh** on `kube-prometheus-stack`, or wait up to 3 minutes. It goes `OutOfSync`, then `Synced`.
4. In Prometheus, open **Status → Command-line flags** and search for `retention`. It now reads `2d`.

Now try drift. Delete the Grafana pod with `kubectl -n monitoring delete pod -l app.kubernetes.io/name=grafana` and watch it come back. Then scale something by hand, for example `kubectl -n monitoring scale deploy otel-collector --replicas=0`, and watch Argo CD's self-heal put it back to 1 within seconds. Git is the source of truth, not `kubectl`.

---

## Done when

- [ ] Repo on GitHub with `bootstrap/`, `platform/` and `values/`
- [ ] Argo CD shows three applications, all Synced and Healthy
- [ ] Grafana screenshot saved
- [ ] Collector targets UP in Prometheus
- [ ] One change shipped by `git push` alone

Mark the Wk 1 milestone **Complete** in the tracker and paste the repo link.

## Talking points for interviews

- "Helm installs Argo CD once. After that, the cluster's state is whatever is in Git, and drift is reverted automatically."
- "Apps send OTLP to one collector, so I can change where telemetry goes without touching application code."
- "I hit the CRD ordering problem between the Prometheus Operator and anything that ships a ServiceMonitor. I solved it with sync waves, retries and `SkipDryRunOnMissingResource`."

## If something goes wrong

| Symptom | Likely cause and fix |
|---|---|
| Pods stuck `Pending` | Docker is short of memory. Give Docker Desktop at least 8 GB, then `kubectl -n monitoring get pods` again. |
| Application shows `ComparisonError ... repository not found` | The repo is private, or the URL is wrong. Make it public and check `repoURL` in `platform/*.yaml`. |
| `kube-prometheus-stack` error: `metadata.annotations: Too long` | Server-side apply is off. Check that `ServerSideApply=true` is still in its `syncOptions`. |
| Change pushed but nothing happens | Argo CD polls every 3 minutes. Click **Refresh** on the app, or check you pushed to `main`. |
| `port-forward` drops | It stops when its pod restarts. Run the command again. |

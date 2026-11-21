# AI Infrastructure Labs

Hands-on labs for the 26-week AI infrastructure plan. Each lab gives you numbered steps, the commands to run and the config files. At the end you have the evidence the tracker asks for.

Everything runs on your laptop in a `kind` cluster and is deployed the GitOps way: Argo CD reads this repo and installs whatever is in `platform/`.

## Labs

| Lab | Tracker milestone | Time |
|---|---|---|
| [01 · Local observability cluster](labs/01-local-observability-cluster/README.md) | Wk 1: kind cluster with kube-prometheus-stack, Grafana and an OTel Collector, via Helm + Argo CD | 2–3 h |
| [02 · Training metrics](labs/02-training-metrics/README.md) | Wk 2: character-level GPT trained; training loop emits OTel metrics (step time, tokens/s, loss) | 2 h |
| [03 · Logs and traces](labs/03-logs-and-traces/README.md) | Wk 3: Loki + Tempo; one training run correlated across metrics, logs and traces | 2 h |
| [04 · Dashboards and SLOs as code](labs/04-dashboards-and-slos/README.md) | Wk 4: first dashboard as code + SLOs in a repo (Sloth) | 2–3 h |

Do them in order, because each lab builds on the one before.

## What you need

- **Docker Desktop**, with at least **8 GB memory and 4 CPUs** (Settings → Resources).
- **kind, kubectl, helm, git.** On a Mac: `brew install kind kubectl helm git`. On Windows, use WSL2 with Docker Desktop and run the same commands inside Ubuntu.
- **Python 3.10+** for the trainer (Lab 2 onwards).
- **A GitHub account.** The repo must be **public** so Argo CD can read it without credentials, which also suits a portfolio.

## How it fits together

```
 your laptop                              kind cluster (namespace: monitoring)
 ───────────                              ─────────────────────────────────────
 trainer/train_gpt.py ──OTLP :4317──▶ OTel Collector ──metrics──▶ Prometheus ──▶ Grafana
   (port-forward)                         │          ──traces───▶ Tempo ───────▶   ▲
                                          │          ──logs─────▶ Loki ────────▶   │
                                          └─ self-metrics ──▶ Prometheus ─▶ Sloth SLOs + alerts

 GitHub repo (this one) ──▶ Argo CD ──▶ installs everything listed in platform/
```

## Repo layout

| Path | What it holds |
|---|---|
| `bootstrap/` | The only things installed by hand: kind config, Argo CD values and the root "app of apps". |
| `platform/` | Argo CD Applications. Add a file here and push, and Argo CD installs it. |
| `values/` | Helm values for each component. Change them, push, and Argo CD applies the change. |
| `labs/apps/` | Applications for later labs. Each lab tells you when to copy one into `platform/`. |
| `dashboards/` | Grafana dashboards as JSON, deployed as ConfigMaps (Lab 4). |
| `slos/` | SLO definitions for Sloth (Lab 4). |
| `trainer/` | A small GPT trainer that sends metrics, logs and traces. You run it and don't need to edit it. |

## Handy commands

```bash
# Open the UIs (each in its own terminal tab)
kubectl -n argocd     port-forward svc/argocd-server 8080:80                      # http://localhost:8080
kubectl -n monitoring port-forward svc/kube-prometheus-stack-grafana 3000:80      # http://localhost:3000  admin / labs-admin
kubectl -n monitoring port-forward svc/kube-prometheus-stack-prometheus 9090:9090 # http://localhost:9090
kubectl -n monitoring port-forward svc/otel-collector 4317:4317                   # for the trainer

# What is Argo CD doing?
kubectl -n argocd get applications

# Pause for the day (keeps everything)
docker stop ai-infra-control-plane      # docker start ai-infra-control-plane to resume

# Delete it all
kind delete cluster --name ai-infra
```

## Pinned versions (checked 21 Nov 2026)

| Component | Helm chart | Version |
|---|---|---|
| Argo CD | `argo/argo-cd` | 10.18.2 |
| kube-prometheus-stack | `prometheus-community/kube-prometheus-stack` | 93.6.1 |
| OpenTelemetry Collector | `open-telemetry/opentelemetry-collector` | 0.182.0 |
| Loki | `grafana/loki` | 7.7.0 |
| Tempo | `grafana-community/tempo` | 3.2.1 |
| Sloth | `sloth/sloth` | 0.16.0 |

Grafana moved its community charts (Grafana, Tempo) to `grafana-community.github.io/helm-charts` in 2026. Loki is still published at `grafana.github.io/helm-charts`. To upgrade a component, bump `targetRevision` in its Application file and push. That's a GitOps upgrade, worth a line in your write-up.

## Coming next

Labs for Months 2–5 follow the same pattern but need a rented GPU (or the GPU node pool in Month 4):

- **Month 2:** DCGM exporter plus idle-GPU alerts.
- **Month 3:** vLLM metrics, a TTFT SLO and tracing.
- **Month 4:** GPU Operator, Kueue, KubeRay and the inference gateway.
- **Month 5:** KEDA autoscaling for the capstone.

The Month 1 stack you build here is reused in every one of them.

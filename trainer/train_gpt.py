"""
Character-level GPT trainer that reports its own telemetry.

You do not need to edit this file. Run it, then watch it in Grafana.

    python train_gpt.py                 # trains with metrics, logs and traces
    python train_gpt.py --steps 3000    # train for longer
    python train_gpt.py --no-otel       # plain training, no telemetry

What it does
  * Downloads Tiny Shakespeare (about 1 MB of text).
  * Trains a small GPT (the same design as Karpathy's "Let's build GPT").
  * Sends telemetry over OTLP to an OpenTelemetry Collector on localhost:4317
    (Lab 1 sets up the collector; Lab 2 port-forwards it to your laptop).

Telemetry it sends
  Metrics (names as they appear in Prometheus):
    train_loss                         training loss, updated every step
    train_val_loss                     validation loss, updated at each evaluation
    train_tokens_per_second            throughput over the last step
    train_tokens_total                 tokens processed so far
    train_step_duration_seconds_*      histogram of step times (for p50 / p95)
  Traces (Lab 3): one trace per evaluation interval, with a span per step
    and an "evaluate" span.
  Logs (Lab 3): one log line per evaluation, linked to the trace it happened in.
"""

import argparse
import logging
import os
import time
import urllib.request
import uuid

import torch
import torch.nn as nn
from torch.nn import functional as F

DATA_URL = "https://raw.githubusercontent.com/karpathy/char-rnn/master/data/tinyshakespeare/input.txt"
log = logging.getLogger("trainer")


# --------------------------------------------------------------------------
# Telemetry setup (OpenTelemetry SDK)
# --------------------------------------------------------------------------
class Telemetry:
    """Wraps the OpenTelemetry metric, trace and log providers."""

    def __init__(self, enabled, endpoint, run_id, config):
        self.enabled = enabled
        self.latest = {"loss": None, "val_loss": None, "tps": None}
        if not enabled:
            from contextlib import nullcontext
            self.span = lambda name, **kw: nullcontext()
            return

        from opentelemetry import metrics, trace
        from opentelemetry._logs import set_logger_provider
        from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
        from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.metrics import Observation
        from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
        from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        # Resource attributes describe *what* is sending telemetry.
        # They become labels in Prometheus (service_name, run_id, ...).
        resource = Resource.create({
            "service.name": "nanogpt-trainer",
            "run.id": run_id,
            "model.layers": config["n_layer"],
            "model.embedding": config["n_embd"],
            "train.batch_size": config["batch_size"],
            "train.device": config["device"],
        })

        # Metrics: exported every 5 seconds.
        reader = PeriodicExportingMetricReader(
            OTLPMetricExporter(endpoint=endpoint, insecure=True), export_interval_millis=5000)
        self.meter_provider = MeterProvider(resource=resource, metric_readers=[reader])
        metrics.set_meter_provider(self.meter_provider)
        meter = metrics.get_meter("trainer")

        def gauge(key):
            def callback(_options):
                value = self.latest[key]
                return [] if value is None else [Observation(value)]
            return callback

        meter.create_observable_gauge("train.loss", callbacks=[gauge("loss")], description="Training loss")
        meter.create_observable_gauge("train.val_loss", callbacks=[gauge("val_loss")], description="Validation loss")
        meter.create_observable_gauge("train.tokens_per_second", callbacks=[gauge("tps")], description="Training throughput")
        self.tokens = meter.create_counter("train.tokens", unit="{token}", description="Tokens processed")
        self.step_time = meter.create_histogram(
            "train.step.duration", unit="s", description="Wall-clock time per training step",
            explicit_bucket_boundaries_advisory=[0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5])

        # Traces.
        self.tracer_provider = TracerProvider(resource=resource)
        self.tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint, insecure=True)))
        trace.set_tracer_provider(self.tracer_provider)
        self.tracer = trace.get_tracer("trainer")
        self.span = lambda name, **kw: self.tracer.start_as_current_span(name, **kw)

        # Logs: Python logging records also go to the collector, stamped with
        # the current trace_id and span_id so Grafana can jump between them.
        self.logger_provider = LoggerProvider(resource=resource)
        self.logger_provider.add_log_record_processor(BatchLogRecordProcessor(OTLPLogExporter(endpoint=endpoint, insecure=True)))
        set_logger_provider(self.logger_provider)
        logging.getLogger().addHandler(LoggingHandler(level=logging.INFO, logger_provider=self.logger_provider))

    def record_step(self, loss, seconds, tokens):
        self.latest["loss"] = loss
        self.latest["tps"] = tokens / seconds
        if self.enabled:
            self.tokens.add(tokens)
            self.step_time.record(seconds)

    def shutdown(self):
        if self.enabled:
            self.meter_provider.shutdown()
            self.tracer_provider.shutdown()
            self.logger_provider.shutdown()


# --------------------------------------------------------------------------
# The model: a small GPT (decoder-only transformer)
# --------------------------------------------------------------------------
class Block(nn.Module):
    def __init__(self, n_embd, n_head, dropout):
        super().__init__()
        self.ln1 = nn.LayerNorm(n_embd)
        self.attn = nn.MultiheadAttention(n_embd, n_head, dropout=dropout, batch_first=True)
        self.ln2 = nn.LayerNorm(n_embd)
        self.mlp = nn.Sequential(nn.Linear(n_embd, 4 * n_embd), nn.GELU(), nn.Linear(4 * n_embd, n_embd), nn.Dropout(dropout))

    def forward(self, x, mask):
        h = self.ln1(x)
        x = x + self.attn(h, h, h, attn_mask=mask, need_weights=False)[0]
        return x + self.mlp(self.ln2(x))


class GPT(nn.Module):
    def __init__(self, vocab, block_size, n_embd, n_head, n_layer, dropout):
        super().__init__()
        self.block_size = block_size
        self.tok = nn.Embedding(vocab, n_embd)
        self.pos = nn.Embedding(block_size, n_embd)
        self.blocks = nn.ModuleList([Block(n_embd, n_head, dropout) for _ in range(n_layer)])
        self.ln = nn.LayerNorm(n_embd)
        self.head = nn.Linear(n_embd, vocab)
        mask = torch.triu(torch.ones(block_size, block_size, dtype=torch.bool), diagonal=1)
        self.register_buffer("mask", mask)  # stops each position seeing the future

    def forward(self, idx, targets=None):
        t = idx.shape[1]
        x = self.tok(idx) + self.pos(torch.arange(t, device=idx.device))
        for blk in self.blocks:
            x = blk(x, self.mask[:t, :t])
        logits = self.head(self.ln(x))
        loss = None if targets is None else F.cross_entropy(logits.view(-1, logits.size(-1)), targets.view(-1))
        return logits, loss

    @torch.no_grad()
    def generate(self, idx, n):
        for _ in range(n):
            logits, _ = self(idx[:, -self.block_size:])
            nxt = torch.multinomial(F.softmax(logits[:, -1, :], dim=-1), 1)
            idx = torch.cat([idx, nxt], dim=1)
        return idx


# --------------------------------------------------------------------------
# Training
# --------------------------------------------------------------------------
def load_text(path="input.txt"):
    if not os.path.exists(path):
        print(f"Downloading Tiny Shakespeare to {path} ...")
        urllib.request.urlretrieve(DATA_URL, path)
    with open(path, encoding="utf-8") as f:
        return f.read()


def pick_device():
    if torch.cuda.is_available():
        return "cuda"
    if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
        return "mps"  # Apple Silicon GPU
    return "cpu"


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--steps", type=int, default=2000)
    p.add_argument("--eval-every", type=int, default=100)
    p.add_argument("--batch-size", type=int, default=32)
    p.add_argument("--block-size", type=int, default=128, help="context length in characters")
    p.add_argument("--n-embd", type=int, default=128)
    p.add_argument("--n-head", type=int, default=4)
    p.add_argument("--n-layer", type=int, default=4)
    p.add_argument("--lr", type=float, default=1e-3)
    p.add_argument("--device", default=pick_device())
    p.add_argument("--otlp-endpoint", default=os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317"))
    p.add_argument("--no-otel", action="store_true", help="train without sending telemetry")
    a = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    torch.manual_seed(1337)
    run_id = uuid.uuid4().hex[:8]

    text = load_text()
    chars = sorted(set(text))
    stoi = {c: i for i, c in enumerate(chars)}
    data = torch.tensor([stoi[c] for c in text], dtype=torch.long)
    split = int(0.9 * len(data))
    train_data, val_data = data[:split], data[split:]

    def batch(source):
        ix = torch.randint(len(source) - a.block_size - 1, (a.batch_size,))
        x = torch.stack([source[i:i + a.block_size] for i in ix])
        y = torch.stack([source[i + 1:i + a.block_size + 1] for i in ix])
        return x.to(a.device), y.to(a.device)

    model = GPT(len(chars), a.block_size, a.n_embd, a.n_head, a.n_layer, dropout=0.1).to(a.device)
    opt = torch.optim.AdamW(model.parameters(), lr=a.lr)
    n_params = sum(p.numel() for p in model.parameters())

    tel = Telemetry(not a.no_otel, a.otlp_endpoint, run_id, {
        "n_layer": a.n_layer, "n_embd": a.n_embd, "batch_size": a.batch_size, "device": a.device})
    log.info("run %s: %.2fM parameters on %s, %d steps", run_id, n_params / 1e6, a.device, a.steps)

    @torch.no_grad()
    def evaluate():
        model.eval()
        losses = [model(*batch(val_data))[1].item() for _ in range(20)]
        model.train()
        return sum(losses) / len(losses)

    tokens_per_step = a.batch_size * a.block_size
    try:
        for start in range(0, a.steps, a.eval_every):
            end = min(start + a.eval_every, a.steps)
            # One trace per interval, so traces show up in Tempo while training runs.
            with tel.span("train_interval", attributes={"run.id": run_id, "step.start": start, "step.end": end}):
                for step in range(start, end):
                    with tel.span("train_step", attributes={"step": step}):
                        t0 = time.perf_counter()
                        _, loss = model(*batch(train_data))
                        opt.zero_grad(set_to_none=True)
                        loss.backward()
                        opt.step()
                        loss_value = loss.item()  # .item() waits for the GPU, so timing is honest
                        dt = time.perf_counter() - t0
                    tel.record_step(loss_value, dt, tokens_per_step)
                with tel.span("evaluate"):
                    val = evaluate()
                    tel.latest["val_loss"] = val
                    log.info("run %s step %d: train loss %.3f, val loss %.3f, %.0f tokens/s",
                             run_id, end, loss_value, val, tel.latest["tps"])
    except KeyboardInterrupt:
        log.info("run %s stopped early", run_id)

    model.eval()  # turn off dropout: MPS attention can't run it, and sampling shouldn't use it anyway
    sample = model.generate(torch.zeros((1, 1), dtype=torch.long, device=a.device), 300)[0].tolist()
    print("\n--- sample ---\n" + "".join(chars[i] for i in sample) + "\n--------------")
    tel.shutdown()


if __name__ == "__main__":
    main()

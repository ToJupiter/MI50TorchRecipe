from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from . import bootstrap

bootstrap.configure()

import torch
import torch.nn as nn

from .config import TrainConfig
from .data import (
    DatasetSummary,
    GpuBatchIterator,
    build_gpu_dataset,
    build_ram_dataset,
    make_loader,
)
from .device import (
    describe_device,
    locate_card,
    resolve_device_index,
    resolve_dtype,
    select_device,
    theoretical_peak_tflops,
)
from .model import build_model, count_parameters, forward_flops_per_sample
from .telemetry import GpuMonitor, GpuTelemetry, TelemetrySummary, warn_if_not_boosting

log = logging.getLogger(__name__)


@dataclass(slots=True)
class TrainStats:
    preset: str
    parameters: int
    dataset: DatasetSummary
    batch_size: int
    steps: int
    samples: int
    seconds: float
    samples_per_second: float
    steps_per_second: float
    tflops: float
    peak_tflops: float
    efficiency_pct: float
    final_loss: float
    amp: str
    loss_history: list[float] = field(default_factory=list)
    step_losses: list[tuple[int, float]] = field(default_factory=list)
    telemetry: TelemetrySummary = field(default_factory=TelemetrySummary)
    peak_torch_alloc_gb: float = 0.0
    peak_torch_reserved_gb: float = 0.0


def _build_batches(config: TrainConfig, input_dim: int, device: torch.device):
    if config.data_source == "vram":
        features, targets, summary = build_gpu_dataset(
            input_dim, config.vram_dataset_gb, device, config.seed
        )
        return GpuBatchIterator(features, targets, config.batch_size, config.seed), summary
    if config.data_source == "ram":
        features, targets, summary = build_ram_dataset(input_dim, config.ram_gb, config.seed)
        loader = make_loader(
            features, targets, config.batch_size, config.workers, config.prefetch, device
        )
        return loader, summary
    raise ValueError(f"unknown data_source {config.data_source!r}, expected 'ram' or 'vram'")


def train(config: TrainConfig) -> TrainStats:
    torch.manual_seed(config.seed)
    index = resolve_device_index(config.device_index)
    device = select_device(index)
    device_report = describe_device(index)
    card = locate_card(index)
    telemetry = GpuTelemetry.for_card(card) if card else GpuTelemetry.find(index)
    model = build_model(config.preset, config.input_dim).to(device)
    parameters = count_parameters(model)
    flops_per_sample = forward_flops_per_sample(model)
    input_dim = model.net[0].in_features
    amp_dtype = None if config.amp == "none" else resolve_dtype(config.amp)
    if amp_dtype is torch.bfloat16 and device_report.arch.startswith("gfx906"):
        log.warning("gfx906 has no native bf16 support, training will use a slow emulation path")
    log.info(
        "model preset=%s parameters=%d input_dim=%d flops/sample=%.3f MFLOP",
        config.preset,
        parameters,
        input_dim,
        flops_per_sample / 1e6,
    )
    batches, dataset = _build_batches(config, input_dim, device)
    available_steps = len(batches)
    if available_steps == 0:
        raise RuntimeError("dataset smaller than one batch, increase dataset size or lower batch size")
    total_steps = config.steps or max(1, int(available_steps * config.epochs))
    log.info(
        "training plan: data_source=%s samples=%d batches=%d target_steps=%d batch=%d amp=%s",
        dataset.source,
        dataset.samples,
        available_steps,
        total_steps,
        config.batch_size,
        config.amp,
    )
    optimizer = torch.optim.AdamW(model.parameters(), lr=config.lr)
    criterion = nn.MSELoss()
    scaler = torch.amp.GradScaler("cuda", enabled=amp_dtype is torch.float16)
    torch.cuda.reset_peak_memory_stats()
    monitor = GpuMonitor(telemetry)
    monitor.start()
    started = time.perf_counter()
    step = 0
    samples = 0
    loss_total = 0.0
    loss_history: list[float] = []
    step_losses: list[tuple[int, float]] = []
    epoch = 0
    while step < total_steps:
        epoch += 1
        for features, targets in batches:
            features = features.to(device, non_blocking=True)
            targets = targets.to(device, non_blocking=True)
            optimizer.zero_grad(set_to_none=True)
            with torch.amp.autocast("cuda", dtype=amp_dtype, enabled=amp_dtype is not None):
                predictions = model(features)
                loss = criterion(predictions, targets)
            if scaler.is_enabled():
                scaler.scale(loss).backward()
                scaler.step(optimizer)
                scaler.update()
            else:
                loss.backward()
                optimizer.step()
            step += 1
            batch_size = features.shape[0]
            samples += batch_size
            loss_value = loss.item()
            loss_total += loss_value
            loss_history.append(loss_value)
            if step % config.log_every == 0 or step == 1:
                elapsed = time.perf_counter() - started
                throughput = samples / elapsed
                instant_tflops = 3.0 * flops_per_sample * throughput / 1e12
                sample = telemetry.read() if telemetry is not None else None
                log.info(
                    "step %5d loss %.6f | %8.0f samples/s | %.3f TFLOPS | %.1f W | %.0f MHz | %.2f GB vram",
                    step,
                    loss_value,
                    throughput,
                    instant_tflops,
                    sample.power_w if sample and sample.power_w else 0.0,
                    sample.sclk_mhz if sample and sample.sclk_mhz else 0.0,
                    sample.vram_used_gb if sample and sample.vram_used_gb else 0.0,
                )
            if step >= total_steps:
                break
        if step >= total_steps:
            break
    torch.cuda.synchronize()
    seconds = time.perf_counter() - started
    monitor.stop()
    samples_per_second = samples / seconds
    tflops = 3.0 * flops_per_sample * samples_per_second / 1e12
    summary = monitor.summary()
    warn_if_not_boosting(summary, device_report.max_clock_mhz, device_report.card)
    for step_index in range(0, step, max(1, step // 10)):
        step_losses.append((step_index + 1, loss_history[step_index]))
    peak_tflops = theoretical_peak_tflops(
        config.amp if amp_dtype is not None else "fp32",
        device_report.max_clock_mhz,
        device_report.compute_units,
    )
    stats = TrainStats(
        preset=config.preset,
        parameters=parameters,
        dataset=dataset,
        batch_size=config.batch_size,
        steps=step,
        samples=samples,
        seconds=seconds,
        samples_per_second=samples_per_second,
        steps_per_second=step / seconds,
        tflops=tflops,
        peak_tflops=peak_tflops,
        efficiency_pct=100.0 * tflops / peak_tflops if peak_tflops else 0.0,
        final_loss=loss_total / max(1, step),
        amp=config.amp,
        loss_history=loss_history,
        step_losses=step_losses,
        telemetry=summary,
        peak_torch_alloc_gb=torch.cuda.max_memory_allocated() / 1e9,
        peak_torch_reserved_gb=torch.cuda.max_memory_reserved() / 1e9,
    )
    log.info(
        "training finished: %d steps | %d samples in %.2f s | %.0f samples/s | %.3f TFLOPS (%.1f%% of %.2f peak)",
        step,
        samples,
        seconds,
        samples_per_second,
        tflops,
        stats.efficiency_pct,
        peak_tflops,
    )
    del model, batches
    torch.cuda.empty_cache()
    return stats

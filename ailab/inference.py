from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field

from . import bootstrap

bootstrap.configure()

import torch
import torch.nn as nn
import torch.nn.functional as F

from .config import InferConfig
from .device import (
    DeviceReport,
    describe_device,
    locate_card,
    resolve_device_index,
    resolve_dtype,
    select_device,
    theoretical_peak_tflops,
)
from .model import count_parameters
from .telemetry import GpuMonitor, GpuTelemetry, warn_if_not_boosting

log = logging.getLogger(__name__)


class RMSNorm(nn.Module):
    def __init__(self, dim: int, eps: float = 1e-5):
        super().__init__()
        self.eps = eps
        self.weight = nn.Parameter(torch.ones(dim))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        variance = x.pow(2).mean(-1, keepdim=True)
        return self.weight * x * torch.rsqrt(variance + self.eps)


def build_rope_cache(length: int, head_dim: int, theta: float, device: torch.device):
    inverse_frequency = 1.0 / (
        theta ** (torch.arange(0, head_dim, 2, device=device).float() / head_dim)
    )
    positions = torch.arange(length, device=device).float()
    angles = torch.outer(positions, inverse_frequency)
    return angles.cos(), angles.sin()


def apply_rope(x: torch.Tensor, cos: torch.Tensor, sin: torch.Tensor) -> torch.Tensor:
    even = x[..., ::2]
    odd = x[..., 1::2]
    rotated_even = even * cos - odd * sin
    rotated_odd = even * sin + odd * cos
    return torch.stack((rotated_even, rotated_odd), dim=-1).flatten(-2)


class Attention(nn.Module):
    def __init__(self, config: InferConfig):
        super().__init__()
        self.n_heads = config.n_heads
        self.n_kv_heads = config.n_kv_heads
        self.head_dim = config.dim // config.n_heads
        self.q_proj = nn.Linear(config.dim, self.n_heads * self.head_dim, bias=False)
        self.k_proj = nn.Linear(config.dim, self.n_kv_heads * self.head_dim, bias=False)
        self.v_proj = nn.Linear(config.dim, self.n_kv_heads * self.head_dim, bias=False)
        self.o_proj = nn.Linear(self.n_heads * self.head_dim, config.dim, bias=False)

    def forward(self, x: torch.Tensor, cos: torch.Tensor, sin: torch.Tensor, cache=None):
        batch, length, _ = x.shape
        query = self.q_proj(x).view(batch, length, self.n_heads, self.head_dim).transpose(1, 2)
        key = self.k_proj(x).view(batch, length, self.n_kv_heads, self.head_dim).transpose(1, 2)
        value = self.v_proj(x).view(batch, length, self.n_kv_heads, self.head_dim).transpose(1, 2)
        query = apply_rope(query, cos, sin)
        key = apply_rope(key, cos, sin)
        if cache is not None:
            key = torch.cat((cache[0], key), dim=2)
            value = torch.cat((cache[1], value), dim=2)
        new_cache = (key, value)
        repeats = self.n_heads // self.n_kv_heads
        if repeats > 1:
            key = key.repeat_interleave(repeats, dim=1)
            value = value.repeat_interleave(repeats, dim=1)
        scores = query @ key.transpose(-2, -1) / math.sqrt(self.head_dim)
        if length > 1:
            causal = torch.full(
                (length, length), float("-inf"), device=x.device, dtype=scores.dtype
            ).triu(1)
            scores = scores + causal
        weights = torch.softmax(scores, dim=-1)
        attended = weights @ value
        attended = attended.transpose(1, 2).reshape(batch, length, -1)
        return self.o_proj(attended), new_cache


class FeedForward(nn.Module):
    def __init__(self, config: InferConfig):
        super().__init__()
        self.gate_proj = nn.Linear(config.dim, config.ffn_hidden, bias=False)
        self.up_proj = nn.Linear(config.dim, config.ffn_hidden, bias=False)
        self.down_proj = nn.Linear(config.ffn_hidden, config.dim, bias=False)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.down_proj(F.silu(self.gate_proj(x)) * self.up_proj(x))


class Block(nn.Module):
    def __init__(self, config: InferConfig):
        super().__init__()
        self.attention_norm = RMSNorm(config.dim)
        self.attention = Attention(config)
        self.ffn_norm = RMSNorm(config.dim)
        self.feed_forward = FeedForward(config)

    def forward(self, x: torch.Tensor, cos: torch.Tensor, sin: torch.Tensor, cache=None):
        attended, new_cache = self.attention(self.attention_norm(x), cos, sin, cache)
        x = x + attended
        x = x + self.feed_forward(self.ffn_norm(x))
        return x, new_cache


class TinyLlama(nn.Module):
    def __init__(self, config: InferConfig):
        super().__init__()
        self.config = config
        self.embed_tokens = nn.Embedding(config.vocab_size, config.dim)
        self.layers = nn.ModuleList([Block(config) for _ in range(config.n_layers)])
        self.norm = RMSNorm(config.dim)
        self.lm_head = nn.Linear(config.dim, config.vocab_size, bias=False)
        self.lm_head.weight = self.embed_tokens.weight
        head_dim = config.dim // config.n_heads
        cos, sin = build_rope_cache(config.max_seq, head_dim, config.rope_theta, torch.device("cpu"))
        self.register_buffer("rope_cos", cos[None, None, :, :], persistent=False)
        self.register_buffer("rope_sin", sin[None, None, :, :], persistent=False)

    def forward(self, input_ids: torch.Tensor, caches=None):
        _, length = input_ids.shape
        offset = caches[0][0].shape[2] if caches is not None else 0
        cos = self.rope_cos[:, :, offset : offset + length]
        sin = self.rope_sin[:, :, offset : offset + length]
        hidden = self.embed_tokens(input_ids)
        new_caches = []
        for index, layer in enumerate(self.layers):
            layer_cache = caches[index] if caches is not None else None
            hidden, cache = layer(hidden, cos, sin, layer_cache)
            new_caches.append(cache)
        hidden = self.norm(hidden)
        return self.lm_head(hidden), new_caches


def attention_flops(length: int, config: InferConfig) -> float:
    return 4.0 * config.n_layers * length * length * config.dim


def prefill_flops(length: int, parameters: int, config: InferConfig) -> float:
    return 2.0 * parameters * length + attention_flops(length, config)


def decode_flops(parameters: int, batch: int, context: float, config: InferConfig) -> float:
    return 2.0 * parameters * batch + 4.0 * config.n_layers * context * config.dim * batch


@dataclass(slots=True)
class InferResult:
    stage: str
    dtype: str
    batch: int
    length: int
    time_ms: float
    tokens_per_second: float
    tflops: float
    peak_tflops: float
    efficiency_pct: float
    bandwidth_gbs: float
    power_avg_w: float
    power_peak_w: float
    energy_j: float
    busy_avg_pct: float
    vram_peak_gb: float


@dataclass
class InferReport:
    device: DeviceReport
    parameters: int
    weight_mb: float
    dtype: str
    prefill: list[InferResult] = field(default_factory=list)
    decode: list[InferResult] = field(default_factory=list)
    sample_tokens: list[int] = field(default_factory=list)
    power_cap_w: float = 0.0


def _peak(dtype_name: str, device_report: DeviceReport) -> float:
    return theoretical_peak_tflops(
        dtype_name, device_report.max_clock_mhz, device_report.compute_units
    )


def _measure(telemetry, warmup: int, iterations: int, operation) -> tuple[float, object]:
    with torch.inference_mode():
        for _ in range(warmup):
            operation()
    torch.cuda.synchronize()
    monitor = GpuMonitor(telemetry)
    monitor.start()
    started = time.perf_counter()
    with torch.inference_mode():
        for _ in range(iterations):
            operation()
    torch.cuda.synchronize()
    elapsed = time.perf_counter() - started
    monitor.stop()
    return elapsed / iterations, monitor.summary()


def run_prefill(
    config: InferConfig,
    model: TinyLlama,
    parameters: int,
    device,
    telemetry,
    device_report: DeviceReport,
) -> list[InferResult]:
    results: list[InferResult] = []
    for length in config.prefill_lengths:
        input_ids = torch.randint(0, config.vocab_size, (1, length), device=device)
        flops = prefill_flops(length, parameters, config)

        def operation() -> None:
            model(input_ids)

        seconds, summary = _measure(telemetry, config.warmup, config.iters, operation)
        tflops = flops / seconds / 1e12
        peak = _peak(config.dtype, device_report)
        warn_if_not_boosting(summary, device_report.max_clock_mhz, device_report.card)
        results.append(
            InferResult(
                stage="prefill",
                dtype=config.dtype,
                batch=1,
                length=length,
                time_ms=seconds * 1e3,
                tokens_per_second=length / seconds,
                tflops=tflops,
                peak_tflops=peak,
                efficiency_pct=100.0 * tflops / peak if peak else 0.0,
                bandwidth_gbs=0.0,
                power_avg_w=summary.power_avg_w,
                power_peak_w=summary.power_peak_w,
                energy_j=summary.energy_j,
                busy_avg_pct=summary.busy_avg_pct,
                vram_peak_gb=summary.vram_peak_gb,
            )
        )
        log.info(
            "prefill len %5d | %9.3f ms | %8.1f tokens/s | %7.2f TFLOPS | %5.1f%% peak | %6.1f W | %5.1f%% busy",
            length,
            seconds * 1e3,
            length / seconds,
            tflops,
            results[-1].efficiency_pct,
            summary.power_avg_w,
            summary.busy_avg_pct,
        )
    return results


def run_decode(
    config: InferConfig,
    model: TinyLlama,
    parameters: int,
    device,
    telemetry,
    device_report: DeviceReport,
) -> list[InferResult]:
    results: list[InferResult] = []
    dtype_bytes = resolve_dtype(config.dtype).itemsize
    kv_bytes_per_token = (
        2 * config.n_layers * config.n_kv_heads * (config.dim // config.n_heads) * dtype_bytes
    )
    for batch in config.decode_batches:
        prompt = torch.randint(0, config.vocab_size, (batch, config.decode_prompt), device=device)

        def prefill():
            with torch.inference_mode():
                _, caches = model(prompt)
            return caches

        def decode_from(caches, token, tokens: int) -> None:
            with torch.inference_mode():
                for _ in range(tokens):
                    logits, caches = model(token, caches)
                    token = logits[:, -1].argmax(-1, keepdim=True)

        for _ in range(config.warmup):
            warm_token = torch.randint(0, config.vocab_size, (batch, 1), device=device)
            decode_from(prefill(), warm_token, 8)
        torch.cuda.synchronize()
        caches = prefill()
        token = torch.randint(0, config.vocab_size, (batch, 1), device=device)
        torch.cuda.synchronize()
        monitor = GpuMonitor(telemetry)
        monitor.start()
        started = time.perf_counter()
        decode_from(caches, token, config.decode_tokens)
        torch.cuda.synchronize()
        seconds = time.perf_counter() - started
        monitor.stop()
        summary = monitor.summary()
        context = config.decode_prompt + config.decode_tokens / 2
        flops = decode_flops(parameters, batch, context, config) * config.decode_tokens
        tflops = flops / seconds / config.decode_tokens / 1e12
        moved_bytes = (
            parameters * dtype_bytes + batch * context * kv_bytes_per_token
        ) * config.decode_tokens
        peak = _peak(config.dtype, device_report)
        warn_if_not_boosting(summary, device_report.max_clock_mhz, device_report.card)
        results.append(
            InferResult(
                stage="decode",
                dtype=config.dtype,
                batch=batch,
                length=config.decode_tokens,
                time_ms=seconds * 1e3,
                tokens_per_second=batch * config.decode_tokens / seconds,
                tflops=tflops,
                peak_tflops=peak,
                efficiency_pct=100.0 * tflops / peak if peak else 0.0,
                bandwidth_gbs=moved_bytes / seconds / 1e9,
                power_avg_w=summary.power_avg_w,
                power_peak_w=summary.power_peak_w,
                energy_j=summary.energy_j,
                busy_avg_pct=summary.busy_avg_pct,
                vram_peak_gb=summary.vram_peak_gb,
            )
        )
        log.info(
            "decode batch %3d | %8.3f ms/step | %8.1f tokens/s | %7.3f TFLOPS | %6.1f GB/s | %6.1f W | %5.1f%% busy",
            batch,
            seconds * 1e3 / config.decode_tokens,
            batch * config.decode_tokens / seconds,
            tflops,
            results[-1].bandwidth_gbs,
            summary.power_avg_w,
            summary.busy_avg_pct,
        )
    return results


def sample_generation(config: InferConfig, model: TinyLlama, device) -> list[int]:
    prompt = torch.randint(0, config.vocab_size, (1, 16), device=device)
    generated: list[int] = []
    with torch.inference_mode():
        logits, caches = model(prompt)
        token = logits[:, -1].argmax(-1, keepdim=True)
        for _ in range(16):
            generated.append(int(token.item()))
            logits, caches = model(token, caches)
            token = logits[:, -1].argmax(-1, keepdim=True)
    return generated


def run_inference(config: InferConfig) -> InferReport:
    index = resolve_device_index(config.device_index)
    device = select_device(index)
    device_report = describe_device(index)
    card = locate_card(index)
    telemetry = GpuTelemetry.for_card(card) if card else GpuTelemetry.find(index)
    dtype = resolve_dtype(config.dtype)
    if dtype is torch.bfloat16 and device_report.arch.startswith("gfx906"):
        log.warning("gfx906 has no native bf16 support, use fp16 instead")
    model = TinyLlama(config).to(device=device, dtype=dtype)
    model.eval()
    parameters = count_parameters(model)
    weight_mb = parameters * dtype.itemsize / 1e6
    log.info(
        "inference model: %d parameters (%.1f MB %s) | dim=%d layers=%d heads=%d kv_heads=%d ffn=%d vocab=%d max_seq=%d",
        parameters,
        weight_mb,
        config.dtype,
        config.dim,
        config.n_layers,
        config.n_heads,
        config.n_kv_heads,
        config.ffn_hidden,
        config.vocab_size,
        config.max_seq,
    )
    report = InferReport(
        device=device_report,
        parameters=parameters,
        weight_mb=weight_mb,
        dtype=config.dtype,
        power_cap_w=telemetry.power_cap_w if telemetry and telemetry.power_cap_w else 0.0,
    )
    report.prefill = run_prefill(config, model, parameters, device, telemetry, device_report)
    report.decode = run_decode(config, model, parameters, device, telemetry, device_report)
    report.sample_tokens = sample_generation(config, model, device)
    log.info("greedy sample tokens: %s", report.sample_tokens)
    del model
    torch.cuda.empty_cache()
    return report

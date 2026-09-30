from __future__ import annotations

from dataclasses import dataclass

MODEL_PRESETS: dict[str, tuple[int, int]] = {
    "tiny": (2048, 48),
    "small": (2048, 512),
    "medium": (2048, 1024),
    "large": (4096, 2048),
}

DTYPE_NAMES = ("fp32", "fp16", "bf16")


@dataclass(slots=True)
class BenchConfig:
    sizes: tuple[int, ...] = (4096, 8192, 16384)
    dtypes: tuple[str, ...] = ("fp32", "fp16")
    iters: int = 20
    warmup: int = 5
    conv_batch: int = 32
    conv_channels: int = 64
    conv_out_channels: int = 128
    conv_size: int = 112
    bandwidth_gb: float = 1.0
    bandwidth_iters: int = 20
    bandwidth_seconds: float = 3.0
    sustained_seconds: float = 10.0
    sustained_size: int = 4096
    device_index: int | None = None


@dataclass(slots=True)
class TrainConfig:
    preset: str = "medium"
    input_dim: int = 2048
    data_source: str = "vram"
    ram_gb: float = 8.0
    vram_dataset_gb: float = 4.0
    batch_size: int = 8192
    epochs: float = 1.0
    steps: int = 0
    lr: float = 1e-3
    workers: int = 4
    prefetch: int = 4
    seed: int = 1337
    amp: str = "none"
    log_every: int = 25
    device_index: int | None = None


@dataclass(slots=True)
class InferConfig:
    dtype: str = "fp16"
    vocab_size: int = 16384
    dim: int = 512
    n_layers: int = 6
    n_heads: int = 8
    n_kv_heads: int = 8
    ffn_hidden: int = 1408
    max_seq: int = 1024
    rope_theta: float = 10000.0
    prefill_lengths: tuple[int, ...] = (128, 256, 512, 1024)
    decode_batches: tuple[int, ...] = (1, 8, 32)
    decode_prompt: int = 64
    decode_tokens: int = 64
    iters: int = 10
    warmup: int = 3
    device_index: int | None = None

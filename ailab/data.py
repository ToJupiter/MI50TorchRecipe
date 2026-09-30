from __future__ import annotations

import logging
from dataclasses import dataclass

import torch
from torch.utils.data import DataLoader, Dataset

log = logging.getLogger(__name__)


@dataclass(slots=True)
class DatasetSummary:
    source: str
    samples: int
    features: int
    gb: float


def sample_count(input_dim: int, gb: float, bytes_per_value: int = 4) -> int:
    return max(1, int(gb * 1e9 / bytes_per_value / input_dim))


def _synthesize_targets(
    features: torch.Tensor,
    teacher: torch.Tensor,
    noise_scale: float,
    generator: torch.Generator,
    chunk: int = 65536,
) -> torch.Tensor:
    targets = torch.empty(features.shape[0], 1, dtype=torch.float32, device=features.device)
    for start in range(0, features.shape[0], chunk):
        end = min(start + chunk, features.shape[0])
        block = features[start:end]
        targets[start:end] = torch.sin(block @ teacher)
        targets[start:end] += noise_scale * torch.randn(
            end - start, 1, generator=generator, device=features.device
        )
    return targets


def build_ram_dataset(input_dim: int, gb: float, seed: int = 1337) -> tuple[torch.Tensor, torch.Tensor, DatasetSummary]:
    samples = sample_count(input_dim, gb)
    generator = torch.Generator().manual_seed(seed)
    features = torch.randn(samples, input_dim, generator=generator)
    teacher = torch.randn(input_dim, 1, generator=generator) / input_dim**0.5
    targets = _synthesize_targets(features, teacher, 0.01, generator)
    summary = DatasetSummary("host-ram", samples, input_dim, features.numel() * 4 / 1e9)
    log.info(
        "built host dataset: %d samples x %d features = %.2f GB fp32 in RAM",
        samples,
        input_dim,
        summary.gb,
    )
    return features, targets, summary


def build_gpu_dataset(
    input_dim: int, gb: float, device: torch.device, seed: int = 1337
) -> tuple[torch.Tensor, torch.Tensor, DatasetSummary]:
    samples = sample_count(input_dim, gb)
    generator = torch.Generator(device=device).manual_seed(seed)
    features = torch.randn(samples, input_dim, generator=generator, device=device)
    teacher = torch.randn(input_dim, 1, generator=generator, device=device) / input_dim**0.5
    targets = _synthesize_targets(features, teacher, 0.01, generator)
    summary = DatasetSummary("device-vram", samples, input_dim, features.numel() * 4 / 1e9)
    log.info(
        "built device dataset: %d samples x %d features = %.2f GB fp32 in VRAM",
        samples,
        input_dim,
        summary.gb,
    )
    return features, targets, summary


class TensorRegression(Dataset):
    def __init__(self, features: torch.Tensor, targets: torch.Tensor):
        if features.shape[0] != targets.shape[0]:
            raise ValueError("features and targets must share the first dimension")
        self.features = features
        self.targets = targets

    def __len__(self) -> int:
        return self.features.shape[0]

    def __getitem__(self, index: int) -> tuple[torch.Tensor, torch.Tensor]:
        return self.features[index], self.targets[index]


def make_loader(
    features: torch.Tensor,
    targets: torch.Tensor,
    batch_size: int,
    workers: int,
    prefetch: int,
    device: torch.device,
) -> DataLoader:
    dataset = TensorRegression(features, targets)
    return DataLoader(
        dataset,
        batch_size=batch_size,
        shuffle=True,
        drop_last=True,
        num_workers=workers,
        pin_memory=device.type == "cuda",
        persistent_workers=workers > 0,
        prefetch_factor=prefetch if workers > 0 else None,
    )


class GpuBatchIterator:
    def __init__(
        self,
        features: torch.Tensor,
        targets: torch.Tensor,
        batch_size: int,
        seed: int = 1337,
    ):
        self.features = features
        self.targets = targets
        self.batch_size = batch_size
        self.generator = torch.Generator(device=features.device).manual_seed(seed)
        self.steps = max(0, features.shape[0] // batch_size)

    def __len__(self) -> int:
        return self.steps

    def __iter__(self):
        order = torch.randperm(self.features.shape[0], device=self.features.device, generator=self.generator)
        for start in range(0, self.steps * self.batch_size, self.batch_size):
            index = order[start : start + self.batch_size]
            yield self.features[index], self.targets[index]

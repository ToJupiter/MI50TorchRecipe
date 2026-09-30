from __future__ import annotations

import argparse
import logging

from ailab import bootstrap

bootstrap_actions = bootstrap.configure()

from ailab.config import BenchConfig, InferConfig, TrainConfig
from ailab.logging_setup import setup_logging
from ailab.report import print_bench, print_devices, print_infer, print_train

log = logging.getLogger("ailab.main")


def parse_int_tuple(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in text.replace(",", " ").split())


def parse_float_tuple(text: str) -> tuple[float, ...]:
    return tuple(float(part) for part in text.replace(",", " ").split())


def parse_str_tuple(text: str) -> tuple[str, ...]:
    return tuple(part for part in text.replace(",", " ").split())


def build_parser() -> argparse.ArgumentParser:
    bench_defaults = BenchConfig()
    train_defaults = TrainConfig()
    infer_defaults = InferConfig()
    parser = argparse.ArgumentParser(
        prog="ailab",
        description="ROCm benchmark, training and inference lab for the Radeon Pro VII (gfx906)",
    )
    parser.add_argument(
        "--device",
        type=int,
        default=None,
        help="torch device index (default: auto-select the gfx906 discrete GPU)",
    )
    parser.add_argument("--debug", action="store_true", help="enable debug logging")
    commands = parser.add_subparsers(dest="command", required=True)

    devices = commands.add_parser("devices", help="list visible ROCm devices and their sysfs mapping")
    devices.set_defaults(func=run_devices)

    watch = commands.add_parser("watch", help="live TFLOPS, power and clock monitor (ctrl-c to stop)")
    watch.add_argument("--size", type=int, default=8192)
    watch.add_argument("--dtype", choices=("fp32", "fp16", "bf16"), default="fp32")
    watch.add_argument("--interval", type=float, default=1.0, help="print interval in seconds")
    watch.add_argument("--seconds", type=float, default=0.0, help="stop after N seconds (0 = run until ctrl-c)")
    watch.set_defaults(func=run_watch)

    bench = commands.add_parser("bench", help="matmul, conv and bandwidth benchmarks")
    bench.add_argument("--sizes", type=parse_int_tuple, default=bench_defaults.sizes)
    bench.add_argument("--dtypes", type=parse_str_tuple, default=bench_defaults.dtypes)
    bench.add_argument("--iters", type=int, default=bench_defaults.iters)
    bench.add_argument("--warmup", type=int, default=bench_defaults.warmup)
    bench.add_argument("--bandwidth-gb", type=float, default=bench_defaults.bandwidth_gb)
    bench.add_argument("--bandwidth-seconds", type=float, default=bench_defaults.bandwidth_seconds)
    bench.add_argument("--sustained-seconds", type=float, default=bench_defaults.sustained_seconds)
    bench.set_defaults(func=run_bench)

    train = commands.add_parser("train", help="train a small MLP on synthetic data")
    train.add_argument("--preset", choices=("tiny", "small", "medium", "large"), default=train_defaults.preset)
    train.add_argument("--data-source", choices=("vram", "ram"), default=train_defaults.data_source)
    train.add_argument("--ram-gb", type=float, default=train_defaults.ram_gb)
    train.add_argument("--vram-gb", type=float, default=train_defaults.vram_dataset_gb)
    train.add_argument("--batch-size", type=int, default=train_defaults.batch_size)
    train.add_argument("--steps", type=int, default=train_defaults.steps)
    train.add_argument("--epochs", type=float, default=train_defaults.epochs)
    train.add_argument("--lr", type=float, default=train_defaults.lr)
    train.add_argument("--workers", type=int, default=train_defaults.workers)
    train.add_argument("--amp", choices=("none", "fp16", "bf16"), default=train_defaults.amp)
    train.add_argument("--log-every", type=int, default=train_defaults.log_every)
    train.set_defaults(func=run_train)

    infer = commands.add_parser("infer", help="prefill and decode benchmark of a llama-style model")
    infer.add_argument("--dtype", choices=("fp32", "fp16", "bf16"), default=infer_defaults.dtype)
    infer.add_argument("--prefill", type=parse_int_tuple, default=infer_defaults.prefill_lengths)
    infer.add_argument("--batch", type=parse_int_tuple, default=infer_defaults.decode_batches)
    infer.add_argument("--decode-tokens", type=int, default=infer_defaults.decode_tokens)
    infer.add_argument("--dim", type=int, default=infer_defaults.dim)
    infer.add_argument("--layers", type=int, default=infer_defaults.n_layers)
    infer.add_argument("--iters", type=int, default=infer_defaults.iters)
    infer.add_argument("--warmup", type=int, default=infer_defaults.warmup)
    infer.set_defaults(func=run_infer)

    everything = commands.add_parser("all", help="run bench, train and infer back to back")
    everything.set_defaults(func=run_all)
    return parser


def run_devices(args: argparse.Namespace) -> None:
    from ailab.device import list_devices

    print_devices(list_devices())


def run_watch(args: argparse.Namespace) -> None:
    from ailab.watch import WatchConfig, run_watch as watch_loop

    watch_loop(
        WatchConfig(
            size=args.size,
            dtype=args.dtype,
            interval=args.interval,
            seconds=args.seconds,
            device_index=args.device,
        )
    )


def run_bench(args: argparse.Namespace) -> None:
    from ailab.benchmark import run_benchmark

    config = BenchConfig(
        sizes=args.sizes,
        dtypes=args.dtypes,
        iters=args.iters,
        warmup=args.warmup,
        bandwidth_gb=args.bandwidth_gb,
        bandwidth_seconds=args.bandwidth_seconds,
        sustained_seconds=args.sustained_seconds,
        device_index=args.device,
    )
    report = run_benchmark(config)
    print_bench(report)


def run_train(args: argparse.Namespace) -> None:
    from ailab.train import train

    config = TrainConfig(
        preset=args.preset,
        data_source=args.data_source,
        ram_gb=args.ram_gb,
        vram_dataset_gb=args.vram_gb,
        batch_size=args.batch_size,
        steps=args.steps,
        epochs=args.epochs,
        lr=args.lr,
        workers=args.workers,
        amp=args.amp,
        log_every=args.log_every,
        device_index=args.device,
    )
    stats = train(config)
    print_train(stats)


def run_infer(args: argparse.Namespace) -> None:
    from ailab.inference import run_inference

    config = InferConfig(
        dtype=args.dtype,
        dim=args.dim,
        n_layers=args.layers,
        prefill_lengths=args.prefill,
        decode_batches=args.batch,
        decode_tokens=args.decode_tokens,
        iters=args.iters,
        warmup=args.warmup,
        device_index=args.device,
    )
    report = run_inference(config)
    print_infer(report)


def run_all(args: argparse.Namespace) -> None:
    from ailab.benchmark import run_benchmark
    from ailab.inference import run_inference
    from ailab.train import train

    bench_report = run_benchmark(
        BenchConfig(
            sizes=(4096, 8192),
            iters=10,
            warmup=3,
            sustained_seconds=6.0,
            device_index=args.device,
        )
    )
    print_bench(bench_report)
    stats = train(
        TrainConfig(
            preset="medium",
            data_source="vram",
            vram_dataset_gb=4.0,
            batch_size=8192,
            steps=400,
            log_every=100,
            device_index=args.device,
        )
    )
    print_train(stats)
    infer_report = run_inference(InferConfig(device_index=args.device))
    print_infer(infer_report)


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    setup_logging(args.debug)
    if bootstrap_actions:
        log.info("bootstrap applied: %s", "; ".join(bootstrap_actions))
    else:
        log.debug("bootstrap: environment already clean")
    args.func(args)


if __name__ == "__main__":
    main()

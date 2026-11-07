# Boost-recovery documentation

| Order | File | Read it for |
| --- | --- | --- |
| 0 | [`../../BOOST_RECOVERY.md`](../../BOOST_RECOVERY.md) | **The ordered, confirmed recipe** — start here |
| 1 | [`01-ROOT-CAUSE.md`](01-ROOT-CAUSE.md) | Condensed knowledge: the three layers, driver internals, corrections |
| 2 | [`02-REFERENCE.md`](02-REFERENCE.md) | PPSMC codes, reset-method enum, `gpu_metrics` offsets, wrong turns, FAQ, env vars |
| — | [`../README.md`](../README.md) | Original 2026-09/10 investigation log (older; ordering superseded) |

Quick commands (from the repo root):

```bash
gpu-boost/tools/verify-boost.sh                    # no sudo; PASS / FAIL
gpu-boost/tools/mi50-diagnose.sh                  # no sudo; deep diagnosis
sudo gpu-boost/tools/mi50-fix.sh                  # ordered recovery driver
sudo gpu-boost/tools/restore-boost.sh --verify    # the fix step
sudo gpu-boost/systemd/install-guard.sh --now     # boot-time re-arm guard
```

> At idle a healthy Vega20 also reports 859 MHz. Always decide with a **load
> test**, never with an idle clock reading.

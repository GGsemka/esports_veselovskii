"""Measure full guest HTML responses from a running, seeded benchmark instance."""

import argparse
import math
import statistics
from time import perf_counter
from urllib.request import ProxyHandler, build_opener


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8189/")
    args = parser.parse_args()
    opener = build_opener(ProxyHandler({}))
    samples = []
    for i in range(105):
        before = perf_counter()
        with opener.open(args.url, timeout=10) as response:
            html = response.read()
            if response.status != 200 or b"tournament-card" not in html:
                raise RuntimeError("Expected the tournament catalogue")
        if i >= 5:
            samples.append((perf_counter() - before) * 1000)
    samples.sort()
    print(f"GET /, 100 requests, 5 warmups, HTML {len(html)} bytes")
    print(
        f"median {statistics.median(samples):.2f} ms | "
        f"p95 {samples[math.ceil(0.95 * len(samples)) - 1]:.2f} ms | max {max(samples):.2f} ms"
    )
    return int(max(samples) >= 500)


if __name__ == "__main__":
    raise SystemExit(main())

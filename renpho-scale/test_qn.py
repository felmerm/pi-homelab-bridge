"""Bounded QN reception test. Never print or persist measurement values."""
import argparse
import asyncio
import json
import logging
import math

from renpho_escs20m import RenphoQNScale, ScaleData, WEIGHT_KEY, WeightUnit


def report(event, **fields):
    print(json.dumps({"event": event, **fields}), flush=True)


async def run(address, seconds):
    # Upstream debug/error messages can include raw packets and measurements.
    logging.disable(logging.CRITICAL)
    received = asyncio.Event()

    def on_reading(data: ScaleData):
        weight = data.measurements.get(WEIGHT_KEY)
        if isinstance(weight, (int, float)) and math.isfinite(weight) and weight > 0:
            report("weight_received", display_unit="lb", value_withheld=True)
            received.set()

    scale = RenphoQNScale(
        address, on_reading, display_unit=WeightUnit.LB,
        profile=None, clear_stored_measurements=False, max_connect_attempts=1,
    )
    connected_seen = False
    try:
        await scale.async_start()
        report("ready", seconds=seconds, display_unit="lb")
        deadline = asyncio.get_running_loop().time() + seconds
        while asyncio.get_running_loop().time() < deadline and not received.is_set():
            client = scale._client
            if client is not None and client.is_connected and not connected_seen:
                connected_seen = True
                report("bluetooth_connected")
            await asyncio.sleep(0.25)
        if received.is_set():
            # Allow the library's end-measurement command to finish.
            await asyncio.sleep(1)
        report("result", connected=connected_seen, reading_received=received.is_set())
    except Exception as exc:
        # Exception text may contain payloads; expose only its class.
        report("test_error", error_type=type(exc).__name__)
    finally:
        # Pinned 0.8.0 async_stop stops scanning only. Explicitly cancel its
        # tracked work and disconnect the GATT client as well.
        await scale.async_stop()
        tasks = list(scale._bg_tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.wait_for(scale._teardown_client(), timeout=10)
        report("stopped")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--address", default="AA:BB:CC:DD:EE:FF")
    parser.add_argument("--seconds", type=int, default=60)
    args = parser.parse_args()
    if not 1 <= args.seconds <= 60:
        parser.error("seconds must be between 1 and 60")
    asyncio.run(run(args.address, args.seconds))

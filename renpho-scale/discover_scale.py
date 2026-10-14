"""Bounded BLE discovery for the broadcast-only Renpho variant; no GATT writes."""
import argparse
import asyncio
import json
import logging
from pathlib import Path
from bleak import BleakScanner
from renpho_escs20m.detection import is_aabb_frame, is_qn_frame, detect_protocol
from renpho_escs20m.xaabb.protocol import parse_broadcast


async def discover(seconds, state):
    seen = {}
    candidates = set()
    logging.disable(logging.CRITICAL)

    def on_advertisement(device, advertisement):
        name = advertisement.local_name or device.name or ''
        if any(tag in name.lower() for tag in ('qn-scale', 'renpho', 'cs20')) and device.address not in candidates:
            candidates.add(device.address)
            protocol = detect_protocol(name, advertisement.manufacturer_data, device.address)
            print(json.dumps({'event': 'protocol_candidate', 'name': name,
                'address': device.address, 'classifier': protocol.value if protocol else None,
                'manufacturer_entries': [{'company': hex(company), 'length': len(payload),
                    'prefix': payload[:2].hex(), 'qn_frame': is_qn_frame(payload, device.address),
                    'aabb_frame': is_aabb_frame(payload, device.address)}
                    for company, payload in advertisement.manufacturer_data.items()]}), flush=True)
        for company, payload in advertisement.manufacturer_data.items():
            if company != 0xffff or not is_aabb_frame(payload, device.address):
                continue
            if len(payload) < 19:
                continue
            record = seen.setdefault(device.address, {'address': device.address,
                'protocol': 'aabb', 'stable_reading_received': False})
            if not record.get('reported'):
                print(json.dumps({'event': 'scale_found', 'address': device.address,
                    'signal_dbm': advertisement.rssi}), flush=True)
                record['reported'] = True
            reading = parse_broadcast(company, bytearray(payload))
            if reading and not record['stable_reading_received']:
                record['stable_reading_received'] = True
                record['display_unit'] = reading.display_unit.name if reading.display_unit is not None else 'unknown'
                print(json.dumps({'event': 'stable_weight_received',
                    'address': device.address, 'display_unit': record['display_unit']}), flush=True)

    # Active BLE scanning requests advertisements, but never connects or writes
    # a profile/settings to the scale. No packet dumps or weight values logged.
    async with BleakScanner(detection_callback=on_advertisement):
        print('SCAN_READY: wake the scale now; scan duration ' + str(seconds) + ' seconds.', flush=True)
        await asyncio.sleep(seconds)
    if len(seen) == 1:
        record = dict(next(iter(seen.values())))
        record.pop('reported', None)
        if state:
            path = Path(state)
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            path.write_text(json.dumps(record, indent=2) + '\n')
            path.chmod(0o600)
        print('SCAN_COMPLETE: one compatible scale identified.', flush=True)
    elif not seen:
        if candidates:
            print('SCAN_COMPLETE: scale candidate found, but no AABB weight broadcast decoded. Check the observed protocol.', flush=True)
        else:
            print('SCAN_COMPLETE: no scale detected. Wake it during the scan and check proximity.', flush=True)
    else:
        print('SCAN_COMPLETE: multiple compatible scales detected; no target chosen.', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--seconds', type=int, default=45)
    parser.add_argument('--state')
    args = parser.parse_args()
    if not 1 <= args.seconds <= 60:
        parser.error('Scan duration must be between 1 and 60 seconds.')
    asyncio.run(discover(args.seconds, args.state))

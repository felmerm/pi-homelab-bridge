"""Read selected Echo settings without printing account session data."""
import asyncio
from alexa_direct import AlexaDirect


async def main():
    client = AlexaDirect()
    try:
        await client.connect()
        api, device = client.api, client.device
        print("Selected Echo:", device.account_name)
        print("Model:", device.model, "| Online:", device.online)
        for label, action in (
            ("volume", lambda: api._media_handler.device_volumes),
            ("DND", api._dnd_handler.sync_do_not_disturb_status),
        ):
            try:
                result = await action()
                if label == "volume":
                    state = result.get(device.serial_number)
                    print("Volume:", state.volume if state else "unknown",
                          "| Muted:", state.is_muted if state else "unknown")
                else:
                    print("Do not disturb:", api._dnd_handler.dnd_states.get(device.serial_number, "unknown"))
            except Exception as exc:
                print(label, "check unavailable:", type(exc).__name__)
        try:
            preferences = await api._communication_handler.get_communication_preferences([device])
            print("Communication settings:", preferences.get(device.serial_number, {}))
        except Exception as exc:
            print("Settings check unavailable:", type(exc).__name__)
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())

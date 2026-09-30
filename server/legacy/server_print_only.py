import asyncio
import websockets

async def handle_connection(websocket):
    print("\n✅ ESP32 Connected via WebSocket!")
    try:
        async for message in websocket:
            if isinstance(message, str):
                print(f"[ALERT] {message}")
            else:
                print(f"Received {len(message)} bytes of binary data.")
    except websockets.exceptions.ConnectionClosed:
        print("❌ ESP32 Disconnected.")

async def main():
    print("Starting SIH 2026 WebSocket Server on port 8765...")
    # Disabled ping_interval and ping_timeout so the AI math doesn't cause a disconnect
    async with websockets.serve(handle_connection, "0.0.0.0", 8765, ping_interval=None, ping_timeout=None):
        await asyncio.Future()

if __name__ == "__main__":
    asyncio.run(main())
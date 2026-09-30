import asyncio
import websockets
import time

async def receive_audio(websocket):
    print("ESP32 Connected! Waiting for audio stream...")
    try:
        async for message in websocket:
            # message contains the binary audio data
            packet_size = len(message)
            print(f"[{time.strftime('%H:%M:%S')}] Received {packet_size} bytes of audio data!", end='\r')
    except websockets.exceptions.ConnectionClosed:
        print("\nESP32 Disconnected.")

async def main():
    # 0.0.0.0 allows it to accept connections from your local Wi-Fi network
    async with websockets.serve(receive_audio, "0.0.0.0", 8765):
        print("WebSocket Server running on port 8765...")
        await asyncio.Future()  # Run forever

if __name__ == "__main__":
    asyncio.run(main())
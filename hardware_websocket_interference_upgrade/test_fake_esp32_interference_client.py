"""
test_fake_esp32_interference_client.py

하드웨어 없이 Raspberry Pi WebSocket 서버를 테스트하는 가짜 ESP32 클라이언트입니다.

실행:
1. python rpi_websocket_destructive_interference_server.py
2. python test_fake_esp32_interference_client.py
"""

import asyncio
import numpy as np
import websockets

SERVER_URI = "ws://127.0.0.1:8765"
FS = 1000
FRAME_SIZE = 128

async def main():
    async with websockets.connect(SERVER_URI) as websocket:
        print("Connected to WebSocket server")

        index = 0

        while True:
            n = np.arange(FRAME_SIZE)
            freq = 120.0
            signal = 9000 * np.sin(2 * np.pi * freq * (index + n) / FS)
            frame = signal.astype(np.int16)

            await websocket.send(frame.tobytes())

            response = await websocket.recv()
            anti = np.frombuffer(response, dtype=np.int16)

            in_rms = np.sqrt(np.mean(frame.astype(float) ** 2))
            out_rms = np.sqrt(np.mean(anti.astype(float) ** 2))

            print(f"sent RMS={in_rms:.1f} | anti RMS={out_rms:.1f}")

            index += FRAME_SIZE
            await asyncio.sleep(FRAME_SIZE / FS)

asyncio.run(main())

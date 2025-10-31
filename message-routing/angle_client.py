import asyncio
import websockets
import json
import random

async def send_voice_data():
    uri = "ws://localhost:8765/voice"
    async with websockets.connect(uri) as websocket:
        print("已连接到服务器 /voice")
        try:
            while True:
                # 模拟一个随机的角度值
                angle = random.randint(0, 360)
                message = json.dumps({"angle": angle})
                await websocket.send(message)
                print(f"发送角度信息: {angle}")
                await asyncio.sleep(1) # 每秒发送一次
        except websockets.exceptions.ConnectionClosed:
            print("与服务器的连接已关闭")

if __name__ == "__main__":
    asyncio.run(send_voice_data())


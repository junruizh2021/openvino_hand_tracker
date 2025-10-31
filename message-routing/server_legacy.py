import asyncio
import json
import websockets
from urllib.parse import urlparse

# 存储所有已连接的客户端
video_clients = set()
voice_clients = set()

async def video_handler(websocket):
    """处理视频客户端的连接和消息"""
    video_clients.add(websocket)
    print("视频客户端已连接")
    try:
        async for message in websocket:
            print(f"收到视频数据块，大小: {len(message)}")
            # 这里可以添加处理视频数据的逻辑，例如保存文件或广播
            # 为了演示，我们将收到的视频数据广播给所有其他视频客户端
            for client in video_clients:
                if client != websocket:
                    await client.send(message)
    except websockets.exceptions.ConnectionClosed:
        print("视频客户端已断开连接")
    finally:
        video_clients.remove(websocket)

async def voice_handler(websocket):
    """处理语音客户端的连接和消息"""
    voice_clients.add(websocket)
    print("语音客户端已连接")
    try:
        async for message in websocket:
            try:
                data = json.loads(message)
                if 'angle' in data:
                    print(f"收到来自语音客户端的角度信息: {data['angle']}")
                    # 将角度信息广播给所有其他语音客户端
                    for client in voice_clients:
                        if client != websocket:
                            await client.send(message)
            except json.JSONDecodeError:
                print("收到无效的JSON格式数据")
    except websockets.exceptions.ConnectionClosed:
        print("语音客户端已断开连接")
    finally:
        voice_clients.remove(websocket)

async def main_handler(websocket, path=None):
    """根据路径路由连接"""
    # 兼容不同版本的websockets库
    if path is None:
        path = getattr(websocket, 'path', '/')
    
    parsed_path = urlparse(path)
    if parsed_path.path == "/video":
        await video_handler(websocket)
    elif parsed_path.path == "/voice":
        await voice_handler(websocket)
    else:
        print(f"收到未知路径的连接: {path}")
        await websocket.close(code=4000, reason="Invalid path")

async def main():
    async with websockets.serve(main_handler, "localhost", 8765):
        print("服务器已启动，监听 localhost:8765")
        await asyncio.Future()  # 永远运行

if __name__ == "__main__":
    asyncio.run(main())


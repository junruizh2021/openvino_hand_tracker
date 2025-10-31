import asyncio
import json
import websockets
import time
import base64

async def video_client():
    """视频客户端 - 发送视频帧数据"""
    uri = "ws://localhost:8765"
    
    async with websockets.connect(uri) as websocket:
        print("📹 视频客户端已连接到服务器")
        
        try:
            # 监听服务器消息
            async def listen_for_messages():
                async for message in websocket:
                    try:
                        data = json.loads(message)
                        if data.get('type') == 'system':
                            print(f"📢 系统消息: {data.get('message')}")
                        elif data.get('type') == 'video':
                            print(f"📺 收到视频数据: {data.get('data')} (来自客户端 {data.get('from_client')})")
                        elif data.get('type') == 'error':
                            print(f"❌ 错误: {data.get('message')}")
                    except json.JSONDecodeError:
                        print(f"📦 收到二进制数据，大小: {len(message)}")
            
            # 启动消息监听任务
            listen_task = asyncio.create_task(listen_for_messages())
            
            # 发送视频数据
            for i in range(10):
                # 模拟视频帧数据
                frame_data = f"frame_{i:03d}_timestamp_{int(time.time())}"
                
                # 发送 JSON 格式的视频消息
                video_message = {
                    'type': 'video',
                    'data': {
                        'frame_id': i,
                        'timestamp': time.time(),
                        'frame_data': frame_data,
                        'resolution': '1920x1080'
                    },
                    'timestamp': time.time(),
                    'client_name': 'video_client'
                }
                await websocket.send(json.dumps(video_message))
                print(f"📤 发送视频帧: {frame_data}")
                
                # 也可以发送二进制数据
                binary_data = f"binary_frame_{i}".encode('utf-8')
                await websocket.send(binary_data)
                print(f"📤 发送二进制视频数据: {len(binary_data)} bytes")
                
                await asyncio.sleep(3)  # 每3秒发送一次
                
        except websockets.exceptions.ConnectionClosed:
            print("🔌 与服务器的连接已关闭")
        except Exception as e:
            print(f"❌ 发生错误: {e}")
        finally:
            listen_task.cancel()

if __name__ == "__main__":
    asyncio.run(video_client())
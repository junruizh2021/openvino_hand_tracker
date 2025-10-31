import asyncio
import json
import websockets
import time

async def chat_client():
    """聊天客户端 - 发送聊天消息"""
    uri = "ws://localhost:8765"
    
    async with websockets.connect(uri) as websocket:
        print("💬 聊天客户端已连接到服务器")
        
        try:
            # 监听服务器消息
            async def listen_for_messages():
                async for message in websocket:
                    try:
                        data = json.loads(message)
                        if data.get('type') == 'system':
                            print(f"📢 系统消息: {data.get('message')}")
                        elif data.get('type') == 'chat':
                            print(f"💬 收到聊天消息: {data.get('message')} (来自客户端 {data.get('from_client')})")
                        elif data.get('type') == 'pong':
                            print(f"🏓 收到心跳回复: {data.get('timestamp')}")
                        elif data.get('type') == 'error':
                            print(f"❌ 错误: {data.get('message')}")
                    except json.JSONDecodeError:
                        print(f"📦 收到二进制数据，大小: {len(message)}")
            
            # 启动消息监听任务
            listen_task = asyncio.create_task(listen_for_messages())
            
            # 发送聊天消息
            messages = [
                "大家好！",
                "这是一个基于消息类型路由的 WebSocket 示例",
                "所有消息都通过同一个连接发送",
                "服务器会根据消息类型进行不同的处理",
                "这种方式比多连接更高效！"
            ]
            
            for i, message_text in enumerate(messages):
                chat_message = {
                    'type': 'chat',
                    'message': message_text,
                    'timestamp': time.time(),
                    'client_name': 'chat_client'
                }
                await websocket.send(json.dumps(chat_message))
                print(f"📤 发送聊天消息: {message_text}")
                
                await asyncio.sleep(2)
                
                # 发送心跳
                ping_message = {
                    'type': 'ping',
                    'timestamp': time.time(),
                    'client_name': 'chat_client'
                }
                await websocket.send(json.dumps(ping_message))
                print(f"📤 发送心跳: ping")
                
                await asyncio.sleep(1)
                
        except websockets.exceptions.ConnectionClosed:
            print("🔌 与服务器的连接已关闭")
        except Exception as e:
            print(f"❌ 发生错误: {e}")
        finally:
            listen_task.cancel()

if __name__ == "__main__":
    asyncio.run(chat_client())

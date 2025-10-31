import asyncio
import json
import websockets
import time
from typing import Dict, Set, Any
from dataclasses import dataclass

# 存储所有已连接的客户端
clients: Set[websockets.WebSocketServerProtocol] = set()

@dataclass
class Message:
    type: str
    data: Any
    timestamp: float
    client_id: str = None

async def validate_message(data: dict) -> bool:
    """验证消息格式"""
    required_fields = ['type']
    return all(field in data for field in required_fields)

async def handle_message(websocket, message):
    """根据消息内容进行路由处理"""
    try:
        # 尝试解析 JSON 消息
        data = json.loads(message)
        
        # 验证消息格式
        if not await validate_message(data):
            await send_error(websocket, "Invalid message format: missing required fields")
            return
            
        message_type = data.get('type', 'unknown')
        timestamp = data.get('timestamp', time.time())
        
        # 创建消息对象
        msg = Message(
            type=message_type,
            data=data.get('data', data),
            timestamp=timestamp,
            client_id=id(websocket)
        )
        
        # 根据消息类型路由
        if message_type == 'video':
            await handle_video_message(websocket, msg)
        elif message_type == 'voice':
            await handle_voice_message(websocket, msg)
        elif message_type == 'chat':
            await handle_chat_message(websocket, msg)
        elif message_type == 'ping':
            await handle_ping_message(websocket, msg)
        else:
            await send_error(websocket, f"Unknown message type: {message_type}")
            
    except json.JSONDecodeError:
        # 如果不是 JSON，可能是二进制数据（如视频帧）
        await handle_binary_message(websocket, message)
    except Exception as e:
        print(f"处理消息时出错: {e}")
        await send_error(websocket, f"Internal server error: {str(e)}")

async def send_error(websocket, error_message: str):
    """发送错误消息"""
    error_response = {
        'type': 'error',
        'message': error_message,
        'timestamp': time.time()
    }
    await websocket.send(json.dumps(error_response))

async def handle_video_message(websocket, msg: Message):
    """处理视频消息"""
    print(f"收到视频消息: {msg.data}")
    # 广播给所有其他客户端
    response = {
        'type': 'video',
        'data': msg.data,
        'timestamp': msg.timestamp,
        'from_client': msg.client_id
    }
    await broadcast_to_others(websocket, response)

async def handle_voice_message(websocket, msg: Message):
    """处理语音消息"""
    angle = msg.data.get('angle') if isinstance(msg.data, dict) else msg.data
    print(f"收到语音角度信息: {angle}")
    # 广播给所有其他客户端
    response = {
        'type': 'voice',
        'angle': angle,
        'timestamp': msg.timestamp,
        'from_client': msg.client_id
    }
    await broadcast_to_others(websocket, response)

async def handle_chat_message(websocket, msg: Message):
    """处理聊天消息"""
    message_text = msg.data.get('message') if isinstance(msg.data, dict) else msg.data
    print(f"收到聊天消息: {message_text}")
    # 广播给所有其他客户端
    response = {
        'type': 'chat',
        'message': message_text,
        'timestamp': msg.timestamp,
        'from_client': msg.client_id
    }
    await broadcast_to_others(websocket, response)

async def handle_ping_message(websocket, msg: Message):
    """处理心跳消息"""
    print(f"收到心跳消息: {msg.data}")
    # 回复 pong
    pong_response = {
        'type': 'pong',
        'timestamp': time.time(),
        'original_timestamp': msg.timestamp
    }
    await websocket.send(json.dumps(pong_response))

async def broadcast_to_others(sender_websocket, message: dict):
    """广播消息给除发送者外的所有客户端"""
    if not clients:
        return
        
    disconnected_clients = set()
    for client in clients:
        if client != sender_websocket:
            try:
                await client.send(json.dumps(message))
            except websockets.exceptions.ConnectionClosed:
                disconnected_clients.add(client)
    
    # 清理断开的连接
    clients.difference_update(disconnected_clients)

async def handle_binary_message(websocket, message):
    """处理二进制消息（如视频帧）"""
    print(f"收到二进制数据，大小: {len(message)}")
    # 广播给所有其他客户端
    disconnected_clients = set()
    for client in clients:
        if client != websocket:
            try:
                await client.send(message)
            except websockets.exceptions.ConnectionClosed:
                disconnected_clients.add(client)
    
    # 清理断开的连接
    clients.difference_update(disconnected_clients)

async def main_handler(websocket):
    """主处理函数 - 单一连接处理所有消息类型"""
    client_id = id(websocket)
    clients.add(websocket)
    print(f"客户端已连接 (ID: {client_id})")
    
    # 发送欢迎消息
    welcome_message = {
        'type': 'system',
        'message': 'Connected to server',
        'client_id': client_id,
        'timestamp': time.time()
    }
    await websocket.send(json.dumps(welcome_message))
    
    try:
        async for message in websocket:
            await handle_message(websocket, message)
    except websockets.exceptions.ConnectionClosed:
        print(f"客户端已断开连接 (ID: {client_id})")
    except Exception as e:
        print(f"处理客户端 {client_id} 时出错: {e}")
    finally:
        clients.discard(websocket)  # 使用 discard 避免 KeyError

async def main():
    async with websockets.serve(main_handler, "localhost", 8765):
        print("🚀 服务器已启动，监听 localhost:8765")
        print("📡 支持的消息类型:")
        print("   - video: 视频数据")
        print("   - voice: 语音/角度数据") 
        print("   - chat: 聊天消息")
        print("   - ping: 心跳检测")
        print("   - 二进制数据: 直接转发")
        print("=" * 50)
        await asyncio.Future()  # 永远运行

if __name__ == "__main__":
    asyncio.run(main())

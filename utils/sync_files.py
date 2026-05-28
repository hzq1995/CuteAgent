import os
import socket
import struct
import json
import argparse
import threading
from datetime import datetime
from pathlib import Path

# 默认配置
BUFFER_SIZE = 4096
DEFAULT_PORT = 7070
DEFAULT_EXTENSIONS = ['.py', '.html', '.css', '.js', '.md']  # 默认同步的文件类型
HANDSHAKE_MSG = b"SYNC_START_V1"
HANDSHAKE_ACK = b"SYNC_ACK_V1"

def send_file(sock, relative_path, absolute_path):
    """发送单个文件"""
    try:
        with open(absolute_path, 'rb') as f:
            content = f.read()
            size = len(content)
            
            # 发送头信息: 路径编码成字节
            path_bytes = relative_path.encode('utf-8')
            header = struct.pack('!I', len(path_bytes)) + path_bytes + struct.pack('!Q', size)
            sock.sendall(header)
            
            # 发送文件内容
            sock.sendall(content)
            print(f"已发送: {relative_path} ({size} 字节)")
    except Exception as e:
        print(f"发送文件 {relative_path} 失败: {e}")

def run_local(host, port, extensions):
    """客户端模式：扫描并传输文件"""
    # 假设项目根目录是 scripts 的上一级
    root_dir = Path(__file__).resolve().parent.parent
    
    files_to_send = []
    for ext in extensions:
        files_to_send.extend(list(root_dir.rglob(f"*{ext}")))
    
    # 排除一些不需要同步的目录，如 .git, __pycache__, venv
    exclude_dirs = {'.git', '__pycache__', 'venv', '.vscode', 'output', 'daily_plots', 'datasets'}
    
    # 过滤文件
    filtered_files = []
    for f in files_to_send:
        # 检查路径中是否包含排除目录
        if any(part in exclude_dirs for part in f.parts):
            continue
        if f.is_file():
            filtered_files.append(f)

    if not filtered_files:
        print("没有找到需要同步的文件。")
        return

    print(f"找到 {len(filtered_files)} 个符合待同步的文件。")
    print(f"尝试连接到服务器 {host}:{port}...")
    
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(10) # 设置10秒超时
        s.connect((host, port))
        
        # 握手协议
        s.sendall(HANDSHAKE_MSG)
        ack = s.recv(len(HANDSHAKE_ACK))
        if ack != HANDSHAKE_ACK:
            print(f"错误: 握手失败。收到无效响应: {ack}")
            return

        print("连接成功，开始传输...")
        
        # 发送文件总数
        s.sendall(struct.pack('!I', len(filtered_files)))
        
        for file_path in filtered_files:
            relative_path = str(file_path.relative_to(root_dir))
            send_file(s, relative_path, file_path)
        
        print(f"\n同步完成，共成功发送 {len(filtered_files)} 个文件。")
    except socket.timeout:
        print(f"错误: 连接服务器 {host}:{port} 超时。")
    except ConnectionRefusedError:
        print(f"错误: 无法连接到服务器 {host}:{port}。请确保服务器已启动后再运行本地模式。")
    except Exception as e:
        print(f"发生错误: {e}")
    finally:
        s.close()

def recv_all(sock, n):
    """助手函数：确保接收指定字节数"""
    data = b''
    while len(data) < n:
        packet = sock.recv(n - len(data))
        if not packet:
            return None
        data += packet
    return data

def handle_client(conn, addr, save_dir):
    """处理单个客户端连接"""
    try:
        print(f"\n来自 {addr} 的新连接")
        # 设置连接超时，防止死连接
        conn.settimeout(60)
        
        # 验证握手
        msg = recv_all(conn, len(HANDSHAKE_MSG))
        if msg != HANDSHAKE_MSG:
            print(f"非法连接尝试: {addr}")
            return
        
        conn.sendall(HANDSHAKE_ACK)
        
        # 接收文件总数
        count_data = recv_all(conn, 4)
        if not count_data:
            return
        num_files = struct.unpack('!I', count_data)[0]
        print(f"准备接收 {num_files} 个文件...")
        
        for i in range(num_files):
            # 接收路径长度
            path_len_data = recv_all(conn, 4)
            if not path_len_data:
                print("连接意外中断: 无法读取路径长度")
                break
            path_len = struct.unpack('!I', path_len_data)[0]
            
            # 接收路径
            path_bytes = recv_all(conn, path_len)
            if not path_bytes:
                print("连接意外中断: 无法读取路径内容")
                break
            relative_path = path_bytes.decode('utf-8')
            
            # 接收文件大小
            size_data = recv_all(conn, 8)
            if not size_data:
                print("连接意外中断: 无法读取文件大小")
                break
            file_size = struct.unpack('!Q', size_data)[0]
            
            # 接收文件内容
            file_content = recv_all(conn, file_size)
            if file_content is None:
                print(f"连接意外中断: 无法读取文件内容 ({relative_path})")
                break
            
            # 保存文件
            target_path = save_dir / relative_path
            target_path.parent.mkdir(parents=True, exist_ok=True)
            with open(target_path, 'wb') as f:
                f.write(file_content)
            
            print(f"[{i+1}/{num_files}] 已保存: {relative_path} ({file_size} 字节)")
        
        print(f"来自 {addr} 的同步任务已完成。当前时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    except socket.timeout:
        print(f"错误: 来自 {addr} 的连接超时。")
    except Exception as e:
        print(f"处理来自 {addr} 的数据时出错: {e}")
    finally:
        conn.close()

def run_server(port):
    """服务端模式：接收并保存文件"""
    host = '0.0.0.0'
    root_dir = Path(__file__).resolve().parent.parent
    save_dir = root_dir / 'CuteAgent'
    
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # 允许端口重用
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    
    # 开启 TCP Keep-Alive，防止长连接被中间网络设备断开
    if hasattr(socket, 'SO_KEEPALIVE'):
        s.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        # Linux 特有设置
        if hasattr(socket, 'TCP_KEEPIDLE'):
            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 60)
            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 10)
            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 5)
    
    try:
        s.bind((host, port))
        s.listen(10)
        print(f"服务器已启动，监听端口 {port}...")
        print(f"文件将保存至: {save_dir}")
        
        while True:
            conn, addr = s.accept()
            # 为每个连接启动一个新线程
            client_thread = threading.Thread(target=handle_client, args=(conn, addr, save_dir))
            client_thread.daemon = True
            client_thread.start()
    except Exception as e:
        print(f"服务器致命错误: {e}")
    finally:
        s.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="文件同步工具")
    subparsers = parser.add_subparsers(dest="mode", help="运行模式")
    
    # 服务器模式参数
    server_parser = subparsers.add_parser("server", help="启动服务器模式")
    server_parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"监听端口，默认 {DEFAULT_PORT}")
    
    # 本地模式参数
    # 42.194.140.20 GuangzhouBro
    # 122.51.119.247 ShanghaiBigBro
    # 110.40.166.228 ShanghaiSmallBro
    # 43.156.4.123 SigaporeBigBro
    # 38.95.74.246 HongKongBro
    # 43.133.7.44 JapanBro
    local_parser = subparsers.add_parser("local", help="启动本地同步模式")
    local_parser.add_argument("--host", default="tenzi.store", help="云服务器 IP 地址")  # 
    local_parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"服务器端口，默认 {DEFAULT_PORT}")
    local_parser.add_argument("--ext", nargs='+', default=DEFAULT_EXTENSIONS, help=f"要同步的文件后缀，默认 {' '.join(DEFAULT_EXTENSIONS)}")
    
    # 处理默认模式：无参数时默认运行 local
    import sys
    if len(sys.argv) == 1:
        args = parser.parse_args(["local"])
    else:
        args = parser.parse_args()
    
    if args.mode == "server":
        run_server(args.port)
    elif args.mode == "local":
        run_local(args.host, args.port, args.ext)
    else:
        parser.print_help()

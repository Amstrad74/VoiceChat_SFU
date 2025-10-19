# Server.py
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
SFU Voice Chat Server
- UDP: audio streaming (port 8889)
- TCP: text chat + room management (port 8888)
- Rooms: users hear only others in the same room
- No audio mixing — pure SFU (Selective Forwarding Unit)
- Console-only mode (GUI optional via server_gui.py)
"""

import socket
import threading
import json
import logging
import sys
from collections import defaultdict, namedtuple

# === Настройка логирования ===
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("SFU_Server")

# === Константы ===
TCP_PORT = 8888
UDP_PORT = 8889
HOST = "0.0.0.0"  # Слушать все интерфейсы (важно для внешних подключений)

# === Структуры данных ===
ClientInfo = namedtuple("ClientInfo", ["name", "tcp_conn", "udp_addr", "room"])

# Глобальные состояния (потокобезопасность через блокировки)
rooms = defaultdict(set)  # room_name -> set of ClientInfo
clients_by_tcp = {}       # tcp_conn -> ClientInfo
clients_by_name = {}      # name -> ClientInfo
udp_to_client = {}        # udp_addr -> ClientInfo

tcp_lock = threading.Lock()
udp_lock = threading.Lock()

# === TCP обработчик для одного клиента ===
def handle_tcp_client(conn, addr):
    try:
        logger.info(f"Новое TCP-подключение от {addr}")
        # Ожидаем первое сообщение: {"type": "join", "user": "...", "room": "..."}
        data = conn.recv(1024).decode("utf-8")
        if not data:
            conn.close()
            return

        msg = json.loads(data)
        if msg.get("type") != "join":
            conn.send('{"error": "Ожидался join"}'.encode('utf-8'))
            conn.close()
            return

        user = msg["user"]
        room = msg.get("room", "general")

        with tcp_lock:
            if user in clients_by_name:
                conn.send(json.dumps({"error": "Имя уже занято"}).encode())
                conn.close()
                return

            # Создаём временный ClientInfo (UDP ещё неизвестен)
            client = ClientInfo(name=user, tcp_conn=conn, udp_addr=None, room=room)
            clients_by_tcp[conn] = client
            clients_by_name[user] = client

        logger.info(f"Пользователь {user} присоединился к комнате {room} (TCP)")

        # Отправляем подтверждение
        conn.send(json.dumps({"status": "joined", "room": room}).encode())

        # Добавим в комнату позже — после получения UDP-адреса
        # Пока что слушаем команды
        while True:
            data = conn.recv(1024)
            if not data:
                break
            try:
                msg = json.loads(data.decode("utf-8"))
                handle_tcp_message(conn, msg)
            except Exception as e:
                logger.error(f"Ошибка обработки TCP от {user}: {e}")
                break

    except Exception as e:
        logger.error(f"TCP-ошибка для {addr}: {e}")
    finally:
        cleanup_client(conn)

# === Обработка TCP-сообщений ===
def handle_tcp_message(conn, msg):
    with tcp_lock:
        if conn not in clients_by_tcp:
            return
        client = clients_by_tcp[conn]
        user = client.name
        room = client.room

    msg_type = msg.get("type")

    if msg_type == "text":
        # Рассылаем текст всем в комнате
        broadcast_text(room, f"{user}: {msg['payload']}", exclude=conn)

    elif msg_type == "list_rooms":
        with tcp_lock:
            room_list = list(rooms.keys())
        conn.send(json.dumps({"type": "room_list", "rooms": room_list}).encode())

    elif msg_type == "list_users":
        with tcp_lock:
            users_in_room = [c.name for c in rooms.get(room, [])]
        conn.send(json.dumps({"type": "user_list", "users": users_in_room}).encode())

    elif msg_type == "leave":
        cleanup_client(conn)

# === Рассылка текста по TCP ===
def broadcast_text(room, text, exclude=None):
    with tcp_lock:
        targets = [c.tcp_conn for c in rooms[room] if c.tcp_conn != exclude and c.tcp_conn]
        for target in targets:
            try:
                target.send(json.dumps({"type": "text", "payload": text}).encode())
            except:
                pass  # клиент отключился

# === UDP-сервер (аудио SFU) ===
def udp_audio_server():
    udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp_sock.bind((HOST, UDP_PORT))
    logger.info(f"UDP-сервер запущен на порту {UDP_PORT}")

    while True:
        try:
            data, addr = udp_sock.recvfrom(4096)  # PCM фрейм

            with udp_lock:
                if addr not in udp_to_client:
                    # Первое UDP-сообщение → регистрируем адрес
                    # Ищем клиента по имени (должен быть уже в TCP)
                    # Для простоты: первые 32 байта — имя в UTF-8 с паддингом
                    raw_name = data[:32].rstrip(b'\x00').decode('utf-8', errors='ignore')
                    if raw_name in clients_by_name:
                        old = clients_by_name[raw_name]
                        new_client = ClientInfo(
                            name=old.name,
                            tcp_conn=old.tcp_conn,
                            udp_addr=addr,
                            room=old.room
                        )
                        # Обновляем все ссылки
                        with tcp_lock:
                            clients_by_tcp[old.tcp_conn] = new_client
                            clients_by_name[raw_name] = new_client
                            rooms[old.room].discard(old)
                            rooms[old.room].add(new_client)
                        udp_to_client[addr] = new_client
                        logger.info(f"UDP-адрес {addr} привязан к {raw_name}")
                        audio_data = data[32:]
                    else:
                        continue  # неизвестный клиент
                else:
                    audio_data = data

                client = udp_to_client[addr]
                room = client.room

                # SFU: пересылаем аудио всем в комнате, кроме отправителя
                for other in rooms[room]:
                    if other.udp_addr and other.udp_addr != addr:
                        try:
                            udp_sock.sendto(audio_data, other.udp_addr)
                        except Exception as e:
                            logger.debug(f"Не удалось отправить UDP в {other.udp_addr}: {e}")

        except Exception as e:
            logger.error(f"Ошибка UDP: {e}")

# === Очистка при отключении клиента ===
def cleanup_client(tcp_conn):
    with tcp_lock:
        if tcp_conn not in clients_by_tcp:
            return
        client = clients_by_tcp[tcp_conn]
        user = client.name
        room = client.room

        # Удаляем из комнаты
        rooms[room].discard(client)
        if not rooms[room]:
            del rooms[room]

        # Удаляем из всех маппингов
        clients_by_tcp.pop(tcp_conn, None)
        clients_by_name.pop(user, None)

        # Закрываем TCP
        try:
            tcp_conn.close()
        except:
            pass

        logger.info(f"Пользователь {user} отключился")

    # Удаляем UDP-связку (асинхронно)
    def remove_udp():
        with udp_lock:
            keys_to_remove = [k for k, v in udp_to_client.items() if v.name == user]
            for k in keys_to_remove:
                udp_to_client.pop(k, None)

    threading.Thread(target=remove_udp, daemon=True).start()

# === Основной запуск ===
def main():
    logger.info("Запуск SFU-сервера...")

    # TCP-сервер
    tcp_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    tcp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    tcp_sock.bind((HOST, TCP_PORT))
    tcp_sock.listen(100)
    logger.info(f"TCP-сервер запущен на порту {TCP_PORT}")

    # Запуск UDP в отдельном потоке
    threading.Thread(target=udp_audio_server, daemon=True).start()

    try:
        while True:
            conn, addr = tcp_sock.accept()
            threading.Thread(target=handle_tcp_client, args=(conn, addr), daemon=True).start()
    except KeyboardInterrupt:
        logger.info("Сервер остановлен пользователем")
    finally:
        tcp_sock.close()

if __name__ == "__main__":
    main()
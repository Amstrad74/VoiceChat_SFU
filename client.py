# client.py (версия 1.1)
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
SFU Voice Chat Client — Версия 1.1
- TCP: text + room management (port 8888)
- UDP: audio streaming (port 8889)
- Audio: 16-bit PCM, 16kHz, mono
- UDP packet format: [32-byte zero-padded UTF-8 name][raw PCM]
- Fully compatible with server.py v1.1
"""

import socket
import threading
import json
import sys
import argparse
import logging
import pyaudio
import time

# === Логирование ===
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("Client")

# === Аудио-параметры ===
CHUNK = 1024
FORMAT = pyaudio.paInt16
CHANNELS = 1
RATE = 16000

# === Глобальные переменные ===
tcp_sock = None
udp_sock = None
audio = None
stream_in = None
stream_out = None
running = True
my_name = ""
my_room = ""
server_ip = "127.0.0.1"
tcp_port = 8888
udp_port = 8889
muted = False

# === Инициализация аудио ===
def init_audio():
    global audio, stream_in, stream_out
    audio = pyaudio.PyAudio()
    stream_in = audio.open(
        format=FORMAT,
        channels=CHANNELS,
        rate=RATE,
        input=True,
        frames_per_buffer=CHUNK
    )
    stream_out = audio.open(
        format=FORMAT,
        channels=CHANNELS,
        rate=RATE,
        output=True,
        frames_per_buffer=CHUNK
    )

# === Завершение работы ===
def cleanup():
    global running
    running = False
    if stream_in:
        stream_in.stop_stream()
        stream_in.close()
    if stream_out:
        stream_out.stop_stream()
        stream_out.close()
    if audio:
        audio.terminate()
    if tcp_sock:
        tcp_sock.close()
    if udp_sock:
        udp_sock.close()
    logger.info("Клиент остановлен")

# === Приём текста по TCP ===
def tcp_receive_loop():
    global running
    while running:
        try:
            data = tcp_sock.recv(1024)
            if not 
                break
            msg = json.loads(data.decode("utf-8"))
            if msg.get("type") == "text":
                print(f"\n[ЧАТ] {msg['payload']}")
            elif msg.get("type") == "room_list":
                print(f"\n[КОМНАТЫ] {', '.join(msg['rooms'])}")
            elif msg.get("type") == "user_list":
                print(f"\n[УЧАСТНИКИ] {', '.join(msg['users'])}")
            elif "error" in msg:
                print(f"\n[ОШИБКА] {msg['error']}")
            elif "status" in msg and msg["status"] == "joined":
                print(f"\n✅ Успешно подключено к комнате '{msg['room']}'")
        except (ConnectionResetError, OSError):
            if running:
                print("\n[СЕРВЕР] Соединение потеряно")
            break
        except Exception as e:
            if running:
                logger.error(f"Ошибка приёма TCP: {e}")
            break
    cleanup()

# === Приём аудио по UDP ===
def udp_receive_loop():
    global running
    udp_local = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp_local.bind(("0.0.0.0", 0))
    while running:
        try:
            data, _ = udp_local.recvfrom(4096)
            if stream_out and not muted:
                stream_out.write(data)
        except Exception as e:
            if running:
                logger.debug(f"Ошибка приёма UDP: {e}")
    udp_local.close()

# === Отправка аудио по UDP ===
def udp_send_loop():
    global running
    while running:
        try:
            if not muted:
                audio_data = stream_in.read(CHUNK, exception_on_overflow=False)
                # Формат: [32-byte имя][аудио]
                name_bytes = my_name.encode("utf-8")[:32]
                padded_name = name_bytes.ljust(32, b"\x00")
                packet = padded_name + audio_data
                udp_sock.sendto(packet, (server_ip, udp_port))
        except Exception as e:
            logger.debug(f"Ошибка отправки аудио: {e}")
        time.sleep(0.001)

# === Обработка пользовательского ввода ===
def handle_user_input():
    print("\nДоступные команды:")
    print("  /list          — список комнат")
    print("  /users         — участники текущей комнаты")
    print("  /mute          — отключить микрофон")
    print("  /unmute        — включить микрофон")
    print("  /exit          — выйти из чата\n")

    while running:
        try:
            user_input = input().strip()
            if not user_input:
                continue

            if user_input == "/exit":
                cleanup()
                break
            elif user_input == "/mute":
                global muted
                muted = True
                print("[МИКРОФОН ВЫКЛЮЧЕН]")
            elif user_input == "/unmute":
                muted = False
                print("[МИКРОФОН ВКЛЮЧЕН]")
            elif user_input == "/list":
                tcp_sock.send(json.dumps({"type": "list_rooms"}).encode("utf-8"))
            elif user_input == "/users":
                tcp_sock.send(json.dumps({"type": "list_users"}).encode("utf-8"))
            else:
                tcp_sock.send(json.dumps({"type": "text", "payload": user_input}).encode("utf-8"))
        except (EOFError, KeyboardInterrupt):
            cleanup()
            break
        except Exception as e:
            logger.error(f"Ошибка ввода: {e}")
            break

# === Основная функция ===
def main():
    global tcp_sock, udp_sock, my_name, my_room, server_ip, tcp_port, udp_port

    parser = argparse.ArgumentParser(description="SFU Voice Chat Client v1.1")
    parser.add_argument("--name", required=True, help="Ваше имя (уникальное)")
    parser.add_argument("--server", default="127.0.0.1", help="IP-адрес сервера")
    parser.add_argument("--tcp-port", type=int, default=8888, help="TCP порт сервера")
    parser.add_argument("--udp-port", type=int, default=8889, help="UDP порт сервера")
    parser.add_argument("--room", default="general", help="Имя комнаты")
    args = parser.parse_args()

    my_name = args.name.strip()
    my_room = args.room
    server_ip = args.server
    tcp_port = args.tcp_port
    udp_port = args.udp_port

    if not my_name:
        print("❌ Ошибка: имя не может быть пустым")
        return

    # Подключение TCP
    tcp_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        tcp_sock.connect((server_ip, tcp_port))
    except Exception as e:
        print(f"❌ Не удалось подключиться к {server_ip}:{tcp_port}: {e}")
        return

    # Отправка join-запроса
    join_msg = {"type": "join", "user": my_name, "room": my_room}
    tcp_sock.send(json.dumps(join_msg).encode("utf-8"))

    # Ожидание ответа
    try:
        response = tcp_sock.recv(1024)
        resp = json.loads(response.decode("utf-8"))
        if "error" in resp:
            print(f"❌ Ошибка сервера: {resp['error']}")
            tcp_sock.close()
            return
    except Exception as e:
        print(f"❌ Некорректный ответ от сервера: {e}")
        tcp_sock.close()
        return

    print(f"📡 Подключение установлено. Сервер: {server_ip}")
    print(f"👤 Имя: {my_name} | 🏠 Комната: {my_room}")

    # Инициализация аудио
    try:
        init_audio()
    except Exception as e:
        print(f"❌ Ошибка инициализации аудио: {e}")
        tcp_sock.close()
        return

    # UDP-сокет для отправки
    udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    # Запуск потоков
    threading.Thread(target=tcp_receive_loop, daemon=True).start()
    threading.Thread(target=udp_receive_loop, daemon=True).start()
    threading.Thread(target=udp_send_loop, daemon=True).start()

    # Основной цикл ввода
    try:
        handle_user_input()
    except KeyboardInterrupt:
        cleanup()

if __name__ == "__main__":
    main()
# Client.py
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
SFU Voice Chat Client (console version)
- TCP: text + room management (port 8888)
- UDP: audio streaming (port 8889)
- Audio: 16-bit PCM, 16kHz, mono
- Name + room sent in first TCP message
- UDP packets: [32-byte zero-padded name][raw PCM]
"""

import socket
import threading
import json
import sys
import argparse
import logging
import pyaudio
import time

# === Настройка логирования ===
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("Client")

# === Константы аудио ===
CHUNK = 1024
FORMAT = pyaudio.paInt16
CHANNELS = 1
RATE = 16000  # 16 kHz
AUDIO_TIMEOUT = 0.1  # сек

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
push_to_talk = False  # False = постоянно передаём, True = только по пробелу (не реализовано в этом варианте)
muted = False

# === Инициализация PyAudio ===
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

# === Поток приёма текста по TCP ===
def tcp_receive_loop():
    global running
    while running:
        try:
            data = tcp_sock.recv(1024)
            if not data:
                break
            msg = json.loads(data.decode("utf-8"))
            if msg["type"] == "text":
                print(f"\n[ЧАТ] {msg['payload']}")
            elif msg["type"] == "room_list":
                print(f"\n[КОМНАТЫ] {', '.join(msg['rooms'])}")
            elif msg["type"] == "user_list":
                print(f"\n[УЧАСТНИКИ] {', '.join(msg['users'])}")
            elif "error" in msg:
                print(f"\n[ОШИБКА] {msg['error']}")
        except Exception as e:
            if running:
                logger.error(f"Ошибка TCP-приёма: {e}")
            break
    cleanup()

# === Поток приёма аудио по UDP ===
def udp_receive_loop():
    global running
    udp_local = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp_local.bind(("0.0.0.0", 0))  # любой свободный порт
    while running:
        try:
            data, _ = udp_local.recvfrom(4096)
            if stream_out and not muted:
                stream_out.write(data)
        except Exception as e:
            if running:
                logger.debug(f"Ошибка UDP-приёма: {e}")
    udp_local.close()

# === Поток отправки аудио по UDP ===
def udp_send_loop():
    global running
    while running:
        try:
            if not muted:
                audio_data = stream_in.read(CHUNK, exception_on_overflow=False)
                # Формируем пакет: [32-byte name][audio]
                name_padded = my_name.encode("utf-8").ljust(32, b"\x00")[:32]
                packet = name_padded + audio_data
                udp_sock.sendto(packet, (server_ip, udp_port))
        except Exception as e:
            logger.debug(f"Ошибка отправки аудио: {e}")
        time.sleep(0.001)  # ~1ms — достаточно для 16kHz

# === Обработка команд ввода ===
def handle_user_input():
    print("\nДоступные команды:")
    print("  /list          — список комнат")
    print("  /users         — участники комнаты")
    print("  /mute          — выключить микрофон")
    print("  /unmute        — включить микрофон")
    print("  /exit          — выйти\n")

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
                tcp_sock.send(json.dumps({"type": "list_rooms"}).encode())
            elif user_input == "/users":
                tcp_sock.send(json.dumps({"type": "list_users"}).encode())
            else:
                # Текстовое сообщение
                tcp_sock.send(json.dumps({"type": "text", "payload": user_input}).encode())
        except KeyboardInterrupt:
            cleanup()
            break
        except Exception as e:
            logger.error(f"Ошибка ввода: {e}")
            break

# === Основная функция ===
def main():
    global tcp_sock, udp_sock, my_name, my_room, server_ip, tcp_port, udp_port

    parser = argparse.ArgumentParser(description="SFU Voice Chat Client")
    parser.add_argument("--name", required=True, help="Ваше имя")
    parser.add_argument("--server", default="127.0.0.1", help="IP сервера")
    parser.add_argument("--tcp-port", type=int, default=8888, help="TCP порт сервера")
    parser.add_argument("--udp-port", type=int, default=8889, help="UDP порт сервера")
    parser.add_argument("--room", default="general", help="Имя комнаты")
    args = parser.parse_args()

    my_name = args.name
    my_room = args.room
    server_ip = args.server
    tcp_port = args.tcp_port
    udp_port = args.udp_port

    if not my_name.strip():
        print("Ошибка: имя не может быть пустым")
        return

    # Подключение TCP
    tcp_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        tcp_sock.connect((server_ip, tcp_port))
    except Exception as e:
        print(f"Не удалось подключиться к серверу {server_ip}:{tcp_port}: {e}")
        return

    # Отправка join-сообщения
    join_msg = {"type": "join", "user": my_name, "room": my_room}
    tcp_sock.send(json.dumps(join_msg).encode())

    response = tcp_sock.recv(1024)
    resp = json.loads(response.decode())
    if "error" in resp:
        print(f"Ошибка сервера: {resp['error']}")
        tcp_sock.close()
        return

    print(f"✅ Подключено к комнате '{my_room}' как '{my_name}'")
    print(f"Сервер: {server_ip}:{tcp_port} (TCP), {server_ip}:{udp_port} (UDP)")

    # Инициализация аудио
    try:
        init_audio()
    except Exception as e:
        print(f"Ошибка инициализации аудио: {e}")
        tcp_sock.close()
        return

    # UDP-сокет для отправки
    udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    # Запуск потоков
    threading.Thread(target=tcp_receive_loop, daemon=True).start()
    threading.Thread(target=udp_receive_loop, daemon=True).start()
    threading.Thread(target=udp_send_loop, daemon=True).start()

    # Основной ввод
    try:
        handle_user_input()
    except KeyboardInterrupt:
        cleanup()

if __name__ == "__main__":
    main()
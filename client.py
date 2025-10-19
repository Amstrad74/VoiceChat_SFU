# client.py (версия 1.2.2)
# !/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
SFU Voice Chat Client — Версия 1.2.2
- TCP: text + room management (port 8888)
- UDP: audio streaming (port 8889)
- Audio: 16-bit PCM, 16kHz, mono
- UDP packet format: [32-byte zero-padded UTF-8 name][raw PCM]
- Push-to-Talk (PTT) на клавиши Ctrl (левый или правый)
- Изначально микрофон ВЫКЛЮЧЕН, но PTT работает
- /unmute — включить микрофон постоянно (PTT игнорируется)
- /mute — выключить микрофон, PTT снова активен
- Fully compatible with server.py v1.1+
"""

import socket
import threading
import json
import sys
import argparse
import logging
import pyaudio
import time
import keyboard  # Для отслеживания нажатий клавиш в реальном времени

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

# Состояния микрофона:
# - "MUTED": микрофон выключен, но PTT работает
# - "UNMUTED": микрофон включён постоянно, PTT игнорируется
mic_state = "MUTED"  # По умолчанию — выключен, но PTT активен
ptt_active = False  # True, когда удерживается клавиша PTT


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
            if not data:
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
            if stream_out and mic_state != "MUTED_AUDIO_ONLY":  # упрощённая логика: всегда воспроизводим
                # Примечание: приём аудио не зависит от состояния микрофона
                stream_out.write(data)
        except Exception as e:
            if running:
                logger.debug(f"Ошибка приёма UDP: {e}")
    udp_local.close()


# === Отправка аудио по UDP ===
def udp_send_loop():
    global running, mic_state, ptt_active
    while running:
        try:
            # Определяем, передавать ли аудио
            transmitting = False
            if mic_state == "UNMUTED":
                transmitting = True
            elif ptt_active:  # работает в состоянии "MUTED"
                transmitting = True

            if transmitting:
                audio_data = stream_in.read(CHUNK, exception_on_overflow=False)
                name_bytes = my_name.encode("utf-8")[:32]
                padded_name = name_bytes.ljust(32, b"\x00")
                packet = padded_name + audio_data
                udp_sock.sendto(packet, (server_ip, udp_port))
        except Exception as e:
            logger.debug(f"Ошибка отправки аудио: {e}")
        time.sleep(0.001)


# === Мониторинг клавиш PTT ===
def ptt_monitor():
    """
    Отслеживает нажатия клавиш Ctrl (левый и правый) для Push-to-Talk (PTT).

    Используются имена клавиш от модуля `keyboard`:
      - 'ctrl'        → левый Ctrl (и иногда правый, в зависимости от ОС)
      - 'right ctrl'  → правый Ctrl (особенно на Windows)

    При удержании любого из них в состоянии "MUTED" активируется передача.
    При отпускании — передача останавливается.
    """
    global ptt_active, mic_state, running
    # Используем оба возможных имени для Ctrl  или только левый {'ctrl'}
    # ptt_keys = {'ctrl', 'right ctrl'}
    ptt_keys = {'ctrl'}

    while running:
        try:
            event = keyboard.read_event()
            if not running:
                break
            if event.event_type == keyboard.KEY_DOWN and event.name in ptt_keys:
                if mic_state == "MUTED":
                    ptt_active = True
            elif event.event_type == keyboard.KEY_UP and event.name in ptt_keys:
                if mic_state == "MUTED":
                    ptt_active = False
        except Exception as e:
            if running:
                logger.debug(f"Ошибка PTT монитора: {e}")
            break


# === Обработка пользовательского ввода ===
def handle_user_input():
    print("\nДоступные команды:")
    print("  /list          — список комнат")
    print("  /users         — участники текущей комнаты")
    print("  /mute          — отключить микрофон (включить режим PTT)")
    print("  /unmute        — включить микрофон постоянно")
    print("  /exit          — выйти из чата\n")

    while running:
        try:
            user_input = input().strip()
            if not user_input:
                continue

            if user_input == "/exit":
                try:
                    tcp_sock.send(json.dumps({"type": "leave"}).encode("utf-8"))
                    # Дать время на отправку (неблокирующее, но с задержкой)
                    time.sleep(0.1)
                except:
                    pass  # Игнорировать ошибки отправки
                cleanup()
                break
            elif user_input == "/mute":
                global mic_state, ptt_active
                mic_state = "MUTED"
                ptt_active = False  # сбросить активность при ручном mute
                print("[МИКРОФОН ВЫКЛЮЧЕН. Режим PTT активен]")
            elif user_input == "/unmute":
                mic_state = "UNMUTED"
                print("[МИКРОФОН ВКЛЮЧЕН ПОСТОЯННО. PTT отключён]")
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
    global tcp_sock, udp_sock, my_name, my_room, server_ip, tcp_port, udp_port, mic_state

    parser = argparse.ArgumentParser(description="SFU Voice Chat Client v1.2.2")
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
    print("[МИКРОФОН ВЫКЛЮЧЕН. Нажмите и удерживайте Ctrl (левый или правый) для передачи (PTT)]")

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
    threading.Thread(target=ptt_monitor, daemon=True).start()

    # Основной цикл ввода
    try:
        handle_user_input()
    except KeyboardInterrupt:
        cleanup()


if __name__ == "__main__":
    main()
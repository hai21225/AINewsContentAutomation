import sys
import time
import threading


class Console:
    _running = False
    _thread = None
    _message = ""

    @staticmethod
    def _animate():
        dots = 0

        while Console._running:
            dots = (dots + 1) % 4

            text = Console._message + "." * dots

            sys.stdout.write("\r" + text + " " * 20)
            sys.stdout.flush()

            time.sleep(0.4)

    @staticmethod
    def loading(message):
        # Nếu đang có loading cũ thì dừng trước
        Console.stop()

        Console._message = message
        Console._running = True

        Console._thread = threading.Thread(
            target=Console._animate,
            daemon=True
        )

        Console._thread.start()

    @staticmethod
    def stop():
        if Console._running:
            Console._running = False

            if Console._thread:
                Console._thread.join()

            sys.stdout.write("\r" + " " * 120 + "\r")
            sys.stdout.flush()

    @staticmethod
    def print(message):
        Console.stop()
        print(message)

    @staticmethod
    def success(message):
        Console.stop()
        print(f"✓ {message}")

    @staticmethod
    def error(message):
        Console.stop()
        print(f"✗ {message}")

    @staticmethod
    def info(message):
        Console.stop()
        print(f"→ {message}")
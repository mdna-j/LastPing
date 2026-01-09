import os
import time


def main():
    interval = int(os.environ.get("HEARTBEAT_INTERVAL", "60"))
    print("Starting LastPing placeholder worker")
    try:
        while True:
            print("[worker] heartbeat")
            time.sleep(interval)
    except KeyboardInterrupt:
        print("Worker stopping")


if __name__ == "__main__":
    main()

import os

from lastping_sdk import HeartbeatClient, send_event


def main():
    base_url = os.environ.get("LASTPING_BASE_URL", "http://localhost:8000")
    api_key = os.environ.get("LASTPING_API_KEY")
    if not api_key:
        raise SystemExit("Set LASTPING_API_KEY in your environment before running this example.")

    c = HeartbeatClient(base_url, api_key)
    # send heartbeat
    try:
        c.send(project_id=1, name="example-check")
    except Exception as e:
        print("heartbeat failed:", e)

    # send an event via webhook helper
    resp = send_event(base_url, api_key, 1, "example-check", event="down", message="testing")
    print("event response:", resp)


if __name__ == "__main__":
    main()

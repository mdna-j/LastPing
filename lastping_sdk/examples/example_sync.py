from lastping_sdk import HeartbeatClient, send_event


def main():
    c = HeartbeatClient("http://localhost:8000", "MY_API_KEY")
    # send heartbeat
    try:
        c.send(project_id=1, name="example-check")
    except Exception as e:
        print("heartbeat failed:", e)

    # send an event via webhook helper
    resp = send_event("http://localhost:8000", "MY_API_KEY", 1, "example-check", event="down", message="testing")
    print("event response:", resp)


if __name__ == "__main__":
    main()

import zmq
import threading
import time
import json

# Fake RTC data channel
class DummyRTCChannel:
    def __init__(self):
        self.readyState = "open"

    def send(self, msg):
        print(f"[RTC SEND] {msg}")

# Fake ZMQ PUB publisher to simulate sleap-train
def simulate_training_pub(zmq_address):
    ctx = zmq.Context()
    pub_socket = ctx.socket(zmq.PUB)
    pub_socket.bind(zmq_address)

    # Give SUB time to connect
    time.sleep(1)

    for i in range(5):
        update = json.dumps({"epoch": i, "loss": round(1.0 / (i + 1), 4)})
        print(f"[PUB] Sending: {update}")
        pub_socket.send_string(update)
        time.sleep(1)

# Your real ZMQ listener (the one inside Worker)
def start_progress_listener(zmq_address, rtc_channel):
    ctx = zmq.Context()
    sub_socket = ctx.socket(zmq.SUB)
    sub_socket.connect(zmq_address)
    sub_socket.setsockopt_string(zmq.SUBSCRIBE, "")

    print("[SUB] Connected. Waiting for messages...")
    while True:
        msg = sub_socket.recv_string()
        print(f"[SUB] Received: {msg}")
        if rtc_channel.readyState == "open":
            rtc_channel.send(f"TRAIN_PROGRESS:{msg}")
        else:
            print("[RTC] Data channel not open.")

# Main entrypoint
if __name__ == "__main__":
    zmq_address = "tcp://127.0.0.1:9001"
    rtc_channel = DummyRTCChannel()

    # Start fake training publisher
    threading.Thread(target=start_progress_listener, args=(zmq_address, rtc_channel), daemon=True).start()

    # Give SUB socket time to fully connect/subscribe
    time.sleep(1)

    # Start progress listener (your real logic)
    simulate_training_pub(zmq_address)

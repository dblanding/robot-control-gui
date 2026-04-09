# display_scan.py
# Run this on the laptop
import asyncio
import aiomqtt
import json
import time
import numpy as np
import matplotlib.pyplot as plt

debug = False

class MQTTSubscriber:
    def __init__(self, broker, port, topics):
        self.broker = broker
        self.port = port
        self.topics = topics
        self.latest_messages = {topic: None for topic in topics}
        self.client = None

    async def connect_and_subscribe(self):
        try:
            async with aiomqtt.Client(hostname=self.broker, port=self.port) as client:
                self.client = client
                for topic in self.topics:
                    await client.subscribe(topic)
                    print(f"Subscribed to topic: {topic}")
                
                await self.message_listener()
        except aiomqtt.MqttError as e:
            print(f"MQTT error: {e}")
        except Exception as e:
            print(f"An unexpected error occurred: {e}")

    async def message_listener(self):
        async for message in self.client.messages:
            topic = message.topic.value
            payload = message.payload.decode('utf-8')
            self.latest_messages[topic] = payload
            # print(f"Received on '{topic}' @ {time.monotonic()} sec.")

    def get_latest_messages(self):
        return self.latest_messages

async def main():
    broker_address = "192.168.1.85"
    mqtt_topics = ["lidar/data"]
    
    subscriber = MQTTSubscriber(broker_address, 1883, mqtt_topics)
    last_scan = None
    listener_task = asyncio.create_task(subscriber.connect_and_subscribe())

    plt.ion()  # Turn on the interactive mode for real-time plotting
    print("Start driving")
    
    while True:
        await asyncio.sleep(0.1)
        current_messages = subscriber.get_latest_messages()
        for topic, message in current_messages.items():
            if topic == "lidar/data":
                if message:
                    scan = json.loads(message)
                    if scan != last_scan:  # Check if the scan is new
                        if debug:
                            print(f"Received a new scan of length {len(scan)}")
                        plot_scan(scan)  # Plot the new scan
                        last_scan = scan  # Update last scan

def plot_scan(scan):
    """Plot the LIDAR scan data in a polar format."""
    # Extract angles and distances from the list of dictionaries
    angles = [entry["a"] for entry in scan]  # Angles in radians
    distances = [entry["d"] for entry in scan]  # Distances

    # Negate angles to comply with CCW convention
    angles = [-angle for angle in angles]

    plt.clf()  # Clear the current figure
    ax = plt.subplot(111, projection='polar')
    
    ax.set_ylim(0, 4.1)  # Set the maximum radius to 4 m
    ax.set_theta_zero_location('N')  # Set 0 radians to the top (North)
    ax.set_theta_direction(-1)  # Set direction to clockwise

    ax.plot(angles, distances, marker='o', linestyle='-', markersize=3, color='blue')
    ax.set_title('Real-time LIDAR Scan (Bird\'s Eye View)', va='bottom')
    plt.pause(0.1)  # Pause to update the plot

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Subscriber stopped manually.")
    finally:
        print("Stopping the Scan Motor")

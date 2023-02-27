#!/usr/bin/env python3
#
# Filename: ember-mqtt-bridge.py
#
# Author: Simon Redman <simon@ergotech.com>
# File Created: 25.02.2023
# Last Modified: Sat 25 Feb 2023 08:38:58 PM EST
# Description: 
#

import asyncio
import asyncio_mqtt
from asyncio_mqtt import Client

from ember_mug.scanner import find_mug, discover_mugs
from ember_mug.mug import EmberMug

import argparse
from exceptiongroup import ExceptionGroup, catch
from collections import namedtuple
import json
import yaml

MqttPayload = namedtuple("MqttPayload", ["topic", "payload"])

EmberMug.get_state_topic = lambda mug: f"ember/{EmberMqttBridge.sanitise_mac(mug.device.address)}/state"

EMBER_MANUFACTURER = "Ember"

class EmberMqttBridge:
    def __init__(
        self,
        MAC,
        mqtt_broker,
        mqtt_broker_port,
        mqtt_username,
        mqtt_password,
        update_interval,
        discovery_prefix,
        ):
        self.MAC = MAC
        self.mqtt_broker = mqtt_broker
        self.mqtt_broker_port = mqtt_broker_port
        self.mqtt_username = mqtt_username
        self.mqtt_password = mqtt_password
        self.update_interval = update_interval
        self.discovery_prefix = discovery_prefix

        self.validate_parameters()

        self.known_devices = set()
        self.known_devices_lock = asyncio.Lock()

    def validate_parameters(self):
        unsupplied_params = [var for var in vars(self) if getattr(self, var) is None]

        if len(unsupplied_params) > 0:
            raise ExceptionGroup(
                "One or more parameters was not provided",
                [ValueError(param) for param in unsupplied_params])

    async def start(self):
        async with Client(
            hostname=self.mqtt_broker,
            port=self.mqtt_broker_port,
            username=self.mqtt_username,
            password=self.mqtt_password,
            ) as client:
            async with asyncio.TaskGroup() as tg:
                tg.create_task(self.read_existing_mqtt_devices(client))

    async def add_known_device(self, device_mac: str) -> None:
        async with self.known_devices_lock:
            self.known_devices.add(device_mac)

    def sanitise_mac(mac: str) -> str:
        """Clean up a MAC so it's suitable for use where colons aren't"""
        return mac.replace(":", "_")

    def get_device_definition(self, mug: EmberMug):
        return {
            "connections": [("mac", self.MAC)],
            "model": mug.device.name,
            "manufacturer": EMBER_MANUFACTURER,
            "suggested_area": "Office",
        }

    async def send_root_device(self, client: Client, mug: EmberMug):
        root_device_payload: MqttPayload = MqttPayload(
            topic= f"{self.discovery_prefix}/climate/{EmberMqttBridge.sanitise_mac(self.MAC)}/config",
            payload={
                "name": mug.device.name,
                "mode_state_topic": mug.get_state_topic(),
                "mode_state_template": "{{ value_json.power }}",
                "current_temperature_topic": mug.get_state_topic(),
                "current_temperature_template": "{{ value_json.current_temperature }}",
                "temperature_state_topic": mug.get_state_topic(),
                "temperature_state_template": "{{ value_json.desired_temperature }}",
                "availability_topic": mug.get_state_topic(),
                "availability_template": "{{ value_json.availability }}",
                "mode_command_topic": f"ember/{EmberMqttBridge.sanitise_mac(self.MAC)}/power/set",
                "temperature_command_topic": f"ember/{EmberMqttBridge.sanitise_mac(self.MAC)}/temperature/set",
                "modes": ["heat", "off"],
                "temperature_unit": "C" if mug.data.use_metric else "F",
                "temp_step": 1,
                "unique_id": self.MAC,
                "device": self.get_device_definition(mug),
                "icon": "mdi:coffee",
                "max_temp": 145,
                "min_temp": 120,
            })
        await mqtt.publish(root_device_payload.topic, json.dumps(root_device_payload.payload), retain=True)

    async def start_listener_loop(self, client: Client, mug: EmberMug):
        pass

    async def read_existing_mqtt_devices(self, mqtt: Client):
        '''
        Look for MQTT messages indicating devices which have been discovered in the past,
        which we should be on the lookout for.
        These devices may be from other Ember bridges running on the same MQTT server,
        so we _cannot_ expect that they are paired.
        '''
        async with mqtt.messages() as messages:
            await mqtt.subscribe(f"{self.discovery_prefix}/#")
            async for message in messages:
                data = json.loads(message.payload)
                if data and "device" in data:
                    device = data["device"]
                    if "connections" in device and "manufacturer" in device:
                        if device["manufacturer"] == EMBER_MANUFACTURER:
                            connections = device["connections"]
                            await self.add_known_device(connections[0][1])

def main():
    parser = argparse.ArgumentParser(
        prog="EmberMqttBridge",
        description="Integrate your Ember mug with your MQTT server")
    
    parser.add_argument("-c", "--config-file",
        help="Path to a YAML file from which to read options. If any options are specified in this file and on the command line, the command line option will take prescidence.")
    
    parser.add_argument("--MAC",
        help="MAC address of your Ember mug.")

    parser.add_argument("-b", "--mqtt-broker",
        help="Target MQTT broker, like test.mosquitto.org.")

    parser.add_argument("-P", "--mqtt-broker-port", type=int, default=1883,
        help="Target MQTT broker port, like 1883.")
    
    parser.add_argument("-u", "--mqtt-username",
        help="Username to authenticate to the MQTT broker.")

    parser.add_argument("-p", "--mqtt-password",
        help="Password to authenticate to the MQTT broker.")

    parser.add_argument("-i", "--update-interval", type=int, default=30,
        help="Frequency at which to send out update messages, in seconds.")

    parser.add_argument("--discovery-prefix", default="homeassistant",
        help="MQTT discovery prefix.")
    
    parser.add_argument

    args = parser.parse_args()
    config = {}

    if args.config_file:
        with open(args.config_file, "r", encoding="utf-8") as config_file:
            config = yaml.safe_load(config_file)

    for arg in vars(args):
        val = getattr(args, arg)
        if val is not None:
            config[arg] = val

    del config["config_file"]

    bridge = EmberMqttBridge(**config)
    asyncio.run(bridge.start()) # Should never return

if __name__ == "__main__":
    main()
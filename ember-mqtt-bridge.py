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
from asyncio_mqtt import Client, MqttError

import ember_mug.consts as ember_mug_consts
import ember_mug.scanner as ember_mug_scanner
from ember_mug.mug import EmberMug

import argparse
from bleak import BleakError
from collections import namedtuple
from exceptiongroup import ExceptionGroup, catch
import json
import logging
from typing import Dict
import yaml

MqttPayload = namedtuple("MqttPayload", ["topic", "payload"])


EMBER_MANUFACTURER = "Ember"

class MqttEmberMug:
    '''
    Wrapper class to hold the EmberMug and all associated state
    '''
    def __init__(self, mug: EmberMug) -> None:
        self.mug: EmberMug = mug

    def topic_root(self) -> str:
        return f"ember/{EmberMqttBridge.sanitise_mac(self.mug.device.address)}"

    def state_topic(self) -> str:
        return f"{self.topic_root()}/state"

    def mode_command_topic(self) -> str:
        return f"{self.topic_root()}/power/set"

    def temperature_command_topic(self) -> str:
        return f"{self.topic_root()}/temperature/set"

    def get_device_definition(self):
        return {
            # This connection may strictly not be a MAC if you are (for instance) running on
            # MacOS where Bleak isn't allowed to acces the MAC information.
            "connections": [("mac", self.mug.device.address)],
            "model": self.mug.device.name,
            "manufacturer": EMBER_MANUFACTURER,
            "suggested_area": "Office",
        }

    def get_root_device(self) -> Dict[str, str]:
        return {
                    "name": self.mug.device.name,
                    "mode_state_topic": self.state_topic(),
                    "mode_state_template": "{{ value_json.power }}",
                    "current_temperature_topic": self.state_topic(),
                    "current_temperature_template": "{{ value_json.current_temperature }}",
                    "temperature_state_topic": self.state_topic(),
                    "temperature_state_template": "{{ value_json.desired_temperature }}",
                    "availability_topic": self.state_topic(),
                    "availability_template": "{{ value_json.availability }}",
                    "mode_command_topic": self.mode_command_topic(),
                    "temperature_command_topic": self.temperature_command_topic(),
                    "modes": ["auto", "off"],
                    "temperature_unit": "C" if self.mug.data.use_metric else "F",
                    "temp_step": 1,
                    "unique_id": self.mug.device.address,
                    "device": self.get_device_definition(),
                    "icon": "mdi:coffee",
                    "max_temp": 62.5 if self.mug.data.use_metric else 145,
                    "min_temp": 50 if self.mug.data.use_metric else 120,
                }

    def get_root_topic(self, discovery_prefix: str) -> str:
        return f"{discovery_prefix}/climate/{EmberMqttBridge.sanitise_mac(self.mug.device.address)}/config"

class EmberMqttBridge:
    def __init__(
        self,
        mqtt_broker,
        mqtt_broker_port,
        mqtt_username,
        mqtt_password,
        update_interval,
        discovery_prefix,
        ):
        self.mqtt_broker = mqtt_broker
        self.mqtt_broker_port = mqtt_broker_port
        self.mqtt_username = mqtt_username
        self.mqtt_password = mqtt_password
        self.update_interval = update_interval
        self.discovery_prefix = discovery_prefix

        self.validate_parameters()

        self.tracked_mugs: Dict[str, MqttEmberMug] = {}

        self.retry_interval_secs = 1

        self.logger = logging.getLogger(__name__)

        # Devices which we know about from seeing thier MQTT advertisements,
        # but with which we may or may not be connected.
        self.known_devices = set()
        self.known_devices_lock = asyncio.Lock()

    def validate_parameters(self):
        unsupplied_params = [var for var in vars(self) if getattr(self, var) is None]

        if len(unsupplied_params) > 0:
            raise ExceptionGroup(
                "One or more parameters was not provided",
                [ValueError(param) for param in unsupplied_params])

    async def start(self):
        while True:
            try:
                async with Client(
                    hostname=self.mqtt_broker,
                    port=self.mqtt_broker_port,
                    username=self.mqtt_username,
                    password=self.mqtt_password,
                    ) as client:
                    try:
                        async with asyncio.TaskGroup() as tg:
                            tg.create_task(self.start_mug_polling(client))
                            tg.create_task(self.start_mqtt_listener(client))
                    except (KeyboardInterrupt, asyncio.exceptions.CancelledError):
                        # We are closing down. Send out a notice that the devices we control are offline.
                        for mqtt_mug in self.tracked_mugs.values():
                            await self.send_update_offline(client, mqtt_mug)
                        raise
            except MqttError as err:
                self.logger.warning(f"MQTT connection failed with {err}")
                await asyncio.sleep(self.retry_interval_secs)

    async def add_known_device(self, device_mac: str) -> None:
        async with self.known_devices_lock:
            self.known_devices.add(device_mac)

    def sanitise_mac(mac: str) -> str:
        """Clean up a MAC so it's suitable for use where colons aren't"""
        return mac.replace(":", "_")

    async def send_root_device(self, mqtt: Client, mug: MqttEmberMug):
        root_device_payload: MqttPayload = MqttPayload(
            topic= mug.get_root_topic(self.discovery_prefix),
            payload=mug.get_root_device())
        await mqtt.publish(root_device_payload.topic, json.dumps(root_device_payload.payload), retain=True)

    async def send_update(self, mqtt: Client, mqtt_mug: MqttEmberMug):
        match mqtt_mug.mug.data.liquid_state:
            case ember_mug_consts.LiquidState.HEATING:
                mode = "auto"
            case ember_mug_consts.LiquidState.TARGET_TEMPERATURE:
                mode = "auto"
            case ember_mug_consts.LiquidState.COOLING:
                mode = "auto"
            case other:
                mode = "off"
        state = {
            "power": mode,
            "current_temperature": mqtt_mug.mug.data.current_temp,
            "desired_temperature": mqtt_mug.mug.data.target_temp,
            "availability": "online", # If we're sending this, the device must have been visible. Need to send a last will message when we lose connection.
        }
        update_payload: MqttPayload = MqttPayload(
            topic=mqtt_mug.state_topic(),
            payload=state
        )
        await mqtt.publish(update_payload.topic, json.dumps(update_payload.payload))

    async def subscribe_mqtt_topic(self, mqtt: Client, mqtt_mug: MqttEmberMug):
        '''
        Subscribe to the update topics for the given mug.
        '''
        await mqtt.subscribe(f"{mqtt_mug.topic_root()}/+/set")

    async def send_update_offline(self, mqtt: Client, mqtt_mug: MqttEmberMug):
        state = {
            "availability": "offline"
        }
        update_payload: MqttPayload = MqttPayload(
            topic=mqtt_mug.state_topic(),
            payload=state
        )
        await mqtt.publish(update_payload.topic, json.dumps(update_payload.payload))

    async def unsubscribe_mqtt_topic(self, mqtt: Client, mqtt_mug: MqttEmberMug):
        '''
        Unsubscribe from the update topics for the given mug
        '''
        await mqtt.unsubscribe(f"{mqtt_mug.topic_root()}/+/set")

    async def start_mug_polling(self, mqtt: Client):
        while True:
            missing_mugs = [] # Paired mugs which we could not find
            visible_mugs = [EmberMug(mug) for mug in await ember_mug_scanner.discover_mugs()] # Find un-paired mugs in pairing mode
            async with self.known_devices_lock:
                for addr in self.known_devices:
                    if addr in self.tracked_mugs:
                        if self.tracked_mugs[addr].mug._client.is_connected:
                            pass # Already connected, nothing to do
                        else:
                            missing_mugs.append(addr)
                    else:
                        device = await ember_mug_scanner.find_mug(addr) # Find paired mugs
                        if device is None:
                            pass # I guess it's not in range. Send an "offline" status update?
                        else:
                            if not addr in self.tracked_mugs:
                                self.tracked_mugs[addr] = MqttEmberMug(EmberMug(device))
            for addr in self.tracked_mugs:
                try:
                    wrapped_mug: MqttEmberMug = self.tracked_mugs[addr]
                    mug: EmberMug = wrapped_mug.mug
                    # Using target_temp as a proxy for data being initialized.
                    if mug.data.target_temp == 0:
                        await mug.update_all()
                        await mug.subscribe()
                        await self.subscribe_mqtt_topic(mqtt, wrapped_mug)
                        await self.send_root_device(mqtt, wrapped_mug)

                    await mug.update_queued_attributes()
                    await self.send_update(mqtt, wrapped_mug)

                except BleakError as be:
                    if addr in self.tracked_mugs:
                        missing_mugs.append(addr)
                    logging.warning(f"Error while communicating with mug: {be}")

            for addr in missing_mugs:
                wrapped_mug = self.tracked_mugs[addr]
                del self.tracked_mugs[addr]
                await self.send_update_offline(mqtt, wrapped_mug)
                await self.unsubscribe_mqtt_topic(mqtt, wrapped_mug)

            await asyncio.sleep(self.update_interval)

    async def start_mqtt_listener(self, mqtt: Client):
        async with mqtt.messages() as messages:
            await mqtt.subscribe(f"{self.discovery_prefix}/#") # Listen for knowledge of mugs we cannot see
            async for message in messages:
                if message.topic.value.startswith(f"{self.discovery_prefix}"):
                    '''
                    Look for MQTT messages indicating devices which have been discovered in the past,
                    which we should be on the lookout for.
                    These devices may be from other Ember bridges running on the same MQTT server,
                    so we _cannot_ expect that they are paired.
                    '''
                    data = json.loads(message.payload)
                    if data and "device" in data:
                        device = data["device"]
                        if "connections" in device and "manufacturer" in device:
                            if device["manufacturer"] == EMBER_MANUFACTURER:
                                connections = device["connections"]
                                await self.add_known_device(connections[0][1])
                if message.topic.value.startswith("ember") and message.topic.value.endswith("set"):
                    '''
                    Look for messages indicating a command from the user.
                    TODO: Make this section accept mugs which are handled by another MQTT instance
                    '''
                    # Get the mug to which this message belongs
                    # There is certainly a better way to do this but I am lazy
                    matching_mugs = [wrapped_mug for wrapped_mug in self.tracked_mugs.values() if message.topic.value.startswith(wrapped_mug.topic_root())]
                    if len(matching_mugs) == 0:
                        logging.error(f"No mugs matched {message.topic.value}. This is a bug.")
                    elif len(matching_mugs) > 1:
                        logging.error(f"More than one mug matched {message.topic.value}. This is a bug.")
                    else:
                        mqtt_mug = matching_mugs[0]

                        if message.topic.value == mqtt_mug.mode_command_topic():
                            if message.payload.decode() == "off":
                                await mqtt_mug.mug.set_target_temp(0)
                            else:
                                # Not sure what to do here: The mug turns iteslf on when it has hot water in it.
                                # For lack of a better idea, do SOMETHING. If there's no water in the mug, this
                                # will likely have no effect.
                                await mqtt_mug.mug.set_target_temp(100)
                        elif message.topic.value == mqtt_mug.temperature_command_topic():
                            await mqtt_mug.mug.set_target_temp(float(message.payload.decode()))

def main():
    parser = argparse.ArgumentParser(
        prog="EmberMqttBridge",
        description="Integrate your Ember mug with your MQTT server")
    
    parser.add_argument("-c", "--config-file",
        help="Path to a YAML file from which to read options. If any options are specified in this file and on the command line, the command line option will take prescidence.")

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
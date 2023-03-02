#!/usr/bin/env python3
#
# Filename: ember_mqtt_bridge.py
#
# Author: Simon Redman <simon@ergotech.com>
# File Created: 25.02.2023
# Last Modified: Sat 25 Feb 2023 08:38:58 PM EST
# Description: 
#

import consts
from consts import EMBER_MANUFACTURER
from mqtt_ember_mug import MqttEmberMug, MqttPayload

import asyncio
from asyncio_mqtt import Client, MqttError

import ember_mug.consts as ember_mug_consts
import ember_mug.scanner as ember_mug_scanner
import ember_mug.data as ember_mug_data
from ember_mug.mug import EmberMug

import argparse
from bleak import BleakError
import json
import logging
from typing import Dict, List
import yaml

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
                    except:
                        # We are closing down. Send out a notice that the devices we control are offline.
                        for mqtt_mug in self.tracked_mugs.values():
                            await mqtt_mug.send_update(client, online=False)
                        raise
            except MqttError as err:
                self.logger.warning(f"MQTT connection failed with {err}")
                await asyncio.sleep(self.retry_interval_secs)

    async def add_known_device(self, device_mac: str) -> None:
        async with self.known_devices_lock:
            self.known_devices.add(device_mac)

    async def send_entity_discovery(self, mqtt: Client, mug: MqttEmberMug):
        entities: List[MqttPayload] = [
            mug.get_climate_entity(self.discovery_prefix),
            mug.get_battery_entity(self.discovery_prefix),
            mug.get_charging_entity(self.discovery_prefix),
            mug.get_led_entity(self.discovery_prefix),
        ]
        for entity in entities:
            await mqtt.publish(entity.topic, json.dumps(entity.payload), retain=entity.retain)

    async def subscribe_mqtt_topic(self, mqtt: Client, mqtt_mug: MqttEmberMug):
        '''
        Subscribe to the update topics for the given mug.
        '''
        await mqtt.subscribe(f"{mqtt_mug.topic_root()}/+/set")

    async def unsubscribe_mqtt_topic(self, mqtt: Client, mqtt_mug: MqttEmberMug):
        '''
        Unsubscribe from the update topics for the given mug.
        '''
        await mqtt.unsubscribe(f"{mqtt_mug.topic_root()}/+/set")

    async def handle_mug_disconnect(self, mqtt: Client, mqtt_mug: MqttEmberMug):
        '''
        Clean up everything which should be cleaned up when we lose connection
        with a mug.
        '''
        del self.tracked_mugs[mqtt_mug.mug.device.address]
        await mqtt_mug.send_update(mqtt, online=False)
        await self.unsubscribe_mqtt_topic(mqtt, mqtt_mug)

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
                    # Using current_temp as a proxy for data being initialized.
                    if mug.data.current_temp == 0:
                        # This intentionally leaves the connection open.
                        # If we do not, we do not get the notices to which we've subscribed.
                        await mug.update_all()
                        await mug.subscribe()
                        await self.subscribe_mqtt_topic(mqtt, wrapped_mug)
                        await self.send_entity_discovery(mqtt, wrapped_mug)

                    changes: List[ember_mug_data.Change] = await mug.update_queued_attributes()

                    # Determine whether we need to send an update to the entity, if one of the top-level configs changed
                    for changed_attr in [change.attr for change in changes]:
                        if changed_attr in consts.NAME_TO_EVENT_ID:
                            attr_code = consts.NAME_TO_EVENT_ID[changed_attr]
                            match attr_code:
                                case ember_mug_consts.PushEvent.LIQUID_STATE_CHANGED:
                                    # We use the LIQUID_STATE to control the "modes" field of the "climate" entity
                                    await self.send_entity_discovery(mqtt, wrapped_mug)

                    await wrapped_mug.send_update(mqtt, online=True)

                except BleakError as be:
                    if addr in self.tracked_mugs:
                        missing_mugs.append(addr)
                    logging.warning(f"Error while communicating with mug: {be}")

            for addr in missing_mugs:
                wrapped_mug = self.tracked_mugs[addr]
                await self.handle_mug_disconnect(mqtt, wrapped_mug)

            await asyncio.sleep(self.update_interval)

    async def start_mqtt_listener(self, mqtt: Client):
        async with mqtt.messages() as messages:
            await mqtt.subscribe(f"{self.discovery_prefix}/#") # Listen for knowledge of mugs we cannot see
            async for message in messages:
                topic = message.topic.value
                if topic.startswith(f"{self.discovery_prefix}"):
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

                if topic.startswith("ember") and topic.endswith("set"):
                    '''
                    Look for messages indicating a command from the user.
                    TODO: Make this section accept mugs which are handled by another MQTT instance
                    '''
                    # Get the mug to which this message belongs
                    # There is certainly a better way to do this but I am lazy
                    matching_mugs = [wrapped_mug for wrapped_mug in self.tracked_mugs.values() if topic.startswith(wrapped_mug.topic_root())]
                    if len(matching_mugs) == 0:
                        logging.error(f"No mugs matched {topic}. This is a bug.")
                    elif len(matching_mugs) > 1:
                        logging.error(f"More than one mug matched {topic}. This is a bug.")
                    else:
                        mqtt_mug = matching_mugs[0]

                        try:
                            if topic == mqtt_mug.mode_command_topic():
                                if message.payload.decode() == "off":
                                    await mqtt_mug.mug.set_target_temp(0)
                                    # Hack the liquid state, because otherwise we won't get the state update right away.
                                    mqtt_mug.mug.data.liquid_state = ember_mug_consts.LiquidState.WARM_NO_TEMP_CONTROL
                                else:
                                    # Not sure what to do here: The mug turns iteslf on when it has hot water in it.
                                    # For lack of a better idea, do SOMETHING. If there's no water in the mug, this
                                    # will likely have no effect.
                                    await mqtt_mug.mug.set_target_temp(100)
                                    mqtt_mug.mug.data.liquid_state = ember_mug_consts.LiquidState.HEATING
                            elif topic == mqtt_mug.temperature_command_topic():
                                await mqtt_mug.mug.set_target_temp(float(message.payload.decode()))
                            elif topic == mqtt_mug.led_color_command_topic():
                                r,g,b = [int(val) for val in message.payload.decode().replace(")", "").replace("(", "").split(",")]
                                await mqtt_mug.mug.set_led_colour(ember_mug_data.Colour(r,g,b))
                            else:
                                logging.error(f"Unsupported command {topic}.")

                            await mqtt_mug.send_update(mqtt, online=True)
                        except BleakError:
                            # Mug has gone unavailable since we last updated it.
                            await self.handle_mug_disconnect(mqtt, mqtt_mug)


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
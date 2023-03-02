#!/usr/bin/env python3
#
# Filename: ember-mqtt-bridge.py
#
# Author: Simon Redman <simon@ergotech.com>
# File Created: 25.02.2023
# Last Modified: Sat 25 Feb 2023 08:38:58 PM EST
# Description: 
#

import consts

import asyncio
import asyncio_mqtt
from asyncio_mqtt import Client, MqttError

import ember_mug.consts as ember_mug_consts
import ember_mug.scanner as ember_mug_scanner
import ember_mug.data as ember_mug_data
from ember_mug.mug import EmberMug

import argparse
from bleak import BleakError
from collections import namedtuple
from exceptiongroup import ExceptionGroup, catch
import json
import logging
from typing import Dict, List
import yaml

MqttPayload = namedtuple("MqttPayload", ["topic", "payload"])


EMBER_MANUFACTURER = "Ember"

class MqttEmberMug:
    '''
    Wrapper class to hold the EmberMug and all associated state
    '''
    def __init__(self, mug: EmberMug) -> None:
        self.mug: EmberMug = mug

    def sanitised_mac(self) -> str:
        '''
        Return my connection address in a form which is suitable where colons aren't
        '''
        return self.mug.device.address.replace(":", "_")

    def topic_root(self) -> str:
        return f"ember/{self.sanitised_mac()}"

    def state_topic(self) -> str:
        return f"{self.topic_root()}/state"

    def mode_command_topic(self) -> str:
        return f"{self.topic_root()}/power/set"

    def temperature_command_topic(self) -> str:
        return f"{self.topic_root()}/temperature/set"

    def led_command_topic(self) -> str:
        return f"{self.topic_root()}/led/set"

    def led_brightness_command_topic(self) -> str:
        return f"{self.topic_root()}/led_brightness/set"

    def led_color_command_topic(self) -> str:
        return f"{self.topic_root()}/led_color/set"

    def get_device_definition(self):
        return {
            # This connection may strictly not be a MAC if you are (for instance) running on
            # MacOS where Bleak isn't allowed to acces the MAC information.
            "connections": [("mac", self.mug.device.address)],
            "model": self.mug.device.name,
            "manufacturer": EMBER_MANUFACTURER,
            "suggested_area": "Office",
        }

    def get_climate_entity(self, discovery_prefix: str) -> MqttPayload:
        return MqttPayload(
            topic=f"{discovery_prefix}/climate/{self.sanitised_mac()}/config",
            payload={
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
                "modes": ["heat", "off"] if not self.mug.data.liquid_state == ember_mug_consts.LiquidState.EMPTY else ["off"],
                "temperature_unit": "C" if self.mug.data.use_metric else "F",
                "temp_step": 1,
                "unique_id": self.mug.device.address,
                "device": self.get_device_definition(),
                "icon": "mdi:coffee",
                "max_temp": 62.5 if self.mug.data.use_metric else 145,
                "min_temp": 50 if self.mug.data.use_metric else 120,
            }
        )

    def get_battery_entity(self, discovery_prefix: str) -> MqttPayload:
        return MqttPayload(
            topic= f"{discovery_prefix}/sensor/{self.sanitised_mac()}_battery/config",
            payload={
                "name": "Mug Battery",
                "device_class": "battery",
                "unit_of_measurement": "%",
                "device": self.get_device_definition(),
                "unique_id": f"{self.mug.device.address}_battery",
                "availability_topic": self.state_topic(),
                "availability_template": "{{ value_json.availability }}",
                "state_topic": self.state_topic(),
                "value_template": "{{ value_json.battery_percent }}",
            }
        )

    def get_charging_entity(self, discovery_prefix: str) -> MqttPayload:
        return MqttPayload(
            topic= f"{discovery_prefix}/binary_sensor/{self.sanitised_mac()}_battery_charging/config",
            payload={
                "name": "Mug Battery Charging",
                "device_class": "battery_charging",
                "device": self.get_device_definition(),
                "unique_id": f"{self.mug.device.address}_battery_charging",
                "availability_topic": self.state_topic(),
                "availability_template": "{{ value_json.availability }}",
                "state_topic": self.state_topic(),
                "value_template": "{{ value_json.battery_charging }}",
            }
        )

    def get_led_entity(self, discovery_prefix: str) -> MqttPayload:
        return MqttPayload(
            topic= f"{discovery_prefix}/light/{self.sanitised_mac()}_led/config",
            payload={
                "name": "Mug LED",
                "device_class": "battery_charging",
                "device": self.get_device_definition(),
                "unique_id": f"{self.mug.device.address}_led",
                "optimistic": False,
                "availability_topic": self.state_topic(),
                "availability_template": "{{ value_json.availability }}",
                "state_topic": self.state_topic(),
                "state_value_template": "{{ value_json.led }}",
                # You cannot control the on/off state of the LED, but this is a required field.
                "command_topic": self.led_command_topic(),
                # You cannot actually control the brightness of the mug LED, but if you don't have this here,
                # the HomeAssistant GUI only shows you a brightness slider, and it sends updates to the colour using that.
                "brightness_command_topic": self.led_brightness_command_topic(),
                "rgb_command_topic": self.led_color_command_topic(),
                "rgb_command_template": "{{ red,green,blue }}",
                "rgb_state_topic": self.state_topic(),
                "rgb_value_template": "{{ value_json.led_rgb }}",
            }
        )

    async def send_update(self, mqtt: Client, online: bool):
        match self.mug.data.liquid_state:
            case ember_mug_consts.LiquidState.HEATING:
                mode = "heat"
            case ember_mug_consts.LiquidState.TARGET_TEMPERATURE:
                mode = "heat"
            case ember_mug_consts.LiquidState.COOLING:
                mode = "heat"
            case other:
                mode = "off"
        led_color: ember_mug_data.Colour = self.mug.data.led_colour
        state = {
            "power": mode,
            "current_temperature": self.mug.data.current_temp,
            "desired_temperature": self.mug.data.target_temp,
            "availability": "online" if online else "offline",
            "battery_percent": self.mug.data.battery.percent if self.mug.data.battery else None,
            "battery_charging": "UNKNOWN" if not self.mug.data.battery else "ON" if self.mug.data.battery.on_charging_base else "OFF",
            "led": "ON", # The LED on the mug is always on. Necessary to say so in order to get Home Assistant to behave.
            "led_rgb": f"{led_color.red},{led_color.green},{led_color.blue}" if led_color else "UNKNOWN",
        }
        update_payload: MqttPayload = MqttPayload(
            topic=self.state_topic(),
            payload=state
        )
        await mqtt.publish(update_payload.topic, json.dumps(update_payload.payload))

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
            await mqtt.publish(entity.topic, json.dumps(entity.payload), retain=True)

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
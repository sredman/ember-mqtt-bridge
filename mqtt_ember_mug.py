#!/usr/bin/env python3
#
# Filename: mqtt-ember-mug.py
#
# Author: Simon Redman <simon@ergotech.com>
# File Created: 01.03.2023
# Last Modified: Sat 25 Feb 2023 08:38:58 PM EST
# Description: Wrapper class to hold the EmberMug and all associated state
#

from consts import EMBER_MANUFACTURER, MqttPayload

import asyncio
from asyncio_mqtt import Client

import ember_mug.consts as ember_mug_consts
import ember_mug.data as ember_mug_data
from ember_mug.mug import EmberMug

import json
from typing import Dict

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

    def pairing_button_command_topic(self) -> str:
        return f"{self.topic_root()}/pairing_button/set"

    def get_device_definition(self):
        return {
            # This connection may strictly not be a MAC if you are (for instance) running on
            # MacOS where Bleak isn't allowed to acces the MAC information.
            "name": self.mug.device.name,
            "connections": [("mac", self.mug.device.address)],
            "model": self.mug.device.name,
            "manufacturer": EMBER_MANUFACTURER,
            "suggested_area": "Office",
        }

    def get_climate_entity(self, discovery_prefix: str) -> MqttPayload:
        return MqttPayload(
            topic=f"{discovery_prefix}/climate/{self.sanitised_mac()}/root/config",
            payload={
                "name": "Mug Temperature Control",
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
            topic= f"{discovery_prefix}/sensor/{self.sanitised_mac()}/battery/config",
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
            topic= f"{discovery_prefix}/binary_sensor/{self.sanitised_mac()}/battery_charging/config",
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
            topic= f"{discovery_prefix}/light/{self.sanitised_mac()}/led/config",
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

    def get_pairing_button_entity(self, discovery_prefix: str) -> MqttPayload:
        return MqttPayload(
            topic= f"{discovery_prefix}/button/{self.sanitised_mac()}/pairing_button/config",
            payload={
                "name": f"Pair With Device",
                "device": self.get_device_definition(),
                "unique_id": f"{self.mug.device.address}_pairing_button",
                "icon": "mdi:coffee-off-outline",
                "command_topic": self.pairing_button_command_topic(),
            },
            retain=False
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
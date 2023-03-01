# ember-mqtt-bridge
BLE to MQTT bridge for the Ember mug, designed to be used with Home Assistant

Uses the [python-ember-mug](https://pypi.org/project/python-ember-mug/) library
for the BLE communication, and [asyncio-mqtt](https://pypi.org/project/asyncio-mqtt/)
for MQTT communication.

Provides autoconfiguration messages for Home Assistant.

## Features

Automatically connects with any mug in pairing mode (hopefully your neighbors want that!),
and allows control over the temperature and the LED color. Allows turning the heater off,
though turning it back on is somewhat trickier (it seems the mug needs to have water in
order for it to allow you to turn on the heater).

## Usage

Simply run the executable, like:

`./ember-mqtt-bridge.py --config-file=./ember-mqtt-bridge-config.yml`

If you like, you can override some parameters on the command line:

`./ember-mqtt-bridge.py --config-file=./ember-mqtt-bridge.config.yml --mqtt-password:$password`

## Notes

My Raspberry Pi Zero W's Bluetooth doesn't seem to get a good signal from the mug, even while
sitting on the same desk. This bridge was mostly written from my laptop.
If you want to use the Pi, try a USB-attached Bluetooth dongle!

## Whole-home coverage

This bridge intends to support running multiple instances in the same network.
It will use MQTT to detect devices which are announced, and attempt to keep
track of them. This is not deeply tested, but is a design goal, and might work.

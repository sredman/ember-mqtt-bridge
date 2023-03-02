# ember-mqtt-bridge
BLE to MQTT bridge for the Ember mug, designed to be used with Home Assistant

Uses the [python-ember-mug](https://pypi.org/project/python-ember-mug/) library
for the BLE communication, and [asyncio-mqtt](https://pypi.org/project/asyncio-mqtt/)
for MQTT communication.

Provides autoconfiguration messages for Home Assistant.

## Features

Allows control over the mug temperature and the LED color. Allows turning the heater off,
though turning it back on can be flaky if the mug doesn't believe it has anything in it.

## Usage

Simply run the executable, like:

`./ember-mqtt-bridge.py --config-file=./ember-mqtt-bridge-config.yml`

If you like, you can override some parameters on the command line:

`./ember-mqtt-bridge.py --config-file=./ember-mqtt-bridge.config.yml --mqtt-password:$password`

## Pairing

To pair a mug, put it in pairing mode. The bridge will present a pairing button. Find that in
your Home Assistant interface, and press it. The mug will be picked up in the next iteration (or two).

## Notes

My Raspberry Pi Zero W's Bluetooth doesn't seem to get a good signal from the mug, even while
sitting on the same desk. This bridge was mostly written from my laptop.
If you want to use the Pi, try a USB-attached Bluetooth dongle!

## Whole-home coverage

This bridge intends to support running multiple instances in the same network.
It will use MQTT to detect devices which are announced, and attempt to keep
track of them. This is not deeply tested, but is a design goal, and might work.

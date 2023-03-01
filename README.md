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

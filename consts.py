#!/usr/bin/env python3
#
# Filename: consts.py
#
# Author: Simon Redman <simon@ergotech.com>
# File Created: 01.03.2023
# Last Modified: Sat 25 Feb 2023 08:38:58 PM EST
# Description: Constants used within the ember-mqtt-bridge project
#

import ember_mug.consts as ember_mug_consts

from typing import Dict, List

'''
Basically reverse the operation which happens in python-ember-mug/ember-mug/mug._notify_callback
'''
NAME_TO_EVENT_ID: Dict[str, ember_mug_consts.PushEvent] = {
    "liquid_state": ember_mug_consts.PushEvent.LIQUID_STATE_CHANGED,
}
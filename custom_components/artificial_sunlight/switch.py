# pylint: disable=fixme
"""Switch for the Artificial Sunlight integration."""
from __future__ import annotations

import asyncio
import bisect
from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass
import datetime
from datetime import timedelta
import functools
import hashlib
import logging
import math
from typing import Any, Optional, Union

import astral
import pytz

# from astral import SunDirection

# from astral.sun import SunDirection, Depression
import voluptuous as vol

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_BRIGHTNESS_PCT,
    ATTR_BRIGHTNESS_STEP,
    ATTR_BRIGHTNESS_STEP_PCT,
    ATTR_COLOR_NAME,
    ATTR_COLOR_TEMP,
    ATTR_HS_COLOR,
    ATTR_KELVIN,
    ATTR_RGB_COLOR,
    ATTR_TRANSITION,
    ATTR_WHITE_VALUE,
    ATTR_XY_COLOR,
    DOMAIN as LIGHT_DOMAIN,
    SUPPORT_BRIGHTNESS,
    SUPPORT_COLOR,
    SUPPORT_COLOR_TEMP,
    SUPPORT_TRANSITION,
    SUPPORT_WHITE_VALUE,
    VALID_TRANSITION,
    is_on,
    COLOR_MODE_RGB,
    COLOR_MODE_RGBW,
    COLOR_MODE_HS,
    COLOR_MODE_XY,
    COLOR_MODE_COLOR_TEMP,
    COLOR_MODE_BRIGHTNESS,
    ATTR_SUPPORTED_COLOR_MODES,
)

from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN, SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_DOMAIN,
    ATTR_ENTITY_ID,
    ATTR_SERVICE,
    ATTR_SERVICE_DATA,
    ATTR_SUPPORTED_FEATURES,
    CONF_NAME,
    EVENT_CALL_SERVICE,
    EVENT_HOMEASSISTANT_STARTED,
    EVENT_STATE_CHANGED,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
)
from homeassistant.core import (
    Context,
    Event,
    HomeAssistant,
    ServiceCall,
    State,
    callback,
)
from homeassistant.helpers import entity_platform
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.sun import get_astral_location
from homeassistant.util import slugify
from homeassistant.util.color import (
    color_RGB_to_xy,
    color_temperature_kelvin_to_mired,
    color_temperature_to_rgb,
    color_xy_to_hs,
)
import homeassistant.util.dt as dt_util

from .const import (
    ADAPT_BRIGHTNESS_SWITCH,
    ADAPT_COLOR_SWITCH,
    ATTR_ADAPT_BRIGHTNESS,
    ATTR_ADAPT_COLOR,
    ATTR_TURN_ON_OFF_LISTENER,
    CONF_DETECT_NON_HA_CHANGES,
    CONF_INITIAL_TRANSITION,
    CONF_SLEEP_TRANSITION,
    CONF_INTERVAL,
    CONF_LIGHTS,
    CONF_MANUAL_CONTROL,
    CONF_MAX_BRIGHTNESS,
    CONF_MAX_COLOR_TEMP,
    CONF_MIN_BRIGHTNESS,
    CONF_MIN_COLOR_TEMP,
    CONF_ONLY_ONCE,
    CONF_PREFER_RGB_COLOR,
    CONF_SEPARATE_TURN_ON_COMMANDS,
    CONF_SLEEP_BRIGHTNESS,
    CONF_SLEEP_COLOR_TEMP,
    CONF_SUNRISE_OFFSET,
    CONF_SUNRISE_TIME,
    CONF_SUNSET_OFFSET,
    CONF_SUNSET_TIME,
    CONF_TAKE_OVER_CONTROL,
    CONF_TRANSITION,
    CONF_TURN_ON_LIGHTS,
    DOMAIN,
    EXTRA_VALIDATION,
    ICON,
    SERVICE_APPLY,
    SERVICE_SET_MANUAL_CONTROL,
    SLEEP_MODE_SWITCH,
    EVENT_MIDNIGHT,
    EVENT_NOON,
    EVENT_SUNRISE,
    EVENT_SUNSET,
    EVENT_BLUE_HOUR_MORNING,
    EVENT_BLUE_GOLDEN_TRANSITION,
    EVENT_GOLDEN_HOUR_MORNING,
    EVENT_DAWN,
    EVENT_DUSK,
    EVENT_GOLDEN_HOUR_EVENING,
    EVENT_GOLDEN_BLUE_TRANSITION,
    EVENT_BLUE_HOUR_EVENING,
    TURNING_OFF_DELAY,
    VALIDATION_TUPLES,
    replace_none_str,
    ######### Natural change addition #########
    CONF_TWILIGHT_STAGE,
    CONF_LANDSCAPE_HORIZON,
    CONF_DAWN_COLOR_TEMP,
    CONF_DUSK_COLOR_TEMP,
    CONF_SUNRISE_COLOR_TEMP,
    CONF_SUNSET_COLOR_TEMP,
    CONF_BLUEHOUR_CT,
    CONF_USE_NIGHT_COLOR_RGB,
    CONF_NIGHT_COLOR,
    CONF_EXTEND_CCT_RGB_COLOR,
    ######### Natural change addition #########
)

_SUPPORT_OPTS = {
    "brightness": SUPPORT_BRIGHTNESS,
    "white_value": SUPPORT_WHITE_VALUE,
    "color_temp": SUPPORT_COLOR_TEMP,
    "color": SUPPORT_COLOR,
    "transition": SUPPORT_TRANSITION,
}

_ORDER = (
    EVENT_SUNRISE,
    EVENT_NOON,
    EVENT_SUNSET,
    EVENT_MIDNIGHT,
)
_ORDER_IlLUM = (
    EVENT_DAWN,
    EVENT_SUNRISE,
    EVENT_SUNSET,
    EVENT_DUSK,
)
_ORDER_CT = (
    EVENT_BLUE_HOUR_MORNING,
    EVENT_BLUE_GOLDEN_TRANSITION,
    EVENT_GOLDEN_HOUR_MORNING,
    EVENT_NOON,
    EVENT_GOLDEN_HOUR_EVENING,
    EVENT_GOLDEN_BLUE_TRANSITION,
    EVENT_BLUE_HOUR_EVENING,
    EVENT_MIDNIGHT,
)

_ALLOWED_ORDERS = {_ORDER[i:] + _ORDER[:i] for i in range(len(_ORDER))}
_ALLOWED_ORDERS_ILLUM = {
    _ORDER_IlLUM[i:] + _ORDER_IlLUM[:i] for i in range(len(_ORDER_IlLUM))
}
_ALLOWED_ORDERS_CT = {_ORDER_CT[i:] + _ORDER_CT[:i] for i in range(len(_ORDER_CT))}

_LOGGER = logging.getLogger(__name__)

# SCAN_INTERVAL = timedelta(seconds=10)  # HA Polling Data from HA API Intervall, seems to be not needed in that INtegration

# Thresholds f or checking if there was a manual light change outside this integration. Consider it a significant change when attribute changes more than
BRIGHTNESS_CHANGE = 25  # ≈10% of total range
COLOR_TEMP_CHANGE = 20  # ≈5% of total range
RGB_REDMEAN_CHANGE = 80  # ≈10% of total range

COLOR_ATTRS = {  # Should ATTR_PROFILE be in here?
    ATTR_COLOR_NAME,
    ATTR_COLOR_TEMP,
    ATTR_HS_COLOR,
    ATTR_KELVIN,
    ATTR_RGB_COLOR,
    ATTR_XY_COLOR,
}

BRIGHTNESS_ATTRS = {
    ATTR_BRIGHTNESS,
    ATTR_WHITE_VALUE,
    ATTR_BRIGHTNESS_PCT,
    ATTR_BRIGHTNESS_STEP,
    ATTR_BRIGHTNESS_STEP_PCT,
}

# Keep a short domain version for the context instances (which can only be 36 chars)
_DOMAIN_SHORT = "artif_lght"

# TODO Reorganize vars to a more hierachy style prefixes like: "ct_abcd", "illum_abcd"


def _short_hash(string: str, length: int = 4) -> str:
    """Create a hash of 'string' with length 'length'."""
    return hashlib.sha1(string.encode("UTF-8")).hexdigest()[:length]


def create_context(
    name: str, which: str, index: int, parent: Optional[Context] = None
) -> Context:
    """Create a context that can identify this integration."""
    # Use a hash for the name because otherwise the context might become
    # too long (max len == 36) to fit in the database.
    name_hash = _short_hash(name)
    parent_id = parent.id if parent else None
    return Context(
        id=f"{_DOMAIN_SHORT}_{name_hash}_{which}_{index}", parent_id=parent_id
    )


def is_our_context(context: Optional[Context]) -> bool:
    """Check whether this integration created 'context'."""
    if context is None:
        return False
    return context.id.startswith(_DOMAIN_SHORT)


def _split_service_data(service_data, adapt_brightness, adapt_color):
    """Split service_data into two dictionaries (for color and brightness)."""
    transition = service_data.get(ATTR_TRANSITION)
    if transition is not None:
        # Split the transition over both commands
        service_data[ATTR_TRANSITION] /= 2
    service_datas = []
    if adapt_color:
        service_data_color = service_data.copy()
        service_data_color.pop(ATTR_WHITE_VALUE, None)
        service_data_color.pop(ATTR_BRIGHTNESS, None)
        service_datas.append(service_data_color)
    if adapt_brightness:
        service_data_brightness = service_data.copy()
        service_data_brightness.pop(ATTR_RGB_COLOR, None)
        service_data_brightness.pop(ATTR_COLOR_TEMP, None)
        service_datas.append(service_data_brightness)
    return service_datas


async def handle_apply(switch: ArtifSunSwitch, service_call: ServiceCall):
    """Handle the entity service apply."""
    hass = switch.hass
    data = service_call.data
    all_lights = data[CONF_LIGHTS]
    if not all_lights:
        all_lights = switch._lights  # pylint: disable=protected-access
    all_lights = _expand_light_groups(hass, all_lights)
    switch.turn_on_off_listener.lights.update(all_lights)
    _LOGGER.debug(
        "Called 'artificial_sunlight.apply' service with '%s'",
        data,
    )
    for light in all_lights:
        if data[CONF_TURN_ON_LIGHTS] or is_on(hass, light):
            # COMMENT service call: Executing time independend coroutines for adapting a single entity
            await switch._adapt_light(  # pylint: disable=protected-access
                light,
                data[CONF_TRANSITION],
                data[ATTR_ADAPT_BRIGHTNESS],
                data[ATTR_ADAPT_COLOR],
                data[CONF_PREFER_RGB_COLOR],
                data[CONF_EXTEND_CCT_RGB_COLOR],
                force=True,
                context=switch.create_context("service", parent=service_call.context),
            )


async def handle_set_manual_control(switch: ArtifSunSwitch, service_call: ServiceCall):
    """Set or unset lights as 'manually controlled'."""
    lights = service_call.data[CONF_LIGHTS]
    if not lights:
        all_lights = switch._lights  # pylint: disable=protected-access
    else:
        all_lights = _expand_light_groups(switch.hass, lights)
    _LOGGER.debug(
        "Called 'artificial_sunlight.set_manual_control' service with '%s'",
        service_call.data,
    )
    if service_call.data[CONF_MANUAL_CONTROL]:
        for light in all_lights:
            switch.turn_on_off_listener.manual_control[light] = True
            _fire_manual_control_event(switch, light, service_call.context)
    else:
        switch.turn_on_off_listener.reset(*all_lights)
        # pylint: disable=protected-access
        if switch.is_on:
            _LOGGER.debug("Manual control light")
            await switch._update_attrs_and_maybe_adapt_lights(
                all_lights,
                transition=switch._initial_transition,
                force=True,
                context=switch.create_context("service", parent=service_call.context),
            )


@callback
def _fire_manual_control_event(
    switch: ArtifSunSwitch, light: str, context: Context, is_async=True
):
    """Fire an event that 'light' is marked as manual_control."""
    hass = switch.hass
    fire = hass.bus.async_fire if is_async else hass.bus.fire
    _LOGGER.debug(
        "'artificial_sunlight.manual_control' event fired for %s for light %s",
        switch.entity_id,
        light,
    )
    fire(
        f"{DOMAIN}.manual_control",
        {ATTR_ENTITY_ID: light, SWITCH_DOMAIN: switch.entity_id},
        context=context,
    )


########  Subscribe to HASS, Fetch initial data so we have data when entities subscribe  ########


async def async_setup_entry(
    hass: HomeAssistant, config_entry: ConfigEntry, async_add_entities: bool
):
    """Set up the ArtificialSunlight switch."""
    data = hass.data[DOMAIN]
    assert config_entry.entry_id in data

    if ATTR_TURN_ON_OFF_LISTENER not in data:
        data[ATTR_TURN_ON_OFF_LISTENER] = TurnOnOffListener(hass)
    turn_on_off_listener = data[ATTR_TURN_ON_OFF_LISTENER]
    loc = get_astral_location(hass)
    sleep_mode_switch = SimpleSwitch("Sleep Mode", False, hass, config_entry)
    adapt_color_switch = SimpleSwitch("Adapt Color", True, hass, config_entry)
    adapt_brightness_switch = SimpleSwitch("Adapt Brightness", True, hass, config_entry)

    switch = ArtifSunSwitch(
        hass,
        config_entry,
        turn_on_off_listener,
        sleep_mode_switch,
        adapt_color_switch,
        adapt_brightness_switch,
        loc,
    )

    data[config_entry.entry_id][SLEEP_MODE_SWITCH] = sleep_mode_switch
    data[config_entry.entry_id][ADAPT_COLOR_SWITCH] = adapt_color_switch
    data[config_entry.entry_id][ADAPT_BRIGHTNESS_SWITCH] = adapt_brightness_switch
    data[config_entry.entry_id][SWITCH_DOMAIN] = switch

    async_add_entities(
        [switch, sleep_mode_switch, adapt_color_switch, adapt_brightness_switch],
        update_before_add=True,
    )

    # Register `apply` service
    platform = entity_platform.current_platform.get()
    platform.async_register_entity_service(
        SERVICE_APPLY,
        {
            vol.Optional(
                CONF_LIGHTS, default=[]
            ): cv.entity_ids,  # pylint: disable=protected-access
            vol.Optional(
                CONF_TRANSITION,
                default=switch._initial_transition,  # pylint: disable=protected-access
            ): VALID_TRANSITION,
            vol.Optional(ATTR_ADAPT_BRIGHTNESS, default=True): cv.boolean,
            vol.Optional(ATTR_ADAPT_COLOR, default=True): cv.boolean,
            vol.Optional(CONF_PREFER_RGB_COLOR, default=False): cv.boolean,
            vol.Optional(CONF_TURN_ON_LIGHTS, default=False): cv.boolean,
        },
        handle_apply,
    )

    platform.async_register_entity_service(
        SERVICE_SET_MANUAL_CONTROL,
        {
            vol.Optional(CONF_LIGHTS, default=[]): cv.entity_ids,
            vol.Optional(CONF_MANUAL_CONTROL, default=True): cv.boolean,
        },
        handle_set_manual_control,
    )


def validate(config_entry: ConfigEntry):
    """Get the options and data from the config_entry and add defaults."""
    defaults = {key: default for key, default, _ in VALIDATION_TUPLES}
    data = deepcopy(defaults)
    data.update(config_entry.options)  # come from options flow
    data.update(config_entry.data)  # all yaml settings come from data
    data = {key: replace_none_str(value) for key, value in data.items()}
    for key, (validate_value, _) in EXTRA_VALIDATION.items():
        value = data.get(key)
        if value is not None:
            data[key] = validate_value(value)  # Fix the types of the inputs
    return data


def match_switch_state_event(event: Event, from_or_to_state: list[str]):
    """Match state event when either 'from_state' or 'to_state' matches."""
    old_state = event.data.get("old_state")
    from_state_match = old_state is not None and old_state.state in from_or_to_state

    new_state = event.data.get("new_state")
    to_state_match = new_state is not None and new_state.state in from_or_to_state

    match = from_state_match or to_state_match
    return match


def _expand_light_groups(hass: HomeAssistant, lights: list[str]) -> list[str]:
    all_lights = set()
    turn_on_off_listener = hass.data[DOMAIN][ATTR_TURN_ON_OFF_LISTENER]
    for light in lights:
        state = hass.states.get(light)
        if state is None:
            _LOGGER.debug("State of %s is None", light)
            all_lights.add(light)
        elif "entity_id" in state.attributes:  # it's a light group
            group = state.attributes["entity_id"]
            turn_on_off_listener.lights.discard(light)
            all_lights.update(group)
            _LOGGER.debug("Expanded %s to %s", light, group)
        else:
            all_lights.add(light)
    return list(all_lights)


def _supported_features(hass: HomeAssistant, light: str):
    state = hass.states.get(light)
    supported_features = state.attributes[ATTR_SUPPORTED_FEATURES]
    supported = {
        key for key, value in _SUPPORT_OPTS.items() if supported_features & value
    }
    supported_color_modes = state.attributes.get(ATTR_SUPPORTED_COLOR_MODES, set())
    if COLOR_MODE_RGB in supported_color_modes:
        supported.add("color")
        # Adding brightness here, see
        # comment https://github.com/basnijholt/adaptive-lighting/issues/112#issuecomment-836944011
        supported.add("brightness")
    if COLOR_MODE_RGBW in supported_color_modes:
        supported.add("color")
        supported.add("brightness")  # see above url
    if COLOR_MODE_XY in supported_color_modes:
        supported.add("color")
        supported.add("brightness")  # see above url
    if COLOR_MODE_HS in supported_color_modes:
        supported.add("color")
        supported.add("brightness")  # see above url
    if COLOR_MODE_COLOR_TEMP in supported_color_modes:
        supported.add("color_temp")
        supported.add("brightness")  # see above url
    if COLOR_MODE_BRIGHTNESS in supported_color_modes:
        supported.add("brightness")
    return supported


def color_difference_redmean(
    rgb1: tuple[float, float, float], rgb2: tuple[float, float, float]
) -> float:
    """Distance between colors in RGB space (redmean metric).

    The maximal distance between (255, 255, 255) and (0, 0, 0) ≈ 765.

    Sources:
    - https://en.wikipedia.org/wiki/Color_difference#Euclidean
    - https://www.compuphase.com/cmetric.htm
    """
    r_hat = (rgb1[0] + rgb2[0]) / 2
    delta_r, delta_g, delta_b = [(col1 - col2) for col1, col2 in zip(rgb1, rgb2)]
    red_term = (2 + r_hat / 256) * delta_r**2
    green_term = 4 * delta_g**2
    blue_term = (2 + (255 - r_hat) / 256) * delta_b**2
    return math.sqrt(red_term + green_term + blue_term)


def _attributes_have_changed(
    light: str,
    old_attributes: dict[str, Any],
    new_attributes: dict[str, Any],
    adapt_brightness: bool,
    adapt_color: bool,
    context: Context,
) -> bool:
    if (
        adapt_brightness
        and ATTR_BRIGHTNESS in old_attributes
        and ATTR_BRIGHTNESS in new_attributes
    ):
        last_brightness = old_attributes[ATTR_BRIGHTNESS]
        current_brightness = new_attributes[ATTR_BRIGHTNESS]
        if abs(current_brightness - last_brightness) > BRIGHTNESS_CHANGE:
            _LOGGER.debug(
                "Brightness of '%s' significantly changed from %s to %s with"
                " context.id='%s'",
                light,
                last_brightness,
                current_brightness,
                context.id,
            )
            return True

    if (
        adapt_brightness
        and ATTR_WHITE_VALUE in old_attributes
        and ATTR_WHITE_VALUE in new_attributes
    ):
        last_white_value = old_attributes[ATTR_WHITE_VALUE]
        current_white_value = new_attributes[ATTR_WHITE_VALUE]
        if abs(current_white_value - last_white_value) > BRIGHTNESS_CHANGE:
            _LOGGER.debug(
                "White Value of '%s' significantly changed from %s to %s with"
                " context.id='%s'",
                light,
                last_white_value,
                current_white_value,
                context.id,
            )
            return True

    if (
        adapt_color
        and ATTR_COLOR_TEMP in old_attributes
        and ATTR_COLOR_TEMP in new_attributes
    ):
        last_color_temp = old_attributes[ATTR_COLOR_TEMP]
        current_color_temp = new_attributes[ATTR_COLOR_TEMP]
        if abs(current_color_temp - last_color_temp) > COLOR_TEMP_CHANGE:
            _LOGGER.debug(
                "Color temperature of '%s' significantly changed from %s to %s with"
                " context.id='%s'",
                light,
                last_color_temp,
                current_color_temp,
                context.id,
            )
            return True

    if (
        adapt_color
        and ATTR_RGB_COLOR in old_attributes
        and ATTR_RGB_COLOR in new_attributes
    ):
        last_rgb_color = old_attributes[ATTR_RGB_COLOR]
        current_rgb_color = new_attributes[ATTR_RGB_COLOR]
        redmean_change = color_difference_redmean(last_rgb_color, current_rgb_color)
        if redmean_change > RGB_REDMEAN_CHANGE:
            _LOGGER.debug(
                "color RGB of '%s' significantly changed from %s to %s with"
                " context.id='%s'",
                light,
                last_rgb_color,
                current_rgb_color,
                context.id,
            )
            return True

    switched_color_temp = (
        ATTR_RGB_COLOR in old_attributes and ATTR_RGB_COLOR not in new_attributes
    )
    switched_to_rgb_color = (
        ATTR_COLOR_TEMP in old_attributes and ATTR_COLOR_TEMP not in new_attributes
    )
    if switched_color_temp or switched_to_rgb_color:
        # Light switched from RGB mode to color_temp or visa versa
        _LOGGER.debug(
            "'%s' switched from RGB mode to color_temp or visa versa",
            light,
        )
        return True
    return False


class ArtifSunSwitch(SwitchEntity, RestoreEntity):
    """Representation of a Artificial Sunlight switch."""

    def __init__(
        self,
        hass,
        config_entry: ConfigEntry,
        turn_on_off_listener: TurnOnOffListener,
        sleep_mode_switch: SimpleSwitch,
        adapt_color_switch: SimpleSwitch,
        adapt_brightness_switch: SimpleSwitch,
        loc,
    ):
        """Initialize the Artificial Sunlight switch."""
        self.hass = hass
        self.turn_on_off_listener = turn_on_off_listener
        self.sleep_mode_switch = sleep_mode_switch
        self.adapt_color_switch = adapt_color_switch
        self.adapt_brightness_switch = adapt_brightness_switch

        data = validate(config_entry)
        self._name = data[CONF_NAME]
        self._lights = data[CONF_LIGHTS]

        self._detect_non_ha_changes = data[CONF_DETECT_NON_HA_CHANGES]
        self._initial_transition = data[CONF_INITIAL_TRANSITION]
        self._sleep_transition = data[CONF_SLEEP_TRANSITION]
        self._interval = data[CONF_INTERVAL]
        self._only_once = data[CONF_ONLY_ONCE]
        self._prefer_rgb_color = data[CONF_PREFER_RGB_COLOR]
        self._separate_turn_on_commands = data[CONF_SEPARATE_TURN_ON_COMMANDS]
        self._take_over_control = data[CONF_TAKE_OVER_CONTROL]
        self._transition = data[CONF_TRANSITION]

        self._use_night_color = data[CONF_USE_NIGHT_COLOR_RGB]
        self._extend_cct_rgb_color = data[CONF_EXTEND_CCT_RGB_COLOR]
        self._night_col = data[CONF_NIGHT_COLOR]

        if isinstance(loc, tuple):
            # Astral v2.2
            a_location, obs_elevation = loc

        # lat = self.hass.config.latitude
        # lon = self.hass.config.longitude
        # elev = self.hass.config.elevation
        # tz = self.hass.config.time_zone
        # observer = astral.Observer(lat, lon, elev)

        self._sun_light_settings = SunSettings(
            name=self._name,
            astral_location=a_location,
            elevation_observer=obs_elevation,
            max_brightness=data[CONF_MAX_BRIGHTNESS],
            max_color_temp=data[CONF_MAX_COLOR_TEMP],
            min_brightness=data[CONF_MIN_BRIGHTNESS],
            min_color_temp=data[CONF_MIN_COLOR_TEMP],
            sleep_brightness=data[CONF_SLEEP_BRIGHTNESS],
            sleep_color_temp=data[CONF_SLEEP_COLOR_TEMP],
            sunrise_offset=data[CONF_SUNRISE_OFFSET],
            sunrise_time=data[CONF_SUNRISE_TIME],
            sunset_offset=data[CONF_SUNSET_OFFSET],
            sunset_time=data[CONF_SUNSET_TIME],
            time_zone=self.hass.config.time_zone,
            transition=data[CONF_TRANSITION],
            depression=data[CONF_TWILIGHT_STAGE],
            horizon=data[CONF_LANDSCAPE_HORIZON],
            dawn_ct=data[CONF_DAWN_COLOR_TEMP],
            dusk_ct=data[CONF_DUSK_COLOR_TEMP],
            sunrise_ct=data[CONF_SUNRISE_COLOR_TEMP],
            sunset_ct=data[CONF_SUNSET_COLOR_TEMP],
            bl_hr_ct=data[CONF_BLUEHOUR_CT],
            use_night_color=data[CONF_USE_NIGHT_COLOR_RGB],
            night_col=data[CONF_NIGHT_COLOR],
        )

        # Set other attributes
        self._icon = ICON
        self._state = None

        # Tracks 'off' → 'on' state changes
        self._on_to_off_event: dict[str, Event] = {}
        # Tracks 'on' → 'off' state changes
        self._off_to_on_event: dict[str, Event] = {}
        # Locks that prevent light adjusting when waiting for a light to 'turn_off'
        self._locks: dict[str, asyncio.Lock] = {}
        # To count the number of `Context` instances
        self._context_cnt: int = 0

        # Set in self._update_attrs_and_maybe_adapt_lights
        self._settings: dict[str, Any] = {}

        # Set and unset tracker in async_turn_on and async_turn_off
        self.remove_listeners = []
        _LOGGER.debug(
            "%s: Setting up with '%s',"
            " config_entry.data: '%s',"
            " config_entry.options: '%s', converted to '%s'",
            self._name,
            self._lights,
            config_entry.data,
            config_entry.options,
            data,
        )
        # print("_sun_light_settings: " + str(self._sun_light_settings))

    @property
    def name(self):
        """Return the name of the device if any."""
        return f"Artificial Sunlight: {self._name}"

    @property
    def unique_id(self):
        """Return the unique ID of entity."""
        return self._name

    @property
    def is_on(self) -> Optional[bool]:
        """Return true if natural artificial sunlight is on."""
        return self._state

    async def async_added_to_hass(self) -> None:
        """Call when entity about to be added to hass."""
        if self.hass.is_running:
            await self._setup_listeners()
        else:
            self.hass.bus.async_listen_once(
                EVENT_HOMEASSISTANT_STARTED, self._setup_listeners
            )
        last_state = await self.async_get_last_state()
        is_new_entry = last_state is None  # newly added to HA
        if is_new_entry or last_state.state == STATE_ON:
            await self.async_turn_on(adapt_lights=not self._only_once)
        else:
            self._state = False
            assert not self.remove_listeners

    async def async_will_remove_from_hass(self):
        """Remove the listeners upon removing the component."""
        self._remove_listeners()

    def _expand_light_groups(self) -> None:
        all_lights = _expand_light_groups(self.hass, self._lights)
        self.turn_on_off_listener.lights.update(all_lights)
        self._lights = list(all_lights)

    async def _setup_listeners(self, _=None) -> None:
        _LOGGER.debug("%s: Called '_setup_listeners'", self._name)
        if not self.is_on or not self.hass.is_running:
            _LOGGER.debug(
                "%s: Cancelled '_setup_listeners', System not ready yet", self._name
            )
            return

        assert not self.remove_listeners

        remove_interval = async_track_time_interval(
            self.hass, self._async_update_at_interval, self._interval
        )
        remove_sleep = async_track_state_change_event(
            self.hass,
            self.sleep_mode_switch.entity_id,
            self._sleep_mode_switch_state_event,
        )

        self.remove_listeners.extend([remove_interval, remove_sleep])

        if self._lights:
            self._expand_light_groups()
            remove_state = async_track_state_change_event(
                self.hass, self._lights, self._light_event
            )
            self.remove_listeners.append(remove_state)
        _LOGGER.debug(
            "%s: Finished '_setup_listeners', start with Interval", self._name
        )

    def _remove_listeners(self) -> None:
        while self.remove_listeners:
            remove_listener = self.remove_listeners.pop()
            remove_listener()

    @property
    def icon(self) -> str:
        """Icon to use in the frontend, if any."""
        return self._icon

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the attributes of the switch."""
        if not self.is_on:
            return {key: None for key in self._settings}
        manual_control = [
            light
            for light in self._lights
            if self.turn_on_off_listener.manual_control.get(light)
        ]
        return dict(self._settings, manual_control=manual_control)

    def create_context(
        self, which: str = "default", parent: Optional[Context] = None
    ) -> Context:
        """Create a context that identifies this Artificial Sunlight instance."""
        # Right now the highest number of each context_id it can create is
        # 'adapt_lgt_XXXX_turn_on_9999999999999'
        # 'adapt_lgt_XXXX_interval_999999999999'
        # 'adapt_lgt_XXXX_adapt_lights_99999999'
        # 'adapt_lgt_XXXX_sleep_999999999999999'
        # 'adapt_lgt_XXXX_light_event_999999999'
        # 'adapt_lgt_XXXX_service_9999999999999'
        # So 100 million calls before we run into the 36 chars limit.
        context = create_context(self._name, which, self._context_cnt, parent=parent)
        self._context_cnt += 1
        return context

    async def async_turn_on(  # pylint: disable=arguments-differ
        self, adapt_lights: bool = True
    ) -> None:
        """Turn on natural artificial sunlight."""
        _LOGGER.debug(
            "%s: Called 'async_turn_on', current state is '%s'", self._name, self._state
        )
        if self.is_on:
            return
        self._state = True
        self.turn_on_off_listener.reset(*self._lights)
        await self._setup_listeners()
        if adapt_lights:
            _LOGGER.debug("%s: Initial control light", self._name)
            await self._update_attrs_and_maybe_adapt_lights(
                transition=self._initial_transition,
                force=True,
                context=self.create_context("turn_on"),
            )

    async def async_turn_off(self, **kwargs) -> None:
        """Turn off natural artificial sunlight."""
        if not self.is_on:
            return
        self._state = False
        self._remove_listeners()
        self.turn_on_off_listener.reset(*self._lights)

    async def _async_update_at_interval(self, now=None) -> None:
        _LOGGER.debug("%s: Loop control light", self._name)
        await self._update_attrs_and_maybe_adapt_lights(
            transition=self._transition,
            force=False,
            context=self.create_context("interval"),
        )

    # COMMENT Function which prepares update data to Hass and updates with "light.turn_on" / "light.turn_off" on a single entity
    async def _adapt_light(
        self,
        light: str,
        transition: Optional[int] = None,
        adapt_brightness: Optional[bool] = None,
        adapt_color: Optional[bool] = None,
        prefer_rgb_color: Optional[bool] = None,
        extend_cct_rgb_color: Optional[bool] = None,
        force: bool = False,
        context: Optional[Context] = None,
    ) -> None:
        lock = self._locks.get(light)
        if lock is not None and lock.locked():
            _LOGGER.debug("%s: '%s' is locked", self._name, light)
            return
        service_data = {ATTR_ENTITY_ID: light}
        features = _supported_features(self.hass, light)

        if transition is None:
            transition = self._transition
        if adapt_brightness is None:
            adapt_brightness = self.adapt_brightness_switch.is_on
        if adapt_color is None:
            adapt_color = self.adapt_color_switch.is_on
        if prefer_rgb_color is None:
            prefer_rgb_color = self._prefer_rgb_color
        if extend_cct_rgb_color is None:
            extend_cct_rgb_color = self._extend_cct_rgb_color

        if "transition" in features:
            service_data[ATTR_TRANSITION] = transition

        if "brightness" in features and adapt_brightness:
            brightness = round(255 * self._settings["brightness_pct"] / 100)
            service_data[ATTR_BRIGHTNESS] = brightness

        if "white_value" in features and adapt_brightness:
            white_value = round(255 * self._settings["brightness_pct"] / 100)
            service_data[ATTR_WHITE_VALUE] = white_value

        # TODO use max/min mired for transition between ct and rgb to extend CT Range of CCT / RGB entity

        if (
            "color" in features and adapt_color and not "color_temp" in features
        ):  # COMMENT: Logic for RGB and RGB CCT if RGB is prefered
            service_data[ATTR_RGB_COLOR] = self._settings["rgb_color"]

        if (
            "color_temp" in features and adapt_color and not ("color" in features)
        ):  # COMMENT: Logic for CT only Lights and RGB CCT Lights if not RGB Color prefered
            attributes = self.hass.states.get(light).attributes
            min_mireds, max_mireds = attributes["min_mireds"], attributes["max_mireds"]
            color_temp_mired = self._settings["color_temp_mired"]
            # color_temp_mired = max(min(color_temp_mired, max_mireds), min_mireds)
            # service_data[ATTR_COLOR_TEMP] = color_temp_mired
        elif (
            "color_temp" and "color" in features and adapt_color
        ):  # COMMENT: Logic for RGB CCT Lights to extend CT with RGB
            attributes = self.hass.states.get(light).attributes
            min_mireds, max_mireds = attributes["min_mireds"], attributes["max_mireds"]
            color_temp_mired = self._settings["color_temp_mired"]
            if (
                (
                    extend_cct_rgb_color
                    and not min_mireds <= color_temp_mired <= max_mireds
                )
                or prefer_rgb_color
                or (self._settings["use_night_color"] and self._settings["night"])
            ):
                service_data[ATTR_RGB_COLOR] = self._settings["rgb_color"]
            else:
                color_temp_mired = max(min(color_temp_mired, max_mireds), min_mireds)
                service_data[ATTR_COLOR_TEMP] = color_temp_mired
            # and (prefer_rgb_color or use_night_color)
        # if (
        #
        #     and adapt_color
        #     and not (prefer_rgb_color and "color" in features)
        # ):  # COMMENT: Logic for CT only Lights and RGB CCT Lights
        #     attributes = self.hass.states.get(light).attributes
        #     min_mireds, max_mireds = attributes["min_m
        #     color_temp_mired = self._settings["color_temp_mired"]
        #     color_temp_mired = max(min(color_temp_mired, max_mireds), min_mireds)
        #     service_data[ATTR_COLOR_TEMP] = color_temp_mired
        # elif (
        #     "color" in features and adapt_color
        # ):  # COMMENT: Logic for RGB and RGB CCT if RGB is prefered
        #     service_data[ATTR_RGB_COLOR] = self._settings["rgb_color"]

        ####

        context = context or self.create_context("adapt_lights")
        if (
            self._take_over_control
            and self._detect_non_ha_changes
            and not force
            and await self.turn_on_off_listener.significant_change(
                self,
                light,
                adapt_brightness,
                adapt_color,
                context,
            )
        ):
            return
        self.turn_on_off_listener.last_service_data[light] = service_data

        # Function which is sending actual data change to Hass
        async def turn_on(service_data):
            _LOGGER.info(
                "%s: Service called 'light.turn_on' on: %s",
                self._name,
                service_data,
            )
            # _LOGGER.debug(
            #     "%s: Scheduling 'light.turn_on' with the following 'service_data': %s"
            #     " with context.id='%s'",
            #     self._name,
            #     service_data,
            #     context.id,
            # )

            # Call to send Data to Hass
            await self.hass.services.async_call(
                LIGHT_DOMAIN,
                SERVICE_TURN_ON,
                service_data,
                context=context,
            )

        if not self._separate_turn_on_commands:
            await turn_on(service_data)
        else:
            # Could be a list of length 1 or 2
            service_datas = _split_service_data(
                service_data, adapt_brightness, adapt_color
            )
            items_service_datas = len(service_datas)
            await turn_on(service_datas[0])
            if items_service_datas == 2:
                transition = service_datas[0].get(ATTR_TRANSITION)
                if transition is not None:
                    await asyncio.sleep(transition)
                await turn_on(service_datas[1])

    async def _update_attrs_and_maybe_adapt_lights(
        self,
        lights: Optional[list[str]] = None,
        transition: Optional[int] = None,
        force: bool = False,
        context: Optional[Context] = None,
    ) -> None:
        assert context is not None
        _LOGGER.debug(
            "%s: '_update_attrs_and_maybe_adapt_lights' called with context.id='%s'",
            self._name,
            context.id,
        )
        assert self.is_on
        # COMMENT Retrieve actual illumination/color settings based on astral/manual logic
        self._settings = self._sun_light_settings.get_settings(
            self.sleep_mode_switch.is_on, transition
        )
        if lights is None:
            lights = self._lights
        if (self._only_once and not force) or not lights:
            return
        await self._adapt_lights(lights, transition, force, context)

    async def _adapt_lights(
        self,
        lights: list[str],
        transition: Optional[int],
        force: bool,
        context: Optional[Context],
    ) -> None:
        assert context is not None
        _LOGGER.debug(
            "%s: '_adapt_lights(%s, %s, force=%s, context.id=%s)' called",
            self.name,
            lights,
            transition,
            force,
            context.id,
        )
        for light in lights:
            if not is_on(self.hass, light):
                continue
            if (
                self._take_over_control
                and self.turn_on_off_listener.is_manually_controlled(
                    self,
                    light,
                    force,
                    self.adapt_brightness_switch.is_on,
                    self.adapt_color_switch.is_on,
                )
            ):
                _LOGGER.warning(
                    "%s: '%s' is being manually controlled, stop adapting, context.id=%s",
                    self._name,
                    light,
                    context.id,
                )
                continue
            # COMMENT Executing time independend coroutines for adapting a single entity in running loops
            await self._adapt_light(light, transition, force=force, context=context)

    async def _sleep_mode_switch_state_event(self, event: Event) -> None:
        if not match_switch_state_event(event, (STATE_ON, STATE_OFF)):
            return
        _LOGGER.debug(
            "%s: _sleep_mode_switch_state_event, event: '%s'", self._name, event
        )
        # Reset the manually controlled status when the "sleep mode" changes
        self.turn_on_off_listener.reset(*self._lights)
        _LOGGER.debug("%s: Sleep mode control light", self._name)
        await self._update_attrs_and_maybe_adapt_lights(
            transition=self._sleep_transition,
            force=True,
            context=self.create_context("sleep", parent=event.context),
        )

    async def _light_event(self, event: Event) -> None:
        old_state = event.data.get("old_state")
        new_state = event.data.get("new_state")
        entity_id = event.data.get("entity_id")
        if (
            old_state is not None
            and old_state.state == STATE_OFF
            and new_state is not None
            and new_state.state == STATE_ON
        ):
            _LOGGER.debug(
                "%s: Detected a 'off' → 'on' event for '%s' with context.id='%s'",
                self._name,
                entity_id,
                event.context.id,
            )
            self.turn_on_off_listener.reset(entity_id, reset_manual_control=False)
            # Tracks 'off' → 'on' state changes
            self._off_to_on_event[entity_id] = event
            lock = self._locks.get(entity_id)
            if lock is None:
                lock = self._locks[entity_id] = asyncio.Lock()
            async with lock:
                if await self.turn_on_off_listener.maybe_cancel_adjusting(
                    entity_id,
                    off_to_on_event=event,
                    on_to_off_event=self._on_to_off_event.get(entity_id),
                ):
                    # Stop if a rapid 'off' → 'on' → 'off' happens.
                    _LOGGER.warning(
                        "%s: Cancelling adjusting lights for %s", self._name, entity_id
                    )
                    return
            _LOGGER.debug("%s: Light Event control light", self._name)
            await self._update_attrs_and_maybe_adapt_lights(
                lights=[entity_id],
                transition=self._initial_transition,
                force=True,
                context=self.create_context("light_event", parent=event.context),
            )
        elif (
            old_state is not None
            and old_state.state == STATE_ON
            and new_state is not None
            and new_state.state == STATE_OFF
        ):
            _LOGGER.debug(
                "%s: Detected a 'on' → 'off' event for '%s' with context.id='%s'",
                self._name,
                entity_id,
                event.context.id,
            )
            # Tracks 'off' → 'on' state changes
            self._on_to_off_event[entity_id] = event
            self.turn_on_off_listener.reset(entity_id)


class SimpleSwitch(SwitchEntity, RestoreEntity):
    """Representation of a Artificial Sunlight switch."""

    def __init__(
        self, which: str, initial_state: bool, hass: HomeAssistant, config_entry
    ):
        """Initialize the Artificial Sunlight switch."""
        self.hass = hass
        data = validate(config_entry)
        self._icon = ICON
        self._state = None
        self._which = which
        name = data[CONF_NAME]
        self._unique_id = f"{name}_{slugify(self._which)}"
        self._name = f"Artificial Sunlight {which}: {name}"
        self._initial_state = initial_state

    @property
    def name(self):
        """Return the name of the device if any."""
        return self._name

    @property
    def unique_id(self):
        """Return the unique ID of entity."""
        return self._unique_id

    @property
    def icon(self) -> str:
        """Icon to use in the frontend, if any."""
        return self._icon

    @property
    def is_on(self) -> Optional[bool]:
        """Return true if natural artificial sunlight is on."""
        return self._state

    # register ArtificialSunlight light switches to Hass using last state.
    async def async_added_to_hass(self) -> None:
        """Call when entity about to be added to hass."""
        last_state = await self.async_get_last_state()
        _LOGGER.debug("%s: last state is %s", self._name, last_state)
        if (last_state is None and self._initial_state) or (
            last_state is not None and last_state.state == STATE_ON
        ):
            await self.async_turn_on()
        else:
            await self.async_turn_off()

    async def async_turn_on(self, **kwargs) -> None:
        """Turn on natural artificial sunlight sleep mode."""
        self._state = True

    async def async_turn_off(self, **kwargs) -> None:
        """Turn off natural artificial sunlight sleep mode."""
        self._state = False


@dataclass(frozen=True)
class SunSettings:
    """Sunlight Settings: Track the state of the sun and associated light settings."""

    name: str
    astral_location: astral.location
    elevation_observer: float
    max_brightness: int
    max_color_temp: int
    min_brightness: int
    min_color_temp: int
    sleep_brightness: int
    sleep_color_temp: int
    sunrise_offset: Optional[datetime.timedelta]
    sunrise_time: Optional[datetime.time]
    sunset_offset: Optional[datetime.timedelta]
    sunset_time: Optional[datetime.time]
    time_zone: datetime.tzinfo
    transition: int
    depression: str
    horizon: float
    dawn_ct: int
    dusk_ct: int
    sunrise_ct: int
    sunset_ct: int
    bl_hr_ct: int
    use_night_color: Optional[bool]
    night_col: tuple[int, int, int]

    def get_sun_events(self, date: datetime.datetime) -> dict[str, float]:
        """Get the four sun event's timestamps at 'date'."""
        # This function is called three times, for yesterday, today and tomorrow for the current date each interval

        def _replace_time(date: datetime.datetime, time) -> datetime.datetime:
            date_time = datetime.datetime.combine(date, time)
            try:  # HA ≤2021.05, https://github.com/basnijholt/adaptive-lighting/issues/128
                utc_time = self.time_zone.localize(date_time).astimezone(dt_util.UTC)
            except AttributeError:  # HA ≥2021.06
                utc_time = date_time.replace(
                    tzinfo=dt_util.DEFAULT_TIME_ZONE
                ).astimezone(dt_util.UTC)
            return utc_time

        # TODO reorganize astral using own Observer instead HASS integrated function
        location = self.astral_location

        # Preperation for fetching Sun Values
        location.solar_depression = self.depression
        rising = astral.SunDirection.RISING
        setting = astral.SunDirection.SETTING

        # TODO Reorganize with Local TZ

        # New Values for Brightness render
        SunSettings.dawn = location.dawn(
            date, local=False, observer_elevation=self.elevation_observer
        )
        SunSettings.dusk = location.dusk(
            date, local=False, observer_elevation=self.elevation_observer
        )
        SunSettings.lscpe_hrzn_mrng = location.time_at_elevation(
            self.horizon, date, rising, local=False
        )
        SunSettings.lscpe_hrzn_eve = location.time_at_elevation(
            self.horizon, date, setting, local=False
        )
        (SunSettings.daylight_strt, SunSettings.daylight_end) = location.daylight(
            date, local=False
        )
        (SunSettings.night_strt, SunSettings.night_end) = location.night(
            date, local=False
        )

        # Get Sunrise and Sunset depending on Sun Depression Setting  with additional Offset or manual set Times with additional Offset
        if self.horizon and (self.sunrise_time is None or self.sunset_time is None):
            SunSettings.sunrise = SunSettings.lscpe_hrzn_mrng
            SunSettings.sunset = SunSettings.lscpe_hrzn_eve
        else:
            SunSettings.sunrise = (
                location.sunrise(
                    date, local=False, observer_elevation=self.elevation_observer
                )
                if self.sunrise_time is None
                else _replace_time(date, (getattr(self, "sunrise_time")))
            )
            SunSettings.sunset = (
                location.sunset(
                    date, local=False, observer_elevation=self.elevation_observer
                )
                if self.sunset_time is None
                else _replace_time(date, (getattr(self, "sunset_time")))
            )
        SunSettings.sunrise += self.sunrise_offset
        SunSettings.sunset += self.sunset_offset

        # From here: get Color Render Values
        SunSettings.solar_noon = location.noon(date, local=False)
        SunSettings.solar_midnight = location.midnight(date, local=False)
        # + timedelta(days=1)
        if (
            SunSettings.solar_midnight.date() < date.date()
            and SunSettings.solar_noon < date
        ):
            # if SunSettings.solar_midnight.date() == (date.date() - timedelta(days=2)):
            SunSettings.prev_solar_midnight = location.midnight(
                (date + timedelta(days=1)), local=False
            )
            SunSettings.next_solar_midnight = location.midnight(
                (date + timedelta(days=1)), local=False
            )
            (SunSettings.next_bl_hr_mrnng_strt, _,) = location.blue_hour(
                rising,
                (date + timedelta(days=1)),
                local=False,
                observer_elevation=self.elevation_observer,
            )
        else:
            SunSettings.next_solar_midnight = location.midnight(
                (date + timedelta(days=1)), local=False
            )
            SunSettings.prev_solar_midnight = location.midnight(date, local=False)
            (SunSettings.next_bl_hr_mrnng_strt, _,) = location.blue_hour(
                rising,
                date,
                local=False,
                observer_elevation=self.elevation_observer,
            )
        (
            SunSettings.bl_hr_mrnng_strt,
            SunSettings.bl_hr_mrnng_end,
        ) = location.blue_hour(
            rising,
            date,
            local=False,
            observer_elevation=self.elevation_observer,
        )
        (SunSettings.bl_hr_nght_strt, SunSettings.bl_hr_nght_end,) = location.blue_hour(
            setting,
            date,
            local=False,
            observer_elevation=self.elevation_observer,
        )
        (
            SunSettings.gldn_hr_mrnng_strt,
            SunSettings.gldn_hr_mrnng_end,
        ) = location.golden_hour(
            rising,
            date,
            local=False,
            observer_elevation=self.elevation_observer,
        )
        (
            SunSettings.gldn_hr_nght_strt,
            SunSettings.gldn_hr_nght_end,
        ) = location.golden_hour(
            setting,
            date,
            local=False,
            observer_elevation=self.elevation_observer,
        )

        # get current solar elevation
        SunSettings.solar_elevation = location.solar_elevation(date)

        events = [
            (EVENT_SUNRISE, SunSettings.sunrise.timestamp()),
            (EVENT_SUNSET, SunSettings.sunset.timestamp()),
            (EVENT_NOON, SunSettings.solar_noon.timestamp()),
            (EVENT_MIDNIGHT, SunSettings.solar_midnight.timestamp()),
        ]
        # events_illum = [
        #     EVENT_DAWN,
        #     EVENT_SUNRISE,
        #     EVENT_SUNSET,
        #     EVENT_DUSK,
        # ]
        # event_ct = [
        #     EVENT_BLUE_HOUR_MORNING,
        #     EVENT_BLUE_GOLDEN_TRANSITION,
        #     EVENT_GOLDEN_HOUR_MORNING,
        #     EVENT_NOON,
        #     EVENT_GOLDEN_HOUR_EVENING,
        #     EVENT_GOLDEN_BLUE_TRANSITION,
        #     EVENT_BLUE_HOUR_EVENING,
        #     EVENT_MIDNIGHT,
        # ]

        # Check whether order is correct
        events = sorted(events, key=lambda x: x[1])
        events_names, _ = zip(*events)
        if events_names not in _ALLOWED_ORDERS:
            msg = (
                "{self.name}: The sun events {events_names} are not in the expected"
                " order. The Artificial Sunlight integration will not work!"
                " This might happen if your sunrise/sunset offset is too large or"
                " your manually set sunrise/sunset time is past/before noon/midnight."
            )
            _LOGGER.error(msg)
            raise ValueError(msg)

        return events

    def relevant_events(self, now: datetime.datetime) -> list[tuple[str, float]]:
        """Get the previous and next sun event."""
        events = [
            self.get_sun_events(now + timedelta(days=days))
            for days in [-1, 0, 1]
            # stores sun events for yesterday, today and tomorrow into an events dict.
        ]
        # print("events: " + str(events))
        events = sum(events, [])  # flatten lists
        events = sorted(events, key=lambda x: x[1])
        # print("events: " + str(events))
        i_now = bisect.bisect([ts for _, ts in events], now.timestamp())
        return events[i_now - 1 : i_now + 1]

    # def calc_percent(self, transition: int) -> float:
    #     """Calculate the position of the sun in %."""
    #     now = dt_util.utcnow()
    #     now = now.replace(tzinfo=pytz.utc)
    #     # print("now: " + str(now))
    #     target_time = now + timedelta(seconds=transition)
    #     target_ts = target_time.timestamp()
    #     today = self.relevant_events(target_time)
    #     # print("today: " + str(today))
    #     (_, prev_ts), (next_event, next_ts) = today
    #     h, x = (  # pylint: disable=invalid-name
    #         (prev_ts, next_ts)
    #         if next_event in (EVENT_SUNSET, EVENT_SUNRISE)
    #         else (next_ts, prev_ts)
    #     )
    #     k = 1 if next_event in (EVENT_SUNSET, EVENT_NOON) else -1
    #     percentage = (0 - k) * ((target_ts - h) / (h - x)) ** 2 + k
    #     return percentage

    def calc_pct_exp(self, val1, val2, val3, val4) -> float:
        """subfunction for calc pct."""
        pct = math.pow((val1 - val2) / (val3 - val4), 2)
        return pct

    def calc_pct_sqrt(self, val1, val2, val3, val4) -> float:
        """subfunction for calc pct."""
        val = (val1 - val2) / (val3 - val4)
        pct = abs(val ** (1 / 2))
        return pct

    def calc_pct_sqrt4(self, val1, val2, val3, val4) -> float:
        """subfunction for calc pct."""
        val = (val1 - val2) / (val3 - val4)
        pct = abs(val ** (1 / 4))
        return pct

    def calc_pct_sqrt6(self, val1, val2, val3, val4) -> float:
        """subfunction for calc pct."""
        val = (val1 - val2) / (val3 - val4)
        pct = abs(val ** (1 / 6))
        return pct

    def calc_pct_sqrt8(self, val1, val2, val3, val4) -> float:
        """subfunction for calc pct."""
        val = (val1 - val2) / (val3 - val4)
        pct = abs(val ** (1 / 8))
        return pct

    def calc_brightness_pct(self, now, is_sleep: bool) -> float:
        """Calculate the natural brightness of the sun in %."""
        if is_sleep:
            return self.sleep_brightness

        delta_brightness = self.max_brightness - self.min_brightness

        if SunSettings.dawn < now < SunSettings.sunrise:
            # brightness transistion morning
            morning_pct = self.calc_pct_exp(
                now,
                SunSettings.dawn,
                SunSettings.sunrise,
                SunSettings.dawn,
            )
            perct = (delta_brightness * morning_pct) + self.min_brightness
            return perct

        if SunSettings.sunset < now < SunSettings.dusk:
            # brightness transistion evening
            evening_pct = self.calc_pct_exp(
                SunSettings.dusk,
                now,
                SunSettings.dusk,
                SunSettings.sunset,
            )
            perct = (delta_brightness * evening_pct) + self.min_brightness
            return perct

        if SunSettings.sunrise <= now <= SunSettings.sunset:
            return self.max_brightness

        return self.min_brightness

    def calc_color_temp_kelvin1(self, now: float, is_sleep: bool) -> float:
        """Calculate the color temperature in Kelvin."""
        if is_sleep:
            night = False
            return self.sleep_color_temp

        # Midnight till blue hour ct transistion
        # - Subprocess is tested
        # TODO Needs to work with Colors, if Night Color Mode is enabled
        # [ ]  Subprocess is tested
        if SunSettings.prev_solar_midnight < now < SunSettings.next_bl_hr_mrnng_strt:
            # night to morning transistion
            night = True
            pct = self.calc_pct_sqrt6(
                SunSettings.next_bl_hr_mrnng_strt,
                now,
                SunSettings.next_bl_hr_mrnng_strt,
                SunSettings.prev_solar_midnight,
            )
            c_t = ((self.min_color_temp - self.dawn_ct) * pct) + self.dawn_ct
            _LOGGER.debug(
                "CT %s Midnight %s -> Blue Hour %s  pct: %s",
                c_t,
                SunSettings.prev_solar_midnight,
                SunSettings.next_bl_hr_mrnng_strt,
                pct,
            )
            return c_t, night

        # Blue Hour to golden hour ct transistion
        # [ ] Subprocess is tested
        if SunSettings.bl_hr_mrnng_strt <= now < SunSettings.gldn_hr_mrnng_strt:
            # night to morning transistion
            night = False
            pct = self.calc_pct_sqrt(
                now,
                SunSettings.bl_hr_mrnng_strt,
                SunSettings.gldn_hr_mrnng_strt,
                SunSettings.bl_hr_mrnng_strt,
            )
            c_t = ((self.bl_hr_ct - self.dawn_ct) * pct) + self.dawn_ct
            _LOGGER.debug(
                "CT %s Blue Hour Morning %s -> Golden Hour %s  pct: %s",
                c_t,
                SunSettings.bl_hr_mrnng_strt,
                SunSettings.gldn_hr_mrnng_strt,
                pct,
            )
            return c_t, night

        # golden Hour to sunrise ct transistion
        # [ ] Subprocess is tested
        if SunSettings.gldn_hr_mrnng_strt <= now < SunSettings.gldn_hr_mrnng_end:
            # night to morning transistion
            night = False
            pct = self.calc_pct_sqrt(
                now,
                SunSettings.gldn_hr_mrnng_strt,
                SunSettings.gldn_hr_mrnng_end,
                SunSettings.gldn_hr_mrnng_strt,
            )
            c_t = ((self.sunrise_ct - self.bl_hr_ct) * pct) + self.bl_hr_ct
            _LOGGER.debug(
                "CT %s Golden Hour Morning %s -> Morning %s  pct: %s",
                c_t,
                SunSettings.gldn_hr_mrnng_strt,
                SunSettings.gldn_hr_mrnng_end,
                pct,
            )
            return c_t, night

        # sunrise to noon ct transistion
        # [ ]  Subprocess is tested
        if SunSettings.gldn_hr_mrnng_end <= now < SunSettings.solar_noon:
            # night to morning transistion
            night = False
            pct = self.calc_pct_sqrt4(
                now,
                SunSettings.gldn_hr_mrnng_end,
                SunSettings.solar_noon,
                SunSettings.gldn_hr_mrnng_end,
            )
            c_t = ((self.max_color_temp - self.sunrise_ct) * pct) + self.sunrise_ct
            _LOGGER.debug(
                "CT %s Morning %s -> Noon %s  pct: %s",
                c_t,
                SunSettings.gldn_hr_mrnng_end,
                SunSettings.solar_noon,
                pct,
            )
            return c_t, night

        # noon to sunset ct transistion
        # [ ]  Subprocess is tested
        if SunSettings.solar_noon <= now < SunSettings.gldn_hr_nght_strt:
            # brightness transistion evening
            night = False
            pct = self.calc_pct_sqrt4(
                SunSettings.gldn_hr_nght_strt,
                now,
                SunSettings.gldn_hr_nght_strt,
                SunSettings.solar_noon,
            )
            c_t = ((self.max_color_temp - self.sunset_ct) * pct) + self.sunset_ct
            _LOGGER.debug(
                "CT %s Noon %s -> Evening %s  pct: %s",
                c_t,
                SunSettings.solar_noon,
                SunSettings.gldn_hr_nght_strt,
                pct,
            )
            return c_t, night

        # sunset to golden hour ct transistion
        # [ ]  Subprocess is tested
        if SunSettings.gldn_hr_nght_strt <= now < SunSettings.gldn_hr_nght_end:
            # brightness transistion evening
            night = False
            pct = self.calc_pct_sqrt(
                SunSettings.gldn_hr_nght_end,
                now,
                SunSettings.gldn_hr_nght_end,
                SunSettings.gldn_hr_nght_strt,
            )
            c_t = ((self.sunset_ct - self.bl_hr_ct) * pct) + self.bl_hr_ct
            _LOGGER.debug(
                "CT %s Golden Hour %s -> Blue Hour %s  pct: %s",
                c_t,
                SunSettings.gldn_hr_nght_strt,
                SunSettings.gldn_hr_nght_end,
                pct,
            )
            return c_t, night

        # golden hour to blue hour ct transistion
        # [ ]  Subprocess is tested
        if SunSettings.gldn_hr_nght_end <= now < SunSettings.bl_hr_nght_end:
            # brightness transistion evening
            night = False
            pct = self.calc_pct_sqrt(
                SunSettings.bl_hr_nght_end,
                now,
                SunSettings.bl_hr_nght_end,
                SunSettings.gldn_hr_nght_end,
            )
            c_t = ((self.bl_hr_ct - self.dusk_ct) * pct) + self.dusk_ct
            _LOGGER.debug(
                "CT %s Blue Hour %s -> Night %s  pct: %s",
                c_t,
                SunSettings.gldn_hr_nght_end,
                SunSettings.bl_hr_nght_end,
                pct,
            )
            return c_t, night

        # blue hour to night ct transistion
        # [ ]  Subprocess is tested
        # TODO Needs to work with Colors, if Night Color Mode is enabled
        if SunSettings.bl_hr_nght_end <= now < SunSettings.next_solar_midnight:
            # brightness transistion evening
            night = True
            pct = self.calc_pct_sqrt6(
                now,
                SunSettings.bl_hr_nght_end,
                SunSettings.next_solar_midnight,
                SunSettings.bl_hr_nght_end,
            )
            c_t = ((self.min_color_temp - self.dusk_ct) * pct) + self.dusk_ct
            _LOGGER.debug(
                "CT %s Night %s -> Midnight %s  pct: %s",
                c_t,
                SunSettings.gldn_hr_nght_end,
                SunSettings.next_solar_midnight,
                pct,
            )
            return c_t, night

        _LOGGER.debug("CT %s Fallback to min CT %s", c_t, now)
        return self.min_color_temp

    def get_settings(
        self, is_sleep, transition
    ) -> dict[str, Union[float, tuple[float, float], tuple[float, float, float]]]:
        """Get all light settings.

        Calculating all values takes <0.5ms.
        """
        # NOTE Reorganize with Local TZ
        now = dt_util.utcnow()
        now = now.replace(tzinfo=pytz.utc)

        # now = now.replace(tzinfo=pytz.utc)

        # TODO add Night Color Mode
        self.get_sun_events(now)
        percent = SunSettings.solar_elevation
        brightness_pct = self.calc_brightness_pct(now, is_sleep)
        color_temp_kelvin, night = self.calc_color_temp_kelvin1(now, is_sleep)
        color_temp_mired: float = color_temperature_kelvin_to_mired(color_temp_kelvin)
        rgb_color: tuple[float, float, float] = color_temperature_to_rgb(
            color_temp_kelvin
        )
        rgb_color = tuple(map(int, rgb_color))
        if night and self.use_night_color:
            rgb_color = eval(self.night_col)  # pylint: disable=eval-used

        xy_color: tuple[float, float] = color_RGB_to_xy(*rgb_color)
        xy_color = tuple(map(int, xy_color))
        hs_color: tuple[float, float] = color_xy_to_hs(*xy_color)
        hs_color = tuple(map(int, hs_color))

        # night_col

        # self._night_col = data[CONF_NIGHT_COLOR]
        # if use_night_color is None:
        #     use_night_color = self._use_night_color

        _LOGGER.info(
            "'%s': Calculating... SunPosition:'%s', Brightness:'%s', color_temp_kelvin='%s', color_temp_mired='%s', rgb_color='%s'",
            self.name,
            percent,
            brightness_pct,
            color_temp_kelvin,
            color_temp_mired,
            rgb_color,
        )  # Custom,readable info log

        return {
            "brightness_pct": brightness_pct,
            "color_temp_kelvin": color_temp_kelvin,
            "color_temp_mired": color_temp_mired,
            "rgb_color": rgb_color,
            "xy_color": xy_color,
            "hs_color": hs_color,
            "sun_position": percent,
            "night": night,
            "use_night_color": self.use_night_color,
        }


class TurnOnOffListener:
    """Track 'light.turn_off' and 'light.turn_on' service calls."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the TurnOnOffListener that is shared among all switches."""
        self.hass = hass
        self.lights = set()

        # Tracks 'light.turn_off' service calls
        self.turn_off_event: dict[str, Event] = {}
        # Tracks 'light.turn_on' service calls
        self.turn_on_event: dict[str, Event] = {}
        # Keep 'asyncio.sleep' tasks that can be cancelled by 'light.turn_on' events
        self.sleep_tasks: dict[str, asyncio.Task] = {}
        # Tracks which lights are manually controlled
        self.manual_control: dict[str, bool] = {}
        # Counts the number of times (in a row) a light had a changed state.
        self.cnt_significant_changes: dict[str, int] = defaultdict(int)
        # Track 'state_changed' events of self.lights resulting from this integration
        self.last_state_change: dict[str, list[State]] = {}
        # Track last 'service_data' to 'light.turn_on' resulting from this integration
        self.last_service_data: dict[str, dict[str, Any]] = {}

        # When a state is different `max_cnt_significant_changes` times in a row,
        # mark it as manually_controlled.
        self.max_cnt_significant_changes = 2

        self.remove_listener = self.hass.bus.async_listen(
            EVENT_CALL_SERVICE, self.turn_on_off_event_listener
        )
        self.remove_listener2 = self.hass.bus.async_listen(
            EVENT_STATE_CHANGED, self.state_changed_event_listener
        )

    def reset(self, *lights, reset_manual_control=True) -> None:
        """Reset the 'manual_control' status of the lights."""
        for light in lights:
            if reset_manual_control:
                self.manual_control[light] = False
            self.last_state_change.pop(light, None)
            self.last_service_data.pop(light, None)
            self.cnt_significant_changes[light] = 0

    async def turn_on_off_event_listener(self, event: Event) -> None:
        """Track 'light.turn_off' and 'light.turn_on' service calls."""
        domain = event.data.get(ATTR_DOMAIN)
        if domain != LIGHT_DOMAIN:
            return

        service = event.data[ATTR_SERVICE]
        service_data = event.data[ATTR_SERVICE_DATA]
        entity_ids = cv.ensure_list_csv(service_data[ATTR_ENTITY_ID])

        if not any(eid in self.lights for eid in entity_ids):
            return

        if service == SERVICE_TURN_OFF:
            transition = service_data.get(ATTR_TRANSITION)
            _LOGGER.debug(
                "Detected a 'light.turn_off' '%s', transition='%s' event with context.id='%s'",
                entity_ids,
                transition,
                event.context.id,
            )
            for eid in entity_ids:
                self.turn_off_event[eid] = event
                self.reset(eid)

        elif service == SERVICE_TURN_ON:
            # _LOGGER.debug(
            #     "Detected a 'light.turn_on' '%s' event with context.id='%s'",
            #     entity_ids,
            #     event.context.id,
            # )
            for eid in entity_ids:
                task = self.sleep_tasks.get(eid)
                if task is not None:
                    task.cancel()
                self.turn_on_event[eid] = event

    async def state_changed_event_listener(self, event: Event) -> None:
        """Track 'state_changed' events."""
        entity_id = event.data.get(ATTR_ENTITY_ID, "")
        if entity_id not in self.lights or entity_id.split(".")[0] != LIGHT_DOMAIN:
            return

        new_state = event.data.get("new_state")
        if new_state is not None and new_state.state == STATE_ON:
            _LOGGER.debug(
                "External Light change Event: '%s'  event: '%s' with context.id='%s'",
                entity_id,
                new_state.attributes,
                new_state.context.id,
            )

            if is_our_context(new_state.context):
                # It is possible to have multiple state change events with the same context.
                # This can happen because a `turn_on.light(brightness_pct=100, transition=30)`
                # event leads to an instant state change of
                # `new_state=dict(brightness=100, ...)`. However, after polling the light
                # could still only be `new_state=dict(brightness=50, ...)`.
                # We save all events because the first event change might indicate at what
                # settings the light will be later *or* the second event might indicate a
                # final state. The latter case happens for example when a light was
                # called with a color_temp outside of its range (and HA reports the
                # incorrect 'min_mireds' and 'max_mireds', which happens e.g., for
                # Philips Hue White GU10 Bluetooth lights).
                old_state: Optional[list[State]] = self.last_state_change.get(entity_id)
                if (
                    old_state is not None
                    and old_state[0].context.id == new_state.context.id
                ):
                    # If there is already a state change event from this event (with this
                    # context) then append it to the already existing list.
                    _LOGGER.debug(
                        "State change event of '%s' is already in 'self.last_state_change' (%s)"
                        " adding this state also",
                        entity_id,
                        new_state.context.id,
                    )
                    self.last_state_change[entity_id].append(new_state)
                else:
                    self.last_state_change[entity_id] = [new_state]

    def is_manually_controlled(
        self,
        switch: ArtifSunSwitch,
        light: str,
        force: bool,
        adapt_brightness: bool,
        adapt_color: bool,
    ) -> bool:
        """Check if the light has been 'on' and is now manually controlled."""
        manual_control = self.manual_control.setdefault(light, False)
        if manual_control:
            # Manually controlled until light is turned on and off
            return True

        turn_on_event = self.turn_on_event.get(light)
        if (
            turn_on_event is not None
            and not is_our_context(turn_on_event.context)
            and not force
        ):
            keys = turn_on_event.data[ATTR_SERVICE_DATA].keys()
            if (adapt_color and COLOR_ATTRS.intersection(keys)) or (
                adapt_brightness and BRIGHTNESS_ATTRS.intersection(keys)
            ):
                # Light was already on and 'light.turn_on' was not called by
                # the artificial_sunlight integration.
                manual_control = self.manual_control[light] = True
                _fire_manual_control_event(switch, light, turn_on_event.context)
                _LOGGER.debug(
                    "'%s' was already on and 'light.turn_on' was not called by the"
                    " artificial_sunlight integration (context.id='%s'), the ArtificialSunlight"
                    " Lighting will stop adapting the light until the switch or the"
                    " light turns off and then on again",
                    light,
                    turn_on_event.context.id,
                )
        return manual_control

    async def significant_change(
        self,
        switch: ArtifSunSwitch,
        light: str,
        adapt_brightness: bool,
        adapt_color: bool,
        context: Context,
    ) -> bool:
        """Has the light made a significant change since last update.

        This method will detect changes that were made to the light without
        calling 'light.turn_on', so outside of Home Assistant. If a change is
        detected, we mark the light as 'manually controlled' until the light
        or switch is turned 'off' and 'on' again.
        """
        if light not in self.last_state_change:
            return False
        old_states: list[State] = self.last_state_change[light]
        await self.hass.helpers.entity_component.async_update_entity(light)
        new_state = self.hass.states.get(light)
        compare_to = functools.partial(
            _attributes_have_changed,
            light=light,
            new_attributes=new_state.attributes,
            adapt_brightness=adapt_brightness,
            adapt_color=adapt_color,
            context=context,
        )
        for index, old_state in enumerate(old_states):
            changed = compare_to(old_attributes=old_state.attributes)
            if not changed:
                _LOGGER.debug(
                    "State of '%s' didn't change wrt change event nr. %s (context.id=%s)",
                    light,
                    index,
                    context.id,
                )
                break

        last_service_data = self.last_service_data.get(light)
        if changed and last_service_data is not None:
            # It can happen that the state change events that are associated
            # with the last 'light.turn_on' call by this integration were not
            # final states. Possibly a later EVENT_STATE_CHANGED happened, where
            # the correct target brightness/color was reached.
            changed = compare_to(old_attributes=last_service_data)
            if not changed:
                _LOGGER.debug(
                    "State of '%s' didn't change wrt 'last_service_data' (context.id=%s)",
                    light,
                    context.id,
                )

        n_changes = self.cnt_significant_changes[light]
        if changed:
            self.cnt_significant_changes[light] += 1
            if n_changes >= self.max_cnt_significant_changes:
                # Only mark a light as significantly changing, if changed==True
                # N times in a row. We do this because sometimes a state changes
                # happens only *after* a new update interval has already started.
                self.manual_control[light] = True
                _fire_manual_control_event(switch, light, context, is_async=False)
        else:
            if n_changes > 1:
                _LOGGER.debug(
                    "State of '%s' had 'cnt_significant_changes=%s' but the state"
                    " changed to the expected settings now",
                    light,
                    n_changes,
                )
            self.cnt_significant_changes[light] = 0

        return changed

    async def maybe_cancel_adjusting(
        self, entity_id: str, off_to_on_event: Event, on_to_off_event: Optional[Event]
    ) -> bool:
        """Cancel the adjusting of a light if it has just been turned off.

        Possibly the lights just got a 'turn_off' call, however, the light
        is actually still turning off (e.g., because of a 'transition') and
        HA polls the light before the light is 100% off. This might trigger
        a rapid switch 'off' → 'on' → 'off'. To prevent this component
        from interfering on the 'on' state, we make sure to wait at least
        TURNING_OFF_DELAY (or the 'turn_off' transition time) between a
        'off' → 'on' event and then check whether the light is still 'on' or
        if the brightness is still decreasing. Only if it is the case we
        adjust the lights.
        """
        if on_to_off_event is None:
            # No state change has been registered before.
            return False

        id_on_to_off = on_to_off_event.context.id

        turn_off_event = self.turn_off_event.get(entity_id)
        if turn_off_event is not None:
            transition = turn_off_event.data[ATTR_SERVICE_DATA].get(ATTR_TRANSITION)
        else:
            transition = None

        turn_on_event = self.turn_on_event.get(entity_id)
        id_turn_on = turn_on_event.context.id

        id_off_to_on = off_to_on_event.context.id

        if id_off_to_on == id_turn_on and id_off_to_on is not None:
            # State change 'off' → 'on' triggered by 'light.turn_on'.
            return False

        if (
            turn_off_event is not None
            and id_on_to_off == turn_off_event.context.id
            and id_on_to_off is not None
            and transition is not None  # 'turn_off' is called with transition=...
        ):
            # State change 'on' → 'off' and 'light.turn_off(..., transition=...)' come
            # from the same event, so wait at least the 'turn_off' transition time.
            delay = max(transition, TURNING_OFF_DELAY)
        else:
            # State change 'off' → 'on' happened because the light state was set.
            # Possibly because of polling.
            delay = TURNING_OFF_DELAY

        delta_time = (dt_util.utcnow() - on_to_off_event.time_fired).total_seconds()
        if delta_time > delay:
            return False

        # Here we could just `return True` but because we want to prevent any updates
        # from happening to this light (through async_track_time_interval or
        # sleep_state) for some time, we wait below until the light
        # is 'off' or the time has passed.

        delay -= delta_time  # delta_time has passed since the 'off' → 'on' event
        _LOGGER.debug("Waiting with adjusting '%s' for %s", entity_id, delay)

        for _ in range(3):
            # It can happen that the actual transition time is longer than the
            # specified time in the 'turn_off' service.
            coro = asyncio.sleep(delay)
            task = self.sleep_tasks[entity_id] = asyncio.ensure_future(coro)
            try:
                await task
            except asyncio.CancelledError:  # 'light.turn_on' has been called
                _LOGGER.debug(
                    "Sleep task is cancelled due to 'light.turn_on('%s')' call",
                    entity_id,
                )
                return False

            if not is_on(self.hass, entity_id):
                return True
            delay = TURNING_OFF_DELAY  # next time only wait this long

        if transition is not None:
            # Always ignore when there's a 'turn_off' transition.
            # Because it seems like HA cannot detect whether a light is
            # transitioning into 'off'. Maybe needs some discussion/input?
            return True

        # Now we assume that the lights are still on and they were intended
        # to be on. In case this still gives problems for some, we might
        # choose to **only** adapt on 'light.turn_on' events and ignore
        # other 'off' → 'on' state switches resulting from polling. That
        # would mean we 'return True' here.
        return False

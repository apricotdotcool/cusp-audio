from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING

import pyatv
from pyatv.const import Protocol

if TYPE_CHECKING:
    from pyatv.interface import AppleTV, BaseConfig

    from cusp.config import CuspConfig

logger = logging.getLogger(__name__)

CREDENTIALS_PATH = Path.home() / ".config" / "cusp" / "credentials.json"

# AirPlay RAOP "sf" status flag bit names.
# Source: https://openairplay.github.io/airplay-spec/status_flags.html
STATUS_FLAGS: dict[int, str] = {
    0: "ProblemDetected",
    1: "DeviceNotConfigured",
    2: "AudioCableAttached",
    3: "PINRequired",
    6: "SupportsAirPlayFromCloud",
    7: "PasswordRequired",
    9: "OneTimePairingRequired",
    10: "DeviceWasSetupForHKAccessControl",
    11: "DeviceSupportsRelay",
    12: "SilentPrimary",
    13: "TightSyncIsGroupLeader",
    14: "TightSyncBuddyNotReachable",
    15: "IsAppleMusicSubscriber",
    16: "CloudLibraryIsOn",
    17: "ReceiverSessionIsActive",
}


# Friendly names for known device model families. The key is the alphabetic
# prefix of the "am" property (the part before the comma-separated version).
MODEL_FAMILIES: dict[str, str] = {
    "AppleTV": "Apple TV",
    "AudioAccessory": "HomePod",
    "iPad": "iPad",
    "iPhone": "iPhone",
    "iMac": "iMac",
    "MacBookAir": "MacBook Air",
    "MacBookPro": "MacBook Pro",
    "Macmini": "Mac mini",
    "MacPro": "Mac Pro",
    "MacStudio": "Mac Studio",
    "Mac": "Mac",
}


def friendly_model(model: str | None) -> str | None:
    """Return a human-friendly name for an AirPlay "am" model string.

    Falls back to the raw value for unknown model families (e.g. Sonos "One").
    """
    if not model:
        return None
    match = re.match(r"^[A-Za-z]+", model)
    if not match:
        return model
    return MODEL_FAMILIES.get(match.group(0), model)


def decode_status_flags(sf: str | int) -> list[tuple[int, str]]:
    """Decode an AirPlay RAOP "sf" status flag value into set (bit, name) pairs.

    Unknown bits are returned with name "Unknown".
    """
    value = int(sf, 16) if isinstance(sf, str) else sf
    result = []
    bit = 0
    while value >> bit:
        if value & (1 << bit):
            result.append((bit, STATUS_FLAGS.get(bit, "Unknown")))
        bit += 1
    return result


def _load_credentials() -> dict[str, dict[str, str]]:
    if CREDENTIALS_PATH.is_file():
        return json.loads(CREDENTIALS_PATH.read_text())
    return {}


def _save_credentials(creds: dict[str, dict[str, str]]) -> None:
    CREDENTIALS_PATH.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    CREDENTIALS_PATH.write_text(json.dumps(creds, indent=2))
    CREDENTIALS_PATH.chmod(0o600)


async def discover_devices(timeout: float = 5.0) -> list[dict]:
    """Discover AirPlay receivers on the network."""
    loop = asyncio.get_event_loop()
    devices = await pyatv.scan(loop, timeout=timeout)
    results = []
    for dev in devices:
        raop = next((s for s in dev.services if s.protocol == Protocol.RAOP), None)
        if raop is None:
            continue
        props = dict(raop.properties)
        airplay = next(
            (s for s in dev.services if s.protocol == Protocol.AirPlay), None
        )
        airplay_props = dict(airplay.properties) if airplay else {}
        results.append(
            {
                "name": dev.name,
                "identifier": dev.identifier,
                "address": str(dev.address),
                "port": raop.port,
                "model": props.get("am"),
                "firmware": props.get("vs"),
                "gid": airplay_props.get("gid"),
                "pgid": airplay_props.get("pgid"),
                "gpn": airplay_props.get("gpn"),
                "properties": props,
                "airplay_properties": airplay_props,
            }
        )
    return results


async def pair_device(name: str) -> None:
    """Pair with a named AirPlay device. Interactive — prompts for PIN if needed."""
    loop = asyncio.get_event_loop()
    devices = await pyatv.scan(loop, timeout=5)
    target = _find_device(devices, name)
    if target is None:
        raise ValueError(f"No AirPlay device found matching '{name}'")

    for service in target.services:
        if service.protocol == Protocol.RAOP:
            pairing = await pyatv.pair(target, Protocol.RAOP, loop=loop)
            await pairing.begin()
            if pairing.device_provides_pin:
                pin = input("Enter PIN displayed on device: ")
                pairing.pin(int(pin))
            await pairing.finish()

            if pairing.has_paired:
                creds = _load_credentials()
                creds[str(target.identifier)] = {
                    "name": target.name,
                    "raop": pairing.service.credentials,
                }
                _save_credentials(creds)
                logger.info("Paired with %s, credentials saved", target.name)
            else:
                logger.error("Pairing failed with %s", target.name)
            await pairing.close()
            return

    raise RuntimeError(f"No RAOP service found on {target.name}")


async def resolve_target(config: CuspConfig) -> BaseConfig:
    """Scan for the configured AirPlay target and apply credentials/password.

    Returns a pyatv device config ready to be passed to `connect_target`.
    Raises ConnectionError if the device is not found on the network.
    """
    loop = asyncio.get_event_loop()
    devices = await pyatv.scan(loop, timeout=5)
    target = _find_device(devices, config.airplay_target)
    if target is None:
        raise ConnectionError(
            f"AirPlay device '{config.airplay_target}' not found on network. "
            "Run `cusp devices` to see available receivers."
        )

    # Apply stored credentials
    creds = _load_credentials()
    if str(target.identifier) in creds:
        stored = creds[str(target.identifier)]
        for service in target.services:
            if service.protocol == Protocol.RAOP and "raop" in stored:
                service.credentials = stored["raop"]

    # Apply password if configured
    if config.airplay_password:
        for service in target.services:
            if service.protocol == Protocol.RAOP:
                service.password = config.airplay_password

    return target


async def connect_target(target: BaseConfig) -> AppleTV:
    """Open a pyatv connection to a pre-resolved target."""
    loop = asyncio.get_event_loop()
    logger.info("Connecting to %s (%s)", target.name, target.address)
    atv = await pyatv.connect(target, loop=loop)
    logger.info("Connected to %s", target.name)
    return atv


def _find_device(devices, name: str):
    """Find a device by name (case-insensitive exact match).

    If no device matches, fall back to treating `name` as an AirPlay group
    name (gpn) and return the group leader device.
    """
    name_lower = name.lower()
    matches = [
        d
        for d in devices
        if name_lower == d.name.lower()
        and any(s.protocol == Protocol.RAOP for s in d.services)
    ]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        names = ", ".join(d.name for d in matches)
        raise ValueError(f"Ambiguous target '{name}' matches: {names}")

    leader = _find_group_leader(devices, name)
    if leader is not None:
        logger.info("Resolved group '%s' to leader %s", name, leader.name)
        return leader
    return None


def _find_group_leader(devices, name: str):
    """Find the leader of an AirPlay group with the given gpn name."""
    name_lower = name.lower()
    for d in devices:
        raop = next((s for s in d.services if s.protocol == Protocol.RAOP), None)
        airplay = next(
            (s for s in d.services if s.protocol == Protocol.AirPlay), None
        )
        if raop is None or airplay is None:
            continue
        gpn = airplay.properties.get("gpn")
        if not gpn or gpn.lower() != name_lower:
            continue
        sf = raop.properties.get("sf")
        if not sf:
            continue
        sf_value = int(sf, 16) if isinstance(sf, str) else sf
        if sf_value & (1 << 13):  # TightSyncIsGroupLeader
            return d
    return None

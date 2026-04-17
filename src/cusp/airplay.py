from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

import pyatv
from pyatv.const import Protocol

if TYPE_CHECKING:
    from pyatv.interface import AppleTV, BaseConfig

    from cusp.config import CuspConfig

logger = logging.getLogger(__name__)

CREDENTIALS_PATH = Path.home() / ".config" / "cusp" / "credentials.json"


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
        has_raop = any(s.protocol == Protocol.RAOP for s in dev.services)
        if has_raop:
            results.append(
                {
                    "name": dev.name,
                    "identifier": dev.identifier,
                    "address": str(dev.address),
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
    """Find a device by name (substring match, case-insensitive)."""
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
    return None

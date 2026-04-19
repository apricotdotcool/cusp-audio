from __future__ import annotations

import asyncio
import sys

import click

from cusp import __version__
from cusp.config import load_config
from cusp.logging_ import setup_logging


@click.group()
@click.version_option(__version__, prog_name="cusp")
def main() -> None:
    """Stream audio from a microphone or USB input to an AirPlay receiver."""


@main.command()
@click.option("--timeout", default=5.0, help="Discovery timeout in seconds.")
@click.option(
    "-v",
    "--verbose",
    is_flag=True,
    help="Show detailed AirPlay receiver info, including decoded status flags.",
)
def devices(timeout: float, verbose: bool) -> None:
    """List available audio input devices and AirPlay receivers."""
    setup_logging("INFO")
    asyncio.run(_list_devices(timeout, verbose))


async def _list_devices(timeout: float, verbose: bool) -> None:
    from cusp.airplay import decode_status_flags, discover_devices, friendly_model
    from cusp.audio import list_input_devices

    click.echo("Audio input devices:")
    click.echo("-" * 40)
    input_devs = list_input_devices()
    if not input_devs:
        click.echo("  (none found)")
    for dev in input_devs:
        click.echo(
            f"  [{dev['index']}] {dev['name']}\n"
            f"    (channels: {dev['max_channels']}, rate: {dev['default_rate']:.0f})"
        )

    click.echo()
    click.echo("AirPlay receivers:")
    click.echo("-" * 40)
    receivers = await discover_devices(timeout=timeout)
    if not receivers:
        click.echo("  (none found)")
        return

    for r in sorted(receivers, key=lambda r: r["name"].lower()):
        model = friendly_model(r["model"])
        click.echo(f"  {r['name']}")
        if not verbose:
            click.echo(
                f"    (model: {model}, ip: {r['address']}, id: {r['identifier']})"
            )
            continue
        props = r["properties"]
        click.echo(f"    id:       {r['identifier']}")
        click.echo(f"    ip:       {r['address']}")
        click.echo(f"    port:     {r['port']}")
        if r["model"]:
            click.echo(f"    model:    {r['model']}")
        if r["firmware"]:
            click.echo(f"    firmware: {r['firmware']}")
        if r["gid"]:
            click.echo(f"    gid:      {r['gid']}")
        if r["pgid"]:
            click.echo(f"    pgid:     {r['pgid']}")
        if r["gpn"]:
            click.echo(f"    gpn:      {r['gpn']}")
        sf = props.get("sf")
        if sf:
            click.echo(f"    status:   {sf}")
            for bit, name in decode_status_flags(sf):
                click.echo(f"      bit {bit}: {name}")
        ft = props.get("ft")
        if ft:
            click.echo(f"    features: {ft}")

    groups: dict[str, list[str]] = {}
    for r in receivers:
        gpn = r["gpn"]
        if gpn:
            groups.setdefault(gpn, []).append(r["name"])

    if groups:
        click.echo()
        click.echo("AirPlay groups:")
        click.echo("-" * 40)
        for gpn in sorted(groups, key=str.lower):
            click.echo(f"  {gpn}")
            members = sorted(groups[gpn], key=str.lower)
            click.echo(f"    {', '.join(members)}")


@main.command()
@click.argument("name")
def pair(name: str) -> None:
    """Pair with an AirPlay device."""
    setup_logging("INFO")
    asyncio.run(_pair_device(name))


async def _pair_device(name: str) -> None:
    from cusp.airplay import pair_device

    await pair_device(name)


@main.command()
@click.option(
    "-d",
    "--device",
    default=None,
    help='Audio input device name, index, or "system" to capture system audio.',
)
@click.option("-t", "--target", default=None, help="AirPlay receiver name.")
@click.option(
    "-c",
    "--config",
    "config_path",
    default=None,
    type=click.Path(exists=True),
    help="Config file path.",
)
@click.option(
    "--sample-rate",
    default=None,
    type=int,
    help="Sample rate in Hz (default: from device).",
)
@click.option(
    "--channels",
    default=None,
    type=int,
    help="Number of channels (default: from device).",
)
@click.option("--blocksize", default=None, type=int, help="Audio block size in frames.")
@click.option("--log-level", default=None, help="Log level (DEBUG/INFO/WARNING/ERROR).")
@click.option("--log-file", default=None, help="Log to file instead of stderr.")
def stream(
    device: str | None,
    target: str | None,
    config_path: str | None,
    sample_rate: int | None,
    channels: int | None,
    blocksize: int | None,
    log_level: str | None,
    log_file: str | None,
) -> None:
    """Start streaming audio to an AirPlay receiver."""

    # Parse device as int if it's a number; normalize "system" shorthand.
    audio_device: str | int | None = None
    if device is not None:
        if device.isdigit():
            audio_device = int(device)
        elif device.lower() == "system":
            audio_device = "system"
        else:
            audio_device = device

    config = load_config(
        config_path=config_path,
        audio_device=audio_device,
        airplay_target=target,
        sample_rate=sample_rate,
        channels=channels,
        blocksize=blocksize,
        log_level=log_level,
        log_file=log_file,
    )
    setup_logging(config.log_level, config.log_file)

    if not config.airplay_target:
        click.echo(
            "Error: No AirPlay target specified. "
            "Use -t or set [airplay] target in config.",
            err=True,
        )
        sys.exit(1)

    asyncio.run(_run_stream(config))


async def _run_stream(config):
    from cusp.pipeline import run_with_reconnect

    await run_with_reconnect(config)

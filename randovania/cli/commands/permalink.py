from __future__ import annotations

import asyncio
import random
from pathlib import Path
from typing import TYPE_CHECKING

from randovania.games.game import RandovaniaGame

if TYPE_CHECKING:
    from argparse import ArgumentParser

    from randovania.layout.preset import Preset


def create_permalink(args):
    from randovania.interface_common.preset_manager import PresetManager
    from randovania.layout.generator_parameters import GeneratorParameters
    from randovania.layout.permalink import Permalink
    from randovania.layout.versioned_preset import VersionedPreset

    presets: list[Preset] = []

    if hasattr(args, "game"):
        game: RandovaniaGame = RandovaniaGame(args.game)
        preset_manager = PresetManager(None)
        for preset_name in args.preset_name:
            versioned = preset_manager.included_preset_with(game, preset_name)
            if versioned is None:
                raise ValueError(
                    "Unknown included preset '{}' for game {}. Valid options are: {}".format(
                        preset_name,
                        game.long_name,
                        [preset.name for preset in preset_manager.included_presets.values() if preset.game == game],
                    )
                )
            presets.append(versioned.get_preset())
    else:
        for preset_file in args.preset_file:
            presets.append(VersionedPreset.from_file_sync(preset_file).get_preset())

    seed = args.seed_number
    if seed is None:
        seed = random.randint(0, 2**31)

    return Permalink.from_parameters(
        GeneratorParameters(
            seed,
            spoiler=not args.race,
            development=args.development,
            presets=presets,
        ),
    )


async def permalink_command_body(args):
    from randovania.layout.permalink import Permalink

    permalink = create_permalink(args)
    print(permalink.as_base64_str)
    Permalink.from_str(permalink.as_base64_str)


def permalink_command(args):
    asyncio.run(permalink_command_body(args))


def add_permalink_arguments(parser: ArgumentParser, from_file: bool):
    if from_file:
        parser.add_argument(
            "--preset-file", required=True, type=Path, nargs="+", help="The paths to rdvpreset files to use."
        )
    else:
        parser.add_argument(
            "--game",
            required=True,
            choices=[game.value for game in RandovaniaGame],
            help="The name of the game of the preset to use.",
        )
        parser.add_argument("--preset-name", required=True, type=str, nargs="+", help="The name of the presets to use")

    parser.add_argument("--seed-number", type=int, help="The seed number. Defaults to a random value.")
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--race", default=False, action="store_true", help="Make a race permalink (without spoiler)."
    )
    mode_group.add_argument(
        "--development",
        default=False,
        action="store_true",
        help="Disables features that maximize randomness in order to make easier to investigate bugs.",
    )


def add_permalink_command(sub_parsers):
    parser: ArgumentParser = sub_parsers.add_parser("permalink", help="Creates a permalink from included presets")
    add_permalink_arguments(parser, False)
    parser.set_defaults(func=permalink_command)

    parser: ArgumentParser = sub_parsers.add_parser("permalink-from-file", help="Creates a permalink from preset files")
    add_permalink_arguments(parser, True)
    parser.set_defaults(func=permalink_command)

import dataclasses
from random import Random
from typing import Iterator, Callable

import randovania
import randovania.games.prime2.exporter.hints
from randovania.exporter import pickup_exporter, item_names
from randovania.exporter.hints import credits_spoiler
from randovania.exporter.hints.hint_namer import HintNamer
from randovania.exporter.patch_data_factory import BasePatchDataFactory
from randovania.game_description import default_database
from randovania.game_description.assignment import PickupTarget
from randovania.game_description.db.area import Area
from randovania.game_description.db.area_identifier import AreaIdentifier
from randovania.game_description.db.dock_node import DockNode
from randovania.game_description.db.node import Node
from randovania.game_description.db.node_identifier import NodeIdentifier
from randovania.game_description.db.region_list import RegionList
from randovania.game_description.db.teleporter_node import TeleporterNode
from randovania.game_description.default_database import default_prime2_memo_data
from randovania.game_description.game_description import GameDescription
from randovania.game_description.game_patches import GamePatches
from randovania.game_description.requirements.base import Requirement
from randovania.game_description.requirements.requirement_and import RequirementAnd
from randovania.game_description.requirements.resource_requirement import ResourceRequirement
from randovania.game_description.resources.item_resource_info import ItemResourceInfo
from randovania.game_description.resources.pickup_entry import PickupModel
from randovania.game_description.resources.resource_info import ResourceGain
from randovania.game_description.resources.resource_type import ResourceType
from randovania.games.game import RandovaniaGame
from randovania.games.prime2.exporter import hints
from randovania.games.prime2.exporter.hint_namer import EchoesHintNamer
from randovania.games.prime2.layout.echoes_configuration import EchoesConfiguration
from randovania.games.prime2.layout.echoes_cosmetic_patches import EchoesCosmeticPatches
from randovania.games.prime2.layout.hint_configuration import HintConfiguration, SkyTempleKeyHintMode
from randovania.games.prime2.patcher import echoes_items
from randovania.generator.pickup_pool import pickup_creator
from randovania.interface_common.players_configuration import PlayersConfiguration
from randovania.layout.base.base_configuration import BaseConfiguration
from randovania.layout.layout_description import LayoutDescription
from randovania.layout.lib.teleporters import TeleporterShuffleMode
from randovania.patching.prime import elevators

_EASTER_EGG_RUN_VALIDATED_CHANCE = 1024
_EASTER_EGG_SHINY_MISSILE = 8192

_ENERGY_CONTROLLER_MAP_ASSET_IDS = [
    618058071,  # Agon EC
    724159530,  # Torvus EC
    988679813,  # Sanc EC
]
_ELEVATOR_ROOMS_MAP_ASSET_IDS = [
    # 0x529F0152,  # Sky Temple Energy Controller
    0xAE06A5D9,  # Sky Temple Gateway

    # cliff
    0x1C7CBD3E,  # agon
    0x92A2ADA3,  # Torvus
    0xFB9E9C00,  # Entrance
    0x74EFFB3C,  # Aerie
    0x932CB12E,  # Aerie Transport Station

    # sand
    0xEF5EA06C,  # Sanc
    0x8E9B3B3F,  # Torvus
    0x7E1BC16F,  # Entrance

    # swamp
    0x46B0EECF,  # Entrance
    0xE6B06473,  # Agon
    0x96DB1F15,  # Sanc

    # tg -> areas
    0x4B2A6FD3,  # Agon
    0x85E70805,  # Torvus
    0xE4229356,  # Sanc

    # tg -> gt
    0x79EFFD7D,
    0x65168477,
    0x84388E13,

    # gt -> tg
    0xA6D44A39,
    0x318EBBCD,
    0xB1B5308D,
]


def item_id_for_item_resource(resource: ItemResourceInfo) -> int:
    return resource.extra["item_id"]


def _area_identifier_to_json(region_list: RegionList, identifier: AreaIdentifier) -> dict:
    region = region_list.region_by_area_location(identifier)
    area = region.area_by_identifier(identifier)

    return {
        "world_asset_id": region.extra['asset_id'],
        "area_asset_id": area.extra['asset_id'],
    }


def _create_spawn_point_field(patches: GamePatches,
                              game: GameDescription,
                              ) -> dict:
    starting_resources = patches.starting_resources()
    starting_resources.set_resource(game.resource_database.get_item(echoes_items.PERCENTAGE), 0)
    capacities = [
        {
            "index": item_id_for_item_resource(item),
            "amount": starting_resources[item],
        }
        for item in game.resource_database.item
    ]

    return {
        "location": _area_identifier_to_json(game.region_list, patches.starting_location.area_identifier),
        "amount": capacities,
        "capacity": capacities,
    }


def _pretty_name_for_elevator(game: RandovaniaGame,
                              region_list: RegionList,
                              original_teleporter_node: TeleporterNode,
                              connection: AreaIdentifier,
                              ) -> str:
    """
    Calculates the name the room that contains this elevator should have
    :param region_list:
    :param original_teleporter_node:
    :param connection:
    :return:
    """
    if original_teleporter_node.keep_name_when_vanilla:
        if original_teleporter_node.default_connection == connection:
            return region_list.nodes_to_area(original_teleporter_node).name

    return f"Transport to {elevators.get_elevator_or_area_name(game, region_list, connection, False)}"


def _create_elevators_field(patches: GamePatches, game: GameDescription) -> list:
    """
    Creates the elevator entries in the patcher file
    :param patches:
    :param game:
    :return:
    """
    region_list = game.region_list

    elevator_fields = []

    for node, connection in patches.all_elevator_connections():
        elevator_fields.append({
            "instance_id": node.extra["teleporter_instance_id"],
            "origin_location": _area_identifier_to_json(game.region_list, node.identifier.area_location),
            "target_location": _area_identifier_to_json(game.region_list, connection),
            "room_name": _pretty_name_for_elevator(game.game, region_list, node, connection)
        })

    num_teleporter_nodes = sum(1 for _ in _get_nodes_by_teleporter_id(region_list))
    if len(elevator_fields) != num_teleporter_nodes:
        raise ValueError("Invalid elevator count. Expected {}, got {}.".format(
            num_teleporter_nodes, len(elevator_fields)
        ))

    return elevator_fields


def _get_nodes_by_teleporter_id(region_list: RegionList) -> Iterator[TeleporterNode]:
    for node in region_list.iterate_nodes():
        if isinstance(node, TeleporterNode) and node.editable:
            yield node


def translator_index_for_requirement(requirement: Requirement) -> int:
    assert isinstance(requirement, RequirementAnd)
    assert 1 <= len(requirement.items) <= 2

    items: set = set()
    for req in requirement.items:
        assert isinstance(req, ResourceRequirement)
        assert req.amount == 1
        assert not req.negate
        assert isinstance(req.resource, ItemResourceInfo)
        items.add(item_id_for_item_resource(req.resource))

    # Remove Scan Visor, as it should always be present
    items.remove(9)
    for it in items:
        return it
    # If nothing is present, then return Scan Visor as "free"
    return 9


def _create_translator_gates_field(game: GameDescription, gate_assignment: dict[NodeIdentifier, Requirement]) -> list:
    """
    Creates the translator gate entries in the patcher file
    :param gate_assignment:
    :return:
    """
    return [
        {
            "gate_index": game.region_list.node_by_identifier(identifier).extra["gate_index"],
            "translator_index": translator_index_for_requirement(requirement),
        }
        for identifier, requirement in gate_assignment.items()
    ]


def _apply_translator_gate_patches(specific_patches: dict, elevator_shuffle_mode: TeleporterShuffleMode) -> None:
    """

    :param specific_patches:
    :param elevator_shuffle_mode:
    :return:
    """
    specific_patches["always_up_gfmc_compound"] = True
    specific_patches["always_up_torvus_temple"] = True
    specific_patches["always_up_great_temple"] = elevator_shuffle_mode != TeleporterShuffleMode.VANILLA


def _create_elevator_scan_port_patches(
        game: RandovaniaGame,
        region_list: RegionList,
        get_elevator_connection_for: Callable[[TeleporterNode], AreaIdentifier],
) -> Iterator[dict]:
    for node in _get_nodes_by_teleporter_id(region_list):
        if node.extra.get("scan_asset_id") is None:
            continue

        target_area_name = elevators.get_elevator_or_area_name(game, region_list,
                                                               get_elevator_connection_for(node), True)
        yield {
            "asset_id": node.extra["scan_asset_id"],
            "strings": [f"Access to &push;&main-color=#FF3333;{target_area_name}&pop; granted.", ""],
        }


def _logbook_title_string_patches():
    return [
        {
            "asset_id": 3271034066,
            "strings": [
                'Hints', 'Violet', 'Cobalt', 'Technology', 'Keys 1, 2, 3', 'Keys 7, 8, 9', 'Regular Hints',
                'Emerald', 'Amber', '&line-spacing=75;Flying Ing\nCache Hints', 'Keys 4, 5, 6', 'Keys 1, 2, 3',
                '&line-spacing=75;Torvus Energy\nController', 'Underground Tunnel', 'Training Chamber',
                'Catacombs', 'Gathering Hall', '&line-spacing=75;Fortress\nTransport\nAccess',
                '&line-spacing=75;Hall of Combat\nMastery', 'Main Gyro Chamber',
                '&line-spacing=75;Sanctuary\nEnergy\nController', 'Main Research', 'Watch Station',
                'Sanctuary Entrance', '&line-spacing=75;Transport to\nAgon Wastes', 'Mining Plaza',
                '&line-spacing=75;Agon Energy\nController', 'Portal Terminal', 'Mining Station B',
                'Mining Station A', 'Meeting Grounds', 'Path of Eyes', 'Path of Roots',
                '&line-spacing=75;Main Energy\nController', "Champions of Aether",
                '&line-spacing=75;Central\nMining\nStation', 'Main Reactor', 'Torvus Lagoon', 'Catacombs',
                'Sanctuary Entrance', "Dynamo Works", 'Storage Cavern A', 'Landing Site', 'Industrial Site',
                '&line-spacing=75;Sky Temple\nKey Hints', 'Keys 7, 8, 9', 'Keys 4, 5, 6', 'Sky Temple Key 1',
                'Sky Temple Key 2', 'Sky Temple Key 3', 'Sky Temple Key 4', 'Sky Temple Key 5',
                'Sky Temple Key 6', 'Sky Temple Key 7', 'Sky Temple Key 8', 'Sky Temple Key 9'
            ],
        }, {
            "asset_id": 2301408881,
            "strings": [
                'Research', 'Mechanisms', 'Luminoth Technology', 'Biology', 'GF Security', 'Vehicles',
                'Aether Studies', 'Aether', 'Dark Aether', 'Phazon', 'Sandgrass', 'Blueroot Tree',
                'Ing Webtrap',
                'Webling', 'U-Mos', 'Bladepod', 'Ing Storage', 'Flying Ing Cache', 'Torvus Bearerpod',
                'Agon Bearerpod', 'Ingworm Cache', 'Ingsphere Cache', 'Plantforms', 'Darklings',
                'GF Gate Mk VI',
                'GF Gate Mk VII', 'GF Lock Mk V', 'GF Defense Shield', 'Kinetic Orb Cannon', 'GF Bridge',
                "Samus's Gunship", 'GFS Tyr', 'Pirate Skiff', 'Visors', 'Weapon Systems', 'Armor',
                'Morph Ball Systems', 'Movement Systems', 'Beam Weapons', 'Scan Visor', 'Combat Visor',
                'Dark Visor',
                'Echo Visor', 'Morph Ball', 'Boost Ball', 'Spider Ball', 'Morph Ball Bomb', 'Power Bomb',
                'Dark Bomb', 'Light Bomb', 'Annihilator Bomb', 'Space Jump Boots', 'Screw Attack',
                'Gravity Boost',
                'Grapple Beam', 'Varia Suit', 'Dark Suit', 'Light Suit', 'Power Beam', 'Dark Beam',
                'Light Beam',
                'Annihilator Beam', 'Missile Launcher', 'Seeker Missile Launcher', 'Super Missile',
                'Sonic Boom',
                'Darkburst', 'Sunburst', 'Charge Beam', 'Missile Systems', 'Charge Combos', 'Morph Balls',
                'Bomb Systems', 'Miscellaneous', 'Dark Temple Keys', 'Bloatsac', 'Luminoth Technology',
                'Light Beacons', 'Light Crystals', 'Lift Crystals', 'Utility Crystals', 'Light Crystal',
                'Energized Crystal', 'Nullified Crystal', 'Super Crystal', 'Light Beacon', 'Energized Beacon',
                'Nullified Beacon', 'Super Beacon', 'Inactive Beacon', 'Dark Lift Crystal',
                'Light Lift Crystal',
                'Liftvine Crystal', 'Torvus Hanging Pod', 'Sentinel Crystal', 'Dark Sentinel Crystal',
                'Systems',
                'Bomb Slot', 'Spinner', 'Grapple Point', 'Spider Ball Track', 'Energy Tank',
                'Beam Ammo Expansion',
                'Missile Expansion', 'Dark Agon Keys', 'Dark Torvus Keys', 'Ing Hive Keys', 'Sky Temple Keys',
                'Temple Grounds', 'Sanctuary Fortress', 'Torvus Bog', 'Agon Wastes', 'Dark Agon Temple Key 1',
                'Dark Agon Temple Key 2', 'Dark Agon Temple Key 3', 'Dark Torvus Temple Key 1',
                'Dark Torvus Temple Key 2', 'Dark Torvus Temple Key 3', 'Ing Hive Temple Key 1',
                'Ing Hive Temple Key 2', 'Ing Hive Temple Key 3', 'Sky Temple Key 1', 'Sky Temple Key 2',
                'Sky Temple Key 3', 'Sky Temple Key 4', 'Sky Temple Key 5', 'Sky Temple Key 6',
                'Sky Temple Key 7',
                'Sky Temple Key 8', 'Sky Temple Key 9', 'Suit Expansions', 'Charge Combo', 'Ingclaw',
                'Dormant Ingclaw', 'Power Bomb Expansion', 'Energy Transfer Module', 'Cocoons',
                'Splinter Cocoon',
                'War Wasp Hive', 'Metroid Cocoon', 'Dark Aether', 'Aether', 'Dark Portal', 'Light Portal',
                'Energy Controller', 'Wall Jump Surface',
            ]
        },
    ]


def _akul_testament_string_patch(namer: HintNamer):
    # update after each tournament! ordered from newest to oldest
    champs = [
        {
            "title": "2022 Champion",
            "name": "Cestrion"
        },
        {
            "title": "CGC 2022 Champions",
            "name": "Cosmonawt and Cestrion"
        },
        {
            "title": "2021 Champion",
            "name": "Dyceron"
        },
        {
            "title": "2020 Champion",
            "name": "Dyceron"
        }
    ]

    title = "Metroid Prime 2: Echoes Randomizer Tournament"
    champ_string = '\n'.join([
        f'{champ["title"]}: {namer.format_player(champ["name"], with_color=True)}'
        for champ in champs
    ])
    latest = champ_string.partition("\n")[0]

    return [
        {
            "asset_id": 0x080BBD00,
            "strings": [
                'Luminoth Datapac translated.\n(Champions of Aether)',
                f"{title}\n\n{latest}",
                f"{title}\n\n{champ_string}",
            ],
        },
    ]


def _create_string_patches(hint_config: HintConfiguration,
                           game: GameDescription,
                           all_patches: dict[int, GamePatches],
                           namer: EchoesHintNamer,
                           players_config: PlayersConfiguration,
                           rng: Random,
                           ) -> list:
    """

    :param hint_config:
    :param game:
    :param all_patches:
    :return:
    """
    patches = all_patches[players_config.player_index]
    string_patches = []

    string_patches.extend(_akul_testament_string_patch(namer))

    # Location Hints
    string_patches.extend(
        hints.create_patches_hints(all_patches, players_config, game.region_list, namer, rng)
    )

    # Sky Temple Keys
    stk_mode = hint_config.sky_temple_keys
    if stk_mode == SkyTempleKeyHintMode.DISABLED:
        string_patches.extend(randovania.games.prime2.exporter.hints.hide_stk_hints(namer))
    else:
        string_patches.extend(randovania.games.prime2.exporter.hints.create_stk_hints(
            all_patches, players_config, game.resource_database,
            namer, stk_mode == SkyTempleKeyHintMode.HIDE_AREA,
        ))

    # Elevator Scans
    if not patches.configuration.use_new_patcher:
        string_patches.extend(_create_elevator_scan_port_patches(game.game, game.region_list,
                                                                 patches.get_elevator_connection_for))

    string_patches.extend(_logbook_title_string_patches())

    return string_patches


def _create_starting_popup(patches: GamePatches) -> list:
    extra_items = item_names.additional_starting_equipment(patches.configuration, patches.game, patches)
    if extra_items:
        return [
            "Extra starting items:",
            ", ".join(extra_items)
        ]
    else:
        return []


def _simplified_memo_data() -> dict[str, str]:
    result = pickup_exporter.GenericAcquiredMemo()
    result["Locked Power Bomb Expansion"] = ("Power Bomb Expansion acquired, "
                                             "but the main Power Bomb is required to use it.")
    result["Locked Missile Expansion"] = "Missile Expansion acquired, but the Missile Launcher is required to use it."
    result["Locked Seeker Launcher"] = "Seeker Launcher acquired, but the Missile Launcher is required to use it."
    return result


def _get_model_name_missing_backup():
    """
    A mapping of alternative model names if some models are missing.
    :return:
    """
    other_game = {
        PickupModel(RandovaniaGame.METROID_PRIME, "Charge Beam"): "ChargeBeam INCOMPLETE",
        PickupModel(RandovaniaGame.METROID_PRIME, "Super Missile"): "SuperMissile",
        PickupModel(RandovaniaGame.METROID_PRIME, "Scan Visor"): "ScanVisor INCOMPLETE",
        PickupModel(RandovaniaGame.METROID_PRIME, "Varia Suit"): "VariaSuit INCOMPLETE",
        PickupModel(RandovaniaGame.METROID_PRIME, "Gravity Suit"): "VariaSuit INCOMPLETE",
        PickupModel(RandovaniaGame.METROID_PRIME, "Phazon Suit"): "VariaSuit INCOMPLETE",
        # PickupModel(RandovaniaGame.PRIME1, "Morph Ball"): "MorphBall INCOMPLETE",
        PickupModel(RandovaniaGame.METROID_PRIME, "Morph Ball Bomb"): "MorphBallBomb",
        PickupModel(RandovaniaGame.METROID_PRIME, "Boost Ball"): "BoostBall",
        PickupModel(RandovaniaGame.METROID_PRIME, "Spider Ball"): "SpiderBall",
        PickupModel(RandovaniaGame.METROID_PRIME, "Power Bomb"): "PowerBomb",
        PickupModel(RandovaniaGame.METROID_PRIME, "Power Bomb Expansion"): "PowerBombExpansion",
        PickupModel(RandovaniaGame.METROID_PRIME, "Missile"): "MissileExpansionPrime1",
        PickupModel(RandovaniaGame.METROID_PRIME, "Grapple Beam"): "GrappleBeam",
        PickupModel(RandovaniaGame.METROID_PRIME, "Space Jump Boots"): "SpaceJumpBoots",
        PickupModel(RandovaniaGame.METROID_PRIME, "Energy Tank"): "EnergyTank",
    }
    return {
        f"{model.game.value}_{model.name}": name
        for model, name in other_game.items()
    }


def _get_model_mapping(randomizer_data: dict):
    jingles = {
        "SkyTempleKey": 2,
        "DarkTempleKey": 2,
        "MissileExpansion": 0,
        "PowerBombExpansion": 0,
        "DarkBeamAmmoExpansion": 0,
        "LightBeamAmmoExpansion": 0,
        "BeamAmmoExpansion": 0,
    }
    return EchoesModelNameMapping(
        index={
            entry["Name"]: entry["Index"]
            for entry in randomizer_data["ModelData"]
        },
        sound_index={
            "SkyTempleKey": 1,
            "DarkTempleKey": 1,
        },
        jingle_index={
            entry["Name"]: jingles.get(entry["Name"], 1)
            for entry in randomizer_data["ModelData"]
        },
    )


def should_keep_elevator_sounds(configuration: EchoesConfiguration):
    elev = configuration.elevators
    if elev.is_vanilla:
        return True

    if elev.mode == TeleporterShuffleMode.ONE_WAY_ANYTHING:
        return False

    return not (set(elev.editable_teleporters) & {
        NodeIdentifier.create("Temple Grounds", "Sky Temple Gateway",
                              "Teleport to Great Temple - Sky Temple Energy Controller"),
        NodeIdentifier.create("Great Temple", "Sky Temple Energy Controller",
                              "Teleport to Temple Grounds - Sky Temple Gateway"),
        NodeIdentifier.create("Sanctuary Fortress", "Aerie",
                              "Elevator to Sanctuary Fortress - Aerie Transport Station"),
    })


class EchoesPatchDataFactory(BasePatchDataFactory):
    cosmetic_patches: EchoesCosmeticPatches
    configuration: EchoesConfiguration

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.namer = EchoesHintNamer(self.description.all_patches, self.players_config)

    def game_enum(self) -> RandovaniaGame:
        return RandovaniaGame.METROID_PRIME_ECHOES

    def create_specific_patches(self):
        # TODO: if we're starting at ship, needs to collect 9 sky temple keys and want item loss,
        # we should disable hive_chamber_b_post_state
        return {
            "hive_chamber_b_post_state": True,
            "intro_in_post_state": True,
            "warp_to_start": self.configuration.warp_to_start,
            "credits_length": 75 if self.cosmetic_patches.speed_up_credits else 259,
            "disable_hud_popup": self.cosmetic_patches.disable_hud_popup,
            "pickup_map_icons": self.cosmetic_patches.pickup_markers,
            "full_map_at_start": self.cosmetic_patches.open_map,
            "dark_world_varia_suit_damage": self.configuration.varia_suit_damage,
            "dark_world_dark_suit_damage": self.configuration.dark_suit_damage,
            "hud_color": self.cosmetic_patches.hud_color if self.cosmetic_patches.use_hud_color else None,
        }

    def _decide_default_items(self):
        pickup_database = default_database.pickup_database_for_game(self.game_enum())
        default_pickups = self.configuration.standard_pickup_configuration.default_pickups
        default_items = {}

        for category in ["visor", "beam"]:
            pickup_category = [cat for cat in default_pickups.keys() if cat.name == category][0]
            default = default_pickups[pickup_category]
            if default is None:
                valid_options: set[str] = {definition.name for definition in pickup_database.default_pickups[pickup_category]}
                pickup_options = [pickup for pickup in self.patches.starting_equipment
                                  if pickup.name in valid_options]
                default_name = self.rng.choice(pickup_options).name
            else:
                default_name = default.name

            default_items[category] = default_name

        return default_items

    def create_data(self) -> dict:

        result = {}
        _add_header_data_to_result(self.description, result)

        result["publisher_id"] = "0R"
        if self.configuration.menu_mod:
            result["publisher_id"] = "1R"

        result["convert_other_game_assets"] = self.cosmetic_patches.convert_other_game_assets
        result["credits"] = "\n\n\n\n\n" + credits_spoiler.prime_trilogy_credits(
            self.configuration.standard_pickup_configuration,
            self.description.all_patches,
            self.players_config,
            self.namer,
            "&push;&main-color=#89D6FF;Major Item Locations&pop;",
            "&push;&main-color=#33ffd6;{}&pop;",
        )

        result["menu_mod"] = self.configuration.menu_mod
        result["dol_patches"] = {
            "world_uuid": str(self.players_config.get_own_uuid()),
            "energy_per_tank": self.configuration.energy_per_tank,
            "beam_configurations": [b.as_json for b in self.configuration.beam_configuration.all_beams],
            "safe_zone_heal_per_second": self.configuration.safe_zone.heal_per_second,
            "user_preferences": self.cosmetic_patches.user_preferences.as_json,
            "default_items": self._decide_default_items(),,
            "unvisited_room_names": (self.configuration.elevators.can_use_unvisited_room_names
                                     and self.cosmetic_patches.unvisited_room_names),
            "teleporter_sounds": should_keep_elevator_sounds(self.configuration),
            "dangerous_energy_tank": self.configuration.dangerous_energy_tank,
        }

        # Add Spawn Point
        result["spawn_point"] = _create_spawn_point_field(self.patches, self.game)

        result["starting_popup"] = _create_starting_popup(self.patches)

        # Add the pickups
        result["pickups"] = _create_pickup_list(self.cosmetic_patches, self.configuration, self.game, self.patches,
                                                self.players_config, self.rng)

        # Add the elevators
        if not self.configuration.use_new_patcher:
            result["elevators"] = _create_elevators_field(self.patches, self.game)
        else:
            result["elevators"] = []

        # Add translators
        result["translator_gates"] = _create_translator_gates_field(self.game, self.patches.configurable_nodes)

        # Scan hints
        result["string_patches"] = _create_string_patches(self.configuration.hints, self.game,
                                                          self.description.all_patches, self.namer,
                                                          self.players_config, self.rng)

        # TODO: if we're starting at ship, needs to collect 9 sky temple keys and want item loss,
        # we should disable hive_chamber_b_post_state
        result["specific_patches"] = self.create_specific_patches()

        result["logbook_patches"] = self.create_logbook_patches()

        if not self.configuration.elevators.is_vanilla and (
                self.cosmetic_patches.unvisited_room_names
                and self.configuration.elevators.can_use_unvisited_room_names
        ):
            exclude_map_ids = _ELEVATOR_ROOMS_MAP_ASSET_IDS
        else:
            exclude_map_ids = []
        result["maps_to_always_reveal"] = _ENERGY_CONTROLLER_MAP_ASSET_IDS
        result["maps_to_never_reveal"] = exclude_map_ids

        _apply_translator_gate_patches(result["specific_patches"], self.configuration.elevators.mode)

        if self.configuration.use_new_patcher:
            result["new_patcher"] = self.new_patcher_configuration()

            # FIXME HACK: don't change Aerie name as that breaks OPR's API
            if self.configuration.portal_rando:
                for elev in result["elevators"]:
                    if elev["instance_id"] == 4260106:
                        elev["room_name"] = "Aerie"

        return result

    def _add_area_to_regions_patch(self,
                                   regions_patch_data: dict,
                                   area_or_node: Area | Node | AreaIdentifier | NodeIdentifier):
        if isinstance(area_or_node, NodeIdentifier):
            area_or_node = self.game.region_list.node_by_identifier(area_or_node)
        if isinstance(area_or_node, Node):
            area_or_node = self.game.region_list.nodes_to_area(area_or_node)
        elif isinstance(area_or_node, AreaIdentifier):
            area_or_node = self.game.region_list.area_by_area_location(area_or_node)
        area = area_or_node

        region = self.game.region_list.region_with_area(area)
        if region.name not in regions_patch_data:
            regions_patch_data[region.name] = {"areas": {}}

        if area.name not in regions_patch_data[region.name]["areas"]:
            regions_patch_data[region.name]["areas"][area.name] = {
                "elevators": [],
                "docks": {},
                "layers": {},
            }

        return region, area

    def _get_dock_patch_data(self, regions_patch_data: dict, node: DockNode) -> dict:
        region, area = self._add_area_to_regions_patch(regions_patch_data, node)

        area_patch_data = regions_patch_data[region.name]["areas"][area.name]

        area_patch_data["low_memory_mode"] = area.extra.get("low_memory_mode", False)
        area_patch_data["docks"][node.extra["dock_name"]] = area_patch_data["docks"].get(node.extra["dock_name"], {})
        return area_patch_data["docks"][node.extra["dock_name"]]

    def add_dock_connection_changes(self, regions_patch_data: dict):
        portal_changes: dict[DockNode, Node] = {
            source: target
            for source, target in self.patches.all_dock_connections()
            if source.dock_type.short_name == "portal" and source.default_connection != target.identifier
        }

        for source, target in list(portal_changes.items()):
            if source not in portal_changes:
                continue

            assert portal_changes.pop(target) is source
            assert isinstance(source, DockNode)
            assert isinstance(target, DockNode)

            dock_patch_data = self._get_dock_patch_data(regions_patch_data, source)
            dock_patch_data.update({
                "connect_to": {
                    "area": self.game.region_list.nodes_to_area(target).name,
                    "dock": target.extra["dock_name"],
                }
            })

    def add_dock_type_changes(self, regions_patch_data: dict):
        dock_changes = {
            dock: {
                "old_door_type": dock.default_dock_weakness.extra["door_type"],
                "new_door_type": weakness.extra["door_type"]
            }
            for dock, weakness in self.patches.all_dock_weaknesses()
            if dock.default_dock_weakness != weakness
        }

        for dock, changes in dock_changes.items():
            dock_patch_data = self._get_dock_patch_data(regions_patch_data, dock)
            dock_patch_data.update(changes)

    def add_new_patcher_elevators(self, regions_patch_data: dict):
        for node, connection in self.patches.all_elevator_connections():
            region, area = self._add_area_to_regions_patch(regions_patch_data, node)
            area_patches = regions_patch_data[region.name]["areas"][area.name]
            area_patches["elevators"].append({
                "instance_id": node.extra["teleporter_instance_id"],
                "target_assets": _area_identifier_to_json(
                    self.game.region_list,
                    connection
                ),
                "target_strg": node.extra["scan_asset_id"],
                "target_name": elevators.get_elevator_or_area_name(
                    self.game.game,
                    self.game.region_list,
                    connection,
                    include_world_name=True
                )
            })

            if "new_name" not in area_patches:
                area_patches["new_name"] = _pretty_name_for_elevator(
                    self.game.game, self.game.region_list, node, connection
                )

    def add_layer_patches(self, regions_patch_data: dict):
        self._add_area_to_regions_patch(
            regions_patch_data,
            AreaIdentifier("Temple Grounds", "Dynamo Chamber")
        )
        self._add_area_to_regions_patch(
            regions_patch_data,
            AreaIdentifier("Temple Grounds", "Trooper Security Station")
        )
        regions_patch_data["Temple Grounds"]["areas"]["Dynamo Chamber"]["layers"] = {
            "1st Pass Scripting": False,
            "2nd Pass Scripting": True,
        }
        regions_patch_data["Temple Grounds"]["areas"]["Trooper Security Station"]["layers"] = {
            "1st Pass": False,
            "2nd Pass": True,
        }

    def new_patcher_configuration(self):
        regions_patch_data = {}
        self.add_layer_patches(regions_patch_data)
        self.add_dock_connection_changes(regions_patch_data)
        self.add_dock_type_changes(regions_patch_data)
        self.add_new_patcher_elevators(regions_patch_data)

        return {
            "worlds": regions_patch_data,
            # "area_patches": {
            #     "torvus_temple": True
            # },
            "small_randomizations": {
                "seed": self.description.get_seed_for_player(self.players_config.player_index),
                "echo_locks": True,
                "minigyro_chamber": True,
                "rubiks": True,
            },
            "inverted": self.configuration.inverted_mode,
        }

    def create_logbook_patches(self):
        return [
            {"asset_id": 25, "connections": [81, 166, 195], },
            {"asset_id": 38, "connections": [4, 33, 120, 251, 364], },
            {"asset_id": 60, "connections": [38, 74, 154, 196], },
            {"asset_id": 74, "connections": [59, 75, 82, 102, 260], },
            {"asset_id": 81, "connections": [148, 151, 156], },
            {"asset_id": 119, "connections": [60, 254, 326], },
            {"asset_id": 124, "connections": [35, 152, 355], },
            {"asset_id": 129, "connections": [29, 118, 367], },
            {"asset_id": 154, "connections": [169, 200, 228, 243, 312, 342], },
            {"asset_id": 166, "connections": [45, 303, 317], },
            {"asset_id": 194, "connections": [1, 6], },
            {"asset_id": 195, "connections": [159, 221, 231], },
            {"asset_id": 196, "connections": [17, 19, 23, 162, 183, 379], },
            {"asset_id": 233, "connections": [58, 191, 373], },
            {"asset_id": 241, "connections": [223, 284], },
            {"asset_id": 254, "connections": [129, 233, 319], },
            {"asset_id": 318, "connections": [119, 216, 277, 343], },
            {"asset_id": 319, "connections": [52, 289, 329], },
            {"asset_id": 326, "connections": [124, 194, 241, 327], },
            {"asset_id": 327, "connections": [46, 275], },
        ]


def generate_patcher_data(description: LayoutDescription,
                          players_config: PlayersConfiguration,
                          cosmetic_patches: EchoesCosmeticPatches,
                          ) -> dict:
    """

    :param description:
    :param players_config:
    :param cosmetic_patches:
    :return:
    """
    return EchoesPatchDataFactory(description, players_config, cosmetic_patches).create_data()


def _create_pickup_list(cosmetic_patches: EchoesCosmeticPatches, configuration: BaseConfiguration,
                        game: GameDescription,
                        patches: GamePatches, players_config: PlayersConfiguration,
                        rng: Random):
    useless_target = PickupTarget(pickup_creator.create_echoes_useless_pickup(game.resource_database),
                                  players_config.player_index)

    if cosmetic_patches.disable_hud_popup:
        memo_data = _simplified_memo_data()
    else:
        memo_data = default_prime2_memo_data()

    pickup_list = pickup_exporter.export_all_indices(
        patches,
        useless_target,
        game.region_list,
        rng,
        configuration.pickup_model_style,
        configuration.pickup_model_data_source,
        exporter=pickup_exporter.create_pickup_exporter(memo_data, players_config),
        visual_etm=pickup_creator.create_visual_etm(),
    )
    multiworld_item = game.resource_database.get_item(echoes_items.MULTIWORLD_ITEM)

    return [
        echoes_pickup_details_to_patcher(details, multiworld_item, rng)
        for details in pickup_list
    ]


def _add_header_data_to_result(description: LayoutDescription, result: dict) -> None:
    result["permalink"] = "-permalink-"
    result["seed_hash"] = f"- {description.shareable_word_hash} ({description.shareable_hash})"
    result["shareable_hash"] = description.shareable_hash
    result["shareable_word_hash"] = description.shareable_word_hash
    result["randovania_version"] = randovania.VERSION


@dataclasses.dataclass(frozen=True)
class EchoesModelNameMapping:
    index: dict[str, int]
    sound_index: dict[str, int]  # 1 for keys, 0 otherwise
    jingle_index: dict[str, int]  # 2 for keys, 1 for major items, 0 otherwise


def _create_pickup_resources_for(resources: ResourceGain):
    return [
        {
            "index": resource.extra["item_id"],
            "amount": quantity
        }
        for resource, quantity in resources
        if quantity > 0 and resource.resource_type == ResourceType.ITEM
    ]


def echoes_pickup_details_to_patcher(details: pickup_exporter.ExportedPickupDetails,
                                     multiworld_item: ItemResourceInfo, rng: Random) -> dict:
    model = details.model.as_json

    if (model["name"] == "MissileExpansion"
            and model["game"] == RandovaniaGame.METROID_PRIME_ECHOES
            and rng.randint(0, _EASTER_EGG_SHINY_MISSILE) == 0):
        # If placing a missile expansion model, replace with Dark Missile Trooper model with a 1/8192 chance
        model["name"] = "MissileExpansionPrime1"

    hud_text = details.collection_text
    if hud_text == ["Energy Transfer Module acquired!"] and (
            rng.randint(0, _EASTER_EGG_RUN_VALIDATED_CHANCE) == 0):
        hud_text = ["Run validated!"]

    multiworld_tuple = (multiworld_item, details.index.index + 1),

    return {
        "pickup_index": details.index.index,
        "resources": _create_pickup_resources_for(
            details.conditional_resources[0].resources + multiworld_tuple
        ),
        "conditional_resources": [
            {
                "item": conditional.item.extra["item_id"],
                "resources": _create_pickup_resources_for(conditional.resources + multiworld_tuple),
            }
            for conditional in details.conditional_resources[1:]
        ],
        "convert": [
            {
                "from_item": conversion.source.extra["item_id"],
                "to_item": conversion.target.extra["item_id"],
                "clear_source": conversion.clear_source,
                "overwrite_target": conversion.overwrite_target,
            }
            for conversion in details.conversion
        ],
        "hud_text": hud_text,
        "scan": f"{details.name}. {details.description}".strip(),
        "model": model,
    }


def adjust_model_name(patcher_data: dict, randomizer_data: dict):
    mapping = _get_model_mapping(randomizer_data)
    backup = _get_model_name_missing_backup()

    for pickup in patcher_data["pickups"]:
        model = pickup.pop("model")
        if model["game"] == RandovaniaGame.METROID_PRIME_ECHOES.value:
            model_name = model["name"]
        else:
            model_name = "{}_{}".format(model["game"], model["name"])

        if model_name not in mapping.index:
            model_name = backup.get(model_name, "EnergyTransferModule")

        pickup["model_index"] = mapping.index[model_name]
        pickup["sound_index"] = mapping.sound_index.get(model_name, 0)
        pickup["jingle_index"] = mapping.jingle_index.get(model_name, 0)

import struct
from typing import List, Optional

from PySide2 import QtWidgets
from PySide2.QtWidgets import QMainWindow
from asyncqt import asyncSlot

from randovania.game_connection.connection_backend import ConnectionBackend, MemoryOperation, _powerup_offset
from randovania.game_connection.connection_base import GameConnectionStatus
from randovania.game_description.node import PickupNode
from randovania.game_description.resources.pickup_entry import PickupEntry
from randovania.games.prime import dol_patcher
from randovania.gui.generated.debug_backend_window_ui import Ui_DebugBackendWindow
from randovania.gui.lib import common_qt_lib
from randovania.gui.lib.qt_network_client import handle_network_errors
from randovania.interface_common import enum_lib
from randovania.interface_common.cosmetic_patches import CosmeticPatches
from randovania.network_common.admin_actions import SessionAdminUserAction


class DebugGameBackendChoice:
    @property
    def pretty_text(self):
        return "Debug"


class DebugBackendWindow(ConnectionBackend, Ui_DebugBackendWindow):
    pickups: List[PickupEntry]
    permanent_pickups: List[PickupEntry]

    def __init__(self):
        super().__init__()
        self.window = QMainWindow()
        self.setupUi(self.window)
        common_qt_lib.set_default_window_icon(self.window)

        for status in enum_lib.iterate_enum(GameConnectionStatus):
            self.current_status_combo.addItem(status.pretty_text, status)

        self.permanent_pickups = []
        self.pickups = []

        self.collect_location_combo.setVisible(False)
        self.setup_collect_location_combo_button = QtWidgets.QPushButton(self.window)
        self.setup_collect_location_combo_button.setText("Load list of locations")
        self.setup_collect_location_combo_button.clicked.connect(self._setup_locations_combo)
        self.gridLayout.addWidget(self.setup_collect_location_combo_button, 1, 0, 1, 1)

        self.collect_location_button.clicked.connect(self._emit_collection)
        self.collect_location_button.setEnabled(False)

        self._expected_patches = dol_patcher.ALL_VERSIONS_PATCHES[1]
        self._game_memory = bytearray(24 * (2 ** 20))
        self._write_memory(self._expected_patches.build_string_address,
                           self._expected_patches.build_string)

        # CPlayerState
        self._write_memory(self._expected_patches.string_display.cstate_manager_global + 0x150c,
                           0xA00000.to_bytes(4, "big"))

    def _read_memory(self, address: int, count: int):
        address &= ~0x80000000
        return self._game_memory[address:address + count]

    def _read_memory_format(self, format_string: str, address: int):
        return struct.unpack_from(format_string, self._game_memory, address & ~0x80000000)

    def _write_memory(self, address: int, data: bytes):
        address &= ~0x80000000
        self._game_memory[address:address + len(data)] = data

    @property
    def current_status(self) -> GameConnectionStatus:
        return self.current_status_combo.currentData()

    @property
    def backend_choice(self):
        return DebugGameBackendChoice()

    @property
    def lock_identifier(self) -> Optional[str]:
        return None

    @property
    def name(self) -> str:
        return "Debug"

    async def update(self, dt: float):
        if not await self._identify_game():
            return

        await self._send_message_from_queue(dt)
        self._read_message_from_game()

        self._inventory = await self._get_inventory()
        if self.checking_for_collected_index:
            await self._check_for_collected_index()

        s = "<br />".join(
            f"{name} x {quantity.amount}/{quantity.capacity}" for name, quantity in self._inventory.items()
        )
        self.inventory_label.setText(s)

    def show(self):
        self.window.show()

    @asyncSlot()
    async def _emit_collection(self):
        multiworld_magic_item = self.game.resource_database.multiworld_magic_item
        magic_address = 0xA00000 + _powerup_offset(multiworld_magic_item.index)
        new_magic_value = self.collect_location_combo.currentData() + 1

        magic_amount, magic_capacity = self._read_memory_format(">II", magic_address)
        magic_amount += new_magic_value
        magic_capacity += new_magic_value
        self._write_memory(magic_address, struct.pack(">II", magic_amount, magic_capacity))

    @asyncSlot()
    @handle_network_errors
    async def _setup_locations_combo(self):
        network_client = common_qt_lib.get_network_client()
        game_session = network_client.current_game_session
        user = network_client.current_user

        game = self.game
        index_to_name = {
            node.pickup_index.index: game.world_list.area_name(area)
            for world, area, node in game.world_list.all_worlds_areas_nodes
            if isinstance(node, PickupNode)
        }

        if game_session is None:
            names = index_to_name
        else:
            patcher_data = await network_client.session_admin_player(user.id,
                                                                     SessionAdminUserAction.CREATE_PATCHER_FILE,
                                                                     CosmeticPatches().as_json)
            names = {
                pickup["pickup_index"]: "{}: {}".format(index_to_name[pickup["pickup_index"]],
                                                        pickup["hud_text"][0])
                for pickup in patcher_data["pickups"]
            }

        self.collect_location_combo.clear()
        for index, name in sorted(names.items()):
            self.collect_location_combo.addItem(name, index)

        self.collect_location_button.setEnabled(True)
        self.collect_location_combo.setVisible(True)
        self.setup_collect_location_combo_button.deleteLater()

    def clear(self):
        self.messages_list.clear()
        self.permanent_pickups = []
        self.pickups.clear()

    def _memory_operation(self, op: MemoryOperation) -> Optional[bytes]:
        op.validate_byte_sizes()

        address = op.address
        if op.offset is not None:
            address = self._read_memory_format(">I", address)[0]
            address += op.offset

        result = None
        if op.read_byte_count is not None:
            result = self._read_memory(address, op.read_byte_count)
        if op.write_bytes is not None:
            self._write_memory(address, op.write_bytes)
        return result

    async def _perform_memory_operations(self, ops: List[MemoryOperation]) -> List[Optional[bytes]]:
        return [
            self._memory_operation(op)
            for op in ops
        ]

    def _read_message_from_game(self):
        has_message_address = self.patches.string_display.cstate_manager_global + 0x2
        if self._read_memory(has_message_address, 1) == b"\x00":
            return

        string_start = self.patches.string_display.message_receiver_string_ref
        message_bytes = self._read_memory(string_start, self.patches.string_display.max_message_size + 2)
        message = message_bytes[:message_bytes.find(b"\x00\x00")].decode("utf-16_be")

        self.messages_list.addItem(message)
        self._write_memory(has_message_address, b"\x00")

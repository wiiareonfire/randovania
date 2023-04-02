# Copyright (C) 2022 The Qt Company Ltd.
# SPDX-License-Identifier: LicenseRef-Qt-Commercial OR BSD-3-Clause

"""PySide6 port of the qt3d/simple-cpp example from Qt v5.x"""
import struct
import sys
from pathlib import Path

from PySide6 import QtCore, QtGui
from PySide6.Qt3DCore import (Qt3DCore)
from PySide6.Qt3DExtras import (Qt3DExtras)
from PySide6.Qt3DRender import Qt3DRender
from PySide6.QtCore import (Property, QObject, Signal, QPropertyAnimation)
from PySide6.QtGui import (QGuiApplication, QMatrix4x4, QQuaternion, QVector3D)

from retro_data_structures.formats.mapa import Mapa, GXPrimitive
from retro_data_structures.game_check import Game

_gx_primitive_to_qt = {
    GXPrimitive.GX_TRIANGLES: Qt3DRender.QGeometryRenderer.PrimitiveType.Triangles,
    GXPrimitive.GX_TRIANGLESTRIP: Qt3DRender.QGeometryRenderer.PrimitiveType.TriangleStrip,
}


def add_vertex_attribute_to_geometry(
        geometry: Qt3DCore.QGeometry,
        buffer: Qt3DCore.QBuffer,
        count: int
):
    attribute = Qt3DCore.QAttribute()
    attribute.setAttributeType(Qt3DCore.QAttribute.AttributeType.VertexAttribute)
    attribute.setBuffer(buffer)
    attribute.setVertexBaseType(Qt3DCore.QAttribute.VertexBaseType.Float)
    attribute.setVertexSize(3)
    attribute.setByteOffset(0)
    attribute.setByteStride(3 * 4)
    attribute.setCount(count)
    attribute.setName(Qt3DCore.QAttribute.defaultPositionAttributeName())
    geometry.addAttribute(attribute)


def add_index_attribute_to_geometry(
        geometry: Qt3DCore.QGeometry,
        buffer: Qt3DCore.QBuffer,
        count: int
):
    attribute = Qt3DCore.QAttribute()
    attribute.setAttributeType(Qt3DCore.QAttribute.AttributeType.IndexAttribute)
    attribute.setBuffer(buffer)
    attribute.setVertexBaseType(Qt3DCore.QAttribute.VertexBaseType.UnsignedShort)
    attribute.setVertexSize(1)
    attribute.setByteOffset(0)
    attribute.setByteStride(0)
    attribute.setCount(count)
    geometry.addAttribute(attribute)


class OrbitTransformController(QObject):
    def __init__(self, parent):
        super().__init__(parent)
        self._target = None
        self._matrix = QMatrix4x4()
        self._radius = 1
        self._angle = 0

    def setTarget(self, t):
        self._target = t

    def getTarget(self):
        return self._target

    def setRadius(self, radius):
        if self._radius != radius:
            self._radius = radius
            self.updateMatrix()
            self.radiusChanged.emit()

    def getRadius(self):
        return self._radius

    def setAngle(self, angle):
        if self._angle != angle:
            self._angle = angle
            self.updateMatrix()
            self.angleChanged.emit()

    def getAngle(self):
        return self._angle

    def updateMatrix(self):
        self._matrix.setToIdentity()
        self._matrix.rotate(self._angle, QVector3D(0, 1, 0))
        self._matrix.translate(self._radius, 0, 0)
        if self._target is not None:
            self._target.setMatrix(self._matrix)

    angleChanged = Signal()
    radiusChanged = Signal()
    angle = Property(float, getAngle, setAngle, notify=angleChanged)
    radius = Property(float, getRadius, setRadius, notify=radiusChanged)


class Window(Qt3DExtras.Qt3DWindow):
    def __init__(self):
        super().__init__()

        # Camera
        self.camera().lens().setPerspectiveProjection(45, 16 / 9, 0.1, 1000)
        self.camera().setPosition(QVector3D(0, 0, 40))
        self.camera().setViewCenter(QVector3D(0, 0, 0))

        # For camera controls
        self.create_scene()
        # self.camController = Qt3DExtras.QOrbitCameraController(self.rootEntity)
        # self.camController.setLinearSpeed(50)
        # self.camController.setLookSpeed(180)
        # self.camController.setCamera(self.camera())

        self.setRootEntity(self.root_entity)

    def create_scene(self):
        # Root entity
        self.root_entity = Qt3DCore.QEntity()

        gfmc = Mapa.parse(Path(r"F:\gfmc.mapa").read_bytes(), target_game=Game.ECHOES)

        # Material
        self.material = Qt3DExtras.QPhongMaterial(self.root_entity)
        self.material.setAmbient(QtCore.Qt.GlobalColor.blue)

        # Torus
        self.entities = []
        # self.torus_entity = Qt3DCore.QEntity(self.root_entity)
        #
        # # self.torus_mesh = Qt3DExtras.QTorusMesh()
        # # self.torus_mesh.setRadius(5)
        # # self.torus_mesh.setMinorRadius(1)
        # # self.torus_mesh.setRings(100)
        # # self.torus_mesh.setSlices(20)
        #
        # self.torus_transform = Qt3DCore.QTransform()
        # self.torus_transform.setScale3D(QVector3D(1.5, 1, 0.5))
        # self.torus_transform.setRotation(QQuaternion.fromAxisAndAngle(QVector3D(1, 0, 0), 45))

        all_vertex_buffer = Qt3DCore.QBuffer()
        all_vertex_buffer.setData(b"".join(
            struct.pack(f"3f", *vertex)
            for vertex in gfmc.raw.vertices
        ))

        for header, table in zip(gfmc.raw.primitive_headers, gfmc.raw.primitive_tables, strict=True):
            for primitive in table.primitives:
                entity = Qt3DCore.QEntity(self.root_entity)

                vertex_buffer = Qt3DCore.QBuffer(entity)
                vertex_buffer.setUsage(Qt3DCore.QBuffer.UsageType.DynamicDraw)
                vertex_buffer.setData(b"".join(
                    struct.pack(f"3f", *vertex)
                    for vertex in [
                        (100.0, -100.0, -10.0),
                        (400.0, -200.0, 40.0),
                        (0.0, 100.0, -10.0),
                    ]
                ))

                index_buffer = Qt3DCore.QBuffer()
                index_buffer.setUsage(Qt3DCore.QBuffer.UsageType.DynamicDraw)
                index_buffer.setData(struct.pack(f"3H", 1, 2, 0))
                # index_buffer.setData(struct.pack(f"{len(primitive.indices)}H", *primitive.indices))

                mesh = Qt3DRender.QGeometryRenderer()
                # mesh.setPrimitiveType(_gx_primitive_to_qt[primitive.type])
                mesh.setPrimitiveType(Qt3DRender.QGeometryRenderer.PrimitiveType.Triangles)

                geometry = Qt3DCore.QGeometry(mesh)
                add_vertex_attribute_to_geometry(geometry, vertex_buffer, 3)
                add_index_attribute_to_geometry(geometry, index_buffer, 3)
                # add_index_attribute_to_geometry(geometry, index_buffer, len(primitive.indices))

                mesh.setInstanceCount(1)
                mesh.setIndexOffset(0)
                mesh.setFirstInstance(0)
                # mesh.setVertexCount(len(primitive.indices))
                mesh.setVertexCount(3)
                mesh.setGeometry(geometry)

                # transform = Qt3DCore.QTransform()
                # transform.setScale3D(QVector3D(1.5, 1, 0.5))
                # transform.setRotation(QQuaternion.fromAxisAndAngle(QVector3D(1, 0, 0), 45))

                self.entities.append(entity)
                entity.addComponent(mesh)
                # entity.addComponent(transform)
                entity.addComponent(self.material)
                print("added entity")
                break

            break

        # Sphere
        self.sphereEntity = Qt3DCore.QEntity(self.root_entity)
        self.sphereMesh = Qt3DExtras.QSphereMesh()
        self.sphereMesh.setRadius(3)

        self.sphereTransform = Qt3DCore.QTransform()
        self.controller = OrbitTransformController(self.sphereTransform)
        self.controller.setTarget(self.sphereTransform)
        self.controller.setRadius(5)

        self.sphereRotateTransformAnimation = QPropertyAnimation(self.sphereTransform)
        self.sphereRotateTransformAnimation.setTargetObject(self.controller)
        self.sphereRotateTransformAnimation.setPropertyName(b"angle")
        self.sphereRotateTransformAnimation.setStartValue(0)
        self.sphereRotateTransformAnimation.setEndValue(360)
        self.sphereRotateTransformAnimation.setDuration(1000)
        self.sphereRotateTransformAnimation.setLoopCount(-1)
        self.sphereRotateTransformAnimation.start()

        self.sphereEntity.addComponent(self.sphereMesh)
        self.sphereEntity.addComponent(self.sphereTransform)
        self.sphereEntity.addComponent(self.material)


if __name__ == '__main__':
    app = QGuiApplication(sys.argv)
    view = Window()
    view.show()
    sys.exit(app.exec())

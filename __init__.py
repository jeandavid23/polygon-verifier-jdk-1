# -*- coding: utf-8 -*-
"""
__init__.py
===========
Point d'entrée standard d'un plugin QGIS.

QGIS appelle `classFactory(iface)` au chargement et attend en retour une
instance de la classe principale du plugin.
"""


def classFactory(iface):
    """
    Charge la classe principale du plugin.

    :param iface: instance de QgisInterface fournie par QGIS.
    :returns: instance de SmartPolygonCleaner.
    """
    from .smart_polygon_cleaner import SmartPolygonCleaner
    return SmartPolygonCleaner(iface)

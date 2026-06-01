# -*- coding: utf-8 -*-
"""
smart_polygon_cleaner.py
========================
Classe principale du plugin, point d'entrée côté QGIS.

Elle est instanciée par `classFactory` (voir __init__.py) et expose les deux
méthodes attendues par QGIS :
  * initGui()  : crée l'action de menu / barre d'outils ;
  * unload()   : retire proprement le plugin.
"""

import os

from qgis.PyQt.QtCore import QCoreApplication
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction
from qgis.core import QgsApplication

from .smart_polygon_cleaner_dialog import SmartPolygonCleanerDialog
from .processing.spc_provider import SpcProvider

PLUGIN_DIR = os.path.dirname(__file__)


class SmartPolygonCleaner(object):
    """Plugin de nettoyage automatique des couches polygonales."""

    def __init__(self, iface):
        self.iface = iface
        self.actions = []
        self.menu = "&Polygon Verifier by JDK"
        self.dialog = None
        self.provider = None   # fournisseur Processing

    # ------------------------------------------------------------------
    @staticmethod
    def tr(message):
        """Traduction (compatibilité Qt Linguist)."""
        return QCoreApplication.translate("SmartPolygonCleaner", message)

    # ------------------------------------------------------------------
    def initProcessing(self):
        """Enregistre le fournisseur dans le registre Processing de QGIS."""
        self.provider = SpcProvider()
        QgsApplication.processingRegistry().addProvider(self.provider)

    # ------------------------------------------------------------------
    def initGui(self):
        """Crée l'icône / l'entrée de menu lors du chargement du plugin."""
        # 1) Enregistrement de la brique d'automatisation (Processing)
        self.initProcessing()

        # 2) Interface graphique classique
        icon_path = os.path.join(PLUGIN_DIR, "icons", "icon.svg")
        icon = QIcon(icon_path) if os.path.exists(icon_path) else QIcon()

        action = QAction(icon, self.tr("Polygon Verifier by JDK"),
                         self.iface.mainWindow())
        action.triggered.connect(self.run)
        action.setStatusTip(self.tr("Détecter et nettoyer les chevauchements "
                                    "de polygones"))

        self.iface.addToolBarIcon(action)
        self.iface.addPluginToVectorMenu(self.menu, action)
        self.actions.append(action)

    # ------------------------------------------------------------------
    def unload(self):
        """Retire l'icône, l'entrée de menu et le fournisseur Processing."""
        for action in self.actions:
            self.iface.removePluginVectorMenu(self.menu, action)
            self.iface.removeToolBarIcon(action)
        self.actions = []
        if self.dialog is not None:
            self.dialog.close()
            self.dialog = None
        if self.provider is not None:
            QgsApplication.processingRegistry().removeProvider(self.provider)
            self.provider = None

    # ------------------------------------------------------------------
    def run(self):
        """Ouvre (ou ré-affiche) la boîte de dialogue principale."""
        if self.dialog is None:
            self.dialog = SmartPolygonCleanerDialog(self.iface,
                                                    self.iface.mainWindow())
        # À chaque ouverture, on rafraîchit la liste des couches.
        self.dialog.populate_layers()
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()

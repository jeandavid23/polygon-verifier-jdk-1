# -*- coding: utf-8 -*-
"""
cleaner_task.py
===============
Tâche d'arrière-plan (QgsTask) qui exécute le moteur `PolygonCleaner` sans
geler l'interface de QGIS, même sur des couches de 100 000 entités et plus.

Principe :
  * Le thread PRINCIPAL extrait les entités de la couche (lecture sûre) puis
    démarre la tâche.
  * Le thread SECONDAIRE (QgsTask.run) effectue tout le calcul lourd
    (index spatial, intersections, corrections).
  * Le thread PRINCIPAL reconstruit la couche de sortie dans `finished()`,
    car la création/édition de couches doit se faire dans le thread GUI.

Signaux émis :
  * progressChanged (hérité)        : avancement 0-100
  * messageLogged(str)              : ligne de journal
  * taskCompleted / taskTerminated  : fin (succès / échec)
"""

from qgis.PyQt.QtCore import pyqtSignal
from qgis.core import QgsTask, QgsMessageLog, Qgis

from .cleaner_engine import PolygonCleaner


class CleanerTask(QgsTask):
    """Encapsule un traitement complet dans un thread Qt géré par QGIS."""

    # Signal personnalisé pour remonter les lignes de journal vers l'UI.
    messageLogged = pyqtSignal(str)

    def __init__(self, description, features, source_crs, options,
                 id_values=None):
        super(CleanerTask, self).__init__(description, QgsTask.CanCancel)
        self.features = features
        self.source_crs = source_crs
        self.options = options
        self.id_values = id_values or {}
        self.result = None
        self.exception = None

    # ------------------------------------------------------------------
    def run(self):
        """
        Code exécuté dans le thread d'arrière-plan.
        IMPORTANT : ne JAMAIS toucher à l'interface (widgets) ici, ni créer
        de couche. On se contente de calculer et de stocker le résultat.
        """
        try:
            cleaner = PolygonCleaner(self.features, self.source_crs,
                                     self.options, id_values=self.id_values)

            self.result = cleaner.run(
                progress_cb=self._on_progress,
                log_cb=self._on_log,
                is_canceled=self.isCanceled,   # annulation coopérative
            )
            return not self.isCanceled()
        except Exception as exc:  # on remonte proprement toute erreur
            self.exception = exc
            QgsMessageLog.logMessage(
                "Erreur dans CleanerTask : %s" % exc,
                "Polygon Verifier by JDK", Qgis.Critical)
            return False

    # ------------------------------------------------------------------
    def _on_progress(self, percent):
        """Relaie l'avancement vers la barre de progression de QGIS."""
        self.setProgress(float(percent))

    def _on_log(self, message):
        """Relaie une ligne de journal vers l'UI via le signal Qt."""
        self.messageLogged.emit(message)

    # ------------------------------------------------------------------
    def cancel(self):
        """Annulation demandée par l'utilisateur."""
        self.messageLogged.emit("Annulation demandée…")
        super(CleanerTask, self).cancel()

# -*- coding: utf-8 -*-
"""
smart_polygon_cleaner_dialog.py
===============================
Boîte de dialogue principale du plugin.

Elle gère :
  * la construction de l'interface (chargement du fichier .ui Qt Designer,
    avec repli programmatique si le .ui est indisponible) ;
  * la sélection de la couche et des options ;
  * le lancement du moteur via une tâche d'arrière-plan (CleanerTask) ;
  * la construction de la couche de sortie nettoyée (dans le thread GUI) ;
  * l'export du rapport (TXT / CSV / PDF).
"""

import os

from qgis.PyQt import uic
from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGroupBox, QLabel, QComboBox,
    QDoubleSpinBox, QCheckBox, QPushButton, QProgressBar, QPlainTextEdit,
    QTextBrowser, QFileDialog, QMessageBox, QSizePolicy,
)
from qgis.PyQt.QtCore import Qt

from qgis.core import (
    QgsProject, QgsVectorLayer, QgsFeature, QgsFields, QgsField,
    QgsGeometry, QgsWkbTypes, QgsApplication, QgsMessageLog, Qgis,
)
from qgis.PyQt.QtCore import QVariant

from .cleaner_engine import CleaningOptions
from .cleaner_task import CleanerTask
from .report_generator import ReportGenerator

PLUGIN_DIR = os.path.dirname(__file__)
UI_PATH = os.path.join(PLUGIN_DIR, "ui", "spc_dialog.ui")


class SmartPolygonCleanerDialog(QDialog):
    """Fenêtre principale du plugin."""

    def __init__(self, iface, parent=None):
        super(SmartPolygonCleanerDialog, self).__init__(parent)
        self.iface = iface
        self.task = None              # référence à la tâche en cours
        self.last_result = None       # dernier CleaningResult (pour le rapport)
        self.last_layer_name = ""

        # --- Construction de l'UI : .ui Qt Designer, sinon programmatique ---
        loaded = False
        if os.path.exists(UI_PATH):
            try:
                uic.loadUi(UI_PATH, self)
                loaded = True
            except Exception as exc:
                QgsMessageLog.logMessage(
                    "Chargement .ui impossible, repli programmatique : %s"
                    % exc, "Polygon Verifier by JDK", Qgis.Warning)
        if not loaded:
            self._build_ui()

        self.setWindowTitle("Polygon Verifier by JDK")
        self.resize(560, 640)

        self._connect_signals()
        self.populate_layers()

    # ==================================================================
    #  Construction programmatique de l'interface (repli)
    # ==================================================================
    def _build_ui(self):
        """
        Construit l'intégralité de l'interface en code PyQt5.
        Les noms d'objets sont identiques à ceux du fichier .ui afin que le
        reste de la classe fonctionne quelle que soit la méthode de chargement.
        """
        root = QVBoxLayout(self)

        # --- Groupe : couche et seuil ---
        g_in = QGroupBox("Données d'entrée")
        f_in = QVBoxLayout(g_in)

        f_in.addWidget(QLabel("Couche polygonale :"))
        self.cmbLayer = QComboBox()
        self.cmbLayer.currentIndexChanged.connect(self._populate_id_fields)
        f_in.addWidget(self.cmbLayer)

        f_in.addWidget(QLabel("Champ identifiant (pour le champ « overlap ») :"))
        self.cmbIdField = QComboBox()
        f_in.addWidget(self.cmbIdField)

        row = QHBoxLayout()
        row.addWidget(QLabel("Seuil de chevauchement (%) :"))
        self.spinThreshold = QDoubleSpinBox()
        self.spinThreshold.setRange(0.0, 100.0)
        self.spinThreshold.setDecimals(2)
        self.spinThreshold.setValue(18.0)   # valeur métier par défaut
        row.addWidget(self.spinThreshold)
        row.addStretch()
        f_in.addLayout(row)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Superficie minimale (ha) :"))
        self.spinMinArea = QDoubleSpinBox()
        self.spinMinArea.setRange(0.0, 1000000.0)
        self.spinMinArea.setDecimals(4)
        self.spinMinArea.setValue(0.01)
        row2.addWidget(self.spinMinArea)
        row2.addStretch()
        f_in.addLayout(row2)
        root.addWidget(g_in)

        # --- Groupe : options ---
        g_opt = QGroupBox("Options")
        f_opt = QVBoxLayout(g_opt)
        self.chkFix = QCheckBox(
            "Corriger les géométries (makeValid + nœuds dupliqués + tampon 0)")
        self.chkSelfInt = QCheckBox(
            "Vérifier la validité (moteur QGIS interne)")
        self.chkDetect = QCheckBox(
            "Détecter et annoter les chevauchements faibles (≤ seuil)")
        self.chkStrong = QCheckBox(
            "Supprimer les chevauchements forts (> seuil)")
        self.chkDuplicates = QCheckBox("Supprimer les doublons exacts")
        self.chkContain = QCheckBox(
            "Supprimer les polygones entièrement superposés (inclusion totale)")
        self.chkSmall = QCheckBox(
            "Supprimer les polygones < superficie minimale")
        self.chkSlivers = QCheckBox("Supprimer les esquilles (slivers)")
        self.chkExport = QCheckBox("Exporter le rapport automatiquement")
        for chk in (self.chkFix, self.chkSelfInt, self.chkDetect, self.chkStrong,
                    self.chkDuplicates, self.chkContain, self.chkSmall,
                    self.chkSlivers):
            chk.setChecked(True)
        for chk in (self.chkFix, self.chkSelfInt, self.chkDetect, self.chkStrong,
                    self.chkDuplicates, self.chkContain, self.chkSmall,
                    self.chkSlivers, self.chkExport):
            f_opt.addWidget(chk)
        root.addWidget(g_opt)

        # --- Boutons d'action ---
        row_btn = QHBoxLayout()
        self.btnAnalyze = QPushButton("Analyser")
        self.btnClean = QPushButton("Nettoyer")
        self.btnReport = QPushButton("Exporter le rapport")
        self.btnReport.setEnabled(False)
        row_btn.addWidget(self.btnAnalyze)
        row_btn.addWidget(self.btnClean)
        row_btn.addWidget(self.btnReport)
        root.addLayout(row_btn)

        # --- Barre de progression ---
        self.progressBar = QProgressBar()
        self.progressBar.setValue(0)
        root.addWidget(self.progressBar)

        # --- Résumé statistique ---
        root.addWidget(QLabel("Résumé :"))
        self.txtSummary = QTextBrowser()
        self.txtSummary.setMinimumHeight(120)
        root.addWidget(self.txtSummary)

        # --- Journal ---
        root.addWidget(QLabel("Journal des traitements :"))
        self.txtLog = QPlainTextEdit()
        self.txtLog.setReadOnly(True)
        self.txtLog.setMinimumHeight(120)
        self.txtLog.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        root.addWidget(self.txtLog)

        # --- Bouton fermer ---
        self.btnClose = QPushButton("Fermer")
        root.addWidget(self.btnClose, alignment=Qt.AlignRight)

    # ==================================================================
    #  Connexions et état initial
    # ==================================================================
    def _connect_signals(self):
        self.btnAnalyze.clicked.connect(lambda: self.start(clean=False))
        self.btnClean.clicked.connect(lambda: self.start(clean=True))
        self.btnReport.clicked.connect(self.export_report)
        # btnClose peut ne pas exister selon le .ui ; on protège l'accès.
        if hasattr(self, "btnClose"):
            self.btnClose.clicked.connect(self.close)

    def populate_layers(self):
        """Remplit la liste déroulante avec les couches polygonales du projet."""
        self.cmbLayer.clear()
        for layer in QgsProject.instance().mapLayers().values():
            if (isinstance(layer, QgsVectorLayer) and
                    layer.geometryType() == QgsWkbTypes.PolygonGeometry):
                # On stocke l'id de la couche en donnée associée.
                self.cmbLayer.addItem(layer.name(), layer.id())
        self._populate_id_fields()

    def _populate_id_fields(self):
        """Remplit la liste des champs identifiants pour la couche choisie."""
        if not hasattr(self, "cmbIdField"):
            return
        self.cmbIdField.clear()
        # Option de repli : FID interne
        self.cmbIdField.addItem("(FID interne)", "")
        layer = self._current_layer()
        if layer is None:
            return
        default_idx = -1
        for i, field in enumerate(layer.fields()):
            self.cmbIdField.addItem(field.name(), field.name())
            if field.name().lower() == "field_id":
                default_idx = self.cmbIdField.count() - 1
        if default_idx >= 0:
            self.cmbIdField.setCurrentIndex(default_idx)

    def _current_layer(self):
        """Retourne la couche actuellement sélectionnée (ou None)."""
        idx = self.cmbLayer.currentIndex()
        if idx < 0:
            return None
        layer_id = self.cmbLayer.itemData(idx)
        return QgsProject.instance().mapLayer(layer_id)

    def _id_field_name(self):
        """Nom du champ identifiant choisi ('' = FID interne)."""
        if not hasattr(self, "cmbIdField"):
            return ""
        return self.cmbIdField.currentData() or ""

    def _collect_options(self):
        """Construit l'objet CleaningOptions depuis l'état de l'interface."""
        opt = CleaningOptions()
        opt.threshold = float(self.spinThreshold.value())
        opt.min_area_ha = float(self.spinMinArea.value())
        opt.fix_geometries = self.chkFix.isChecked()
        opt.check_self_intersections = self.chkSelfInt.isChecked()
        opt.detect_overlaps = self.chkDetect.isChecked()
        opt.remove_exact_duplicates = self.chkDuplicates.isChecked()
        opt.remove_full_containment = self.chkContain.isChecked()
        opt.remove_small = self.chkSmall.isChecked()
        opt.remove_slivers = self.chkSlivers.isChecked()
        opt.remove_over_threshold = self.chkStrong.isChecked()
        opt.id_field = self._id_field_name()
        return opt

    # ==================================================================
    #  Lancement du traitement
    # ==================================================================
    def log(self, message):
        self.txtLog.appendPlainText(message)

    def start(self, clean=False):
        """
        Démarre l'analyse (clean=False) ou le nettoyage complet (clean=True).
        L'analyse ne produit pas de couche : elle calcule seulement les
        statistiques. Le nettoyage produit en plus la couche corrigée.
        """
        layer = self._current_layer()
        if layer is None:
            QMessageBox.warning(self, "Polygon Verifier by JDK",
                                "Veuillez sélectionner une couche polygonale.")
            return
        if self.task is not None:
            QMessageBox.information(self, "Polygon Verifier by JDK",
                                    "Un traitement est déjà en cours.")
            return

        self.txtLog.clear()
        self.progressBar.setValue(0)
        self._produce_layer = clean
        self.last_layer_name = layer.name()

        # --- Extraction des entités dans le thread principal (lecture sûre) ---
        self.log("Lecture de la couche « %s »…" % layer.name())
        id_field = self._id_field_name()
        id_idx = layer.fields().indexFromName(id_field) if id_field else -1
        features = []
        id_values = {}
        for feat in layer.getFeatures():
            geom = feat.geometry()
            fid = feat.id()
            features.append({
                'fid': fid,
                'geometry': QgsGeometry(geom) if geom else QgsGeometry(),
                'attributes': feat.attributes(),
            })
            if id_idx >= 0:
                val = feat.attributes()[id_idx]
                id_values[fid] = "" if val is None else str(val)
        self.log("%d entités lues." % len(features))

        self._source_layer = layer
        options = self._collect_options()

        # --- Création et lancement de la tâche d'arrière-plan ---
        self.task = CleanerTask("Vérification polygonale", features,
                                layer.crs(), options, id_values=id_values)
        self.task.messageLogged.connect(self.log)
        self.task.progressChanged.connect(
            lambda p: self.progressBar.setValue(int(p)))
        self.task.taskCompleted.connect(self._on_finished)
        self.task.taskTerminated.connect(self._on_terminated)

        self.btnAnalyze.setEnabled(False)
        self.btnClean.setEnabled(False)
        QgsApplication.taskManager().addTask(self.task)

    # ==================================================================
    #  Callbacks de fin de tâche (thread principal)
    # ==================================================================
    def _on_finished(self):
        """Succès : on récupère le résultat, on bâtit la couche et le résumé."""
        result = self.task.result
        self.last_result = result
        self.btnAnalyze.setEnabled(True)
        self.btnClean.setEnabled(True)
        self.btnReport.setEnabled(True)

        if self._produce_layer:
            self._build_output_layer(result)

        self._show_summary(result)

        if self.chkExport.isChecked():
            self.export_report(auto=True)

        self.task = None

    def _on_terminated(self):
        """Échec ou annulation."""
        self.btnAnalyze.setEnabled(True)
        self.btnClean.setEnabled(True)
        if self.task and self.task.exception:
            QMessageBox.critical(self, "Polygon Verifier by JDK",
                                 "Erreur : %s" % self.task.exception)
        else:
            self.log("Traitement interrompu.")
        self.task = None

    # ==================================================================
    #  Construction des couches de sortie (thread principal)
    # ==================================================================
    def _build_output_layer(self, result):
        """
        Produit deux couches :
          1. « <nom>_verifie »   : entités conservées + champs Surface_Ha,
             overlap, ovlp_pct, is_ovlp.
          2. « <nom>_supprimes » : entités supprimées + Surface_Ha, raison,
             ref_id (identifiant de l'entité de référence).
        """
        src = self._source_layer
        geom_type = QgsWkbTypes.displayString(src.wkbType())
        crs = src.crs().authid()
        uri = "%s?crs=%s" % (geom_type, crs)

        # ---------- Couche CONSERVÉS (annotée) ----------
        out = QgsVectorLayer(uri, "%s_verifie" % src.name(), "memory")
        prov = out.dataProvider()

        fields = QgsFields(src.fields())
        idx_area_src = fields.indexFromName("Surface_Ha")
        has_area = idx_area_src != -1
        if not has_area:
            fields.append(QgsField("Surface_Ha", QVariant.Double, "double", 20, 6))
        # Champs d'annotation (créés s'ils n'existent pas déjà)
        extra = []
        if fields.indexFromName("overlap") == -1:
            fields.append(QgsField("overlap", QVariant.String, "string", 500))
            extra.append("overlap")
        if fields.indexFromName("ovlp_pct") == -1:
            fields.append(QgsField("ovlp_pct", QVariant.Double, "double", 10, 2))
            extra.append("ovlp_pct")
        if fields.indexFromName("is_ovlp") == -1:
            fields.append(QgsField("is_ovlp", QVariant.String, "string", 5))
            extra.append("is_ovlp")
        prov.addAttributes(fields.toList())
        out.updateFields()

        ov_i = out.fields().indexFromName("overlap")
        pct_i = out.fields().indexFromName("ovlp_pct")
        is_i = out.fields().indexFromName("is_ovlp")
        area_i = out.fields().indexFromName("Surface_Ha")

        new_feats = []
        for item in result.kept_features:
            f = QgsFeature(out.fields())
            f.setGeometry(item['geometry'])
            # On part des attributs d'origine, complétés à la bonne longueur.
            attrs = list(item['attributes'])
            while len(attrs) < len(out.fields()):
                attrs.append(None)
            attrs[area_i] = round(item['area_ha'], 6)
            attrs[ov_i] = item.get('overlap', "")
            attrs[pct_i] = item.get('ovlp_pct', 0.0)
            attrs[is_i] = item.get('is_ovlp', "NON")
            f.setAttributes(attrs)
            new_feats.append(f)
        prov.addFeatures(new_feats)
        out.updateExtents()
        QgsProject.instance().addMapLayer(out)
        self.log("Couche conservés : « %s » (%d entités, dont %d superposées)."
                 % (out.name(), out.featureCount(),
                    result.overlaps_detected))

        # ---------- Couche SUPPRIMÉS ----------
        if result.deleted_features:
            dele = QgsVectorLayer(uri, "%s_supprimes" % src.name(), "memory")
            dprov = dele.dataProvider()
            dfields = QgsFields(src.fields())
            if dfields.indexFromName("Surface_Ha") == -1:
                dfields.append(QgsField("Surface_Ha", QVariant.Double,
                                        "double", 20, 6))
            dfields.append(QgsField("del_reason", QVariant.String, "string", 50))
            dfields.append(QgsField("ref_id", QVariant.String, "string", 100))
            dprov.addAttributes(dfields.toList())
            dele.updateFields()

            darea_i = dele.fields().indexFromName("Surface_Ha")
            reason_i = dele.fields().indexFromName("del_reason")
            ref_i = dele.fields().indexFromName("ref_id")

            del_feats = []
            for item in result.deleted_features:
                f = QgsFeature(dele.fields())
                f.setGeometry(item['geometry'])
                attrs = list(item['attributes'])
                while len(attrs) < len(dele.fields()):
                    attrs.append(None)
                attrs[darea_i] = round(item['area_ha'], 6)
                attrs[reason_i] = item.get('reason', "")
                attrs[ref_i] = item.get('ref_id', "")
                f.setAttributes(attrs)
                del_feats.append(f)
            dprov.addFeatures(del_feats)
            dele.updateExtents()
            QgsProject.instance().addMapLayer(dele)
            self.log("Couche supprimés : « %s » (%d entités)."
                     % (dele.name(), dele.featureCount()))

    # ==================================================================
    #  Résumé et rapport
    # ==================================================================
    def _show_summary(self, result):
        gen = ReportGenerator(result, self.last_layer_name,
                              float(self.spinThreshold.value()))
        self.txtSummary.setHtml(gen.to_html())

    def export_report(self, auto=False):
        """Exporte le rapport. `auto=True` exporte en TXT sans dialogue."""
        if self.last_result is None:
            QMessageBox.information(self, "Polygon Verifier by JDK",
                                    "Aucun résultat à exporter.")
            return

        gen = ReportGenerator(self.last_result, self.last_layer_name,
                              float(self.spinThreshold.value()))

        if auto:
            path = os.path.join(
                os.path.expanduser("~"),
                "spc_rapport_%s.txt" % self.last_layer_name)
            try:
                gen.to_txt(path)
                self.log("Rapport exporté automatiquement : %s" % path)
            except Exception as exc:
                self.log("Échec export auto : %s" % exc)
            return

        path, sel = QFileDialog.getSaveFileName(
            self, "Exporter le rapport", "rapport_nettoyage",
            "PDF (*.pdf);;CSV (*.csv);;Texte (*.txt)")
        if not path:
            return
        try:
            if path.lower().endswith(".pdf") or "pdf" in sel.lower():
                if not path.lower().endswith(".pdf"):
                    path += ".pdf"
                gen.to_pdf(path)
            elif path.lower().endswith(".csv") or "csv" in sel.lower():
                if not path.lower().endswith(".csv"):
                    path += ".csv"
                gen.to_csv(path)
            else:
                if not path.lower().endswith(".txt"):
                    path += ".txt"
                gen.to_txt(path)
            QMessageBox.information(self, "Polygon Verifier by JDK",
                                    "Rapport exporté :\n%s" % path)
        except Exception as exc:
            QMessageBox.critical(self, "Polygon Verifier by JDK",
                                 "Erreur d'export : %s" % exc)

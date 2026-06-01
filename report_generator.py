# -*- coding: utf-8 -*-
"""
report_generator.py
===================
Génère le rapport de traitement à partir d'un objet `CleaningResult`.

Formats pris en charge :
  * TXT  : rapport texte simple ;
  * CSV  : statistiques tabulées (séparateur « ; ») ;
  * PDF  : rapport mis en forme, produit via QTextDocument + QPrinter
           (livrés avec PyQt5 / QtPrintSupport — aucune dépendance externe).

Le HTML intermédiaire sert aussi à l'affichage du résumé dans l'interface.
"""

import csv
import datetime

from qgis.PyQt.QtGui import QTextDocument
from qgis.PyQt.QtPrintSupport import QPrinter


# Libellés lisibles des types d'erreurs topologiques.
ERROR_LABELS = {
    "self_intersection":         "Auto-intersection",
    "ring_self_intersection":    "Auto-intersection d'anneau",
    "duplicate_vertices":        "Sommets dupliqués",
    "invalid_geometry":          "Géométrie invalide",
    "geometry_collection_error": "Erreur de collection géométrique",
    "polygon_hole_error":        "Erreur de trou de polygone",
    "sliver_polygon":            "Polygone esquille (sliver)",
    "null_geometry":             "Géométrie nulle",
    "validation_impossible":     "Validation impossible",
}


class ReportGenerator(object):
    """Construit et exporte le rapport final."""

    def __init__(self, result, layer_name="(couche)", threshold=18.0):
        self.result = result
        self.layer_name = layer_name
        self.threshold = threshold
        self.timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # ------------------------------------------------------------------
    #  Bloc de statistiques commun à tous les formats
    # ------------------------------------------------------------------
    def _stats_rows(self):
        """Retourne la liste (libellé, valeur) des statistiques principales."""
        r = self.result
        area_diff = r.initial_area_ha - r.final_area_ha
        return [
            ("Couche traitée",               self.layer_name),
            ("Date du traitement",           self.timestamp),
            ("Seuil de chevauchement (%)",   "%.2f" % self.threshold),
            ("Polygones initiaux",           r.initial_count),
            ("Polygones conservés",          r.final_count),
            ("Polygones supprimés",          r.deleted_count),
            ("  dont chevauchements forts (>seuil)", r.over_threshold_removed),
            ("  dont doublons exacts",       r.duplicates_removed),
            ("  dont inclusions totales",    r.containment_removed),
            ("  dont surfaces < seuil",      r.small_removed),
            ("  dont esquilles (slivers)",   r.slivers_removed),
            ("Chevauchements faibles annotés", r.overlaps_detected),
            ("Géométries corrigées",         r.geometries_fixed),
            ("Surface initiale (ha)",        "%.4f" % r.initial_area_ha),
            ("Surface finale (ha)",          "%.4f" % r.final_area_ha),
            ("Variation de surface (ha)",    "%.4f" % area_diff),
            ("Temps de traitement (s)",      "%.2f" % r.processing_time),
        ]

    # ------------------------------------------------------------------
    #  TXT
    # ------------------------------------------------------------------
    def to_txt(self, path):
        lines = []
        lines.append("=" * 60)
        lines.append("  RAPPORT DE NETTOYAGE — POLYGON VÉRIFICATER BY JDK")
        lines.append("=" * 60)
        lines.append("")
        for label, value in self._stats_rows():
            lines.append("%-34s : %s" % (label, value))
        lines.append("")
        lines.append("-" * 60)
        lines.append("  ERREURS TOPOLOGIQUES DÉTECTÉES")
        lines.append("-" * 60)
        if self.result.topology_errors:
            for key, count in sorted(self.result.topology_errors.items()):
                lines.append("%-34s : %d" %
                             (ERROR_LABELS.get(key, key), count))
        else:
            lines.append("Aucune erreur topologique détectée.")
        lines.append("")
        lines.append("-" * 60)
        lines.append("  JOURNAL D'EXÉCUTION")
        lines.append("-" * 60)
        lines.extend(self.result.log_lines)

        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        return path

    # ------------------------------------------------------------------
    #  CSV
    # ------------------------------------------------------------------
    def to_csv(self, path):
        with open(path, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f, delimiter=";")
            writer.writerow(["Indicateur", "Valeur"])
            for label, value in self._stats_rows():
                writer.writerow([label, value])
            writer.writerow([])
            writer.writerow(["Type d'erreur topologique", "Nombre"])
            for key, count in sorted(self.result.topology_errors.items()):
                writer.writerow([ERROR_LABELS.get(key, key), count])
            writer.writerow([])
            # Détail des suppressions (traçabilité / audit)
            writer.writerow(["FID supprimé", "Raison",
                             "ID de référence", "Surface (ha)"])
            for d in self.result.deleted_features:
                writer.writerow([d.get('fid'), d.get('reason', ''),
                                 d.get('ref_id', ''),
                                 round(d.get('area_ha', 0.0), 6)])
        return path

    # ------------------------------------------------------------------
    #  HTML (résumé écran + base du PDF)
    # ------------------------------------------------------------------
    def to_html(self):
        rows = "".join(
            "<tr><td style='padding:3px 10px;color:#555'>%s</td>"
            "<td style='padding:3px 10px;font-weight:bold'>%s</td></tr>"
            % (label, value) for label, value in self._stats_rows()
        )

        if self.result.topology_errors:
            err_rows = "".join(
                "<tr><td style='padding:2px 10px'>%s</td>"
                "<td style='padding:2px 10px'>%d</td></tr>"
                % (ERROR_LABELS.get(k, k), c)
                for k, c in sorted(self.result.topology_errors.items())
            )
        else:
            err_rows = ("<tr><td colspan='2' style='padding:2px 10px'>"
                        "Aucune erreur topologique détectée.</td></tr>")

        return """
        <h2 style="color:#1565C0;margin-bottom:4px">Rapport de nettoyage</h2>
        <p style="color:#888;margin-top:0">Polygon Verifier by JDK — %s</p>
        <table style="border-collapse:collapse;width:100%%">%s</table>
        <h3 style="color:#1565C0">Erreurs topologiques</h3>
        <table style="border-collapse:collapse;width:100%%">%s</table>
        """ % (self.timestamp, rows, err_rows)

    # ------------------------------------------------------------------
    #  PDF
    # ------------------------------------------------------------------
    def to_pdf(self, path):
        """
        Génère un PDF à partir du rendu HTML, via QTextDocument + QPrinter.
        Ne nécessite aucune bibliothèque externe (ReportLab inutile).
        """
        doc = QTextDocument()
        doc.setHtml(self.to_html())
        printer = QPrinter(QPrinter.HighResolution)
        printer.setOutputFormat(QPrinter.PdfFormat)
        printer.setOutputFileName(path)
        doc.print_(printer)
        return path

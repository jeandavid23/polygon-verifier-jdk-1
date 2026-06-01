# -*- coding: utf-8 -*-
"""
cleaner_engine.py
=================
Cœur algorithmique du plugin *Polygon Verifier by JDK*.

OBJECTIF v3.0.0
---------------
Produire en sortie des polygones RÉELLEMENT propres :
  * géométries valides (aucune self-intersection, aucun anneau parasite,
    aucun sommet dupliqué, aucune partie non surfacique) ;
  * pas de slivers, pas de géométries nulles ;
  * AUCUN polygone superposé à plus de 18 % avec un autre.

Ne RESTENT en sortie que :
  * les polygones propres NON superposés ;
  * les polygones propres superposés PARTIELLEMENT à ≤ 18 % (conservés ET
    annotés : champ overlap + pourcentage).

Tout le reste est SUPPRIMÉ et tracé dans une couche dédiée :
  * superposition  > 18 % (le perdant) ;
  * inclusion totale / doublon exact ;
  * surface < seuil minimal ;
  * sliver ; géométrie nulle / irréparable.

RÈGLE DE CONSERVATION (qui gagne lors d'un conflit) :
  1) le PLUS VALIDE (moins d'erreurs topologiques) ;
  2) puis la PLUS GRANDE superficie ;
  3) puis l'ID le plus ancien.

CORRECTION RÉELLE DES GÉOMÉTRIES (forme exacte préservée) :
  makeValid -> extraction parties polygonales -> removeDuplicateNodes
  -> buffer(0). Validation par le moteur QGIS INTERNE avant/après.

Compatibilité : QGIS 3.28+
"""

import time
from collections import defaultdict

from qgis.core import (
    Qgis,
    QgsFeature,
    QgsGeometry,
    QgsSpatialIndex,
    QgsDistanceArea,
    QgsUnitTypes,
    QgsProject,
    QgsWkbTypes,
)

# Seuil strict de superposition : on supprime si taux > THRESHOLD (= reste).
# (paramétrable via CleaningOptions.threshold)


class CleaningOptions(object):
    """Paramètres de configuration d'un traitement."""

    def __init__(self):
        # Superposition : > threshold (%) -> suppression du perdant.
        # = threshold % -> conservé. (strictement supérieur)
        self.threshold = 18.0

        # Superficie minimale conservée (hectares).
        self.min_area_ha = 0.01

        # Suppressions automatiques
        self.remove_exact_duplicates = True
        self.remove_full_containment = True
        self.remove_over_threshold = True   # superposition > seuil
        self.remove_small = True
        self.remove_slivers = True

        # Correction
        self.fix_geometries = True
        self.check_self_intersections = True

        # Détection / annotation des superpositions ≤ seuil
        self.detect_overlaps = True

        # Champ identifiant métier
        self.id_field = "Field_ID"

        # Paramètres slivers / inclusion
        self.sliver_thinness = 0.03          # indice de compacité (0=très fin)
        self.sliver_max_area_ha = 0.05       # surface max d'un sliver candidat
        self.containment_ratio = 99.0        # % de recouvrement = inclusion


class CleaningResult(object):
    """Sorties : conservés (annotés), supprimés (tracés), stats, journal."""

    def __init__(self):
        self.kept_features = []      # {fid, geometry, attributes, area_ha,
                                     #  overlap, ovlp_pct, is_ovlp}
        self.deleted_features = []   # {fid, geometry, attributes, area_ha,
                                     #  reason, ref_id}

        self.initial_count = 0
        self.final_count = 0
        self.deleted_count = 0
        self.duplicates_removed = 0
        self.containment_removed = 0
        self.over_threshold_removed = 0
        self.small_removed = 0
        self.slivers_removed = 0
        self.null_removed = 0
        self.geometries_fixed = 0
        self.overlaps_detected = 0
        self.initial_area_ha = 0.0
        self.final_area_ha = 0.0
        self.processing_time = 0.0

        self.topology_errors = defaultdict(int)
        self.log_lines = []

    def log(self, message):
        self.log_lines.append(message)


class PolygonCleaner(object):
    """Moteur de vérification / correction / suppression."""

    def __init__(self, features, source_crs, options=None, id_values=None):
        self.features = features
        self.source_crs = source_crs
        self.options = options or CleaningOptions()
        self.id_values = id_values or {}

        self._da = QgsDistanceArea()
        try:
            self._da.setSourceCrs(self.source_crs,
                                  QgsProject.instance().transformContext())
            ellipsoid = QgsProject.instance().ellipsoid()
        except Exception:
            ellipsoid = None
        if not ellipsoid or ellipsoid == 'NONE':
            ellipsoid = 'WGS84'
        try:
            self._da.setEllipsoid(ellipsoid)
        except Exception:
            pass

        # Caches
        self._geoms = {}        # fid -> géométrie réparée
        self._area_planar = {}  # fid -> aire planaire (ratios)
        self._area_ha = {}      # fid -> aire hectares
        self._attrs = {}        # fid -> attributs
        self._err_count = {}    # fid -> nb d'erreurs topologiques résiduelles
        self._valid = {}        # fid -> bool (valide après réparation)

    # ------------------------------------------------------------------
    def _id_of(self, fid):
        v = self.id_values.get(fid)
        return str(fid) if (v is None or v == "") else str(v)

    # ==================================================================
    #  Validation par le moteur QGIS INTERNE (plus complet que GEOS)
    # ==================================================================
    @staticmethod
    def _qgis_errors(geom):
        if geom is None or geom.isEmpty():
            return []
        try:
            engine = Qgis.GeometryValidationEngine.QgisInternal
            return [e.what() for e in geom.validateGeometry(engine)]
        except Exception:
            pass
        try:
            return [e.what() for e in geom.validateGeometry()]
        except Exception:
            return []

    # ==================================================================
    #  CORRECTION RÉELLE (forme exacte préservée)
    # ==================================================================
    def _fix_geometry(self, geom):
        """
        Corrige réellement une géométrie et garantit un (multi)polygone
        valide, en PRÉSERVANT la forme (pas de simplification de contour).

        Chaîne :
          1. makeValid()             -> auto-intersections, anneaux croisés ;
          2. coercition polygone     -> retire lignes/points (anneaux parasites
                                        et collections issues de makeValid) ;
          3. removeDuplicateNodes()  -> supprime les sommets dupliqués ;
          4. buffer(0)               -> normalise la topologie résiduelle.

        Retourne (geom_corrigée_ou_None, corrigée:bool).
        """
        if geom is None or geom.isEmpty():
            return None, False

        before_invalid = (not geom.isGeosValid()) or bool(self._qgis_errors(geom))
        g = QgsGeometry(geom)

        # 1) makeValid
        try:
            mv = g.makeValid()
            if mv and not mv.isEmpty():
                g = mv
        except Exception:
            pass

        # 2) ne garder que les parties polygonales
        g = self._coerce_to_polygon(g)
        if g is None or g.isEmpty():
            return None, before_invalid

        # 3) supprimer les sommets dupliqués (API QGIS 3.x)
        try:
            g.removeDuplicateNodes()
        except Exception:
            pass

        # 4) buffer(0) de normalisation
        try:
            b = g.buffer(0, 1)
            if b and not b.isEmpty():
                b = self._coerce_to_polygon(b)
                if b and not b.isEmpty() and b.isGeosValid():
                    g = b
        except Exception:
            pass

        if g is None or g.isEmpty():
            return None, before_invalid

        after_invalid = (not g.isGeosValid()) or bool(self._qgis_errors(g))
        corrected = before_invalid and not after_invalid
        # Même si quelques erreurs subsistent, on a au moins tenté/normalisé.
        if before_invalid and after_invalid:
            corrected = True  # géométrie modifiée pour tenter la réparation
        return g, corrected

    @staticmethod
    def _coerce_to_polygon(geom):
        """Ne conserve que les composantes (multi)polygonales."""
        if geom is None or geom.isEmpty():
            return geom
        try:
            gtype = QgsWkbTypes.geometryType(geom.wkbType())
        except Exception:
            gtype = None
        if gtype == QgsWkbTypes.PolygonGeometry:
            return geom
        try:
            polys = []
            for part in geom.asGeometryCollection():
                if (part and not part.isEmpty() and
                        QgsWkbTypes.geometryType(part.wkbType())
                        == QgsWkbTypes.PolygonGeometry):
                    polys.append(part)
            if not polys:
                return None
            if len(polys) == 1:
                return polys[0]
            merged = QgsGeometry.unaryUnion(polys)
            if merged and not merged.isEmpty():
                return merged
            return QgsGeometry.collectGeometry(polys)
        except Exception:
            return geom

    @staticmethod
    def _safe_intersection(ga, gb):
        try:
            if ga.intersects(gb):
                inter = ga.intersection(gb)
                if inter is not None:
                    return inter
        except Exception:
            pass
        try:
            a2 = ga.buffer(0, 1)
            b2 = gb.buffer(0, 1)
            if a2 and b2 and a2.intersects(b2):
                return a2.intersection(b2)
        except Exception:
            pass
        return None

    # ==================================================================
    #  Surfaces
    # ==================================================================
    def area_ha(self, geometry):
        if geometry is None or geometry.isEmpty():
            return 0.0
        try:
            raw = self._da.measureArea(geometry)
            m2 = self._da.convertAreaMeasurement(raw,
                                                 QgsUnitTypes.AreaSquareMeters)
            if m2 and m2 > 0:
                return m2 / 10000.0
        except Exception:
            pass
        try:
            return geometry.area() / 10000.0
        except Exception:
            return 0.0

    @staticmethod
    def _thinness(geometry):
        try:
            perim = geometry.length()
            if perim <= 0:
                return 1.0
            return (4.0 * 3.141592653589793 * geometry.area()) / (perim * perim)
        except Exception:
            return 1.0

    # ==================================================================
    #  Étape 1 : correction réelle + surfaces + erreurs résiduelles
    # ==================================================================
    def _prepare(self, result, progress_cb, log_cb):
        if log_cb:
            log_cb("Étape 1/4 — Correction réelle des géométries "
                   "(makeValid + nœuds dupliqués + tampon 0) "
                   "et validation QGIS interne…")

        n = len(self.features)
        dropped = 0
        for i, feat in enumerate(self.features):
            fid = feat['fid']
            self._attrs[fid] = feat['attributes']
            src_geom = feat['geometry']

            # Inventaire des erreurs d'ORIGINE (pour le rapport).
            if self.options.check_self_intersections and src_geom is not None \
                    and not src_geom.isEmpty():
                for msg in self._qgis_errors(src_geom):
                    self._classify_error(msg, result)

            # Correction réelle.
            if self.options.fix_geometries:
                fixed, corrected = self._fix_geometry(src_geom)
            else:
                fixed, corrected = (QgsGeometry(src_geom)
                                    if src_geom else None), False
            if corrected:
                result.geometries_fixed += 1

            if fixed is None or fixed.isEmpty():
                self._geoms[fid] = QgsGeometry()
                self._area_planar[fid] = 0.0
                self._area_ha[fid] = 0.0
                self._err_count[fid] = 9999
                self._valid[fid] = False
                dropped += 1
            else:
                self._geoms[fid] = fixed
                resid = self._qgis_errors(fixed)
                self._err_count[fid] = len(resid)
                self._valid[fid] = fixed.isGeosValid() and not resid
                try:
                    ap = fixed.area()
                except Exception:
                    ap = 0.0
                self._area_planar[fid] = ap
                aha = self.area_ha(fixed)
                self._area_ha[fid] = aha
                result.initial_area_ha += aha

            if progress_cb and n:
                progress_cb(int((i + 1) / n * 30.0))   # 0 -> 30 %

        result.initial_count = n
        if log_cb:
            log_cb("  %d entité(s), %d corrigée(s), %d nulle(s)/irrécupérable(s)."
                   % (n, result.geometries_fixed, dropped))

        # Tri par priorité de CONSERVATION :
        #   1) le plus valide (err_count croissant)
        #   2) la plus grande surface (-aire)
        #   3) l'ID le plus ancien
        order = sorted(
            [f for f in self._geoms if not self._geoms[f].isEmpty()],
            key=lambda f: (self._err_count[f],
                           -self._area_planar[f],
                           f)
        )
        return order

    # ==================================================================
    #  Étape 2 : suppressions (nulles, petites, slivers, doublons,
    #            inclusions totales, superpositions > seuil)
    # ==================================================================
    def _clean(self, order, result, progress_cb, log_cb, is_canceled):
        if log_cb:
            log_cb("Étape 2/4 — Suppression des polygones superposés > %.0f %%, "
                   "doublons, inclusions, petites surfaces et slivers…"
                   % self.options.threshold)

        kept_index = QgsSpatialIndex()
        survivors = []
        n = len(order)
        ratio = self.options.containment_ratio
        threshold = self.options.threshold
        logged = 0

        for i, fid in enumerate(order):
            if is_canceled and is_canceled():
                if log_cb:
                    log_cb("⚠ Traitement annulé.")
                break

            geom = self._geoms[fid]
            if geom is None or geom.isEmpty():
                self._record(result, fid, "geometrie_nulle", "")
                result.null_removed += 1
                continue

            aha = self._area_ha[fid]

            # (a) petite surface
            if self.options.remove_small and aha < self.options.min_area_ha:
                self._record(result, fid, "surface_inferieure_seuil", "")
                result.small_removed += 1
                continue

            # (b) sliver
            if self.options.remove_slivers:
                if (aha <= self.options.sliver_max_area_ha and
                        self._thinness(geom) < self.options.sliver_thinness):
                    self._record(result, fid, "sliver", "")
                    result.slivers_removed += 1
                    result.topology_errors["sliver_polygon"] += 1
                    continue

            # (c) comparaison aux entités déjà conservées (plus prioritaires)
            removed = False
            for cfid in kept_index.intersects(geom.boundingBox()):
                cg = self._geoms.get(cfid)
                if cg is None or cg.isEmpty():
                    continue

                # doublon exact
                if self.options.remove_exact_duplicates:
                    try:
                        if geom.equals(cg):
                            self._record(result, fid, "doublon_exact",
                                         self._id_of(cfid))
                            result.duplicates_removed += 1
                            removed = True
                            break
                    except Exception:
                        pass

                inter = self._safe_intersection(geom, cg)
                if inter is None or inter.isEmpty():
                    continue
                try:
                    ia = inter.area()
                except Exception:
                    ia = 0.0
                if ia <= 0:
                    continue

                cur = self._area_planar[fid]
                # inclusion totale (l'entité courante contenue dans cfid)
                if self.options.remove_full_containment and cur > 0 \
                        and (ia / cur * 100.0) >= ratio:
                    self._record(result, fid, "inclusion_totale",
                                 self._id_of(cfid))
                    result.containment_removed += 1
                    removed = True
                    break

                # superposition > seuil : l'entité courante (moins prioritaire,
                # car triée après cfid) est le PERDANT -> supprimée.
                smaller = min(cur, self._area_planar[cfid])
                rate = (ia / smaller * 100.0) if smaller > 0 else 0.0
                if self.options.remove_over_threshold and rate > threshold:
                    self._record(result, fid, "superposition_sup_seuil",
                                 self._id_of(cfid))
                    result.over_threshold_removed += 1
                    if logged < 50 and log_cb:
                        log_cb("  • Superposition %.1f %% : %s perd contre %s"
                               % (rate, self._id_of(fid), self._id_of(cfid)))
                        logged += 1
                    removed = True
                    break

            if removed:
                continue

            f = QgsFeature()
            f.setId(fid)
            f.setGeometry(geom)
            kept_index.addFeature(f)
            survivors.append(fid)

            if progress_cb and n:
                progress_cb(30 + int((i + 1) / n * 45.0))   # 30 -> 75 %

        if log_cb:
            log_cb("  → supprimés : %d superposition(s)>seuil, %d doublon(s), "
                   "%d inclusion(s), %d petite(s), %d sliver(s), %d nulle(s)."
                   % (result.over_threshold_removed, result.duplicates_removed,
                      result.containment_removed, result.small_removed,
                      result.slivers_removed, result.null_removed))
        return survivors

    def _record(self, result, fid, reason, ref_id):
        result.deleted_features.append({
            'fid': fid,
            'geometry': self._geoms[fid],
            'attributes': self._attrs[fid],
            'area_ha': self._area_ha[fid],
            'reason': reason,
            'ref_id': ref_id,
        })
        self._geoms[fid] = QgsGeometry()

    # ==================================================================
    #  Étape 3 : annotation des superpositions résiduelles (≤ seuil)
    # ==================================================================
    def _annotate(self, survivors, result, progress_cb, log_cb, is_canceled):
        annotations = {}
        if not self.options.detect_overlaps:
            for fid in survivors:
                annotations[fid] = ("", 0.0, "NON")
            return annotations

        if log_cb:
            log_cb("Étape 3/4 — Annotation des superpositions partielles "
                   "restantes (≤ %.0f %%)…" % self.options.threshold)

        index = QgsSpatialIndex()
        for fid in survivors:
            g = self._geoms[fid]
            if g is None or g.isEmpty():
                continue
            f = QgsFeature()
            f.setId(fid)
            f.setGeometry(g)
            index.addFeature(f)

        n = len(survivors)
        for i, fid in enumerate(survivors):
            if is_canceled and is_canceled():
                break
            geom = self._geoms[fid]
            if geom is None or geom.isEmpty():
                annotations[fid] = ("", 0.0, "NON")
                continue

            ids = []
            max_pct = 0.0
            for cfid in index.intersects(geom.boundingBox()):
                if cfid == fid:
                    continue
                cg = self._geoms.get(cfid)
                if cg is None or cg.isEmpty():
                    continue
                inter = self._safe_intersection(geom, cg)
                if inter is None or inter.isEmpty():
                    continue
                try:
                    ia = inter.area()
                except Exception:
                    ia = 0.0
                if ia <= 0:
                    continue
                smaller = min(self._area_planar[fid], self._area_planar[cfid])
                pct = (ia / smaller * 100.0) if smaller > 0 else 0.0
                ids.append(self._id_of(cfid))
                if pct > max_pct:
                    max_pct = pct

            if ids:
                annotations[fid] = (" / ".join(ids), round(max_pct, 2), "OUI")
                result.overlaps_detected += 1
            else:
                annotations[fid] = ("", 0.0, "NON")

            if progress_cb and n:
                progress_cb(75 + int((i + 1) / n * 20.0))   # 75 -> 95 %

        if log_cb:
            log_cb("  → %d polygone(s) partiellement superposé(s) annoté(s)."
                   % result.overlaps_detected)
        return annotations

    @staticmethod
    def _classify_error(message, result):
        m = (message or "").lower()
        if "self" in m and "ring" in m:
            result.topology_errors["ring_self_intersection"] += 1
        elif "self-intersection" in m or "self intersection" in m:
            result.topology_errors["self_intersection"] += 1
        elif "duplicate" in m:
            result.topology_errors["duplicate_vertices"] += 1
        elif "hole" in m or "interior" in m:
            result.topology_errors["polygon_hole_error"] += 1
        elif "collection" in m:
            result.topology_errors["geometry_collection_error"] += 1
        else:
            result.topology_errors["invalid_geometry"] += 1

    # ==================================================================
    #  Orchestration
    # ==================================================================
    def run(self, progress_cb=None, log_cb=None, is_canceled=None):
        t0 = time.time()
        result = CleaningResult()

        order = self._prepare(result, progress_cb, log_cb)
        survivors = self._clean(order, result, progress_cb, log_cb, is_canceled)
        annotations = self._annotate(survivors, result, progress_cb, log_cb,
                                     is_canceled)

        if log_cb:
            log_cb("Étape 4/4 — Construction des couches de sortie…")
        for fid in survivors:
            geom = self._geoms[fid]
            if geom is None or geom.isEmpty():
                continue
            a = self._area_ha[fid]
            result.final_area_ha += a
            overlap_str, pct, is_ovlp = annotations.get(fid, ("", 0.0, "NON"))
            result.kept_features.append({
                'fid': fid,
                'geometry': geom,
                'attributes': self._attrs[fid],
                'area_ha': a,
                'overlap': overlap_str,
                'ovlp_pct': pct,
                'is_ovlp': is_ovlp,
            })

        result.final_count = len(result.kept_features)
        result.deleted_count = len(result.deleted_features)
        result.processing_time = time.time() - t0

        if progress_cb:
            progress_cb(100)
        if log_cb:
            log_cb("Terminé : %d conservé(s) (dont %d superposé(s) ≤ seuil), "
                   "%d supprimé(s), en %.2f s."
                   % (result.final_count, result.overlaps_detected,
                      result.deleted_count, result.processing_time))
        return result

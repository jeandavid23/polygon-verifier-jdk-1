# Polygon Verifier by JDK

**Détection et nettoyage automatique des chevauchements de polygones pour QGIS 3.34+**

Plugin professionnel de contrôle qualité géométrique destiné aux projets
**fonciers, cadastraux, agricoles, forestiers** et de **conformité EUDR / RA**,
où la propreté topologique des polygones est critique.

---

## 1. Fonctionnalités

- Calcul automatique d'un champ `Surface_Ha` (en hectares, calcul ellipsoïdal robuste).
- Détection des polygones strictement identiques (doublons exacts).
- Calcul du taux de chevauchement : `(surface d'intersection / surface du plus petit) × 100`.
- Suppression des chevauchements dépassant un **seuil métier** (18 % par défaut, paramétrable).
- Gestion des chevauchements multiples (3, 4, 5, 10 polygones et plus) via comparaison spatiale.
- Priorisation de conservation : **plus grande surface → géométrie la plus valide → ID le plus ancien**.
- Correction automatique des géométries (`makeValid`, équivalent *Fix Geometries*).
- Inventaire des erreurs topologiques (auto-intersection, anneaux, sommets dupliqués, trous, collections, slivers, géométries nulles).
- Couche de sortie propre `<nom>_cleaned` ajoutée au projet.
- Rapport exportable en **PDF / CSV / TXT** avec traçabilité des suppressions.
- Optimisé gros volumes : index spatial **R-tree**, traitement en **arrière-plan** (QgsTask) — UI non bloquée jusqu'à 100 000+ polygones.

---

## 2. Installation

### Méthode A — depuis le fichier ZIP (recommandée)
1. Ouvrir QGIS 3.34 ou supérieur.
2. Menu **Extensions ▸ Installer/Gérer les extensions ▸ Installer depuis un ZIP**.
3. Sélectionner `smart_polygon_cleaner.zip`.
4. Cliquer sur **Installer l'extension**.

### Méthode B — installation manuelle
1. Décompresser l'archive.
2. Copier le dossier `smart_polygon_cleaner` dans le répertoire des extensions :
   - **Windows** : `C:\Users\<vous>\AppData\Roaming\QGIS\QGIS3\profiles\default\python\plugins\`
   - **Linux** : `~/.local/share/QGIS/QGIS3/profiles/default/python/plugins/`
   - **macOS** : `~/Library/Application Support/QGIS/QGIS3/profiles/default/python/plugins/`
3. Relancer QGIS, puis activer l'extension dans **Extensions ▸ Gérer les extensions**.

> Aucune dépendance externe : tout repose sur PyQGIS et PyQt5 fournis avec QGIS.

---

## 3. Utilisation

1. Charger la couche polygonale (Shapefile, GeoPackage ou couche mémoire).
2. Cliquer sur l'icône **Polygon Verifier by JDK** (barre d'outils ou menu *Vecteur*).
3. Sélectionner la couche et régler le **seuil** (défaut 18 %).
4. Cocher les options souhaitées.
5. **Analyser** : calcule les statistiques sans modifier les données.
6. **Nettoyer** : produit la couche corrigée `<nom>_cleaned`.
7. **Exporter le rapport** (PDF / CSV / TXT).

### Options
| Option | Effet |
|---|---|
| Supprimer les doublons exacts | Élimine les polygones géométriquement identiques |
| Corriger les géométries | Applique `makeValid` aux entités conservées |
| Vérifier les auto-intersections | Inventorie les erreurs topologiques dans le rapport |
| Conserver la plus grande superficie | Règle de priorité de conservation |
| Exporter le rapport automatiquement | Génère un TXT dans le dossier utilisateur en fin de traitement |

---

## 4. Règle métier (rappel)

```
Taux de chevauchement (%) = (Surface d'intersection / Surface du plus petit polygone) × 100
SEUIL = 18 %
  > 18 %  -> suppression du polygone le moins prioritaire
  <= 18 % -> les deux polygones sont conservés
```

Exemples :
- A = 10 ha, B = 8 ha, intersection 2 ha → 25 % → **B supprimé**.
- A = 10 ha, B = 8 ha, intersection 1 ha → 12,5 % → **A et B conservés**.

---

## 5. Tests de validation

Dans **Extensions ▸ Console Python** de QGIS :

```python
exec(open(r"<chemin>/smart_polygon_cleaner/tests/test_engine.py").read())
```

Les tests couvrent : doublon exact, chevauchement > seuil, chevauchement < seuil,
et performance sur 2 500 polygones.

---

## 6. Notes techniques

- **Surface** : calculée avec `QgsDistanceArea` (ellipsoïde du projet, repli WGS84),
  ce qui corrige le piège de `$area` sur les CRS géographiques (degrés²). Pour des
  surfaces exactes, travailler de préférence dans un CRS **projeté métrique**
  (UTM, Lambert…).
- **Performance** : la détection n'utilise pas de matrice dense O(n²) mais une
  recherche de voisinage par index spatial (R-tree), ce qui rend le traitement
  scalable à 100 000+ entités.
- **Non destructif** : la couche source n'est jamais modifiée ; une nouvelle
  couche est produite.

---

## 7. Améliorations futures recommandées

- Intégration comme *Processing Provider* (utilisable dans le Modeleur / batch).
- Découpe (clip) plutôt que suppression, pour ne retirer que la zone de recouvrement.
- Paramétrage fin de la détection des slivers (indice de finesse, surface max).
- Règles de priorité personnalisables par champ attributaire (ex. date de levé).
- Export du rapport vers un *Layout* QGIS imprimable avec carte de localisation.
- Traductions (Qt Linguist) FR/EN/ES/PT.

---

## 8. Licence

GPLv3 — cohérent avec l'écosystème QGIS.

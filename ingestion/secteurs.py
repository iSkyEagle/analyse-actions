"""
secteurs.py — Taxonomie des secteurs et des industries (phase 3a).

Onze secteurs, sur le modèle de la classification GICS que la presse financière
utilise, et une soixantaine d'industries. Le point de départ est le code SIC que la SEC
attribue à chaque déposant : gratuit, présent pour toutes les sociétés, mais vieux de
1987. Il ne connaît ni internet, ni les paiements électroniques, ni la biotechnologie
moderne. D'où deux mécanismes :

1. Des PLAGES de codes SIC. Un code tombe dans la plage la plus étroite qui le contient :
   l'ordre de la liste ne compte pas, et un code nouveau tombe toujours quelque part.
2. Des CORRECTIONS par CIK, pour les grandes valeurs que leur code classe mal. Chacune
   porte sa raison. La liste reste courte : elle corrige, elle ne remplace pas.

Le secteur sert à comparer : la phase 3b situe chaque métrique d'une société parmi
celles de son industrie, ou de son secteur quand l'industrie compte trop peu de membres.
Une marge brute de 25 % est mauvaise pour un éditeur de logiciels et bonne pour un
distributeur (mémoire, règle 1).

Ce fichier est la seule source de la taxonomie. Pour la modifier, on modifie ce fichier ;
le passage hebdomadaire complet réapplique la classification à toutes les sociétés.
"""

from __future__ import annotations

from typing import Optional

TECH = "Technologie"
COMM = "Communication"
CYCL = "Consommation cyclique"
BASE = "Consommation de base"
SANTE = "Santé"
FIN = "Finance"
IMMO = "Immobilier"
INDU = "Industrie"
MAT = "Matériaux"
NRJ = "Énergie"
UTIL = "Services aux collectivités"
AUTRE = "Non classé"

SECTEURS = (TECH, COMM, CYCL, BASE, SANTE, FIN, IMMO, INDU, MAT, NRJ, UTIL)

QUANTIQUE = "Informatique quantique"     # industrie sans code SIC : corrections seules

# (premier code, dernier code, secteur, industrie). Plages inclusives.
PLAGES = [
    # --- 01-09 : agriculture, forêt, pêche --------------------------------------------
    (100, 999, BASE, "Agriculture et pêche"),
    # --- 10-14 : mines ----------------------------------------------------------------
    (1000, 1099, MAT, "Métaux et mines"),
    (1040, 1044, MAT, "Or et métaux précieux"),
    (1200, 1299, NRJ, "Charbon"),
    (1300, 1399, NRJ, "Exploration et production de pétrole et de gaz"),
    (1380, 1389, NRJ, "Équipements et services pétroliers"),
    (1400, 1499, MAT, "Matériaux de construction"),
    # --- 15-17 : construction ---------------------------------------------------------
    (1500, 1799, INDU, "Construction et ingénierie"),
    (1520, 1531, CYCL, "Construction de logements"),
    # --- 20-39 : industrie manufacturière ---------------------------------------------
    (2000, 2099, BASE, "Agroalimentaire"),
    (2080, 2086, BASE, "Boissons"),
    (2100, 2199, BASE, "Tabac"),
    (2200, 2399, CYCL, "Textile, habillement et chaussures"),
    (2400, 2499, MAT, "Bois et papier"),
    (2450, 2452, CYCL, "Construction de logements"),
    (2500, 2599, CYCL, "Équipement de la maison"),
    (2600, 2699, MAT, "Emballage"),
    (2600, 2629, MAT, "Bois et papier"),
    (2700, 2799, COMM, "Médias et édition"),
    (2800, 2899, MAT, "Chimie"),
    (2830, 2836, SANTE, "Pharmacie"),
    (2835, 2835, SANTE, "Diagnostic"),
    (2836, 2836, SANTE, "Biotechnologie"),
    (2840, 2844, BASE, "Produits ménagers et de soin"),
    (2870, 2879, MAT, "Engrais et chimie agricole"),
    (2900, 2999, NRJ, "Pétrole intégré et raffinage"),
    (3000, 3099, MAT, "Emballage"),
    (3010, 3011, CYCL, "Équipementiers automobiles"),
    (3020, 3021, CYCL, "Textile, habillement et chaussures"),
    (3100, 3199, CYCL, "Textile, habillement et chaussures"),
    (3200, 3299, MAT, "Matériaux de construction"),
    (3300, 3399, MAT, "Métaux et mines"),
    (3310, 3317, MAT, "Sidérurgie"),
    (3400, 3499, INDU, "Machines et équipements"),
    (3410, 3412, MAT, "Emballage"),
    (3480, 3489, INDU, "Aéronautique et défense"),
    (3500, 3599, INDU, "Machines et équipements"),
    (3570, 3579, TECH, "Matériel informatique"),
    (3576, 3576, TECH, "Équipements de communication"),
    (3600, 3699, INDU, "Équipements électriques"),
    (3630, 3639, CYCL, "Équipement de la maison"),
    (3650, 3652, CYCL, "Électronique grand public"),
    (3660, 3669, TECH, "Équipements de communication"),
    (3670, 3679, TECH, "Composants électroniques"),
    (3674, 3674, TECH, "Semi-conducteurs"),
    (3700, 3799, INDU, "Machines et équipements"),
    (3710, 3716, CYCL, "Automobile"),
    (3714, 3714, CYCL, "Équipementiers automobiles"),
    (3716, 3716, CYCL, "Loisirs et biens divers"),
    (3720, 3729, INDU, "Aéronautique et défense"),
    (3732, 3732, CYCL, "Loisirs et biens divers"),
    (3750, 3751, CYCL, "Automobile"),
    (3760, 3769, INDU, "Aéronautique et défense"),
    (3790, 3799, CYCL, "Loisirs et biens divers"),
    (3800, 3899, TECH, "Instruments électroniques"),
    (3810, 3812, INDU, "Aéronautique et défense"),
    (3826, 3826, SANTE, "Outils et services pour la recherche"),
    (3840, 3851, SANTE, "Équipements médicaux"),
    (3870, 3873, CYCL, "Loisirs et biens divers"),
    (3900, 3999, CYCL, "Loisirs et biens divers"),
    # --- 40-49 : transports, télécommunications, services aux collectivités ------------
    (4000, 4099, INDU, "Transport ferroviaire"),
    (4100, 4299, INDU, "Transport routier et logistique"),
    (4400, 4499, INDU, "Transport maritime"),
    (4500, 4599, INDU, "Transport aérien"),
    (4513, 4513, INDU, "Transport routier et logistique"),
    (4600, 4699, NRJ, "Transport et stockage d'énergie"),
    (4700, 4799, INDU, "Transport routier et logistique"),
    (4800, 4899, COMM, "Télécommunications"),
    (4830, 4841, COMM, "Médias et divertissement"),
    (4900, 4999, UTIL, "Électricité"),
    (4920, 4925, UTIL, "Gaz"),
    (4922, 4923, NRJ, "Transport et stockage d'énergie"),
    (4940, 4941, UTIL, "Eau"),
    (4950, 4959, INDU, "Gestion des déchets"),
    # --- 50-51 : commerce de gros -----------------------------------------------------
    (5000, 5199, INDU, "Distribution industrielle"),
    (5040, 5049, TECH, "Distribution technologique"),
    (5047, 5047, SANTE, "Distribution de santé"),
    (5060, 5065, TECH, "Distribution technologique"),
    (5120, 5122, SANTE, "Distribution de santé"),
    (5140, 5149, BASE, "Distribution alimentaire"),
    (5170, 5172, NRJ, "Pétrole intégré et raffinage"),
    # --- 52-59 : commerce de détail ---------------------------------------------------
    (5200, 5299, CYCL, "Distribution spécialisée"),
    (5300, 5399, BASE, "Distribution généraliste"),
    (5311, 5311, CYCL, "Grands magasins"),
    (5400, 5499, BASE, "Distribution alimentaire"),
    (5500, 5599, CYCL, "Distribution automobile"),
    (5600, 5799, CYCL, "Distribution spécialisée"),
    (5800, 5899, CYCL, "Restauration"),
    (5900, 5999, CYCL, "Distribution spécialisée"),
    (5912, 5912, BASE, "Pharmacies"),
    (5960, 5961, CYCL, "Commerce en ligne"),
    # --- 60-67 : finance, assurance, immobilier ----------------------------------------
    (6000, 6099, FIN, "Banques"),
    (6100, 6199, FIN, "Crédit et services financiers"),
    (6200, 6299, FIN, "Courtage et marchés de capitaux"),
    (6280, 6289, FIN, "Gestion d'actifs"),
    (6300, 6399, FIN, "Assurance dommages"),
    (6310, 6321, FIN, "Assurance vie et prévoyance"),
    (6324, 6324, SANTE, "Gestion des soins"),
    (6400, 6499, FIN, "Courtage d'assurance"),
    (6500, 6599, IMMO, "Exploitation immobilière"),
    (6530, 6531, IMMO, "Services immobiliers"),
    (6550, 6552, IMMO, "Promotion immobilière"),
    (6700, 6799, FIN, "Sociétés d'investissement"),
    (6792, 6792, NRJ, "Redevances pétrolières et foncières"),
    (6794, 6794, TECH, "Licences et brevets"),
    (6795, 6795, MAT, "Redevances minières"),
    (6798, 6798, IMMO, "Foncières cotées"),
    # --- 70-89 : services --------------------------------------------------------------
    (7000, 7099, CYCL, "Hôtellerie et loisirs"),
    (7200, 7299, CYCL, "Services aux particuliers"),
    (7300, 7399, INDU, "Services aux entreprises"),
    (7310, 7319, COMM, "Publicité et marketing"),
    (7350, 7359, INDU, "Location d'équipements"),
    (7360, 7363, INDU, "Services professionnels"),
    (7370, 7379, TECH, "Logiciels et services informatiques"),
    (7371, 7371, TECH, "Services informatiques"),
    (7372, 7373, TECH, "Logiciels"),
    (7374, 7374, TECH, "Traitement de données et paiements"),
    (7500, 7599, INDU, "Transport routier et logistique"),
    (7800, 7899, COMM, "Médias et divertissement"),
    (7900, 7999, CYCL, "Hôtellerie et loisirs"),
    (8000, 8099, SANTE, "Services de santé"),
    (8200, 8299, CYCL, "Éducation"),
    (8100, 8199, INDU, "Services professionnels"),
    (8300, 8699, INDU, "Services professionnels"),
    (8700, 8799, INDU, "Services professionnels"),
    (8710, 8711, INDU, "Construction et ingénierie"),
    (8730, 8731, SANTE, "Biotechnologie"),
    (8734, 8734, SANTE, "Outils et services pour la recherche"),
    (8900, 8999, INDU, "Services professionnels"),
]

# Corrections par CIK : (secteur, industrie, raison). Vérifiées sur les dépôts EDGAR.
CORRECTIONS = {
    1652044: (COMM, "Médias interactifs", "Alphabet : SIC 7370, services informatiques ; "
              "son activité est la publicité en ligne et les plateformes"),
    1326801: (COMM, "Médias interactifs", "Meta : SIC 7370, services informatiques ; "
              "son activité est la publicité en ligne et les réseaux sociaux"),
    1403161: (FIN, "Services de paiement", "Visa : SIC 7389, services aux entreprises"),
    1141391: (FIN, "Services de paiement", "Mastercard : SIC 7389, services aux entreprises"),
    1633917: (FIN, "Services de paiement", "PayPal : SIC 7389, services aux entreprises"),
    1075531: (CYCL, "Hôtellerie et loisirs", "Booking : SIC 4700, services de transport ; "
              "son activité est la réservation d'hébergements"),
    97745: (SANTE, "Outils et services pour la recherche", "Thermo Fisher : SIC 3829, "
            "instruments de mesure ; ses clients sont les laboratoires et la pharmacie"),
    313616: (SANTE, "Outils et services pour la recherche", "Danaher : SIC 3823, "
             "instruments industriels ; son activité est désormais les sciences de la vie"),
    1163165: (NRJ, "Exploration et production de pétrole et de gaz", "ConocoPhillips : SIC "
              "2911, raffinage ; elle a cédé son raffinage en 2012"),
    1744489: (COMM, "Médias et divertissement", "Disney : SIC 7990, parcs d'attractions ; "
              "son activité principale est le divertissement"),
    1121788: (CYCL, "Électronique grand public", "Garmin : SIC 3812, navigation ; "
              "l'essentiel de ses ventes va au grand public"),
    1543151: (INDU, "Transport routier et logistique", "Uber : SIC 7389, services aux "
              "entreprises ; son activité est le transport de personnes et la livraison"),
    1138118: (IMMO, "Services immobiliers", "CBRE : SIC 6500 ; un prestataire de "
              "services immobiliers, pas un propriétaire"),
    2012383: (FIN, "Gestion d'actifs", "BlackRock : SIC 6211, courtage ; "
              "son activité est la gestion d'actifs"),
    804328: (TECH, "Semi-conducteurs", "Qualcomm : SIC 3663, équipements radio ; "
             "ses revenus viennent des puces et des licences"),
    40545: (INDU, "Aéronautique et défense", "GE Aerospace : SIC 3600, équipements "
            "électriques ; ne reste que les moteurs d'avion depuis 2024"),
    51143: (TECH, "Services informatiques", "IBM : SIC 3570, matériel ; l'essentiel de "
            "ses revenus vient du logiciel et du conseil"),
    # Informatique quantique (décidé le 13 octobre 2026) : machines vendues, accès par le
    # cloud et contrats publics, en forte perte et en forte croissance. Ni un éditeur de
    # logiciels à 75 % de marge brute, ni un fabricant de PC mûr. Moins de 15 sociétés :
    # les comparaisons se font sur tout le secteur technologie, le repère le plus honnête.
    1824920: (TECH, QUANTIQUE, "IonQ : codé en logiciels ; fabrique des ordinateurs "
              "quantiques à ions piégés"),
    1907982: (TECH, QUANTIQUE, "D-Wave Quantum : codé en traitement de données ; "
              "fabrique des ordinateurs quantiques à recuit"),
    1838359: (TECH, QUANTIQUE, "Rigetti Computing : fabrique des ordinateurs quantiques "
              "supraconducteurs"),
    1758009: (TECH, QUANTIQUE, "Quantum Computing Inc. : machines quantiques photoniques"),
}


def _plage(sic: int):
    """La plage la plus étroite qui contient le code."""
    candidates = [p for p in PLAGES if p[0] <= sic <= p[1]]
    return min(candidates, key=lambda p: p[1] - p[0]) if candidates else None


def classer(sic, cik: Optional[int] = None, profil: Optional[str] = None) -> tuple[str, str]:
    """
    (secteur, industrie) d'une société. Sans code SIC exploitable : « Non classé ».

    `profil` : une société immobilière au profil foncière rejoint « Foncières cotées »
    quel que soit son code — Invitation Homes dépose sous 6510, Gladstone sous 6519. Les
    foncières doivent se comparer entre elles : leur mesure de référence est le FFO.
    """
    if cik is not None and cik in CORRECTIONS:
        s, i, _ = CORRECTIONS[cik]
        return s, i
    try:
        n = int(sic)
    except (TypeError, ValueError):
        return AUTRE, AUTRE
    p = _plage(n)
    if not p:
        return AUTRE, AUTRE
    if p[2] == IMMO and profil == "reit":
        return IMMO, "Foncières cotées"
    return p[2], p[3]


def industries() -> list[tuple[str, str]]:
    """Toutes les paires (secteur, industrie) possibles, pour la documentation et les tests."""
    vues = {(p[2], p[3]) for p in PLAGES} | {(s, i) for s, i, _ in CORRECTIONS.values()}
    return sorted(vues, key=lambda x: (SECTEURS.index(x[0]) if x[0] in SECTEURS else 99, x[1]))


if __name__ == "__main__":
    for s in SECTEURS:
        noms = [i for sec, i in industries() if sec == s]
        print(f"{s} ({len(noms)}) : {', '.join(noms)}")

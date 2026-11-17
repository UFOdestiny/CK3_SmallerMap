"""Map-selection policy. Keep version-specific IDs here, not in algorithms."""

from dataclasses import dataclass


@dataclass(frozen=True)
class MapConfig:
    delete_titles: tuple[str, ...]
    province_receivers: dict[str, int]
    capital_replacements: dict[str, str]
    holy_site_replacements: dict[str, str]
    faith_fallbacks: dict[str, str]


DEFAULT_CONFIG = MapConfig(
    delete_titles=(
        "e_azania", "e_deccan", "e_guinea", "e_mali", "e_kanem_bornu",
        "e_abyssinia", "e_siberia", "e_srivijaya", "e_brunei", "e_majapahit",
        "k_lusung", "k_kabisay-an", "k_tanjungnagara", "k_sulawesi", "k_maluku",
        "k_permia", "k_sahara", "k_angara", "k_anbiya", "k_bjarmaland",
        "k_buryatia", "k_khakassia", "k_orissa", "d_bahitawi",
        "d_laamp_shunkan", "d_laamp_sirafi_mariners", "d_laamp_leaf_hermits",
        "d_laamp_lembongs_band",
    ),
    # Existing impassable province IDs from default.map. These rules only color
    # already-selected deletion regions; they never select additional titles.
    province_receivers={
        **dict.fromkeys(("e_srivijaya", "e_brunei", "k_lusung", "k_kabisay-an",
                        "k_tanjungnagara", "k_sulawesi", "k_maluku"), 11215),
        **dict.fromkeys(("e_siberia", "k_permia", "k_angara", "k_bjarmaland",
                        "k_buryatia", "k_khakassia"), 1464),
    },
    # County IDs, applied only to scalar capital assignments of surviving titles.
    capital_replacements={
        "c_PHI_tondo": "c_hoanya",
        "c_semien": "c_aswan",
        "c_tigre": "c_aswan",
        "c_jenne": "c_tinmallal",
        "c_kongu": "c_bost",
        "c_attie": "c_tinmallal",
        "c_toro": "c_tinmallal",
    },
    holy_site_replacements={
        "bono": "aswan", "danakil": "carthage", "awkar": "aswan",
        "daura": "aswan", "ife": "tinmallal", "kisi": "tinmallal",
    },
    # Used only when a faith otherwise has no surviving ordinary/eminent site.
    faith_fallbacks={
        "aluk": "hoanya", "dayawism": "hoanya", "kaharingan": "hoanya",
        "tolotang": "hoanya", "siberian_pagan": "novgorod", "ngaiism_pagan": "aswan",
    },
)

"""
Bread Maker Companion — Gastroback »Design Bread Maker Advanced« (Art.-No. 62823)

Run:  streamlit run breadmaker_app.py

Features
  1. Recipe picker (all recipes from the operating manual + your own)
  2. Loaf size / batch selection
  3. Step-by-step checklist with amounts in g/ml AND cups/tbsp/tsp
  4. Program timer that tracks the machine phase, with alerts (paddle removal,
     timed add-ins, finish) — in-app banner + sound, optional browser
     notification, optional phone push via ntfy.sh
  5. Custom recipe editor (create, copy a built-in, edit, delete, import/export)

Data (custom recipes, settings, phase calibration, running timer) is stored in
./breadmaker_data next to this script.
"""

import copy
import html
import io
import json
import math
import os
import struct
import threading
import time
import urllib.request
import uuid
import wave
from pathlib import Path

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

# --------------------------------------------------------------------------
# Paths & persistence
# --------------------------------------------------------------------------
DATA_DIR = Path(__file__).resolve().parent / "breadmaker_data"
DATA_DIR.mkdir(exist_ok=True)
F_CUSTOM = DATA_DIR / "custom_recipes.json"
F_SETTINGS = DATA_DIR / "settings.json"
F_CALIB = DATA_DIR / "calibration.json"
F_RUN = DATA_DIR / "active_run.json"

DEFAULT_SETTINGS = {
    "cup_ml": 240,
    "sound": True,
    "ntfy_server": "https://ntfy.sh",
    "ntfy_topic": "",
}


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return copy.deepcopy(default)


def save_json(path, obj):
    tmp = str(path) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


@st.cache_resource
def shared_lock():
    return threading.Lock()


LOCK = shared_lock()


def get_settings():
    s = copy.deepcopy(DEFAULT_SETTINGS)
    s.update(load_json(F_SETTINGS, {}))
    return s


def load_run():
    with LOCK:
        return load_json(F_RUN, None)


def update_run(fn):
    """Read-modify-write the active run under the shared lock."""
    with LOCK:
        run = load_json(F_RUN, None)
        if run is None:
            return None
        fn(run)
        save_json(F_RUN, run)
        return run


def clear_run():
    with LOCK:
        if F_RUN.exists():
            F_RUN.unlink()


# --------------------------------------------------------------------------
# Units
# --------------------------------------------------------------------------
TSP_ML, TBSP_ML = 5.0, 15.0  # as stated in the manual (measuring spoon wells)

# Approximate densities (g per ml) used for g -> cup/spoon conversion.
DENSITY = {
    "water": ("Water / thin liquid", 1.00),
    "milk": ("Milk", 1.03),
    "cream": ("Cream", 1.00),
    "condensed_milk": ("Sweetened condensed milk", 1.30),
    "oil": ("Oil", 0.92),
    "lemon_juice": ("Lemon / fruit juice", 1.03),
    "yoghurt": ("Yoghurt", 1.03),
    "salt": ("Salt (fine)", 1.20),
    "sugar": ("Sugar (granulated)", 0.85),
    "caster_sugar": ("Caster sugar", 0.90),
    "brown_sugar": ("Brown sugar (packed)", 0.90),
    "icing_sugar": ("Icing sugar", 0.50),
    "bread_flour": ("Bread flour", 0.53),
    "plain_flour": ("Plain / all-purpose flour", 0.53),
    "self_raising_flour": ("Self-raising flour", 0.53),
    "wholewheat_flour": ("Wholewheat flour", 0.51),
    "granary_flour": ("Granary / Malthouse flour", 0.48),
    "rye_flour": ("Rye flour", 0.43),
    "cornmeal": ("Corn flour (fine cornmeal)", 0.62),
    "starch": ("Starch (cornflour / cornstarch)", 0.54),
    "milk_powder": ("Milk powder", 0.29),
    "yeast": ("Dried yeast", 0.63),
    "baking_powder": ("Baking powder", 0.81),
    "baking_soda": ("Baking soda", 0.93),
    "butter": ("Butter / margarine", 0.96),
    "cocoa": ("Cocoa powder", 0.36),
    "sesame": ("Sesame seeds", 0.61),
    "walnuts": ("Walnuts, chopped", 0.50),
    "pine_nuts": ("Pine nuts", 0.57),
    "hazelnuts": ("Hazelnuts, chopped", 0.56),
    "coconut": ("Desiccated coconut", 0.36),
    "pumpkin": ("Pumpkin, mashed", 1.03),
    "mashed_potato": ("Mashed potato (packed)", 0.85),
    "banana": ("Banana, pieces / mashed", 0.95),
    "puree": ("Fruit / veg purée", 1.00),
    "fruit_chopped": ("Fruit, chopped / sliced", 0.70),
    "spice": ("Ground spice mix", 0.47),
    "choc_spread": ("Hazelnut nougat cream", 1.25),
    "chocolate": ("Chocolate, chopped", 0.72),
    "muesli": ("Muesli (unsweetened)", 0.40),
    "barley_flour": ("Barley flour", 0.48),
    "beer": ("Beer (flat)", 1.01),
    "mustard": ("Wholegrain mustard", 1.05),
    "buckwheat_flour": ("Buckwheat flour", 0.50),
    "rolled_oats": ("Rolled oats", 0.36),
    "polenta": ("Polenta (coarse)", 0.65),
    "golden_syrup": ("Golden syrup", 1.40),
    "cooked_rice": ("Cooked rice", 0.70),
    "pumpkin_seeds": ("Pumpkin seeds", 0.55),
    "honey": ("Honey", 1.42),
    "dates": ("Dates, chopped", 0.60),
    "molasses": ("Molasses / black treacle", 1.40),
    "oat_bran": ("Oat bran", 0.40),
    "breadcrumbs": ("Dried breadcrumbs", 0.42),
    "coffee_granules": ("Instant coffee granules", 0.22),
    "caraway": ("Caraway seeds", 0.45),
}
DENS_LABELS = ["— none —"] + [v[0] for v in DENSITY.values()]
DENS_BY_LABEL = {v[0]: k for k, v in DENSITY.items()}

UNITS = ["g", "ml", "pcs", "tsp", "tbsp"]

WHERE = {
    "pan": "Bread pan (in listed order)",
    "box": "Ingredients box",
    "beep": "By hand at add-in beep",
    "box_or_beep": "Ingredients box or at beep",
    "timed": "At a set minute (alert)",
    "prep": "Preparation (outside machine)",
    "lateknead": "By hand in the last minutes of kneading (alert)",
    "refresh1": "1st refreshment (added later)",
    "refresh2": "2nd refreshment (added later)",
    "stage2": "Second stage (added to the pan later)",
    "prebake": "Brushed/sprinkled on just before baking (alert)",
    "midbake": "Brushed on halfway through baking (alert)",
    "after": "After the program (topping/finishing)",
}
WHERE_BY_LABEL = {v: k for k, v in WHERE.items()}

FRACS = {0.25: "¼", 1 / 3: "⅓", 0.5: "½", 2 / 3: "⅔", 0.75: "¾"}


def qfmt(x):
    """Number with unicode fractions where it fits (1.5 -> 1½)."""
    if x is None:
        return ""
    whole = int(math.floor(x + 1e-9))
    f = x - whole
    if f < 1e-6:
        return str(whole)
    for val, sym in FRACS.items():
        if abs(f - val) < 0.02:
            return (str(whole) if whole else "") + sym
    return f"{x:g}"


def num(x):
    return f"{x:g}" if isinstance(x, (int, float)) else str(x)


def spoon_text(ml, coarse=False):
    tbsp = int(ml // TBSP_ML)
    step = 1 if coarse else 4  # whole tsp next to cups, quarter tsp otherwise
    tsp = round((ml - tbsp * TBSP_ML) / TSP_ML * step) / step
    if tsp >= 3:
        tbsp, tsp = tbsp + 1, 0
    parts = []
    if tbsp:
        parts.append(f"{tbsp} tbsp")
    if tsp:
        parts.append(f"{qfmt(tsp)} tsp")
    return " + ".join(parts) if parts else "pinch"


def fmt_volume(ml, cup_ml):
    if ml is None or ml <= 0:
        return ""
    if ml < 0.6:
        return "pinch"
    if ml < cup_ml / 4 - 2:
        return spoon_text(ml)
    cups = ml / cup_ml
    whole = int(cups)
    f = cups - whole
    opts = [0, 0.25, 1 / 3, 0.5, 2 / 3, 0.75, 1.0]
    nxt = min(o for o in opts if o >= f - 1e-9)
    if (nxt - f) * cup_ml <= 4:  # within ~1 tsp: round up
        base = whole + nxt
        rem = 0
    else:
        fl = max(o for o in opts if o <= f + 1e-9)
        base = whole + fl
        rem = ml - base * cup_ml
    txt = f"{qfmt(base)} cup{'s' if base > 1 else ''}" if base else ""
    if rem > 1.2:
        sp = spoon_text(rem, coarse=bool(base))
        if sp != "pinch":
            txt = (txt + " + " if txt else "") + sp
    return txt


def amt_at(ing, idx):
    a = ing["amt"]
    if isinstance(a, list):
        return a[idx] if idx < len(a) else a[-1]
    return a


def amount_texts(ing, idx, cup_ml):
    """-> (metric text, cups/spoons text)"""
    a = amt_at(ing, idx)
    if a is None or a == "":
        return "", ""
    unit = ing["unit"]
    d = DENSITY.get(ing.get("d") or "", (None, None))[1]
    if unit == "g":
        return f"{num(a)} g", fmt_volume(a / d, cup_ml) if d else "—"
    if unit == "ml":
        grams = f" (≈{a * d:.0f} g)" if d and abs(d - 1) > 0.04 else ""
        return f"{num(a)} ml{grams}", fmt_volume(a, cup_ml)
    if unit in ("tsp", "tbsp"):
        ml = a * (TSP_ML if unit == "tsp" else TBSP_ML)
        g = f" ≈ {ml * d:.0f} g" if d else ""
        return f"{ml:g} ml{g}", f"{qfmt(a)} {unit}"
    return qfmt(a), ""


def est_grams(ing, idx):
    a = amt_at(ing, idx)
    if not isinstance(a, (int, float)):
        return 0
    d = DENSITY.get(ing.get("d") or "", (None, 1.0))[1] or 1.0
    u = ing["unit"]
    if u == "g":
        return a
    if u == "ml":
        return a * d
    if u == "tsp":
        return a * TSP_ML * d
    if u == "tbsp":
        return a * TBSP_ML * d
    n = ing["name"].lower()
    if "yolk" in n:
        return a * 18
    if "white" in n:
        return a * 30
    if "egg" in n:
        return a * 50
    return 0


# --------------------------------------------------------------------------
# Machine programs (from the manual)
# --------------------------------------------------------------------------
PROGRAMS = {
    1: "Basic bread", 2: "French bread", 3: "Whole wheat", 4: "Sweet bread",
    5: "Rice bread", 6: "Gluten free", 7: "Quick bread", 8: "Fruit bread",
    9: "Cake", 10: "Jam", 11: "Defrost", 12: "Mix", 13: "Knead", 14: "Dough",
    15: "Ice cream", 16: "Bake", 17: "Homemade", 18: "Yoghurt",
}
BREAD_PROGS = {1, 2, 3, 4, 5, 6, 7, 8, 17}       # baking with keep-warm afterwards
LOADED_PROGS = BREAD_PROGS | {9, 12, 13, 14}      # ingredients loaded into pan in order
WEIGHT_PROGS = {1, 2, 3, 4, 5, 6, 7, 8}           # WEIGHT button active (not 9)
CRUST_PROGS = {1, 2, 3, 4, 5, 6, 7, 8, 9}
BLADE_DEFAULT = {1, 2, 3, 4, 5, 8, 17}           # paddle-removal alert on by default
SIZES3 = ["1000 g", "750 g", "500 g"]
SINGLE = ["Single batch"]

# Homemade (17) phase defaults, manual p.22 (minutes)
HOMEMADE_DEFAULT = [["Knead 1", 15], ["Rest 1", 3], ["Knead 2", 20], ["Rest 2", 10],
                    ["Knead 3", 0], ["Ferment 1", 42], ["Ferment 2", 40],
                    ["Ferment 3", 0], ["Bake", 30]]
# Shape templates for programs whose phase times the manual does not publish.
# They are scaled to the total time the LCD shows, so only the proportions matter.
TEMPLATES = {
    "bread": HOMEMADE_DEFAULT,
    "one_rise": [["Knead 1", 15], ["Rest 1", 3], ["Knead 2", 20], ["Ferment 1", 42], ["Bake", 30]],
    "dough": [["Knead 1", 15], ["Rest 1", 3], ["Knead 2", 20], ["Rest 2", 10], ["Ferment 1", 42]],
    "jam": [["Heat & stir", 60]],
    "mix": [["Mix", 20]],
}
PROG_TEMPLATE = {1: "bread", 2: "bread", 3: "bread", 4: "bread", 5: "bread", 7: "bread",
                 8: "bread", 6: "one_rise", 9: "one_rise", 10: "jam", 12: "mix", 14: "dough"}
# Programs where the user sets the processing time with +/-TIME
USER_TIME = {
    11: ("Defrost", list(range(10, 125, 5)), 30),
    13: ("Knead", list(range(5, 65, 5)), 20),
    15: ("Stirring / freezing", [20, 25, 30], 25),
    16: ("Bake", list(range(10, 95, 5)), 30),
    18: ("Fermenting", [480 + 30 * i for i in range(9)], 480),
}
KEEP_WARM_MIN = 60


def phase_profile(p):
    """Calibrated or template phase list [[name, minutes], ...] for program p."""
    calib = load_json(F_CALIB, {"profiles": {}, "lcd": {}})
    prof = calib.get("profiles", {}).get(str(p))
    if prof:
        return prof
    if p == 17:
        return copy.deepcopy(HOMEMADE_DEFAULT)
    return copy.deepcopy(TEMPLATES[PROG_TEMPLATE[p]])


def scale_profile(profile, total_min):
    prof = [[n, m] for n, m in profile if m and m > 0]
    s = sum(m for _, m in prof)
    out, acc = [], 0
    target = int(round(total_min * 60))
    for i, (n, m) in enumerate(prof):
        sec = target - acc if i == len(prof) - 1 else int(round(m / s * target))
        out.append({"name": n, "sec": sec})
        acc += sec
    return out


# --------------------------------------------------------------------------
# Built-in recipes (transcribed from the manual, pp. 46-65)
# --------------------------------------------------------------------------
def I(name, unit, amt, d=None, where="pan", note="", at_min=None):
    return {"name": name, "unit": unit, "amt": amt, "d": d, "where": where,
            "note": note, "at_min": at_min}


def R(name, cat, program, sizes, ings, prep=None, post=None, notes="", blade=None,
      time_default=None, events=None, variants=None, stage2=None, stages=None, stage1_steps=None,
      batches=None):
    d = {"name": name, "category": cat, "program": program, "sizes": sizes,
         "ingredients": ings, "prep": prep or [], "post": post or [], "notes": notes,
         "blade": blade, "time_default": time_default, "events": events or [],
         "builtin": True}
    if variants:
        d["variants"] = variants
    if stage2:
        d["stage2"] = stage2
    if stages:
        d["stages"] = stages
    if stage1_steps:
        d["stage1_steps"] = stage1_steps
    if batches:
        d["batches"] = batches
    return d


FREEZE_BUCKET = ("Freeze the ice bucket (accessory) upright for up to 16 h at ≤ −18 °C. "
                 "It is ready when it no longer gurgles when shaken.")
CUSTARD = ["Whisk the egg yolks and sugar until creamy.",
           "Return the mixture to a clean pan; stir constantly with a wooden spoon on low heat "
           "until it thickens and coats the back of the spoon.",
           "Let it cool, then refrigerate to +5 °C."]

BUILTIN = [
    # ---------------- Bread ----------------
    R("Basic white bread", "Bread", 1, SIZES3, [
        I("Water", "ml", [320, 250, 160], "water"),
        I("Salt", "g", [6, 5, 3], "salt"),
        I("Oil (refined)", "ml", [24, 18, 12], "oil"),
        I("Sugar", "g", [24, 18, 12], "sugar"),
        I("Milk powder", "g", [15, 12, 6], "milk_powder"),
        I("Bread flour", "g", [520, 400, 260], "bread_flour"),
        I("Yeast", "g", [6, 4, 3], "yeast"),
    ]),
    R("Potato bread", "Bread", 1, SIZES3, [
        I("Potato cooking water, at room temperature (top up with tap water)", "ml", [240, 200, 135], "water"),
        I("Sunflower oil", "ml", [30, 30, 20], "oil"),
        I("Salt", "ml", [7.5, 5, 3.75], "salt"),
        I("Granulated sugar", "ml", [10, 7.5, 5], "sugar"),
        I("Unbleached white bread flour", "g", [500, 375, 250], "bread_flour"),
        I("Cold mashed potato (plain)", "g", [175, 125, 85], "mashed_potato"),
        I("Skimmed milk powder", "ml", [22, 15, 10], "milk_powder"),
        I("Easy bake (rapid-rise) dried yeast", "ml", [7.5, 5, 3.75], "yeast"),
        I("Milk, for glazing", "pcs", None, where="midbake"),
    ], prep=["Peel and boil the potatoes (≈ 200 g raw for 1000 g, 175 g for 750 g, 120 g for 500 g) without salt. "
             "Keep the cooking water.",
             "Mash the potatoes without milk or butter and let the potatoes and the water cool to room temperature."],
       notes="Adapted from a bread machine cookbook (Basic breads, p. 79). The book's Medium (≈ 970 g) is used "
             "for 1000 g and its Small (≈ 750 g) for 750 g; its Large (≈ 1.17 kg) is over the 1000 g limit, so the "
             "500 g loaf is the Small scaled by ⅔. Program 1 Basic, medium or light crust. Salt and sugar go in "
             "with the liquids (manual's order). If your mash already has milk and butter in it, use a little less "
             "water. The app alerts you halfway through the bake to brush on the milk."),
    R("Light rye and caraway bread", "Bread", 1, SIZES3, [
        I("Water", "ml", [300, 210, 170], "water"),
        I("Lemon juice", "ml", [10, 5, 3.75], "lemon_juice"),
        I("Sunflower oil", "ml", [22, 15, 12.5], "oil"),
        I("Salt", "ml", [7.5, 5, 3.75], "salt"),
        I("Light muscovado (brown) sugar", "ml", [15, 10, 7.5], "brown_sugar"),
        I("Rye flour", "g", [125, 85, 70], "rye_flour"),
        I("Unbleached white bread flour", "g", [375, 285, 230], "bread_flour"),
        I("Skimmed milk powder", "ml", [22, 15, 12.5], "milk_powder"),
        I("Caraway seeds", "ml", [7.5, 5, 3.75], "caraway"),
        I("Easy bake (rapid-rise) dried yeast", "ml", [5, 3.75, 2.5], "yeast"),
    ], notes="Adapted from a bread machine cookbook (Basic breads, p. 75). The book's Medium (≈ 865 g) is used "
             "for 1000 g and its Small (≈ 625 g) for 750 g; its Large (≈ 1.02 kg) is just over the 1000 g limit, "
             "so the 500 g loaf is the Small scaled by 0.8. Program 1 Basic, medium crust, as the book says. Salt "
             "and sugar go in with the liquids (manual's order); milk powder and caraway on top of the flour."),
    R("Grainy mustard and beer loaf", "Bread", 1, SIZES3, [
        I("Flat beer", "ml", [280, 210, 160], "beer"),
        I("Vegetable oil", "ml", [15, 15, 11.25], "oil"),
        I("Wholegrain mustard", "ml", [45, 30, 22.5], "mustard"),
        I("Salt", "ml", [7.5, 5, 3.75], "salt"),
        I("Granulated sugar", "ml", [10, 7.5, 6.25], "sugar"),
        I("Unbleached white bread flour", "g", [350, 250, 195], "bread_flour"),
        I("Wholemeal (whole-wheat) bread flour", "g", [150, 125, 95], "wholewheat_flour"),
        I("Skimmed milk powder", "ml", [22, 15, 11.25], "milk_powder"),
        I("Easy bake (rapid-rise) dried yeast", "ml", [5, 5, 3.75], "yeast"),
    ], prep=["Open the beer at least 1 hour before you start so it goes flat. Pale ale gives a subtler taste, "
             "brown ale a stronger one."],
       notes="Adapted from a bread machine cookbook (Savoury breads, p. 134). The book's Medium (≈ 870 g) is used "
             "for 1000 g and its Small (≈ 650 g) for 750 g; its Large (≈ 1.04 kg) is over the 1000 g limit, so the "
             "500 g loaf is the Small scaled by 0.77. Program 1 Basic, medium crust, as the book says. Mustard, salt "
             "and sugar go in with the beer and oil (manual's order); milk powder on top of the flour. Great with "
             "cheese and pickles."),
    R("Sesame bread", "Bread", 1, SIZES3, [
        I("Water", "ml", [320, 250, 160], "water"),
        I("Salt", "g", [8, 6, 4], "salt"),
        I("Oil (refined)", "ml", [36, 24, 18], "oil"),
        I("Sugar", "g", [24, 18, 12], "sugar"),
        I("Milk powder", "g", [24, 18, 12], "milk_powder"),
        I("Bread flour", "g", [520, 400, 260], "bread_flour"),
        I("Yeast", "g", [6, 4, 3], "yeast"),
        I("Sesame seeds", "g", [30, 22, 15], "sesame", "box_or_beep"),
    ]),
    R("Walnut or pine-nut bread", "Bread", 1, SIZES3, [
        I("Water", "ml", [280, 230, 140], "water"),
        I("Egg", "pcs", [2, 1, 1]),
        I("Salt", "g", [6, 5, 3], "salt"),
        I("Oil (refined)", "ml", [36, 24, 18], "oil"),
        I("Sugar", "g", [24, 18, 12], "sugar"),
        I("Milk powder", "g", [24, 18, 12], "milk_powder"),
        I("Bread flour", "g", [500, 400, 250], "bread_flour"),
        I("Yeast", "g", [6, 4, 3], "yeast"),
        I("Walnuts or pine nuts, coarsely chopped", "g", [60, 45, 30], "walnuts", "box_or_beep"),
    ], notes="Contains egg — don't use the machine's delay timer."),
    R("Corn meal bread", "Bread", 1, SIZES3, [
        I("Warm water", "ml", [220, 185, 115], "water"),
        I("Lukewarm milk", "ml", [135, 80, 65], "milk"),
        I("Corn oil", "ml", [30, 20, 10], "oil"),
        I("Salt", "ml", [7.5, 5, 3.75], "salt"),
        I("Light muscovado (brown) sugar", "ml", [10, 8.75, 5], "brown_sugar"),
        I("Unbleached white bread flour", "g", [390, 310, 210], "bread_flour"),
        I("Fine corn meal (maize meal)", "g", [195, 130, 75], "cornmeal"),
        I("Easy bake (rapid-rise) dried yeast", "ml", [7.5, 5, 3.75], "yeast"),
        I("Brush the top with water", "pcs", None, where="prebake"),
        I("Sprinkle with coarse polenta", "pcs", None, where="prebake"),
    ], notes="Adapted from a bread machine cookbook (Basic breads, p. 70). The book's loaves weigh ≈ 640 / 840 / "
             "1020 g, so each machine size uses the nearest book size, scaled: 1000 g = Large × 0.97 (≈ 990 g, "
             "just under the limit), 750 g = Medium × 0.88 (≈ 745 g), 500 g = Small × 0.77 (≈ 490 g). The book "
             "prefers a rapid program. This machine's only fast program, 7 Quick bread, is described in the manual "
             "mainly for baking-powder breads and gives a small, dense loaf, so program 1 Basic is the default and "
             "Quick bread is offered under Method. On Quick bread, if the LCD shows less than 1:50, use warm milk "
             "and water as the book advises. Medium crust. Expect a slightly shallow, crumbly loaf. Salt and sugar "
             "go in with the liquids (manual's order).",
       variants={
           "Basic program": {},
           "Quick bread program (book's rapid)": {"program": 7, "blade": False},
       }),
    R("Classic French bread", "Bread", 2, SIZES3, [
        I("Water", "ml", [340, 250, 170], "water"),
        I("Salt", "g", [8, 6, 4], "salt"),
        I("Oil (refined)", "ml", [12, 9, 6], "oil"),
        I("Sugar", "g", [16, 12, 8], "sugar"),
        I("Bread flour", "g", [540, 400, 270], "bread_flour"),
        I("Yeast", "g", [6, 5, 3], "yeast"),
    ]),
    R("Crispy French bread", "Bread", 2, SIZES3, [
        I("Water", "ml", [350, 300, 205], "water"),
        I("Salt", "ml", [7.5, 5, 3.75], "salt"),
        I("Sunflower or other refined oil (instead of the book's butter)", "ml", [12.5, 12.5, 7.5], "oil"),
        I("Unbleached white bread flour", "g", [500, 400, 275], "bread_flour"),
        I("Easy bake (rapid-rise) dried yeast", "ml", [6.25, 5, 3.75], "yeast"),
    ], notes="Adapted from a bread machine cookbook (Basic breads, p. 78). The book's Medium (≈ 880 g) is used "
             "for 1000 g and its Small (≈ 725 g) for 750 g; its Large (≈ 1.06 kg) is over the 1000 g limit, so the "
             "500 g loaf is the Small scaled by 0.69. The manual says program 2 French bread is not suitable for "
             "recipes with butter, margarine or milk, so the book's butter is swapped for a similar amount of fat "
             "as oil (the manual's own French bread uses oil). To use butter as the book does, choose the Basic "
             "program under Method — the crust will be a little softer. Medium crust. Salt and fat go in with the "
             "water (manual's order).",
       variants={
           "French program, oil (manual)": {},
           "Basic program, butter (book)": {
               "program": 1,
               "ingredients": [
                   I("Water", "ml", [350, 300, 205], "water"),
                   I("Salt", "ml", [7.5, 5, 3.75], "salt"),
                   I("Butter", "g", [15, 15, 10], "butter", note="small pieces"),
                   I("Unbleached white bread flour", "g", [500, 400, 275], "bread_flour"),
                   I("Easy bake (rapid-rise) dried yeast", "ml", [6.25, 5, 3.75], "yeast"),
               ],
           },
       }),
    R("Corn bread", "Bread", 2, SIZES3, [
        I("Water", "ml", [320, 240, 160], "water"),
        I("Salt", "g", [6, 5, 3], "salt"),
        I("Oil (refined)", "ml", [24, 18, 12], "oil"),
        I("Sugar", "g", [16, 12, 8], "sugar"),
        I("Milk powder", "g", [16, 12, 8], "milk_powder"),
        I("Bread flour", "g", [400, 300, 200], "bread_flour"),
        I("Corn flour", "g", [120, 100, 60], "cornmeal",
          note="manual lists it after the yeast; added with the flour here so the yeast stays on top"),
        I("Yeast", "g", [6, 4, 3], "yeast"),
    ]),
    R("Soda bread", "Bread", 7, SIZES3, [
        I("Milk + 1 egg (beaten together, total volume)", "ml", [305, 250, 160], "milk"),
        I("Salt", "g", [5, 4, 3], "salt"),
        I("Butter", "g", [45, 36, 24], "butter", note="melted and cooled, or small pieces"),
        I("Sugar", "g", [95, 75, 50], "sugar"),
        I("Corn flour", "g", [170, 130, 90], "cornmeal"),
        I("All-purpose flour", "g", [340, 260, 180], "plain_flour"),
        I("Baking powder", "g", [10, 7, 5], "baking_powder"),
        I("Baking soda", "g", [10, 7, 5], "baking_soda"),
    ], notes="Quick breads: liquids at the bottom, dry on top. If flour gathers in the corners "
             "during the first mix, help it in with a rubber spatula. Contains egg/milk — no delay timer."),
    R("Whole wheat bread", "Bread", 3, SIZES3, [
        I("Water", "ml", [340, 250, 170], "water"),
        I("Salt", "g", [10, 8, 5], "salt"),
        I("Oil (refined)", "ml", [24, 18, 12], "oil"),
        I("Milk powder", "g", [18, 13, 9], "milk_powder"),
        I("Brown sugar", "g", [24, 18, 12], "brown_sugar"),
        I("Bread flour", "g", [200, 150, 100], "bread_flour"),
        I("Whole wheat flour", "g", [360, 250, 180], "wholewheat_flour"),
        I("Yeast", "g", [8, 5, 4], "yeast"),
    ]),
    R("Russian black bread", "Bread", 3, SIZES3, [
        I("Water", "ml", [330, 245, 165], "water"),
        I("Sunflower oil", "tbsp", [2, 1.5, 1], "oil"),
        I("Molasses (black treacle)", "ml", [35, 25, 20], "molasses"),
        I("Salt", "tsp", [1.25, 1, 0.75], "salt"),
        I("Rye flour", "g", [140, 105, 70], "rye_flour"),
        I("Wholemeal bread flour", "g", [80, 60, 40], "wholewheat_flour"),
        I("Unbleached white bread flour", "g", [230, 170, 115], "bread_flour"),
        I("Oat bran", "g", [35, 25, 20], "oat_bran"),
        I("Dried breadcrumbs", "g", [70, 50, 35], "breadcrumbs"),
        I("Cocoa powder (unsweetened)", "tbsp", [1.5, 1, 0.5], "cocoa"),
        I("Instant coffee granules", "tbsp", [2, 1.5, 1], "coffee_granules"),
        I("Caraway seeds", "tsp", [1.25, 1, 0.75], "caraway"),
        I("Easy bake (rapid-rise) dried yeast", "tsp", [1.25, 1, 0.75], "yeast"),
    ], notes="Adapted from a bread machine cookbook (Speciality grains, p. 96). The book's Medium (≈ 1.09 kg) "
             "and Large (≈ 1.25 kg) are over this machine's 1000 g limit, so all three sizes are scaled from the "
             "book's Medium to ≈ 990 / 735 / 500 g. The 750 g version comes out almost identical to the book's "
             "Small (≈ 736 g). Program 3 Whole wheat and medium crust, as the book says. Salt goes in with the "
             "liquids (manual's order) instead of on top of the flour. A dense, heavy loaf is normal for rye."),
    R("Granary bread", "Bread", 3, SIZES3, [
        I("Water", "ml", [325, 240, 180], "water"),
        I("Salt", "ml", [7.5, 5, 3.75], "salt"),
        I("Granulated sugar", "ml", [15, 10, 7.5], "sugar"),
        I("Butter", "g", [25, 20, 15], "butter", note="small pieces"),
        I("Granary (or Malthouse) bread flour", "g", [500, 375, 280], "granary_flour"),
        I("Easy bake (rapid-rise) dried yeast", "ml", [7.5, 5, 3.75], "yeast"),
    ], notes="Adapted from a bread machine cookbook (Basic breads, p. 73). The book's Medium (≈ 875 g) is used "
             "for 1000 g and its Small (≈ 655 g) for 750 g; its Large (≈ 1.05 kg) is over the 1000 g limit, so the "
             "500 g loaf is the Small scaled by 0.75. Program 3 Whole wheat, medium crust, as the book says. "
             "Malthouse flour works instead of Granary. If the loaf collapses slightly before baking, next time add "
             "2–3 tsp (10–15 ml) lemon juice with the water, or a crushed 100 mg vitamin C tablet. Salt, sugar "
             "and butter go in with the water (manual's order)."),
    R("Light wholemeal bread", "Bread", 3, SIZES3, [
        I("Water", "ml", [350, 280, 195], "water"),
        I("Salt", "ml", [7.5, 7.5, 5], "salt"),
        I("Butter", "g", [25, 20, 14], "butter", note="small pieces"),
        I("Skimmed milk powder", "ml", [30, 15, 10], "milk_powder"),
        I("Granulated sugar", "ml", [10, 7.5, 5], "sugar"),
        I("White bread flour", "g", [150, 125, 90], "bread_flour"),
        I("Wholemeal (whole-wheat) bread flour", "g", [350, 250, 175], "wholewheat_flour"),
        I("Easy bake (rapid-rise) dried yeast", "ml", [7.5, 5, 3.75], "yeast"),
    ], notes="Adapted from a bread machine cookbook (Basic breads). The book's Medium (≈ 905 g) is used "
             "for 1000 g and its Small (≈ 700 g) for 750 g; its Large (≈ 1.06 kg) is over the 1000 g limit, so the "
             "500 g loaf is the Small scaled by 0.7. About two-thirds of the flour is wholemeal, so the manual's "
             "Whole wheat program (for 50 % wholemeal or more) is the default; the book uses Basic, which is "
             "quicker and gives a slightly less risen loaf — pick it under Method. Medium or light crust. Salt, "
             "butter, milk powder and sugar go in with the water (manual's order). Variation: brown bread flour "
             "instead of wholemeal gives a lighter loaf.",
       variants={
           "Whole wheat program (manual)": {},
           "Basic program (book)": {"program": 1},
       }),
    R("Buckwheat and walnut bread", "Bread", 8, SIZES3, [
        I("Water", "ml", [315, 210, 155], "water"),
        I("Molasses", "ml", [15, 10, 7.5], "molasses"),
        I("Walnut or olive oil", "ml", [30, 30, 22], "oil"),
        I("Salt", "ml", [7.5, 5, 3.75], "salt"),
        I("Granulated sugar", "ml", [3.75, 2.5, 1.25], "sugar"),
        I("Unbleached white bread flour", "g", [425, 325, 235], "bread_flour"),
        I("Buckwheat flour", "g", [75, 50, 35], "buckwheat_flour"),
        I("Skimmed milk powder", "ml", [22, 15, 11.25], "milk_powder"),
        I("Easy bake (rapid-rise) dried yeast", "ml", [5, 5, 3.75], "yeast"),
        I("Walnut pieces", "g", [50, 40, 30], "walnuts", "box_or_beep"),
    ], notes="Adapted from a bread machine cookbook (Speciality grains, p. 89). The book's Medium (≈ 935 g) is used "
             "for 1000 g and its Small (≈ 680 g) for 750 g; its Large (≈ 1.12 kg) is over the 1000 g limit, so the "
             "500 g loaf is the Small scaled by 0.73. The book's 'basic, raisin setting' is this machine's program 8 "
             "Fruit bread (bakes like Basic, with the add-in release); medium crust. Walnuts go in the ingredients "
             "box, as the book suggests for an automatic dispenser. Salt and sugar go in with the liquids "
             "(manual's order). A compact loaf is normal."),
    R("Sweet bread", "Bread", 4, SIZES3, [
        I("Water", "ml", [280, 220, 140], "water"),
        I("Egg", "pcs", [2, 1, 1]),
        I("Salt", "g", [8, 6, 4], "salt"),
        I("Oil (refined)", "ml", [36, 24, 18], "oil"),
        I("Milk powder", "g", [12, 9, 6], "milk_powder"),
        I("Sugar", "g", [80, 55, 40], "sugar"),
        I("Bread flour", "g", [500, 380, 250], "bread_flour"),
        I("Yeast", "g", [6, 4, 3], "yeast"),
    ], notes="Fresh milk can replace the water + milk powder. Contains egg — no delay timer."),
    R("Cushaw (pumpkin) bread", "Bread", 4, SIZES3, [
        I("Water", "ml", [100, 80, 50], "water"),
        I("Salt", "g", [6, 5, 3], "salt"),
        I("Cushaw / pumpkin, steamed & mashed", "g", [300, 210, 150], "pumpkin"),
        I("Oil (refined)", "ml", [30, 27, 15], "oil"),
        I("Milk powder", "g", [20, 18, 10], "milk_powder"),
        I("Sugar", "g", [48, 40, 24], "sugar"),
        I("Bread flour", "g", [380, 300, 190], "bread_flour"),
        I("Yeast", "g", [8, 5, 4], "yeast"),
    ], prep=["Cut the pumpkin into ½ cm pieces and steam for about 20 min. Drain, let cool, then mash finely."]),
    R("Coffee bread", "Bread", 4, SIZES3, [
        I("Water", "ml", [240, 200, 120], "water"),
        I("Egg", "pcs", [2, 1, 1]),
        I("Salt", "g", [6, 4, 3], "salt"),
        I("Oil (refined)", "ml", [26, 20, 13], "oil"),
        I("Milk powder", "g", [10, 8, 5], "milk_powder"),
        I("Sugar", "g", [50, 40, 25], "sugar"),
        I("Instant coffee (small portions/sachets)", "pcs", [3, 2, 1.5]),
        I("Bread flour", "g", [400, 300, 200], "bread_flour"),
        I("Yeast", "g", [6, 5, 3], "yeast"),
    ], notes="The eggs may be stirred into the water for a lighter crumb. Contains egg — no delay timer."),
    R("Green (lettuce) bread", "Bread", 4, SIZES3, [
        I("Water", "ml", [260, 200, 130], "water"),
        I("Egg", "pcs", [2, 1, 1]),
        I("Salt", "g", [6, 7, 3], "salt",
          note="manual prints 7 g for 750 g (more than the 1000 g loaf) — likely a misprint; ~5 g fits the scale"),
        I("Oil (refined)", "ml", [40, 30, 20], "oil"),
        I("Milk powder", "g", [16, 13, 8], "milk_powder"),
        I("Sugar", "g", [50, 40, 25], "sugar"),
        I("Lettuce leaves, chopped & mashed", "g", [280, 210, 140], "puree"),
        I("Bread flour", "g", [460, 350, 230], "bread_flour"),
        I("Yeast", "g", [6, 4, 3], "yeast"),
    ], prep=["Chop the lettuce and mash it in a food processor."]),
    R("Banana bread", "Bread", 4, SIZES3, [
        I("Water", "ml", [250, 230, 130], "water"),
        I("Egg", "pcs", [2, 1, 1]),
        I("Salt", "g", [7, 6, 4], "salt"),
        I("Milk powder", "g", [65, 60, 35], "milk_powder"),
        I("Sugar", "g", [50, 40, 25], "sugar"),
        I("Bread flour", "g", [450, 400, 230], "bread_flour"),
        I("Yeast", "g", [7, 5, 4], "yeast"),
        I("Banana, cut in pieces", "g", [110, 80, 55], "banana",
          note="add on top after the other ingredients, before starting"),
        I("Butter", "g", [30, 36, 20], "butter", "timed", at_min=10,
          note="manual prints 36 g for 750 g (more than the 1000 g loaf) — likely a misprint; ~23 g fits the scale"),
    ], notes="Butter goes in 10 min after kneading starts — the app alerts you."),
    R("Coconut bread", "Bread", 4, SIZES3, [
        I("Water", "ml", [280, 220, 140], "water"),
        I("Egg", "pcs", [2, 1, 1]),
        I("Salt", "g", [6, 5, 4], "salt"),
        I("Oil (refined)", "ml", [36, 27, 18], "oil"),
        I("Milk powder", "g", [60, 45, 30], "milk_powder"),
        I("Sugar", "g", [50, 40, 25], "sugar"),
        I("Bread flour", "g", [460, 360, 230], "bread_flour"),
        I("Yeast", "g", [5, 5, 4], "yeast"),
        I("Desiccated coconut", "g", [60, 45, 30], "coconut", "box_or_beep"),
    ]),
    R("Three chocolate bread", "Bread", 4, SIZES3, [
        I("Water", "ml", [230, 160, 90], "water"),
        I("Egg", "pcs", [1, 1, 1]),
        I("Unbleached white bread flour", "g", [480, 360, 240], "bread_flour"),
        I("Caster sugar", "g", [24, 18, 12], "caster_sugar"),
        I("Salt", "tsp", [1, 0.75, 0.5], "salt"),
        I("Butter", "g", [24, 18, 12], "butter", note="put sugar, salt and butter in separate corners of the pan"),
        I("Easy bake (rapid-rise) dried yeast", "tsp", [1.5, 1, 0.75], "yeast"),
        I("Plain chocolate with raisins and almonds, coarsely chopped", "g", [50, 35, 25], "chocolate", "beep"),
        I("Plain chocolate with ginger, coarsely chopped", "g", [50, 35, 25], "chocolate", "beep"),
        I("Belgian milk chocolate, coarsely chopped", "g", [70, 55, 35], "chocolate", "beep"),
    ], notes="Adapted from a bread machine cookbook (Sweet breads and yeast cakes, p. 208), rescaled from the "
             "book's Medium loaf so each size totals ≈ 990 / 740 / 495 g, within the machine's 1000 g limit "
             "(the book's Large is ≈ 1.25 kg). Liquid (water + egg) is kept at the book's 58 % of the flour, so the "
             "water is reduced on the smaller loaves to allow for the whole egg. Crust: light for 500 g, medium "
             "otherwise. Add the chocolate gradually at the beep, letting each lot mix in before adding more. "
             "Contains egg — no delay timer."),
    # ---------------- Cakes ----------------
    R("Gingerbread", "Cake", 9, SINGLE, [
        I("Egg", "pcs", 1),
        I("Milk", "ml", 100, "milk"),
        I("Margarine", "g", 15, "butter"),
        I("Vanilla sugar (pack, ≈8 g)", "pcs", 0.5),
        I("Sugar", "g", 75, "sugar", note="manual: 70–80 g"),
        I("Rye flour type 1150", "g", 40, "rye_flour"),
        I("Wheat flour type 405 (plain)", "g", 120, "plain_flour"),
        I("Cocoa", "g", 20, "cocoa"),
        I("Gingerbread spice mix", "g", 10, "spice"),
        I("Baking powder (pack, ≈15 g), sifted", "pcs", 1),
        I("Cooking chocolate (pieces) for the glaze", "pcs", 2, where="after"),
    ], post=["Melt the cooking chocolate and cover the gingerbread with it."],
       notes="Spice mix tip: cardamom, coriander, cinnamon, clove, aniseed, star anise, fennel, "
             "nutmeg, allspice, a little pepper."),
    R("Orange cake", "Cake", 9, SINGLE, [
        I("Egg", "pcs", 1),
        I("Milk", "ml", 100, "milk"),
        I("Margarine", "g", 15, "butter"),
        I("Vanilla sugar (pack, ≈8 g)", "pcs", 0.5),
        I("Orange flavour (vial)", "pcs", 1),
        I("Sugar", "g", 75, "sugar", note="manual: 70–80 g"),
        I("Wheat flour type 405 (plain)", "g", 180, "plain_flour"),
        I("Baking powder (pack, ≈15 g), sifted", "pcs", 1),
        I("Icing sugar (glaze)", "tbsp", 2, "icing_sugar", "after"),
        I("Orange juice (glaze), a few drops", "pcs", 1, where="after"),
    ], post=["Mix the icing sugar with a few drops of orange juice into a thick syrup and ice the cooled cake."]),
    R("Yeast cake", "Cake", 9, SINGLE, [
        I("Butter", "g", 100, "butter", "prep"),
        I("Sugar", "g", 100, "sugar", "prep"),
        I("Eggs", "pcs", 5, where="prep"),
        I("Self-raising flour", "g", 250, "self_raising_flour", "prep"),
        I("Vanilla flavour (vial)", "pcs", 1, where="prep"),
        I("Lemon juice", "ml", 25, "lemon_juice", "prep"),
        I("Yeast", "g", 4, "yeast", "prep"),
    ], prep=["Beat eggs, sugar and butter in a mixer until frothy.",
             "Add the self-raising flour in portions and mix in by hand.",
             "Add all remaining ingredients and mix thoroughly.",
             "Wipe the inside of the bread pan with a little oil, then pour in the batter."]),
    # ---------------- Dough ----------------
    R("Yeast dough (pizza / rolls)", "Dough", 14, SINGLE, [
        I("Water", "ml", 260, "water"),
        I("Bread flour", "g", 400, "bread_flour"),
        I("Yeast", "g", 5, "yeast"),
    ], notes="Water, flour and yeast are the essentials; extras are optional — max 1000 g total."),
    R("Onion focaccia", "Dough", 14, SINGLE, [
        I("Water", "ml", 210, "water"),
        I("Olive oil", "tbsp", 1, "oil"),
        I("Salt", "tsp", 0.5, "salt"),
        I("Granulated sugar", "tsp", 1, "sugar"),
        I("Unbleached white bread flour", "g", 350, "bread_flour"),
        I("Easy bake (rapid-rise) dried yeast", "tsp", 1, "yeast"),
        I("Fresh sage, chopped (kneaded in by hand)", "tbsp", 1, where="after"),
        I("Red onion, chopped (kneaded in by hand)", "tbsp", 1, where="after"),
        I("Olive oil (topping)", "tbsp", 2, "oil", "after"),
        I("Red onion, thinly sliced (topping)", "pcs", 0.5, where="after"),
        I("Fresh sage leaves (topping)", "pcs", 5, where="after"),
        I("Coarse sea salt (topping)", "tsp", 2, where="after"),
        I("Coarsely ground black pepper (topping), to taste", "pcs", None, where="after"),
    ], post=[
        "Lightly oil a 25–28 cm (10–11 in) shallow round cake tin or pizza pan.",
        "Turn the dough out onto a lightly floured surface. Knock it back and flatten it slightly.",
        "Sprinkle over the chopped sage and chopped red onion (1 tbsp each) and knead gently to incorporate.",
        "Shape into a ball, flatten, then roll into a 25–28 cm round. Place in the tin, cover with oiled cling film "
        "and leave to rise in a warm place for 20 min.",
        "Meanwhile preheat the oven to 200 °C (fan ≈ 180 °C) / 400 °F / Gas 6.",
        "Uncover and poke deep dimples all over the surface with your fingertips. Cover and leave to rise another "
        "10–15 min, or until doubled.",
        "Drizzle over 2 tbsp olive oil, then scatter the sliced onion, 5 sage leaves, 2 tsp coarse sea salt and black pepper.",
        "Bake 20–25 min until golden. Turn out onto a wire rack, cool slightly and serve warm.",
    ], notes="From a bread machine cookbook (Flatbreads and pizzas, p. 109). Only the dough is made in the machine "
             "(program 14 Dough, ≈ 590 g — well within the 1000 g limit), so no rescaling is needed; shaping and "
             "baking are done in the oven. Loading order follows the manual (liquids, salt and sugar, flour, yeast on "
             "top) rather than the book's corners method. The sage and onion are kneaded in by hand after the "
             "cycle, because the Dough program has no add-in beep.",
       variants={
        "Oven bake (book)": {},
        "Bake in the machine": {
            "post": [
                "Lift out the pan, turn the dough out onto a lightly floured surface and pull the "
                "kneading paddle off the shaft (use the hook if it sticks). Rinse and dry the pan.",
                "Knock the dough back, sprinkle over the chopped sage and chopped red onion (1 tbsp each) and knead "
                "gently to incorporate.",
                "Brush the inside of the pan (base and sides) with a little olive oil. Without the paddle, the shaft "
                "will leave a small hole in the base of the focaccia — that's normal.",
                "Press the dough into the pan in an even layer, right into the corners.",
                "Poke deep dimples all over with your fingertips. Drizzle over 2 tbsp olive oil, then scatter the "
                "sliced onion, 5 sage leaves, 2 tsp coarse sea salt and black pepper.",
                "Lock the pan back in, close the lid and leave to rise in the (still warm) machine, unplugged, for "
                "25–30 min, until about doubled.",
                "Plug in, touch BAKE (program 16), set **35 min** with +TIME/−TIME, touch START/STOP — and start "
                "the **Stage 2 — Bake** timer in the app.",
                "When done: unplug, oven gloves, lift out the pan, loosen the sides with a soft plastic spatula "
                "and turn out onto a rack. Pale or soft in the middle? Run BAKE for another 5–10 min.",
                "Optional: the machine browns from below, so the top stays paler than in an oven. For a golden top, "
                "slide it under a hot grill for 2–3 min. Cool slightly and serve warm.",
            ],
            "notes": "Machine-only version: program 14 Dough, then shape and top the dough in the bread pan, "
                     "rise in the machine and bake with program 16 Bake. It comes out thicker and loaf-shaped "
                     "(about 4–5 cm deep, in the shape of the pan) rather than a thin round, with a softer, paler top. "
                     "The 35 min bake time is a starting point, so check the first one; the manual lets you add more "
                     "time with program 16.",
            "stages": [
                {"label": "Stage 1 — Dough", "program": 14},
                {"label": "Stage 2 — Bake", "program": 16, "time_default": 35,
                 "caption": "Start this after shaping, topping and the 25–30 min rise in the pan."},
            ],
        },
       }),
    R("Muesli and date bread", "Dough", 14, SINGLE, [
        I("Water", "ml", 260, "water", note="add 1 tbsp more if the dough is too firm (depends on the muesli)"),
        I("Sunflower oil", "ml", 30, "oil"),
        I("Clear honey", "ml", 15, "honey"),
        I("Salt", "ml", 7.5, "salt"),
        I("Unbleached white bread flour", "g", 300, "bread_flour"),
        I("Wholemeal (whole-wheat) bread flour", "g", 75, "wholewheat_flour"),
        I("Unsweetened fruit and nut muesli", "g", 150, "muesli"),
        I("Skimmed milk powder", "ml", 45, "milk_powder"),
        I("Easy bake (rapid-rise) dried yeast", "ml", 7.5, "yeast"),
        I("Stoned (pitted) dates, chopped", "g", 65, "dates", "lateknead"),
    ], post=[
        "Lightly oil a baking sheet.",
        "When the dough cycle has finished, turn the dough out onto a surface dusted with wholemeal flour and knock "
        "it back gently.",
        "Shape into a plump round and place on the baking sheet. With a sharp knife make three cuts about 1 cm deep "
        "across the top, dividing it into six sections.",
        "Cover with lightly oiled cling film and leave 30–45 min, until almost doubled in size.",
        "Meanwhile preheat the oven to 200 °C (fan ≈ 180 °C) / 400 °F / Gas 6.",
        "Bake 30–35 min until golden and hollow-sounding when tapped underneath. Cool on a wire rack.",
    ], notes="Adapted from a bread machine cookbook (Speciality grains, p. 84). One size only, ≈ 925 g of dough — "
             "within the 1000 g limit, so no rescaling. Program 14 Dough has no add-in beep, so the app alerts you "
             "about 5 min before kneading is due to end to add the dates (the book's 'last few minutes of kneading'); "
             "that time is estimated from the LCD total. Salt goes in with the liquids (manual's order).",
       variants={
           "Oven bake (book)": {},
           "Bake in the machine": {
               "program": 8,
               "sizes": ["1000 g"],
               "blade": True,
               "ingredients": [
        I("Water", "ml", 260, "water", note="add 1 tbsp more if the dough is too firm (depends on the muesli)"),
        I("Sunflower oil", "ml", 30, "oil"),
        I("Clear honey", "ml", 15, "honey"),
        I("Salt", "ml", 7.5, "salt"),
        I("Unbleached white bread flour", "g", 300, "bread_flour"),
        I("Wholemeal (whole-wheat) bread flour", "g", 75, "wholewheat_flour"),
        I("Unsweetened fruit and nut muesli", "g", 150, "muesli"),
        I("Skimmed milk powder", "ml", 45, "milk_powder"),
        I("Easy bake (rapid-rise) dried yeast", "ml", 7.5, "yeast"),
        I("Stoned (pitted) dates, chopped", "g", 65, "dates", "beep"),
    ],
               "post": [],
               "notes": "Machine-baked loaf: program 8 Fruit bread, WEIGHT 1000 g (≈ 925 g in the pan). Sticky "
                        "fruit like dates goes straight into the pan at the add-in beep, not in the ingredients box "
                        "(manual). Medium crust. The result is a tall loaf in the shape of the pan rather than the "
                        "book's scored round. Add 1 tbsp more water if the dough looks too firm during the first "
                        "knead.",
           },
       }),
    R("Barley-enriched farmhouse loaf", "Dough", 14, SINGLE, [
        I("Water", "ml", 260, "water"),
        I("Double (heavy) cream", "ml", 45, "cream"),
        I("Granulated sugar", "ml", 10, "sugar"),
        I("Salt", "ml", 10, "salt"),
        I("Unbleached white bread flour", "g", 400, "bread_flour"),
        I("Barley flour", "g", 115, "barley_flour"),
        I("Easy bake (rapid-rise) dried yeast", "ml", 7.5, "yeast"),
        I("Pumpkin seeds", "g", 25, "pumpkin_seeds", "lateknead"),
    ], post=[
        "Lightly oil a 900 g (2 lb) loaf tin, about 18.5 × 12 cm.",
        "When the dough cycle has finished, turn the dough out onto a lightly floured surface and knock it back "
        "gently.",
        "Shape into a rectangle with the long side the same length as the tin. Roll it up lengthways, tuck the ends "
        "under and place it in the tin, seam underneath.",
        "Cover with oiled cling film and leave 30–45 min, until the dough reaches the top of the tin. (Start "
        "preheating the oven towards the end of this rise so it's hot in time.)",
        "Preheat the oven to 220 °C (fan ≈ 200 °C) / 425 °F / Gas 7.",
        "Dust the loaf with flour, make one deep lengthways cut along the top and leave to rest 10 min.",
        "Bake 15 min, then reduce to 200 °C (fan ≈ 180 °C) / 400 °F / Gas 6 and bake 20–25 min more, until golden "
        "and hollow-sounding when tapped on the base. Cool on a wire rack.",
    ], notes="Adapted from a bread machine cookbook (Speciality grains). One size only, ≈ 870 g of dough — "
             "within the 1000 g limit, so no rescaling. Program 14 Dough has no add-in beep, so the app alerts you "
             "about 5 min before kneading is due to end to add the pumpkin seeds (the book's 'last 5 minutes of "
             "kneading'); that time is estimated from the LCD total. Sugar and salt go in with the liquids "
             "(manual's order).",
       variants={
           "Oven bake (book)": {},
           "Bake in the machine": {
               "program": 1,
               "sizes": ["1000 g"],
               "ingredients": [
        I("Water", "ml", 260, "water"),
        I("Double (heavy) cream", "ml", 45, "cream"),
        I("Granulated sugar", "ml", 10, "sugar"),
        I("Salt", "ml", 10, "salt"),
        I("Unbleached white bread flour", "g", 400, "bread_flour"),
        I("Barley flour", "g", 115, "barley_flour"),
        I("Easy bake (rapid-rise) dried yeast", "ml", 7.5, "yeast"),
        I("Pumpkin seeds", "g", 25, "pumpkin_seeds", "box"),
    ],
               "post": [],
               "notes": "Machine-baked loaf: program 1 Basic, WEIGHT 1000 g (≈ 870 g in the pan), medium crust "
                        "(dark if you want it crustier, like the book's). The pumpkin seeds are dry, so they go in "
                        "the ingredients box and drop in automatically at the add-in point. You won't get the "
                        "book's floured, slashed top, and the loaf is the shape of the pan.",
           },
       }),
    R("Wild rice, oat and polenta bread", "Dough", 14, SINGLE, [
        I("Water", "ml", 270, "water"),
        I("Sunflower oil", "tbsp", 1.75, "oil"),
        I("Golden (light corn) syrup", "tbsp", 1.75, "golden_syrup"),
        I("Salt", "tsp", 1.75, "salt"),
        I("Unbleached white bread flour", "g", 290, "bread_flour"),
        I("Strong wholemeal (whole-wheat) flour", "g", 45, "wholewheat_flour"),
        I("Polenta", "g", 45, "polenta"),
        I("Rolled oats", "g", 45, "rolled_oats"),
        I("Skimmed milk powder", "tbsp", 1.75, "milk_powder"),
        I("Easy bake (rapid-rise) dried yeast", "tsp", 1, "yeast"),
        I("Cooked wild rice, drained and cooled (from 45 g raw)", "g", 150, "cooked_rice", "lateknead"),
    ], prep=["Cook 45 g wild rice in boiling salted water as the packet says (wild rice takes 40–60 min). Drain well and "
             "let it cool. You need about 150 g cooked."], post=[
        "Lightly oil a 23 × 13 cm (9 × 5 in) loaf tin.",
        "When the dough cycle has finished, turn the dough out onto a lightly floured surface and knock it back gently.",
        "Divide into six equal pieces. Shape each into an oblong mini loaf about 13 cm long, and place them "
        "widthways, side by side, in the tin.",
        "Cover with lightly oiled cling film and leave in a warm place 30–45 min, until the dough almost reaches the "
        "top of the tin. Meanwhile preheat the oven to 220 °C (fan ≈ 200 °C) / 425 °F / Gas 7.",
        "Bake 30–35 min, until golden and hollow-sounding when tapped on the base. Turn out onto a wire rack to cool.",
    ], notes="Adapted from a bread machine cookbook (Speciality grains, p. 90). The book's quantities come to "
             "≈ 1.02 kg with the cooked rice (wild rice roughly triples in weight), just over the 1000 g limit "
             "including all ingredients, so everything is scaled by 0.9 to ≈ 930 g. Program 14 Dough has no add-in "
             "beep, so the app alerts you about 5 min before kneading is due to end to add the rice (the book's "
             "'last 5 minutes of kneading'). Syrup and salt go in with the water (manual's order). Variation: long "
             "grain brown or white rice (quicker to cook) or red Camargue rice (≈ 1 h) instead of wild rice.",
       variants={
           "Oven bake (book)": {},
           "Bake in the machine": {
               "program": 8,
               "sizes": ["1000 g"],
               "blade": True,
               "ingredients": [
        I("Water", "ml", 270, "water"),
        I("Sunflower oil", "tbsp", 1.75, "oil"),
        I("Golden (light corn) syrup", "tbsp", 1.75, "golden_syrup"),
        I("Salt", "tsp", 1.75, "salt"),
        I("Unbleached white bread flour", "g", 290, "bread_flour"),
        I("Strong wholemeal (whole-wheat) flour", "g", 45, "wholewheat_flour"),
        I("Polenta", "g", 45, "polenta"),
        I("Rolled oats", "g", 45, "rolled_oats"),
        I("Skimmed milk powder", "tbsp", 1.75, "milk_powder"),
        I("Easy bake (rapid-rise) dried yeast", "tsp", 1, "yeast"),
        I("Cooked wild rice, drained and cooled (from 45 g raw)", "g", 150, "cooked_rice", "beep"),
    ],
               "post": [],
               "notes": "Machine-baked loaf: program 8 Fruit bread, WEIGHT 1000 g (≈ 930 g in the pan), medium "
                        "crust. The cooked rice is wet, so it goes straight into the pan at the add-in beep, not in "
                        "the ingredients box (manual). You get one pan-shaped loaf instead of the book's six "
                        "pull-apart mini loaves.",
           },
       }),
    R("Pane all'olio", "Dough", 14, SINGLE, [
        I("Water (biga)", "ml", 105, "water"),
        I("White bread flour (biga)", "g", 175, "bread_flour"),
        I("Easy bake (rapid-rise) dried yeast (biga)", "ml", 5, "yeast"),
        I("Water (dough)", "ml", 90, "water", "stage2"),
        I("Extra virgin olive oil (dough)", "ml", 60, "oil", "stage2"),
        I("Salt (dough)", "ml", 10, "salt", "stage2"),
        I("Granulated sugar (dough)", "ml", 5, "sugar", "stage2"),
        I("Unbleached white bread flour (dough), plus extra for dusting", "g", 225, "bread_flour", "stage2"),
    ], post=[
        "Lightly flour a peel (baker's shovel) or baking sheet.",
        "Turn the dough out onto a lightly floured surface, knock it back gently and shape into a plump round.",
        "With your palms, roll it backwards and forwards, concentrating on the ends, into a tapered torpedo about "
        "30 cm long. Place on the peel or baking sheet.",
        "Cover with lightly oiled cling film and leave in a warm place 45–60 min, until almost doubled.",
        "Meanwhile put a baking stone on a shelf about a third of the way up the oven and preheat to 230 °C "
        "(fan ≈ 210 °C) / 450 °F / Gas 8. No stone? Use unglazed terracotta tiles edge to edge, leaving air "
        "space around the outside.",
        "Dust the top lightly with flour, slash along its length and transfer the loaf to the hot stone.",
        "Mist the inside of the oven with water. Bake 15 min, misting again after 2 and after 4 min.",
        "Reduce to 190 °C (fan ≈ 170 °C) / 375 °F / Gas 5 and bake 20–25 min more, until golden all over and "
        "hollow-sounding when tapped on the base. Cool on a wire rack; serve warm or cold.",
    ], notes="Adapted from a bread machine cookbook (Sourdoughs and starter dough breads, p. 123). One size only, "
             "≈ 670 g in the pan — within the 1000 g limit, so no rescaling. Three stages: the biga is mixed on "
             "program 14 Dough and left 8 h, then the dough ingredients are added on top and run on program 14 "
             "again. Use the 'Timer for' selector for each stage. Salt and sugar go under the flour with the liquids "
             "(manual's order).",
       stage2={"program": 14, "timer_label": "Stage 3 — Dough", "between": [
            "When the dough program ends, switch the machine off (unplug) but leave the biga inside with the lid "
            "closed for **8 hours** — start the **Stage 2 — Biga rest** timer. If you need the machine meanwhile, "
            "move the biga to a bowl, cover with a damp tea towel and leave at room temperature.",
            "After 8 hours, lift out the pan and break the biga into three or four pieces (if you moved it to a "
            "bowl, put it back in the pan).",
        ]},
       stages=[
            {"label": "Stage 1 — Biga", "program": 14, "done_title": "Biga mixed — start the 8 h rest"},
            {"label": "Stage 2 — Biga rest", "program": 14, "fixed_min": 480, "phase_name": "Biga resting",
             "done_title": "Biga ready — add the dough ingredients",
             "caption": "Start this when the biga's dough program ends and the machine is switched off."},
            {"label": "Stage 3 — Dough", "program": 14, "done_title": "Dough ready — shape the loaf"},
        ],
       variants={
           "Oven bake (book)": {},
           "Bake in the machine": {
               "sizes": ["750 g"],
               "stage2": {"program": 2, "timer_label": "Stage 3 — Bake", "between": [
            "When the dough program ends, switch the machine off (unplug) but leave the biga inside with the lid "
            "closed for **8 hours** — start the **Stage 2 — Biga rest** timer. If you need the machine meanwhile, "
            "move the biga to a bowl, cover with a damp tea towel and leave at room temperature.",
            "After 8 hours, lift out the pan and break the biga into three or four pieces (if you moved it to a "
            "bowl, put it back in the pan).",
        ]},
               "stages": [
            {"label": "Stage 1 — Biga", "program": 14, "done_title": "Biga mixed — start the 8 h rest"},
            {"label": "Stage 2 — Biga rest", "program": 14, "fixed_min": 480, "phase_name": "Biga resting",
             "done_title": "Biga ready — add the dough ingredients",
             "caption": "Start this when the biga's dough program ends and the machine is switched off."},
            {"label": "Stage 3 — Bake", "program": 2},
        ],
               "post": [],
               "notes": "Machine-baked loaf: biga as in the book, then the dough ingredients go on top and the "
                        "loaf is made on program 2 French bread, WEIGHT 750 g (≈ 670 g in the pan), medium crust — "
                        "olive oil is fine on the French program (the manual's own French bread uses oil). You get "
                        "a pan-shaped loaf with a crisp crust instead of the book's slashed torpedo.",
           },
       }),
    R("Pain de campagne", "Dough", 14, SINGLE, [
        I("Water (poolish)", "ml", 200, "water"),
        I("Unbleached white bread flour (poolish)", "g", 175, "bread_flour"),
        I("Wholemeal bread flour (poolish)", "g", 50, "wholewheat_flour"),
        I("Easy bake (rapid-rise) dried yeast (poolish)", "tsp", 0.25, "yeast"),
        I("Water (dough)", "ml", 120, "water", "stage2"),
        I("Salt (dough)", "ml", 7.5, "salt", "stage2"),
        I("Granulated sugar (dough)", "ml", 2.5, "sugar", "stage2"),
        I("Unbleached white bread flour (dough), plus extra for dusting", "g", 225, "bread_flour", "stage2"),
        I("Wholemeal bread flour (dough)", "g", 50, "wholewheat_flour", "stage2"),
        I("Rye flour (dough)", "g", 25, "rye_flour", "stage2"),
        I("Easy bake dried yeast (dough)", "ml", 2.5, "yeast", "stage2"),
    ], post=[
        "Turn the dough out onto a lightly floured surface, knock it back gently and shape into a plump, round ball. "
        "Place on a lightly oiled baking sheet.",
        "Cover with a large glass bowl or lightly oiled cling film and leave in a warm place 30–45 min, until almost "
        "doubled. Meanwhile preheat the oven to 220 °C (fan ≈ 200 °C) / 425 °F / Gas 7.",
        "Dust the top with flour. Cut three parallel slashes across the loaf, then three more at right angles to them.",
        "Bake on a shelf near the bottom of the oven for about 40 min, until golden and hollow-sounding when tapped "
        "on the base. Cool on a wire rack.",
    ], notes="Adapted from a bread machine cookbook (Sourdoughs and starter dough breads, p. 124). One size only, "
             "≈ 860 g in the pan — within the 1000 g limit, so no rescaling. Three stages: poolish on program 14 "
             "Dough, a 2–8 h rest in the switched-off machine, then the dough ingredients on top and program 14 "
             "again. Use the 'Timer for' selector for each stage. Salt and sugar go in with the water under the "
             "flour, yeast in a hollow on top (manual's order).",
       stage2={"program": 14, "timer_label": "Stage 3 — Dough", "between": [
            "When the dough program ends, switch the machine off (unplug) but leave the poolish inside with the lid "
            "closed for **2–8 hours** — longer gives a more sour flavour. Set the time and start the **Stage 2 — "
            "Poolish rest** timer.",
            "Then lift out the bread pan and add the dough ingredients on top of the poolish:",
        ]},
       stages=[
            {"label": "Stage 1 — Poolish", "program": 14, "done_title": "Poolish mixed — start the rest"},
            {"label": "Stage 2 — Poolish rest", "program": 14, "range_h": (2, 8, 4), "phase_name": "Poolish resting",
             "done_title": "Poolish ready — add the dough ingredients",
             "caption": "Start this when the poolish's dough program ends and the machine is switched off. "
                        "2 h = mild, 8 h = most sour."},
            {"label": "Stage 3 — Dough", "program": 14, "done_title": "Dough ready — shape the loaf"},
        ],
       variants={
           "Oven bake (book)": {},
           "Bake in the machine": {
               "sizes": ["1000 g"],
               "stage2": {"program": 2, "timer_label": "Stage 3 — Bake", "between": [
            "When the dough program ends, switch the machine off (unplug) but leave the poolish inside with the lid "
            "closed for **2–8 hours** — longer gives a more sour flavour. Set the time and start the **Stage 2 — "
            "Poolish rest** timer.",
            "Then lift out the bread pan and add the dough ingredients on top of the poolish:",
        ]},
               "stages": [
            {"label": "Stage 1 — Poolish", "program": 14, "done_title": "Poolish mixed — start the rest"},
            {"label": "Stage 2 — Poolish rest", "program": 14, "range_h": (2, 8, 4), "phase_name": "Poolish resting",
             "done_title": "Poolish ready — add the dough ingredients",
             "caption": "Start this when the poolish's dough program ends and the machine is switched off. "
                        "2 h = mild, 8 h = most sour."},
            {"label": "Stage 3 — Bake", "program": 2},
        ],
               "post": [],
               "notes": "Machine-baked loaf: poolish and rest as in the book, then the dough ingredients go on top "
                        "and the loaf is made on program 2 French bread, WEIGHT 1000 g (≈ 860 g in the pan), "
                        "medium crust — no butter or milk, so it suits the French program. You get a pan-shaped "
                        "loaf instead of the book's floured, cross-slashed round.",
           },
       }),
    R("Ciabatta", "Dough", 14, SINGLE, [
        I("Water (biga)", "ml", 200, "water"),
        I("Unbleached white bread flour (biga)", "g", 175, "bread_flour"),
        I("Easy bake (rapid-rise) dried yeast (biga)", "ml", 2.5, "yeast"),
        I("Water (dough)", "ml", 200, "water", "stage2"),
        I("Milk (dough)", "ml", 30, "milk", "stage2"),
        I("Extra virgin olive oil (dough)", "ml", 30, "oil", "stage2"),
        I("Salt (dough)", "ml", 7.5, "salt", "stage2"),
        I("Granulated sugar (dough)", "ml", 2.5, "sugar", "stage2"),
        I("Unbleached white bread flour (dough), plus extra for dusting", "g", 325, "bread_flour", "stage2"),
        I("Easy bake dried yeast (dough)", "tsp", 0.25, "yeast", "stage2"),
    ], stage1_steps=["Tap START, start the **Stage 1 — Biga mix** timer (5 min), and let it mix for 5 minutes.", "When the timer goes off, switch the machine off: hold START/STOP for about 3 seconds until it beeps."], post=[
        "Transfer the dough to a bowl, cover with oiled cling film and leave about 1 h, "
        "until tripled in size — start the **Stage 4 — Bowl rise** timer. The dough is meant to be very wet: don't "
        "add more flour.",
        "Sprinkle two baking sheets with flour.",
        "With a spoon or dough scraper divide the dough in two. Carefully tip one portion onto a baking sheet, trying "
        "not to knock the air out. With well-floured hands shape it into a rectangular loaf about 2.5 cm thick, "
        "pulling and stretching as needed. Repeat with the other portion.",
        "Sprinkle both loaves with flour and leave uncovered in a warm place 20–30 min — they will spread and rise. "
        "Meanwhile preheat the oven to 220 °C (fan ≈ 200 °C) / 425 °F / Gas 7.",
        "Bake 25–30 min, until risen, light golden and hollow-sounding when tapped on the base. Cool on a wire rack. "
        "Serve with butter or olive oil for dipping.",
    ], notes="Adapted from a bread machine cookbook (Sourdoughs and starter dough breads, p. 125). Makes 2 loaves "
             "from ≈ 970 g of dough — within the 1000 g limit, so no rescaling. Four stages: the biga is mixed for "
             "5 min on program 14 Dough and the machine switched off; it rests 12 h or more; the dough ingredients "
             "go on top for a full Dough program; then the dough rises 1 h in a bowl before shaping. The dough is "
             "extremely wet by design. Salt and sugar go in with the liquids under the flour (manual's order).",
       stage2={"program": 14, "timer_label": "Stage 3 — Dough", "between": [
            "Leave the biga in the machine (lid closed, switched off), or in a large bowl covered with lightly oiled "
            "cling film, **overnight or at least 12 hours**, until it has risen and is just starting to collapse — "
            "start the **Stage 2 — Biga rest** timer.",
            "Return the biga to the bread pan if you moved it, then add the ciabatta dough ingredients:",
        ]},
       stages=[
            {"label": "Stage 1 — Biga mix", "program": 14, "fixed_min": 5, "phase_name": "Mixing the biga",
             "done_title": "Stop the machine now", "done_msg": "Hold START/STOP ~3 s to switch off, then start the biga rest.",
             "caption": "Start this when you tap START on program 14 Dough."},
            {"label": "Stage 2 — Biga rest", "program": 14, "range_h": (12, 18, 12), "phase_name": "Biga resting",
             "done_title": "Biga ready — add the dough ingredients",
             "caption": "Overnight or at least 12 h, until risen and just starting to collapse."},
            {"label": "Stage 3 — Dough", "program": 14, "done_title": "Dough ready — into a bowl to rise"},
            {"label": "Stage 4 — Bowl rise", "program": 14, "fixed_min": 60, "phase_name": "Rising in the bowl",
             "done_title": "Dough tripled — shape the loaves", "done_msg": "Divide, shape on the floured sheets, rest 20–30 min, preheat the oven.",
             "caption": "Start this when the dough goes into the bowl."},
        ],
       variants={
           "Oven bake (book)": {},
           "Bake in the machine": {
               "sizes": ["1000 g"],
               "blade": False,
               "stage2": {"program": 1, "timer_label": "Stage 3 — Bake", "between": [
            "Leave the biga in the machine (lid closed, switched off), or in a large bowl covered with lightly oiled "
            "cling film, **overnight or at least 12 hours**, until it has risen and is just starting to collapse — "
            "start the **Stage 2 — Biga rest** timer.",
            "Return the biga to the bread pan if you moved it, then add the ciabatta dough ingredients:",
        ]},
               "stages": [
            {"label": "Stage 1 — Biga mix", "program": 14, "fixed_min": 5, "phase_name": "Mixing the biga",
             "done_title": "Stop the machine now", "done_msg": "Hold START/STOP ~3 s to switch off, then start the biga rest.",
             "caption": "Start this when you tap START on program 14 Dough."},
            {"label": "Stage 2 — Biga rest", "program": 14, "range_h": (12, 18, 12), "phase_name": "Biga resting",
             "done_title": "Biga ready — add the dough ingredients",
             "caption": "Overnight or at least 12 h, until risen and just starting to collapse."},
            {"label": "Stage 3 — Bake", "program": 1},
        ],
               "post": [],
               "notes": "Machine-baked loaf: biga as in the book, then the dough ingredients go on top and the loaf "
                        "is made on program 1 Basic, WEIGHT 1000 g (≈ 970 g in the pan), medium crust. Basic rather "
                        "than French because the dough contains milk (the manual says the French program isn't "
                        "suitable for milk). The dough is very wet, so leave the kneading paddle in rather than "
                        "trying to remove it. You get one open-textured, pan-shaped loaf — not the book's two flat "
                        "ciabatte.",
           },
       }),
    R("Pain de seigle", "Dough", 14, SINGLE, [
        I("Water (chef)", "ml", 200, "water"),
        I("Rye flour (chef)", "g", 175, "rye_flour"),
        I("Easy bake (rapid-rise) dried yeast (chef)", "tsp", 0.25, "yeast"),
        I("Water (1st refreshment)", "ml", 70, "water", "refresh1"),
        I("Plain (all-purpose) flour (1st refreshment)", "g", 50, "plain_flour", "refresh1"),
        I("Water (2nd refreshment)", "ml", 15, "water", "refresh2"),
        I("Plain (all-purpose) flour (2nd refreshment)", "g", 50, "plain_flour", "refresh2"),
        I("Water (bread dough)", "ml", 15, "water", "stage2"),
        I("Salt (bread dough)", "ml", 10, "salt", "stage2"),
        I("Clear honey (bread dough)", "ml", 5, "honey", "stage2"),
        I("Unbleached white bread flour (bread dough), plus extra for dusting", "g", 225, "bread_flour", "stage2"),
        I("Easy bake dried yeast (bread dough)", "ml", 2.5, "yeast", "stage2"),
    ], stage1_steps=[
        "Tap START and start the **Stage 1 — Chef mix** timer (10 min).",
        "When it goes off, hold START/STOP for about 3 seconds until it beeps to switch the machine off.",
    ], batches=[
        {"where": "refresh1", "before": [
            "Leave the chef to ferment in the machine, lid closed, for about **24 hours** — start the **Stage 2 — Chef "
            "ferment** timer. If you need the machine meanwhile, move the chef to a bowl, cover with a damp tea towel "
            "and leave at room temperature.",
            "Lift out the pan (return the chef to it if you moved it) and add the 1st refreshment:"],
         "after": [
            "Lock the pan in, select **14 Dough**, tap START and start the **Stage 3 — 1st refreshment mix** timer "
            "(10 min). When it goes off, hold START/STOP for about 3 seconds until it beeps to switch the machine off.",
            "Leave the dough in the machine, lid closed, for a further **24 hours** — start the **Stage 4 — 1st "
            "refreshment rest** timer.",
            "Then add the 2nd refreshment:"]},
        {"where": "refresh2", "after": [
            "Mix as before: **14 Dough**, START, start the **Stage 5 — 2nd refreshment mix** timer (10 min), then "
            "hold START/STOP for about 3 seconds until it beeps to switch the machine off.",
            "Leave the dough in the machine for **8 hours** — start the **Stage 6 — 2nd refreshment rest** timer."]},
    ], post=[
        "Lightly flour a baking sheet.",
        "Turn the dough out onto a lightly floured surface, knock it back gently and divide into two equal pieces. "
        "Roll each into a rope about 45 cm long.",
        "Place the ropes side by side. Starting at the centre, lay one over the other and keep twisting to the end. "
        "Turn the dough round and twist the other ends — keep twisting in the same direction. Dampen the ends with "
        "water and tuck them under to seal.",
        "Place the twist on the baking sheet, cover with oiled cling film and leave in a warm place about 45 min, until "
        "almost doubled — start the **Stage 8 — Final rise** timer. Preheat the oven to 220 °C (fan ≈ 200 °C) / "
        "425 °F / Gas 7 towards the end.",
        "Dust the top lightly with flour and bake 40 min, until golden and hollow-sounding when tapped on the base.",
        "Switch off the oven but leave the loaf inside with the door slightly ajar for 5 min, then cool on a wire rack.",
    ], notes="Adapted from a bread machine cookbook (Sourdoughs and starter dough breads, p. 126). One loaf from "
             "≈ 820 g of dough — within the 1000 g limit, so no rescaling. A rye starter ('chef') built over about "
             "2½ days: chef mixed 10 min and fermented 24 h, refreshed and rested 24 h, refreshed again and rested "
             "8 h, then the bread dough on program 14 Dough. Each mix uses program 14 Dough stopped after 10 min. Use "
             "the 'Timer for' selector for each stage. Salt and honey go with the water under the flour, yeast in a "
             "hollow (manual's order). Serve thickly buttered — the book suggests it with shellfish.",
       stage2={"program": 14, "timer_label": "Stage 7 — Bread dough", "between": [
            "Finally add the bread dough ingredients:"]},
       stages=[
            {"label": "Stage 1 — Chef mix", "program": 14, "fixed_min": 10, "phase_name": "Mixing", "done_title": "Stop the machine now", "done_msg": "Hold START/STOP ~3 s to switch off, then start the next rest."},
            {"label": "Stage 2 — Chef ferment", "program": 14, "fixed_min": 1440, "phase_name": "Chef ferment", "done_title": "Chef ready — add the 1st refreshment"},
            {"label": "Stage 3 — 1st refreshment mix", "program": 14, "fixed_min": 10, "phase_name": "Mixing", "done_title": "Stop the machine now", "done_msg": "Hold START/STOP ~3 s to switch off, then start the next rest."},
            {"label": "Stage 4 — 1st refreshment rest", "program": 14, "fixed_min": 1440, "phase_name": "1st refreshment rest", "done_title": "Add the 2nd refreshment"},
            {"label": "Stage 5 — 2nd refreshment mix", "program": 14, "fixed_min": 10, "phase_name": "Mixing", "done_title": "Stop the machine now", "done_msg": "Hold START/STOP ~3 s to switch off, then start the next rest."},
            {"label": "Stage 6 — 2nd refreshment rest", "program": 14, "fixed_min": 480, "phase_name": "2nd refreshment rest", "done_title": "Add the bread dough ingredients"},
            {"label": "Stage 7 — Bread dough", "program": 14, "done_title": "Dough ready — shape the twist"},
            {"label": "Stage 8 — Final rise", "program": 14, "fixed_min": 45, "phase_name": "Final rise", "done_title": "Risen — dust with flour and bake"},
        ],
       variants={
           "Oven bake (book)": {},
           "Bake in the machine": {
               "sizes": ["1000 g"],
               "stage2": {"program": 2, "timer_label": "Stage 7 — Bake", "between": [
                    "Finally add the bread dough ingredients:"]},
               "stages": [
            {"label": "Stage 1 — Chef mix", "program": 14, "fixed_min": 10, "phase_name": "Mixing", "done_title": "Stop the machine now", "done_msg": "Hold START/STOP ~3 s to switch off, then start the next rest."},
            {"label": "Stage 2 — Chef ferment", "program": 14, "fixed_min": 1440, "phase_name": "Chef ferment", "done_title": "Chef ready — add the 1st refreshment"},
            {"label": "Stage 3 — 1st refreshment mix", "program": 14, "fixed_min": 10, "phase_name": "Mixing", "done_title": "Stop the machine now", "done_msg": "Hold START/STOP ~3 s to switch off, then start the next rest."},
            {"label": "Stage 4 — 1st refreshment rest", "program": 14, "fixed_min": 1440, "phase_name": "1st refreshment rest", "done_title": "Add the 2nd refreshment"},
            {"label": "Stage 5 — 2nd refreshment mix", "program": 14, "fixed_min": 10, "phase_name": "Mixing", "done_title": "Stop the machine now", "done_msg": "Hold START/STOP ~3 s to switch off, then start the next rest."},
            {"label": "Stage 6 — 2nd refreshment rest", "program": 14, "fixed_min": 480, "phase_name": "2nd refreshment rest", "done_title": "Add the bread dough ingredients"},
            {"label": "Stage 7 — Bake", "program": 2},
        ],
               "post": [],
               "notes": "Machine-baked loaf: the chef and both refreshments as in the book, then the bread dough "
                        "ingredients go on top and the loaf is made on program 2 French bread, WEIGHT 1000 g "
                        "(≈ 820 g in the pan), medium crust — no butter or milk, so the French program suits it. "
                        "About a third of the flour is rye, below the manual's 50 % threshold for the Whole wheat "
                        "program. You get a pan-shaped loaf instead of the book's twist.",
           },
       }),
    R("Pasta dough", "Dough", 14, SINGLE, [
        I("Water", "ml", 260, "water"),
        I("Flour", "g", 400, "plain_flour"),
    ], notes="Manual specifies program 14 Dough. Max 1000 g total."),
    # ---------------- Yoghurt ----------------
    R("Natural yoghurt", "Yoghurt", 18, SINGLE, [
        I("Fresh milk", "ml", 1000, "milk"),
        I("Live (lactobacillus) yoghurt", "ml", 100, "yoghurt"),
    ], time_default=480,
       notes="Longer time (up to 12 h) gives a creamier yoghurt. Add sugar/fruit only after it's made. "
             "Home yoghurt is fairly runny; a little gelatine thickens it."),
    R("Nougat drinking yoghurt", "Yoghurt", 18, SINGLE, [
        I("Low-fat milk", "ml", 1000, "milk"),
        I("Live (lactobacillus) yoghurt", "ml", 100, "yoghurt"),
        I("Hazelnut nougat cream", "g", 100, "choc_spread", "after"),
    ], time_default=480,
       post=["Pour the yoghurt into a tall narrow jug, cover and leave at room temperature until the whey separates on top.",
             "Carefully pour off the whey (whey drink: 200 ml whey + 1 tbsp lemon juice + sugar to taste).",
             "Whisk the hazelnut nougat cream into the yoghurt, chill, and serve in 4–6 glasses."]),
    # ---------------- Jam ----------------
    R("Strawberry jam", "Jam", 10, SINGLE, [
        I("Fresh strawberries, hulled, chopped or puréed", "g", 900, "fruit_chopped"),
        I("Sugar", "g", 540, "sugar"),
        I("Starch", "g", 30, "starch"),
        I("Lemon juice", "ml", 15, "lemon_juice"),
    ], prep=["Wash the strawberries, remove the leaves, cut small or purée."],
       notes="Starch-set jam is perishable: keep airtight in the fridge, up to 1 week."),
    R("Orange jam", "Jam", 10, SINGLE, [
        I("Oranges, peeled, seeded, thinly sliced", "g", 900, "fruit_chopped"),
        I("Sugar", "g", 540, "sugar"),
        I("Starch", "g", 30, "starch"),
        I("Lemon juice", "ml", 15, "lemon_juice"),
    ], prep=["Peel the oranges, remove the seeds, cut into thin slices."],
       notes="Starch-set jam is perishable: keep airtight in the fridge, up to 1 week."),
    # ---------------- Ice cream ----------------
    R("Vanilla ice cream", "Ice cream", 15, SINGLE, [
        I("Cream", "ml", 200, "cream", "prep"),
        I("Milk", "ml", 50, "milk", "prep"),
        I("Vanilla extract (or pulp of ½ pod)", "tsp", 0.5, "water", "prep"),
        I("Egg yolks", "pcs", 2, where="prep"),
        I("Caster sugar", "g", 40, "caster_sugar", "prep"),
    ], time_default=25, prep=[FREEZE_BUCKET, CUSTARD[0],
        "Heat milk and vanilla in a heavy-based pan on medium heat until it just simmers; whisk it into the yolks in small portions.",
        CUSTARD[1], CUSTARD[2],
        "Whip the chilled cream in a chilled bowl until stiff, then combine with the custard."],
       notes="Makes ≈300 ml. Keeps ~1 week in the freezer."),
    R("Quick vanilla ice cream", "Ice cream", 15, SINGLE, [
        I("Cream", "ml", 100, "cream", "prep"),
        I("Whole milk", "ml", 120, "milk", "prep"),
        I("Sweetened condensed milk", "ml", 75, "condensed_milk", "prep"),
        I("Vanilla extract", "tsp", 0.5, "water", "prep"),
    ], time_default=25, prep=[FREEZE_BUCKET,
        "Mix the milks and vanilla in a jug and chill in the fridge.",
        "Whip the chilled cream in a chilled bowl until stiff, combine with the milk mixture, refrigerate to +5 °C."],
       notes="Makes ≈300 ml. Keeps ~3 days in the freezer."),
    R("Chocolate ice cream", "Ice cream", 15, SINGLE, [
        I("Cream", "ml", 200, "cream", "prep"),
        I("Milk", "ml", 25, "milk", "prep"),
        I("Dark chocolate, chopped", "g", 75, "chocolate", "prep"),
        I("Egg yolks", "pcs", 2, where="prep"),
        I("Caster sugar", "g", 30, "caster_sugar", "prep"),
    ], time_default=30, prep=[FREEZE_BUCKET, CUSTARD[0],
        "Heat milk and chocolate in a heavy-based pan on low heat until it just simmers; whisk it into the yolks in small portions.",
        CUSTARD[1], CUSTARD[2],
        "Whip the chilled cream in a chilled bowl until stiff, then combine with the custard."],
       notes="Makes ≈350 ml. Keeps ~1 week in the freezer."),
    R("Hazelnut nougat gelato", "Ice cream", 15, SINGLE, [
        I("Milk", "ml", 150, "milk", "prep"),
        I("Hazelnut nougat cream", "g", 75, "choc_spread", "prep"),
        I("Vanilla extract", "tsp", 0.25, "water", "prep"),
        I("Egg yolks", "pcs", 2, where="prep"),
        I("Caster sugar", "g", 75, "caster_sugar", "prep"),
        I("Roasted hazelnuts, finely chopped", "tbsp", 2, "hazelnuts", "prep",
          note="the manual doesn't say when to add them — stirring them into the chilled mix is the usual choice"),
    ], time_default=30, prep=[FREEZE_BUCKET, CUSTARD[0],
        "Heat milk, vanilla and nougat cream in a heavy-based pan on medium heat until it just simmers; whisk into the yolks in small portions.",
        CUSTARD[1], CUSTARD[2]],
       notes="Makes ≈350 ml. Keeps ~1 week in the freezer."),
    R("Frozen strawberry yoghurt", "Ice cream", 15, SINGLE, [
        I("Fresh strawberries", "g", 150, "fruit_chopped", "prep"),
        I("Plain yoghurt", "g", 150, "yoghurt", "prep"),
        I("Caster sugar", "g", 40, "caster_sugar", "prep"),
    ], time_default=25, prep=[FREEZE_BUCKET,
        "Wash and hull the strawberries, then purée.",
        "Mix purée, sugar and yoghurt; refrigerate to +5 °C."],
       notes="Makes ≈340 ml. The yoghurt can be made with program 18."),
    R("Lemon sorbet", "Ice cream", 15, SINGLE, [
        I("Caster sugar", "g", 75, "caster_sugar", "prep"),
        I("Water", "ml", 150, "water", "prep"),
        I("Fresh lemon juice", "ml", 75, "lemon_juice", "prep"),
        I("Small egg white", "pcs", 1, where="prep"),
    ], time_default=25, prep=[FREEZE_BUCKET,
        "Heat water, lemon juice and sugar in a heavy-based pan until the sugar dissolves; simmer gently ~2 min.",
        "Cool the syrup, then refrigerate to +5 °C.",
        "Whip the chilled egg white to a white foam and combine with the syrup."],
       notes="Makes ≈300 ml."),
]


def all_recipes():
    custom = load_json(F_CUSTOM, [])
    for r in custom:
        r["builtin"] = False
    return BUILTIN + custom


# --------------------------------------------------------------------------
# Step generation
# --------------------------------------------------------------------------
def ing_line(ing, idx, cup):
    m, v = amount_texts(ing, idx, cup)
    s = f"**{ing['name']}**" + (f" — {m}" if m else "")
    if v and v != "—":
        s += f" (≈ {v})"
    if ing.get("note"):
        s += f" — _{ing['note']}_"
    return s


def blade_enabled(r):
    return r["blade"] if r.get("blade") is not None else r["program"] in BLADE_DEFAULT


def build_steps(r, idx, size, crust, cup):
    p = r["program"]
    ings = r["ingredients"]
    S = list(r.get("prep", []))
    by = lambda *w: [i for i in ings if i.get("where", "pan") in w]

    if p == 15:
        S += ["Fit the gearbox (black drive wheel up) in the heating compartment and turn clockwise to lock — it must not wobble.",
              "With gloves (never wet hands!), take the ice bucket from the freezer, set it on the gearbox and turn clockwise to lock. Push the stirring paddle onto the centre shaft.",
              "Plug in. MENU → **15 Ice Cream**.",
              f"+TIME/−TIME → **{r.get('time_default') or 25} min**, then tap START/STOP to confirm.",
              "Tap START/STOP again to start — and **Start timer** in the app.",
              "As soon as the paddle turns, briefly stir the chilled mix (≤ 300–350 ml) and pour it in slowly. Close the lid.",
              "At the signal: unplug, put gloves on, unlock and lift out the ice bucket.",
              "Scoop out with a plastic spoon into an airtight container; freeze ≥ ½ h for a firmer texture.",
              "Clean the bucket with lukewarm water only — never heat it."]
        return S + r.get("post", [])

    S.append("Place the machine on a stable, level, heat-resistant surface: ≥ 30 cm clearance at the sides, ≥ 1 m free above.")
    if p == 18:
        S.append("Fit the bread pan: lower it onto the coupling, turn until it drops, then turn **clockwise** to lock.")
    elif p == 10:
        S.append("Push the kneading paddle onto the drive shaft, then fit the pan and turn **clockwise** to lock.")
    else:
        S.append("Fit the bread pan: lower it onto the coupling, turn until it drops, then turn **clockwise** to lock. "
                 "Lightly grease the drive shaft with oil/margarine and push the kneading paddle on.")

    if p == 18:
        for i in by("pan"):
            S.append("Pour into the pan: " + ing_line(i, idx, cup))
        S += ["Stir the milk and yoghurt evenly. Close the lid and plug in.",
              "MENU → **18 Yoghurt** (only this program — others can boil the milk over).",
              f"+TIME/−TIME → **{(r.get('time_default') or 480) / 60:g} h** (8–12 h, 30-min steps).",
              "Tap START/STOP — and **Start timer** in the app.",
              "At the signal: let it cool, pour off the whey (keep it for a drink), refrigerate."]
        return S + r.get("post", [])

    if p == 10:
        fruit = [i for i in by("pan") if "sugar" not in i["name"].lower()]
        sugar = [i for i in by("pan") if "sugar" in i["name"].lower()]
        for i in fruit:
            S.append("Pour into the pan (fruit first): " + ing_line(i, idx, cup))
        for i in sugar:
            S.append("Then add on top: " + ing_line(i, idx, cup))
        S += ["Make sure the outside of the pan is clean and dry, lock it in, close the lid, plug in.",
              "MENU → **10 Jam** (no other settings possible).",
              "Tap START/STOP — and **Start timer** in the app. Enter the time the LCD shows.",
              "At the signal: oven gloves, unlock (anticlockwise) and lift out the pan — hold it level, it's very hot and runny.",
              "Pour carefully into heat-resistant jars and let cool. Setting can take several hours."]
        return S + r.get("post", [])

    if p in (11, 16):
        S += [f"MENU → **{p} {PROGRAMS[p]}**, set the time with +TIME/−TIME, confirm with START/STOP, then START — and **Start timer** in the app."]
        return S + r.get("post", [])

    # Programs that load ingredients into the pan
    S.append("All ingredients at room temperature (20–25 °C).")
    hollow_done = False
    for i in by("pan"):
        if not hollow_done and i.get("d") in ("yeast", "baking_powder", "baking_soda"):
            S.append("Make a shallow hollow in the flour for the yeast/raising agent — keep it away from salt and liquids.")
            hollow_done = True
        S.append("Add to pan: " + ing_line(i, idx, cup))

    box = by("box", "box_or_beep")
    if box:
        alt = " (or add them by hand at the add-in beep instead)" if by("box_or_beep") else ""
        S.append("Push the inner flap of the ingredients box closed, open the slider, add: "
                 + "; ".join(ing_line(i, idx, cup) for i in box)
                 + ", close the slider" + alt + ". Dry, coarse items only — no powder or wet ingredients.")
    S.append("Close the lid gently and plug in.")

    setting = f"MENU → **{p} {PROGRAMS[p]}**"
    if p in WEIGHT_PROGS and size in SIZES3:
        setting += f" · WEIGHT → **{size}**"
    if p in CRUST_PROGS:
        setting += f" · BURNING COLOR → **{crust}**"
    S.append(setting + ".")
    if r.get("stage1_steps"):
        S += r["stage1_steps"]
    else:
        if p != 17:
            S.append("Read the total time on the LCD and enter it in the timer box below.")
        S.append("Tap START on the machine and **Start timer** in the app at the same moment.")

    beep = by("beep")
    if beep:
        S.append("When the machine beeps during kneading, briefly pause (tap START/STOP; it auto-resumes after 3 min) and add directly into the pan: "
                 + "; ".join(ing_line(i, idx, cup) for i in beep))
    for i in by("timed"):
        S.append(f"At ~{i.get('at_min') or 0:g} min after start (app alerts): add " + ing_line(i, idx, cup))
    if blade_enabled(r) and p in BREAD_PROGS:
        S.append("At the paddle alert (ferment icon, phase 2): **unplug** (max 10 min!), lift out the pan, take the dough out "
                 "with floured hands, pull off the paddle, put the dough back, lock the pan, close the lid, plug back in — the program resumes.")
    late = by("lateknead")
    if late:
        S.append("In the last few minutes of kneading (app alerts ~5 min before kneading is due to end): open the lid "
                 "and add " + "; ".join(ing_line(i, idx, cup) for i in late)
                 + ". If kneading has already stopped, knead them in by hand when you shape the dough.")
    pre = by("prebake")
    if pre:
        S.append("Just before the bake phase starts (app alerts ~2 min ahead): open the lid briefly, then "
                 + "; ".join(ing_line(i, idx, cup) for i in pre)
                 + ". Close the lid straight away.")
    for i in by("midbake"):
        S.append("Halfway through the bake (app alerts): open the lid briefly, brush the top of the loaf with "
                 + ing_line(i, idx, cup) + " using a pastry brush, and close the lid straight away.")

    for b in r.get("batches", []):
        S += b.get("before", [])
        for i in by(b["where"]):
            S.append("Add to pan: " + ing_line(i, idx, cup))
        S += b.get("after", [])
    st2 = r.get("stage2")
    if st2:
        S += st2.get("between", [])
        for i in by("stage2"):
            if i.get("d") in ("yeast", "baking_powder", "baking_soda"):
                S.append("Make a shallow hollow in the flour for the yeast — keep it away from salt and liquids.")
            S.append("Add to pan: " + ing_line(i, idx, cup))
        p = st2["program"]
        S.append("Lock the pan in, close the lid gently and plug in.")
        setting = f"MENU → **{p} {PROGRAMS[p]}**"
        if p in WEIGHT_PROGS and size in SIZES3:
            setting += f" · WEIGHT → **{size}**"
        if p in CRUST_PROGS:
            setting += f" · BURNING COLOR → **{crust}**"
        S.append(setting + ". Enter the LCD time in the **" + st2["timer_label"] + "** timer below, then tap "
                 "START and start the timer at the same moment.")
        if p in BREAD_PROGS and r.get("blade", True) is not False:
            S.append("At the paddle alert (ferment icon, phase 2): **unplug** (max 10 min!), lift out the pan, take "
                     "the dough out with floured hands, pull off the paddle, put the dough back, lock the pan, close "
                     "the lid, plug back in — the program resumes.")


    if p in BREAD_PROGS:
        S += ["At the end: 10 beeps, LCD shows 0:00, keep-warm runs up to 60 min — take the bread out during keep-warm.",
              "Unplug. With oven gloves open the lid (steam!), turn the pan **anticlockwise** and lift it out.",
              "Stand the pan on a heat-proof surface, loosen the sides with a soft plastic spatula, turn it over a rack and shake gently.",
              "Rinse the pan with warm water straight away. If the paddle is in the loaf, remove it with the hook.",
              "Cool 15–30 min before slicing. Too pale / underbaked? Use program 16 Bake for a few extra minutes."]
    elif p == 9:
        S += ["When the 10 beeps sound: switch off at once (hold START/STOP 3 s) — no keep-warm for cakes.",
              "Leave the cake ~15 min in the machine with the lid closed and the ingredients-box slider open.",
              "Then take the pan out (oven gloves) and leave the cake in the pan another ~15 min.",
              "Turn out onto a cake rack and cool completely before icing or filling."]
    elif p == 14:
        S += ["When the program ends, unplug and take the dough out for shaping."]
    elif p in (12, 13):
        S += ["When the program ends, unplug and remove the mixture/dough."]
    return S + r.get("post", [])


# --------------------------------------------------------------------------
# Runs (timer) & events
# --------------------------------------------------------------------------
def build_events(r, phases, idx, cup, blade_on):
    p = r["program"]
    ev = []
    total = sum(ph["sec"] for ph in phases)

    def add(at, title, msg, kind="info"):
        ev.append({"id": uuid.uuid4().hex[:8], "at": int(at), "title": title, "msg": msg, "kind": kind})

    for i in r["ingredients"]:
        if i.get("where") == "timed":
            m, v = amount_texts(i, idx, cup)
            add((i.get("at_min") or 0) * 60, f"Add {i['name']}",
                f"Add {i['name']}: {m}" + (f" (≈ {v})" if v and v != "—" else ""), "add")
    for e in r.get("events", []):
        add(float(e.get("min", 0)) * 60, "Reminder", e.get("msg", ""), "add")
    lates = [i for i in r["ingredients"] if i.get("where") == "lateknead"]
    if lates:
        cum, end = 0, None
        for ph in phases:
            if ph["name"].lower().startswith("knead"):
                end = cum + ph["sec"]
            cum += ph["sec"]
        if end is not None:
            add(max(0, end - 300), "Add " + ", ".join(i["name"].split(",")[0].lower() for i in lates),
                "Last minutes of kneading: open the lid and add "
                + ", ".join(i["name"].lower() for i in lates) + ".", "add")
    pres = [i for i in r["ingredients"] if i.get("where") == "prebake"]
    if pres:
        cum = 0
        for ph in phases:
            if ph["name"].lower().startswith("bake"):
                add(max(0, cum - 120), "Glaze & topping before baking",
                    "Baking starts in ~2 min: open the lid briefly, "
                    + "; ".join(i["name"][0].lower() + i["name"][1:] for i in pres) + ", close the lid.", "add")
                break
            cum += ph["sec"]
    mids = [i for i in r["ingredients"] if i.get("where") == "midbake"]
    if mids:
        cum = 0
        for ph in phases:
            if ph["name"].lower().startswith("bake"):
                add(cum + ph["sec"] / 2, "Brush the loaf",
                    "Halfway through baking: open the lid briefly, brush the top with "
                    + ", ".join(i["name"].lower() for i in mids) + ", close the lid.", "add")
                break
            cum += ph["sec"]

    if blade_on and p in BREAD_PROGS:
        cum, target = 0, None
        names = [ph["name"] for ph in phases]
        last_knead = max([k for k, n in enumerate(names) if n.lower().startswith("knead")], default=-1)
        for k, ph in enumerate(phases):
            if ph["name"] == "Ferment 2":
                target = cum
                break
            cum += ph["sec"]
        if target is None:  # fall back: first ferment after the last knead
            cum = 0
            for k, ph in enumerate(phases):
                if k > last_knead and ph["name"].lower().startswith("ferment"):
                    target = cum
                    break
                cum += ph["sec"]
        if target is not None:
            add(target, "Remove the kneading paddle",
                "Unplug (max 10 min), take out pan & dough, pull the paddle, dough back in, lock, plug in. "
                "Press Pause in the app while unplugged.", "blade")

    cum = 0
    for ph in phases:
        if ph["name"].lower().startswith("bake") and p != 16:
            add(cum, "Baking started", "Keep the lid closed from now on.", "info")
        cum += ph["sec"]

    if p in BREAD_PROGS:
        add(total, "Bread is ready", "Keep-warm is on (max 60 min). Unplug and take the bread out.", "done")
        add(total + 50 * 60, "Keep-warm ends in 10 min", "Take the bread out to avoid a soggy loaf.", "info")
    elif p == 9:
        add(total, "Cake is ready", "Switch off now (hold START/STOP 3 s). Leave 15 min, lid closed, box slider open.", "done")
        add(total + 15 * 60, "Take the cake pan out", "Oven gloves — leave the cake in the pan another 15 min.", "info")
        add(total + 30 * 60, "Turn out the cake", "Turn out onto a rack and cool completely.", "info")
    elif p == 15:
        add(total, "Ice cream is ready", "Unplug, gloves on, lift out the bucket, scoop with a plastic spoon.", "done")
    elif p == 18:
        add(total, "Yoghurt is ready", "Let it cool, pour off the whey, refrigerate.", "done")
    elif p == 10:
        add(total, "Jam is ready", "Oven gloves; hold the pan level, pour into heat-proof jars.", "done")
    else:
        add(total, r.get("done_title") or f"{PROGRAMS.get(p, 'Program')} finished",
            r.get("done_msg") or ("Unplug and remove the contents." if not r.get("done_title") else "Time for the next step."),
            "done")
    return sorted(ev, key=lambda e: e["at"])


def start_run(r, size, idx, crust, phases, blade_on, cup):
    run = {
        "recipe": r["name"], "size": size, "crust": crust, "program": r["program"],
        "phases": phases,
        "keep_warm": KEEP_WARM_MIN * 60 if r["program"] in BREAD_PROGS else 0,
        "events": build_events(r, phases, idx, cup, blade_on),
        "start": time.time(), "paused_total": 0.0, "pause_start": None,
        "acked": [], "pushed": [],
    }
    with LOCK:
        save_json(F_RUN, run)


def elapsed(run, now=None):
    now = now or time.time()
    ref = run["pause_start"] or now
    return max(0.0, ref - run["start"] - run["paused_total"])


def program_total(run):
    return sum(ph["sec"] for ph in run["phases"])


def phase_at(run, e):
    cum = 0
    for k, ph in enumerate(run["phases"]):
        if e < cum + ph["sec"]:
            return k, ph["name"], cum, ph["sec"]
        cum += ph["sec"]
    if run["keep_warm"] and e < cum + run["keep_warm"]:
        return len(run["phases"]), "Keep warm", cum, run["keep_warm"]
    return None, "Finished", cum, 0


def set_elapsed(run, target):
    """Re-anchor the run so elapsed == target; auto-ack events skipped over."""
    ref = run["pause_start"] or time.time()
    run["start"] = ref - run["paused_total"] - target
    for ev in run["events"]:
        if ev["at"] < target - 30:
            if ev["id"] not in run["acked"]:
                run["acked"].append(ev["id"])
            if ev["id"] not in run["pushed"]:
                run["pushed"].append(ev["id"])


def hms(sec, show_h=True):
    sec = int(max(0, round(sec)))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if (h or show_h) else f"{m:02d}:{s:02d}"


def parse_hmm(txt):
    txt = (txt or "").strip().replace(".", ":")
    if not txt:
        return None
    try:
        if ":" in txt:
            h, m = txt.split(":", 1)
            return int(h) * 60 + int(m)
        return int(txt)  # plain minutes
    except ValueError:
        return None


# --------------------------------------------------------------------------
# Notifications
# --------------------------------------------------------------------------
def push_ntfy(settings, title, msg):
    topic = (settings.get("ntfy_topic") or "").strip()
    if not topic:
        return False
    url = settings.get("ntfy_server", "https://ntfy.sh").rstrip("/") + "/" + topic
    req = urllib.request.Request(url, data=msg.encode("utf-8"), method="POST", headers={
        "Title": title.encode("latin-1", "replace").decode("latin-1"),
        "Priority": "high", "Tags": "bread"})
    urllib.request.urlopen(req, timeout=10).read()
    return True


def _push_loop(lock):
    """Background thread: sends ntfy pushes even if no browser is open."""
    while True:
        time.sleep(5)
        try:
            settings = get_settings()
            if not settings.get("ntfy_topic"):
                continue
            with lock:
                run = load_json(F_RUN, None)
            if not run:
                continue
            e = elapsed(run)
            due = [ev for ev in run["events"] if ev["at"] <= e and ev["id"] not in run["pushed"]]
            for ev in due:
                try:
                    push_ntfy(settings, ev["title"], ev["msg"])
                except Exception:
                    pass
                with lock:
                    cur = load_json(F_RUN, None)
                    if cur and cur["start"] == run["start"] and ev["id"] not in cur["pushed"]:
                        cur["pushed"].append(ev["id"])
                        save_json(F_RUN, cur)
        except Exception:
            pass


@st.cache_resource
def start_push_worker():
    t = threading.Thread(target=_push_loop, args=(LOCK,), daemon=True)
    t.start()
    return t


start_push_worker()


@st.cache_data
def beep_wav():
    rate = 22050
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        frames = bytearray()
        for rep in range(3):
            for i in range(int(rate * 0.25)):
                env = min(1, i / 200, (rate * 0.25 - i) / 200)
                frames += struct.pack("<h", int(16000 * env * math.sin(2 * math.pi * 880 * i / rate)))
            frames += b"\x00\x00" * int(rate * 0.15)
        w.writeframes(bytes(frames))
    return buf.getvalue()


def html_js(code, height):
    """Run an HTML/JS snippet in an iframe (st.iframe on new Streamlit, components.html otherwise)."""
    if hasattr(st, "iframe"):
        st.iframe(code, height=max(1, height))
    else:
        components.html(code, height=height)


def browser_notify(title, body):
    t, b = json.dumps(title), json.dumps(body)
    html_js(f"""<script>
    (function(){{
      try {{
        var N = window.Notification || (window.parent && window.parent.Notification);
        if (N && N.permission === "granted") {{ new N({t}, {{body: {b}}}); }}
      }} catch(e) {{}}
      try {{ (navigator.vibrate || function(){{}}).call(navigator, [300,150,300,150,600]); }} catch(e) {{}}
    }})();
    </script>""", 1)


# --------------------------------------------------------------------------
# UI
# --------------------------------------------------------------------------
st.set_page_config(page_title="Bread Maker Companion", page_icon="🍞", layout="wide")
SET = get_settings()
CUP = SET["cup_ml"]

st.markdown("""<style>
/* Touch-friendly controls */
.stButton button, .stDownloadButton button, .stFormSubmitButton button {min-height: 3rem; font-size: 1.05rem;}
[data-testid="stCheckbox"] label {padding: .45rem 0; line-height: 1.45; align-items: flex-start;}
[data-testid="stRadio"] label {padding: .2rem .35rem .2rem 0;}
@media (max-width: 640px) {
  .block-container, [data-testid="stMainBlockContainer"] {padding: 2.6rem .75rem 4rem .75rem;}
  h2 {font-size: 1.45rem;} h3 {font-size: 1.2rem;}
}
/* Ingredient cards */
.ing-grid {display: grid; grid-template-columns: repeat(auto-fill, minmax(230px, 1fr)); gap: .55rem; margin: .4rem 0 .6rem;}
.ing {border: 1px solid rgba(128,128,128,.35); border-radius: .6rem; padding: .55rem .75rem; background: rgba(128,128,128,.06);}
.ing-name {font-weight: 600; margin-bottom: .15rem;}
.ing-m {font-size: 1.15rem; font-weight: 700; font-variant-numeric: tabular-nums;}
.ing-v {opacity: .85;}
.ing-when {font-size: .82rem; opacity: .7; margin-top: .2rem;}
.ing-note {font-size: .82rem; font-style: italic; opacity: .75; margin-top: .15rem;}
/* Timer card */
.tcard {border: 1px solid rgba(128,128,128,.35); border-radius: .8rem; padding: .9rem 1rem; text-align: center;
        background: rgba(128,128,128,.06); margin-bottom: .6rem;}
.tcard .t-phase {font-size: 1.5rem; font-weight: 700;}
.tcard .t-big {font-size: 3.4rem; font-weight: 800; line-height: 1.05; font-variant-numeric: tabular-nums;}
.tcard .t-sub {opacity: .8; font-variant-numeric: tabular-nums;}
.mini {font-size: .92rem; opacity: .85; margin: -.3rem 0 .4rem;}
</style>""", unsafe_allow_html=True)

PAGES = ["🍞 Bake", "⏱️ Timer", "✏️ Recipes", "⚙️ Settings"]

# Keep Bake-page choices (recipe, size, method, checklist ticks...) when visiting other pages:
# Streamlit drops the state of widgets that aren't rendered, unless it is re-assigned each run.
_KEEP = ("cat_sel", "recipe_sel|", "size|", "crust|", "method|", "stage|", "chk|")
for _k in list(st.session_state.keys()):
    if isinstance(_k, str) and _k.startswith(_KEEP):
        st.session_state[_k] = st.session_state[_k]
if st.session_state.get("page") not in PAGES:
    st.session_state.page = PAGES[0]
if st.session_state.pop("goto", None):
    st.session_state.page = PAGES[1]

# Navigation at the top of the page (the sidebar is hidden behind a menu on phones)
st.radio("Page", PAGES, key="page", horizontal=True, label_visibility="collapsed")

with st.sidebar:
    st.title("Bread Maker")
    st.caption("Gastroback Design Bread Maker Advanced · 62823")

    @st.fragment(run_every=2)
    def sidebar_status():
        run = load_run()
        if not run:
            st.caption("No timer running.")
            return
        e = elapsed(run)
        _, name, cum, dur = phase_at(run, e)
        st.markdown(f"**{run['recipe']}**  \n{name}"
                    + (f" · {hms(cum + dur - e, False)} left" if dur else "")
                    + ("  \n⏸️ paused" if run["pause_start"] else ""))
        st.progress(min(1.0, e / max(1, program_total(run))))

    sidebar_status()


def ack_event(ev_id):
    update_run(lambda r_: r_["acked"].append(ev_id) if ev_id not in r_["acked"] else None)


@st.fragment(run_every=1)
def alert_bar():
    run = load_run()
    if not run:
        return
    e = elapsed(run)
    if st.session_state.get("page") != PAGES[1]:
        _, name, cum, dur = phase_at(run, e)
        st.markdown(f'<div class="mini">⏱️ <b>{html.escape(run["recipe"])}</b> · {html.escape(name)}'
                    + (f" · {hms(cum + dur - e, False)} left" if dur else "")
                    + (" · ⏸️ paused" if run["pause_start"] else "") + "</div>", unsafe_allow_html=True)
    due = [ev for ev in run["events"] if ev["at"] <= e and ev["id"] not in run["acked"]]
    fired = st.session_state.setdefault("fired", {})
    ring = False
    for ev in due:
        icon = {"blade": "🔪", "add": "➕", "done": "✅"}.get(ev["kind"], "🔔")
        st.error(f"{icon} **{ev['title']}** — {ev['msg']}")
        st.button(f"✔️ Done: {ev['title']}", key="ack_" + ev["id"], width="stretch",
                  on_click=ack_event, args=(ev["id"],))
        if ev["id"] not in fired:
            fired[ev["id"]] = time.time()
            st.toast(f"{icon} {ev['title']}")
            browser_notify(ev["title"], ev["msg"])
        if time.time() - fired[ev["id"]] < 8:
            ring = True
    if ring and SET.get("sound", True):
        st.audio(beep_wav(), format="audio/wav", autoplay=True)


alert_bar()


# ---------------- Bake page ----------------
def page_bake():
    recipes = all_recipes()
    cats = ["All"] + sorted({r["category"] for r in recipes})
    c1, c2 = st.columns([1, 3])
    cat = c1.selectbox("Category", cats, key="cat_sel")
    pool = [r for r in recipes if cat == "All" or r["category"] == cat]
    labels = [("★ " if not r["builtin"] else "") + f"{r['name']}  ·  {r['category']}" for r in pool]
    sel = c2.selectbox("Recipe", range(len(pool)), format_func=lambda i: labels[i], key=f"recipe_sel|{cat}")
    r = pool[sel]
    if r.get("variants"):
        method = st.radio("Method", list(r["variants"]), horizontal=True, key=f"method|{r['name']}")
        r = {**r, **r["variants"][method]}
    p = r["program"]

    st.subheader(r["name"])
    st.caption(f"Program {p} — {PROGRAMS[p]}" + ("  ·  your recipe" if not r["builtin"] else ""))

    c1, c2 = st.columns(2)
    sizes = r["sizes"]
    size = c1.radio("Loaf size / batch", sizes, horizontal=True, key=f"size|{r['name']}") \
        if len(sizes) > 1 else sizes[0]
    if len(sizes) == 1:
        c1.markdown(f"**Size:** {size}")
    idx = sizes.index(size)
    crust = "medium"
    if p in CRUST_PROGS:
        ck = f"crust|{r['name']}"
        if ck not in st.session_state:
            st.session_state[ck] = "medium"
        crust = c2.radio("Crust colour", ["light", "medium", "dark"], horizontal=True, key=ck)
    if r.get("notes"):
        st.info(r["notes"])

    # Ingredient cards (wrap to one column on phones)
    st.markdown("### Ingredients")
    cards, total = [], 0.0
    for ing in r["ingredients"]:
        m, v = amount_texts(ing, idx, CUP)
        when = WHERE.get(ing.get("where", "pan"), "")
        if ing.get("where") == "timed" and ing.get("at_min") is not None:
            when += f" ({ing['at_min']:g} min)"
        cards.append(
            f'<div class="ing"><div class="ing-name">{html.escape(ing["name"])}</div>'
            f'<div><span class="ing-m">{html.escape(m)}</span>'
            + (f' <span class="ing-v">≈ {html.escape(v)}</span>' if v and v != "—" else "")
            + f'</div><div class="ing-when">{html.escape(when)}</div>'
            + (f'<div class="ing-note">{html.escape(ing["note"])}</div>' if ing.get("note") else "")
            + "</div>")
        if ing.get("where", "pan") not in ("prep", "after") or p in (9, 15):
            total += est_grams(ing, idx)
    st.markdown('<div class="ing-grid">' + "".join(cards) + "</div>", unsafe_allow_html=True)
    cup_name = {240: "US cup 240 ml", 250: "metric cup 250 ml"}.get(CUP, f"cup {CUP} ml")
    st.caption(f"Volumes are approximate (spoon-and-level; {cup_name}; tsp 5 ml, tbsp 15 ml). "
               "Weighing is more accurate — the manual recommends scales for solids.")
    if p in LOADED_PROGS | {9}:
        msg = f"Estimated total in the pan ≈ {total:.0f} g."
        if total > 1000:
            st.warning(msg + " The manual's maximum is 1000 g in total — risk of overflow onto the heating element.")
        else:
            st.caption(msg + " (Max 1000 g.)")
    if p in BREAD_PROGS | {9} and any(("egg" in i["name"].lower() or "milk" == i.get("d") or "cream" == i.get("d"))
                                      and i.get("where", "pan") in ("pan", "timed") for i in r["ingredients"]):
        st.caption("⚠️ Contains egg/milk — don't use the machine's delay timer with this recipe.")

    # Steps checklist
    st.markdown("### Steps")
    steps = build_steps(r, idx, size, crust, CUP)
    base = f"chk|{r['name']}|{size}"
    done = 0
    for k, s in enumerate(steps):
        if st.checkbox(f"{k + 1}. {s}", key=f"{base}|{k}"):
            done += 1
    st.progress(done / max(1, len(steps)), text=f"{done}/{len(steps)} steps done")
    if st.button("Reset checklist"):
        for k in range(len(steps)):
            st.session_state.pop(f"{base}|{k}", None)
        st.rerun()

    # Timer setup
    st.markdown("### Timer")
    stages = r.get("stages")
    if stages:
        stage_labels = [st_["label"] for st_ in stages]
        k = stage_labels.index(st.radio("Timer for", stage_labels, horizontal=True, key=f"stage|{r['name']}"))
        stg = stages[k]
        r_t = {**r, "program": stg["program"], "time_default": stg.get("time_default", r.get("time_default")),
               "name": f"{r['name']} — {stg['label']}", "ingredients": r["ingredients"] if k == 0 else [],
               "done_title": stg.get("done_title"), "done_msg": stg.get("done_msg"), "events": []}
        if stg.get("caption"):
            st.caption(stg["caption"])
        if stg.get("fixed_min") or stg.get("range_h"):
            mins = stg.get("fixed_min")
            if stg.get("range_h"):
                lo, hi, dflt = stg["range_h"]
                hrs = st.select_slider("Rest time", options=list(range(lo, hi + 1)), value=dflt,
                                       format_func=lambda h: f"{h} h", key=f"fxh|{r_t['name']}")
                mins = hrs * 60
            stg = {**stg, "fixed_min": mins}
            ph = [{"name": stg.get("phase_name", stg["label"]), "sec": int(mins * 60)}]
            existing = load_run()
            ok = True
            if existing:
                ok = st.checkbox(f"Replace the running timer ({existing['recipe']})", key=f"fxrep|{r_t['name']}")
            lab = f"{mins / 60:g} h" if mins >= 60 else f"{mins:g} min"
            if st.button(f"▶️ Start {lab} timer", type="primary", width="stretch",
                         disabled=not ok, key=f"fxgo|{r_t['name']}"):
                start_run(r_t, size, idx, crust, ph, False, CUP)
                st.session_state.goto = True
                st.rerun()
        else:
            timer_setup(r_t, size, idx, crust)
    else:
        timer_setup(r, size, idx, crust)


def timer_setup(r, size, idx, crust, key_prefix="t"):
    p = r["program"]
    phases = None
    calib = load_json(F_CALIB, {"profiles": {}, "lcd": {}})
    lcd_key = f"{p}|{size}|{crust}"
    if p in USER_TIME:
        label, opts, default = USER_TIME[p]
        d = r.get("time_default") or default
        if d not in opts:
            opts = sorted(set(opts + [d]))
        fmt = (lambda m: f"{m / 60:g} h") if p == 18 else (lambda m: f"{m} min")
        t = st.select_slider("Processing time (set the same with +TIME/−TIME on the machine)",
                             options=opts, value=d, format_func=fmt, key=f"{key_prefix}ut|{r['name']}")
        phases = [{"name": label, "sec": t * 60}]
    elif p == 17:
        prof = phase_profile(17)
        tot = sum(m for _, m in prof)
        st.caption(f"Homemade program phases (edit in Settings to match what you programmed): total {tot} min.")
        phases = scale_profile(prof, tot)
    else:
        prev = calib.get("lcd", {}).get(lcd_key)
        txt = st.text_input("Total time shown on the LCD after selecting program/size/colour (h:mm)",
                            value=f"{prev // 60}:{prev % 60:02d}" if prev else "",
                            placeholder="e.g. 3:00", key=f"{key_prefix}lcd|{r['name']}|{size}|{crust}")
        tot = parse_hmm(txt)
        if txt and tot is None:
            st.error("Use h:mm, e.g. 2:55")
        if tot:
            phases = scale_profile(phase_profile(p), tot)
        st.caption("The manual doesn't publish per-phase times for this program, so the app spreads the LCD total "
                   "over the standard phase sequence (estimate). Use 'Sync' on the Timer page if the machine's "
                   "phase icon changes earlier/later; calibrate phases in Settings.")

    blade_on = False
    if p in BREAD_PROGS:
        blade_on = st.checkbox("Alert me to remove the kneading paddle (start of Ferment 2)",
                               value=blade_enabled(r), key=f"{key_prefix}bl|{r['name']}|{p}")

    if phases:
        with st.expander(f"Phase plan · total {hms(sum(ph['sec'] for ph in phases))}"):
            st.dataframe(pd.DataFrame([{"Phase": ph["name"], "Duration": hms(ph["sec"])} for ph in phases]),
                         hide_index=True, width="stretch")

    existing = load_run()
    confirm = True
    if existing:
        confirm = st.checkbox(f"Replace the running timer ({existing['recipe']})", key=f"{key_prefix}rep|{r['name']}")
    if st.button("▶️ Start timer — press START on the machine now", type="primary", width="stretch",
                 disabled=not phases or not confirm, key=f"{key_prefix}go|{r['name']}"):
        if p not in USER_TIME and p != 17:
            calib.setdefault("lcd", {})[lcd_key] = tot
            save_json(F_CALIB, calib)
        start_run(r, size, idx, crust, phases, blade_on, CUP)
        st.session_state.goto = True
        st.rerun()


# ---------------- Timer page ----------------
def page_timer():
    run = load_run()
    if not run:
        st.info("No timer running. Pick a recipe on the Bake page and start it there.")
        with st.expander("Quick timer without a recipe"):
            p = st.selectbox("Program", list(PROGRAMS), format_func=lambda k: f"{k} {PROGRAMS[k]}")
            dummy = R(f"Program {p} {PROGRAMS[p]}", "Quick", p, SIZES3 if p in WEIGHT_PROGS else SINGLE, [])
            dummy["builtin"] = True
            size = st.radio("WEIGHT", dummy["sizes"], horizontal=True) if len(dummy["sizes"]) > 1 else dummy["sizes"][0]
            timer_setup(dummy, size, dummy["sizes"].index(size), "medium", key_prefix="q")
        return

    @st.fragment(run_every=1)
    def live():
        run = load_run()
        if not run:
            st.rerun()
        e = elapsed(run)
        tot = program_total(run)
        k, name, cum, dur = phase_at(run, e)
        st.markdown(f"#### {run['recipe']}")
        st.caption(f"Program {run['program']} {PROGRAMS.get(run['program'], '')} · {run['size']}"
                   + (f" · {run['crust']} crust" if run["program"] in CRUST_PROGS else ""))
        big = hms(cum + dur - e, show_h=(cum + dur - e) >= 3600) if dur else "—"
        st.markdown(
            f'<div class="tcard"><div class="t-phase">{html.escape(name)}'
            + (" ⏸️" if run["pause_start"] else "") + "</div>"
            f'<div class="t-big">{big}</div>'
            f'<div class="t-sub">left in phase · LCD {hms(max(0, tot - e))}</div></div>',
            unsafe_allow_html=True)
        st.progress(min(1.0, e / max(1, tot)), text=f"Elapsed {hms(e)} of {hms(tot)}")
        if run["pause_start"]:
            pz = time.time() - run["pause_start"]
            if pz > 8 * 60:
                st.error(f"Paused for {hms(pz, False)} — plug back in now! After 10 min unplugged the program is cancelled.")
            else:
                st.warning(f"Paused {hms(pz, False)}. Unplugged: max 10 min. START/STOP pause: machine auto-resumes after 3 min.")
        nxt = [ev for ev in run["events"] if ev["at"] > e]
        if nxt:
            st.info(f"Next alert in **{hms(nxt[0]['at'] - e)}** — {nxt[0]['title']}")

    live()

    c1, c2 = st.columns(2)
    if run["pause_start"]:
        if c1.button("▶️ Resume (plugged back in / resumed)", type="primary", width="stretch"):
            def f(r_):
                r_["paused_total"] += time.time() - r_["pause_start"]
                r_["pause_start"] = None
            update_run(f)
            st.rerun()
    else:
        if c1.button("⏸️ Pause (unplugged / paused)", type="primary", width="stretch"):
            update_run(lambda r_: r_.__setitem__("pause_start", time.time()))
            st.rerun()
    if c2.button("⏹️ Stop timer", width="stretch"):
        clear_run()
        st.session_state.pop("fired", None)
        st.rerun()

    with st.expander("Phase schedule & alerts"):
        @st.fragment(run_every=5)
        def schedule():
            run = load_run()
            if not run:
                return
            e = elapsed(run)
            rows, c = [], 0
            for ph in run["phases"] + ([{"name": "Keep warm", "sec": run["keep_warm"]}] if run["keep_warm"] else []):
                status = "✅" if e >= c + ph["sec"] else ("▶️" if e >= c else "")
                rows.append({"": status, "Phase": ph["name"], "Starts": hms(c), "Length": hms(ph["sec"])})
                c += ph["sec"]
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
            st.dataframe(pd.DataFrame([{"At": hms(ev["at"]), "Alert": ev["title"],
                                        "": "✅" if ev["id"] in run["acked"] else ("🔔" if ev["at"] <= e else "")}
                                       for ev in run["events"]]), hide_index=True, width="stretch")
        schedule()

    with st.expander("Sync with the machine"):
        st.caption("If the app drifts from the machine, re-anchor it to what the machine shows.")
        a, b = st.columns(2)
        rem = a.text_input("LCD remaining time (h:mm)", placeholder="e.g. 1:42")
        if a.button("Sync to LCD", width="stretch"):
            m = parse_hmm(rem)
            if m is None:
                a.error("Use h:mm")
            else:
                update_run(lambda r_: set_elapsed(r_, max(0, program_total(r_) - m * 60)))
                st.rerun()
        names = [ph["name"] for ph in run["phases"]]
        ph = b.selectbox("Machine has just entered phase", names)
        if b.button("Sync to phase start", width="stretch"):
            def f(r_):
                c = 0
                for x in r_["phases"]:
                    if x["name"] == ph:
                        break
                    c += x["sec"]
                set_elapsed(r_, c)
            update_run(f)
            st.rerun()


# ---------------- Custom recipes page ----------------
def recipe_to_df(r):
    rows = []
    for ing in r["ingredients"]:
        row = {"Ingredient": ing["name"], "Unit": ing["unit"]}
        for k, s in enumerate(r["sizes"]):
            a = amt_at(ing, k)
            row[s] = float(a) if isinstance(a, (int, float)) else None
        row["Converter"] = DENSITY.get(ing.get("d") or "", ("— none —",))[0]
        row["Where"] = WHERE.get(ing.get("where", "pan"), WHERE["pan"])
        row["At min"] = ing.get("at_min")
        row["Note"] = ing.get("note", "")
        rows.append(row)
    cols = ["Ingredient", "Unit"] + r["sizes"] + ["Converter", "Where", "At min", "Note"]
    return pd.DataFrame(rows, columns=cols)


def df_to_ings(df, sizes):
    out = []
    for _, row in df.iterrows():
        name = str(row.get("Ingredient") or "").strip()
        if not name or name == "nan":
            continue
        amts = []
        for s in sizes:
            v = row.get(s)
            amts.append(None if v is None or pd.isna(v) else float(v))
        conv = row.get("Converter")
        at = row.get("At min")
        note = row.get("Note")
        out.append({
            "name": name, "unit": row.get("Unit") or "g",
            "amt": amts if len(sizes) > 1 else amts[0],
            "d": DENS_BY_LABEL.get(conv), "where": WHERE_BY_LABEL.get(row.get("Where"), "pan"),
            "note": "" if note is None or (isinstance(note, float) and pd.isna(note)) else str(note),
            "at_min": None if at is None or pd.isna(at) else float(at),
        })
    return out


def page_custom():
    st.header("Custom recipes")
    st.caption("The ingredient grid is easiest on a larger screen; recipes you save here show up on phones too.")
    custom = load_json(F_CUSTOM, [])
    names = [r["name"] for r in custom]
    choices = ["➕ New recipe"] + [f"Edit: {n}" for n in names] + [f"Copy built-in: {r['name']}" for r in BUILTIN]
    choice = st.selectbox("Start from", choices)

    if choice.startswith("Edit: "):
        base = copy.deepcopy(custom[names.index(choice[6:])])
        editing = base["name"]
    elif choice.startswith("Copy built-in: "):
        base = copy.deepcopy(next(r for r in BUILTIN if r["name"] == choice[15:]))
        base["name"] += " (my version)"
        editing = None
    else:
        base = R("", "My recipes", 1, SIZES3, [
            I("Water", "ml", [None, None, None], "water"),
            I("Salt", "g", [None, None, None], "salt"),
            I("Bread flour", "g", [None, None, None], "bread_flour"),
            I("Yeast", "g", [None, None, None], "yeast"),
        ])
        editing = None
    ver = st.session_state.setdefault("editor_ver", 0)
    k = f"{choice}|{ver}"

    c1, c2, c3 = st.columns([2, 1, 2])
    name = c1.text_input("Name", base["name"], key="n" + k)
    category = c2.text_input("Category", base["category"], key="c" + k)
    prog = c3.selectbox("Machine program", list(PROGRAMS), index=list(PROGRAMS).index(base["program"]),
                        format_func=lambda x: f"{x} {PROGRAMS[x]}", key="p" + k)
    multi = st.checkbox("Three loaf sizes (1000 / 750 / 500 g)", value=len(base["sizes"]) > 1, key="m" + k)
    sizes = SIZES3 if multi else SINGLE

    df = recipe_to_df(base)
    if sizes != base["sizes"]:  # size set changed: keep matching columns, fill the rest from the first
        first = df[base["sizes"][0]] if base["sizes"][0] in df else None
        kept = {s: df[s] for s in base["sizes"] if s in df}
        for s in base["sizes"]:
            df = df.drop(columns=s)
        for j, s in enumerate(sizes):
            df.insert(2 + j, s, kept.get(s, first))
    st.caption("Ingredients in loading order. 'Converter' picks the density used for cups/spoons. "
               "'Where' = how it goes in; 'At min' is used for 'At a set minute'.")
    col_cfg = {
        "Ingredient": st.column_config.TextColumn(required=True, width="medium"),
        "Unit": st.column_config.SelectboxColumn(options=UNITS, required=True, width="small"),
        "Converter": st.column_config.SelectboxColumn(options=DENS_LABELS, width="medium"),
        "Where": st.column_config.SelectboxColumn(options=list(WHERE.values()), width="medium"),
        "At min": st.column_config.NumberColumn(min_value=0, max_value=900, step=1, width="small"),
        "Note": st.column_config.TextColumn(width="medium"),
    }
    for s in sizes:
        col_cfg[s] = st.column_config.NumberColumn(min_value=0, step=0.25, width="small")
    edited = st.data_editor(df, num_rows="dynamic", column_config=col_cfg, width="stretch",
                            hide_index=True, key="df" + k + str(multi))

    c1, c2 = st.columns(2)
    prep = c1.text_area("Preparation steps (one per line, before loading)", "\n".join(base.get("prep", [])), key="pr" + k)
    post = c2.text_area("Finishing steps (one per line, after the program)", "\n".join(base.get("post", [])), key="po" + k)
    notes = st.text_area("Notes", base.get("notes", ""), key="no" + k)
    c1, c2 = st.columns(2)
    blade = c1.checkbox("Paddle-removal alert by default", value=blade_enabled({**base, "program": prog}), key="b" + k)
    tdef = None
    if prog in USER_TIME:
        tdef = c2.number_input("Default processing time (min)", 1, 720, int(base.get("time_default") or USER_TIME[prog][2]), key="t" + k)
    ev_df = pd.DataFrame(base.get("events") or [], columns=["min", "msg"])
    st.caption("Extra timed alerts (minutes after start)")
    ev_ed = st.data_editor(ev_df, num_rows="dynamic", hide_index=True, key="ev" + k,
                           column_config={"min": st.column_config.NumberColumn("Minute", min_value=0),
                                          "msg": st.column_config.TextColumn("Message", width="large")})

    new = {"name": name.strip(), "category": category.strip() or "My recipes", "program": prog, "sizes": sizes,
           "ingredients": df_to_ings(edited, sizes),
           "prep": [x.strip() for x in prep.splitlines() if x.strip()],
           "post": [x.strip() for x in post.splitlines() if x.strip()],
           "notes": notes.strip(), "blade": blade, "time_default": tdef,
           "events": [{"min": float(rw["min"]), "msg": str(rw["msg"])} for _, rw in ev_ed.iterrows()
                      if not pd.isna(rw["min"]) and str(rw["msg"]).strip() and str(rw["msg"]) != "nan"]}

    b1, b2, b3 = st.columns(3)
    if b1.button("💾 Save", type="primary", width="stretch"):
        clash = new["name"] in [r["name"] for r in BUILTIN] or (new["name"] in names and new["name"] != editing)
        if not new["name"]:
            st.error("Give the recipe a name.")
        elif clash:
            st.error("That name is already used.")
        elif not new["ingredients"]:
            st.error("Add at least one ingredient.")
        else:
            custom = [r for r in custom if r["name"] != editing]
            custom.append(new)
            save_json(F_CUSTOM, custom)
            st.session_state.editor_ver += 1
            st.success(f"Saved “{new['name']}”. Find it on the Bake page (★).")
    if editing and b2.button("🗑️ Delete", width="stretch"):
        save_json(F_CUSTOM, [r for r in custom if r["name"] != editing])
        st.session_state.editor_ver += 1
        st.rerun()
    b3.download_button("⬇️ Export all custom recipes", json.dumps(custom, indent=2, ensure_ascii=False),
                       "custom_recipes.json", "application/json", width="stretch")

    with st.expander("Preview steps"):
        if new["ingredients"]:
            for j, s in enumerate(build_steps({**new, "builtin": False}, 0, sizes[0], "medium", CUP)):
                st.markdown(f"{j + 1}. {s}")
    with st.expander("Import recipes (JSON)"):
        up = st.file_uploader("custom_recipes.json", type="json")
        if up and st.button("Import (same names are replaced)"):
            try:
                data = json.load(up)
                data = data if isinstance(data, list) else [data]
                keep = {r["name"]: r for r in custom}
                for r in data:
                    if r.get("name") and r["name"] not in [b["name"] for b in BUILTIN]:
                        r.pop("builtin", None)
                        keep[r["name"]] = r
                save_json(F_CUSTOM, list(keep.values()))
                st.success(f"Imported {len(data)} recipe(s).")
            except Exception as ex:
                st.error(f"Could not import: {ex}")


# ---------------- Settings page ----------------
def page_settings():
    st.header("Settings")
    s = get_settings()
    cup_opts = {240: "US cup (240 ml)", 250: "Metric cup (250 ml)", 284: "UK imperial cup (284 ml)"}
    cup = st.radio("Cup size for conversions", list(cup_opts), format_func=cup_opts.get, horizontal=True,
                   index=list(cup_opts).index(s["cup_ml"]) if s["cup_ml"] in cup_opts else 0)
    sound = st.checkbox("Play a beep with alerts", value=s["sound"])

    st.markdown("#### Phone push notifications (optional, ntfy.sh)")
    st.caption("Install the free ntfy app on your phone, subscribe to a hard-to-guess topic name, enter it here. "
               "Pushes are sent by the app's server process, so they arrive even with the browser closed.")
    c1, c2 = st.columns(2)
    server = c1.text_input("ntfy server", s["ntfy_server"])
    topic = c2.text_input("Topic", s["ntfy_topic"], placeholder="e.g. davide-bread-7f3k")
    if st.button("Save settings", type="primary"):
        save_json(F_SETTINGS, {"cup_ml": cup, "sound": sound, "ntfy_server": server, "ntfy_topic": topic})
        st.success("Saved.")
        st.rerun()
    if topic and st.button("Send test push"):
        try:
            push_ntfy({"ntfy_server": server, "ntfy_topic": topic}, "Bread maker test", "Push notifications work.")
            st.success("Sent.")
        except Exception as ex:
            st.error(f"Failed: {ex}")

    st.markdown("#### Browser notifications")
    st.caption("Browsers only allow notifications on localhost or https. To use them from your phone, serve the app "
               "over https with Tailscale (see README: `tailscale serve --bg 8501`) and open the https://…ts.net "
               "address. On iPhone, notifications additionally need the page added to the Home Screen "
               "(Share → Add to Home Screen) and opened from there. Tap once to allow:")
    html_js("""
      <button style="padding:6px 12px;border-radius:6px;border:1px solid #888;cursor:pointer"
        onclick="var N=window.Notification||parent.Notification;
                 if(!N){document.getElementById('s').textContent=' Not supported here';return;}
                 N.requestPermission().then(function(p){document.getElementById('s').textContent=' Permission: '+p;
                   if(p==='granted'){new N('Bread maker',{body:'Notifications enabled'});}});">
        Enable browser notifications</button><span id="s" style="font-family:sans-serif"></span>""", 45)

    st.markdown("#### Phase calibration")
    st.caption("Per-program phase proportions used to split the LCD total. For 17 Homemade, enter exactly what "
               "you programmed on the machine (minutes; 0 = phase off). Time a real run and adjust once for accuracy.")
    calib = load_json(F_CALIB, {"profiles": {}, "lcd": {}})
    progs = [p for p in PROGRAMS if p in PROG_TEMPLATE or p == 17]
    p = st.selectbox("Program", progs, format_func=lambda x: f"{x} {PROGRAMS[x]}")
    prof = pd.DataFrame(phase_profile(p), columns=["Phase", "Minutes"])
    ed = st.data_editor(prof, num_rows="dynamic", hide_index=True, key=f"cal{p}",
                        column_config={"Minutes": st.column_config.NumberColumn(min_value=0, max_value=900)})
    c1, c2 = st.columns(2)
    if c1.button("Save phases"):
        rows = [[str(r_["Phase"]), float(r_["Minutes"] or 0)] for _, r_ in ed.iterrows()
                if str(r_["Phase"]).strip() and str(r_["Phase"]) != "nan"]
        if not any(m > 0 for _, m in rows):
            st.error("At least one phase needs minutes > 0.")
        else:
            calib.setdefault("profiles", {})[str(p)] = rows
            save_json(F_CALIB, calib)
            st.success("Saved.")
    if c2.button("Reset to default"):
        calib.get("profiles", {}).pop(str(p), None)
        save_json(F_CALIB, calib)
        st.rerun()
    if calib.get("lcd"):
        with st.expander("Remembered LCD totals"):
            st.dataframe(pd.DataFrame([{"Program|size|crust": k_, "Minutes": v} for k_, v in calib["lcd"].items()]),
                         hide_index=True)
            if st.button("Forget LCD totals"):
                calib["lcd"] = {}
                save_json(F_CALIB, calib)
                st.rerun()


page = st.session_state.page
if page == PAGES[0]:
    page_bake()
elif page == PAGES[1]:
    page_timer()
elif page == PAGES[2]:
    page_custom()
else:
    page_settings()

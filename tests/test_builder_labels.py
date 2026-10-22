"""services/builder/labels.py — человекочитаемые подписи (Этап 9).

Примеры из ТЗ Этапа 9 (Шаг 1): fmt_qty с единицами, fmt_band/fmt_freq с
запятой и тире, fmt_params через « · », slot_status_line «⬜/✅».
"""

from services.builder.labels import (
    fmt_band,
    fmt_freq,
    fmt_params,
    fmt_qty,
    label,
    slot_status_line,
)


# ---------- label ----------

def test_label_known_and_unknown():
    assert label("frame") == "Рама"
    assert label("motor") == "Моторы"
    assert label("fc_esc_stack") == "Стек FC+ESC"
    assert label("совсем_не_категория") == "Прочее"   # не машинный ключ!
    assert label("") == "Прочее"
    assert label(None) == "Прочее"


# ---------- fmt_qty ----------

def test_fmt_qty_examples():
    assert fmt_qty(448) == "448 шт"
    assert fmt_qty(448.0) == "448 шт"
    assert fmt_qty(6.4, "упак") == "6,4 упак"
    assert fmt_qty(19.5, "кг") == "19,5 кг"
    assert fmt_qty(1, "компл") == "1 компл."
    assert fmt_qty(0) == "0 шт"
    assert fmt_qty(None) == "0 шт"
    assert fmt_qty("2,000", "шт") == "2 шт"          # значения из БД приходят строками


# ---------- fmt_band / fmt_freq ----------

def test_fmt_band_examples():
    assert fmt_band((5.8, 5.8)) == "5,8 ГГц"
    assert fmt_band((6.1, 7.2)) == "6,1–7,2 ГГц"


def test_fmt_freq_examples():
    assert fmt_freq((400, 400)) == "400 МГц"
    assert fmt_freq((2400, 2400)) == "2,4 ГГц"
    assert fmt_freq((300, 1950)) == "300–1950 МГц"


# ---------- fmt_params ----------

def test_fmt_params_examples():
    assert fmt_params({"size": 10}) == "10″"
    assert fmt_params({"stator": "3115"}) == "статор 3115"
    assert fmt_params({"kv": 900}) == "900 KV"
    assert fmt_params({"current": 60}) == "60 А"
    assert fmt_params({"power_w": 3}) == "3 Вт"
    assert fmt_params({"video_band": (5.8, 5.8)}) == "5,8 ГГц"
    assert fmt_params({"control_freq": (300, 1950)}) == "300–1950 МГц"
    assert (
        fmt_params({"size": 10, "stator": "3115", "kv": 900})
        == "10″ · статор 3115 · 900 KV"
    )
    assert fmt_params({}) == ""
    assert fmt_params(None) == ""


# ---------- slot_status_line ----------

def test_slot_status_line_empty():
    assert slot_status_line("frame", None) == "⬜ Рама — не выбрано"
    assert slot_status_line("fc_esc_stack", None) == "⬜ Стек FC+ESC — не выбрано"


def test_slot_status_line_with_part():
    part = {
        "name": "Рама 10 МАРК4-V2",
        "quantity": 2.0,
        "unit": "шт",
        "params": {"size": 10},
    }
    assert slot_status_line("frame", part) == "✅ Рама: Рама 10 МАРК4-V2 · 2 шт · 10″"


def test_slot_status_line_part_without_params():
    part = {"name": "Патч антенна", "quantity": 6.5, "unit": "упак", "params": {}}
    assert slot_status_line("antenna_ground", part) == "✅ Антенна наземная: Патч антенна · 6,5 упак"

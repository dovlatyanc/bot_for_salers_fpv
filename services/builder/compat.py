"""Проверка совместимости конфигурации «🛠 Конструктора дронов» (ЭТАП 5).

Чистый модуль: импортирует только stdlib, справочники data/builder/ и
чистые функции services/builder/recipes.py (required_slots — хотфикс
Этапа 10а; цикла нет, recipes.py не импортирует compat) — НИКАКИХ db/,
handlers/, network. Данные
проверка получает ГОТОВЫМИ от вызывающего (хендлер Этапа 6 / админка
Этапа 9 забирает их фасадами db.repositories_builder) и в БД не ходит.

Контракт входа (зафиксирован, используется хендлерами Этапов 6 и 9):

  CONFIG = {
      "size": int | None,          # явный размер сборки; может отсутствовать
      "slots": {
          <category>: {"key": str, "qty": int},   # qty по умолчанию 1
          ...                                      # ключи — категории слотов
      },                                           # BUILD_SLOTS (data/builder/categories.py)
  }
  PARTS_BY_SLOT = {<category>: PART_DICT}   # подготовлено вызывающим через
                                            # фасады; PART_DICT: id/key/name/
                                            # category/unit/params/quantity/
                                            # updated_at (params — dict, интервалы
                                            # video_band/control_freq — [lo, hi])
  rules — список RULE_DICT (как из list_compat_rules):
          {id, rule_type, params, description, is_active}

Контракт результата:

  RESULT = {
      "ok": bool,          # complete AND нет errors
      "complete": bool,    # все обязательные слоты (required_slots) присутствуют
      "missing": [str],    # обязательные слоты, которых нет в config["slots"]
      "build_size": int | None,
      "errors": [str],     # блокирующие проблемы (несовместимость, нехватка)
      "error_details": [   # Этап 11: попарно к errors — details[i]["text"] ==
          {"text": str, "suggest_slot": str | None},
          # suggest_slot — слот-адресат подсказки замены на экране проверки
          # (pairwise-частотные → слот-не-якорь; остальные проверки → None)
      ],
      "warnings": [str],   # advisory и «не удалось проверить»
  }

Сообщения — человекочитаемые строки на русском с именами деталей
(PART_DICT["name"]) и числами (диапазоны «6.1–7.2 ГГц», частоты «400 МГц»,
2400 МГц — «2.4G»). Порядок сообщений детерминирован: порядок правил из
rules, внутри правила — порядок слотов rule.params["slots"]; попарные
частотные проверки (Этап 11) дают по ОДНОМУ сообщению на каждый слот,
не пересекающий якорь, в порядке слотов правила; встроенные
проверки после правил (сначала «деталь не найдена», потом «недостаточно
на складе» — обе в порядке перечисления слотов конфига); missing — в
порядке required_slots. Два вызова на одном входе дают идентичные RESULT.

Проверки по rule_type (dispatch CHECKS):
  video_band_match      — пересечение ГГц-интервалов слотов из params["slots"]
                          (bands_intersect, tol по умолчанию); слот в конфиге,
                          но band не распознан → warning, НЕ error;
  control_freq_match    — то же для МГц-интервалов tx/rx (freqs_intersect);
                          хотфикс Д4: в участниках также антенны тракта
                          управления (control_freq без video_band);
  size_consistency      — размеры frame/prop напрямую, мотор по СТАТОРУ
                          через MOTOR_STATOR_BY_SIZE (приоритет статора над
                          params["size"], Этап 10; params["size"] — только
                          без статора); build_size неизвестен → warning;
                          хотфикс Д2: рама без params["size"] → warning
                          «размер рамы не определён»;
  esc_current_advisory  — ток ESC/стека ниже MIN_ESC_CURRENT_BY_SIZE → warning
                          (severity из params: «error» уводит сообщение в errors);
  kv_advisory           — KV мотора вне KV_RANGE_BY_SIZE → warning.
Неизвестный rule_type → warning «правило … неизвестно, пропущено»;
is_active=False → правило пропускается молча.

Встроенные проверки (вне правил, выполняются всегда): комплектность по
required_slots (обязательные слоты BUILD_SLOTS; условная обязательность
antenna_air упразднена — слот выведен из сборки, хотфикс Этапа 10б);
наличие детали (PART_DICT отсутствует в
parts_by_slot) и достаточность остатка (quantity < qty → error; сравнение
без эпсилонов — значения из БД приходят float).
"""

from __future__ import annotations

from data.builder.control_freq import freqs_intersect
from data.builder.drone_sizes import MOTOR_STATOR_BY_SIZE
from data.builder.electrical import KV_RANGE_BY_SIZE, MIN_ESC_CURRENT_BY_SIZE
from data.builder.frequencies import bands_intersect
from services.builder.recipes import required_slots

__all__ = [
    "CHECKS",
    "check_compatibility",
    "control_freq_match",
    "derive_build_size",
    "esc_current_advisory",
    "kv_advisory",
    "size_consistency",
    "video_band_match",
]

# ============================================================
# форматирование чисел и интервалов (сообщения на русском)
# ============================================================
def _fmt_num(value: float) -> str:
    """«5.8» → «5.8», «6.0» → «6», «2.0» → «2», «4.85» → «4.85» (без хвостовых нулей)."""
    text = f"{float(value):.2f}".rstrip("0").rstrip(".")
    return text if text not in ("", "-") else "0"


def _fmt_ghz(band) -> str:
    """[lo, hi] ГГц → «5.8 ГГц» / «6.1–7.2 ГГц»."""
    lo, hi = float(band[0]), float(band[1])
    if lo == hi:
        return f"{_fmt_num(lo)} ГГц"
    return f"{_fmt_num(lo)}–{_fmt_num(hi)} ГГц"


def _fmt_mhz(value: float) -> str:
    """2400 МГц → «2.4G», остальное → «400 МГц» / «915 МГц»."""
    if float(value) == 2400.0:
        return "2.4G"
    return f"{_fmt_num(value)} МГц"


def _fmt_mhz_band(band) -> str:
    """[lo, hi] МГц → «400 МГц» / «300–1950 МГц» / «2.4G»."""
    lo, hi = float(band[0]), float(band[1])
    if lo == hi:
        return _fmt_mhz(lo)
    return f"{_fmt_num(lo)}–{_fmt_num(hi)} МГц"


# ============================================================
# размер сборки
# ============================================================
def derive_build_size(config, parts_by_slot) -> int | None:
    """Размер сборки: config["size"] → params["size"] рамы → пропа → мотора → None.

    Слот без детали в parts_by_slot пропускается (размер от неё получить
    нельзя) — приоритет переходит к следующему источнику.
    """
    config = config or {}
    parts_by_slot = parts_by_slot or {}
    explicit = config.get("size")
    if explicit is not None:
        return int(explicit)
    slots = config.get("slots") or {}
    for category in ("frame", "prop", "motor"):
        if category not in slots:
            continue
        part = parts_by_slot.get(category)
        if part is None:
            continue
        size = (part.get("params") or {}).get("size")
        if size is not None:
            return int(size)
    return None


# ============================================================
# проверки по rule_type: (rule, config, parts_by_slot, ctx)
#     -> (errors, warnings, details)
# details — error_details, попарно к errors (details[i]["text"] == errors[i];
# Этап 11): {"text": str, "suggest_slot": category | None} — слот-адресат
# подсказки замены на экране проверки (pairwise-частотные → слот-не-якорь,
# остальные проверки → None).
# ctx = {"build_size": int | None} — считается один раз в check_compatibility.
# Слот, заявленный в правиле, но без детали в parts_by_slot, здесь
# пропускается: ошибка «деталь не найдена» — зона встроенной проверки.
# ============================================================
def _plain_details(errors: list[str]) -> list[dict]:
    """error_details для проверок без адресата замены (suggest_slot=None)."""
    return [{"text": text, "suggest_slot": None} for text in errors]


def video_band_match(rule, config, parts_by_slot, ctx):
    """Попарная проверка видеодиапазонов против ЯКОРЯ (Этап 11, A1).

    Якорь — первый слот правила (порядок params["slots"]) с непустым
    params["video_band"]; каждый следующий слот, чей диапазон НЕ пересекает
    якорь (bands_intersect, штатный tol), даёт ОТДЕЛЬНОЕ сообщение
    «{имя} ({диапазон}) не пересекается с {имя_якоря} ({диапазон_якоря})»
    и detail с suggest_slot = слот-не-якорь. Пересекающие якорь в сообщения
    не попадают (сравнение всегда с якорем, не с бегущим пересечением —
    кейс владельца: VTX 5.8 + VRX 4.8–6.1 + антенна 5.8 + патч 6–7.2 →
    ровно одно сообщение про патч). Слот в конфиге, но band не распознан →
    warning «не удалось проверить диапазон» (НЕ error: отсутствие данных —
    не несовместимость). Хотфикс Д5: антенна тракта управления
    (control_freq без video_band) из этого warning исключена — у неё нет
    и не должно быть видеодиапазона, её частоту проверяет
    control_freq_match (Д4).
    """
    errors: list[str] = []
    details: list[dict] = []
    warnings: list[str] = []
    slots = (rule.get("params") or {}).get("slots") or ()
    configured = (config or {}).get("slots") or {}
    with_band: list[tuple[str, str, tuple[float, float]]] = []
    without_band: list[str] = []
    for category in slots:
        if category not in configured:
            continue
        part = parts_by_slot.get(category)
        if part is None:
            continue
        name = part.get("name") or str(category)
        params = part.get("params") or {}
        band = params.get("video_band")
        if band:
            with_band.append((str(category), name, (float(band[0]), float(band[1]))))
        elif (
            category in ("antenna_air", "antenna_ground")
            and params.get("control_freq")
        ):
            # Хотфикс Д5: антенна тракта управления (control_freq без
            # video_band) проверяется правилом control_freq_match (Д4) —
            # предупреждение о видеодиапазоне к ней не относится.
            continue
        else:
            without_band.append(name)
    if len(with_band) >= 2:
        anchor_category, anchor_name, anchor_band = with_band[0]
        for category, name, band in with_band[1:]:
            if bands_intersect(anchor_band, band):
                continue
            message = (
                f"{name} ({_fmt_ghz(band)}) не пересекается с "
                f"{anchor_name} ({_fmt_ghz(anchor_band)})"
            )
            errors.append(message)
            details.append({"text": message, "suggest_slot": category})
    warnings.extend(
        f"Не удалось проверить диапазон: {name}" for name in without_band
    )
    return errors, warnings, details


def control_freq_match(rule, config, parts_by_slot, ctx):
    """Попарная проверка частот управления против ЯКОРЯ (Этап 11, A2).

    Та же форма, что у video_band_match, но по params["control_freq"] и
    freqs_intersect (без допуска); единицы в сообщениях — МГц, 2400 МГц
    показывается как «2.4G». suggest_slot — слот-не-якорь.

    Хотфикс Д4: кроме слотов params["slots"] (tx/rx) в участниках — антенны
    тракта управления (antenna_air/antenna_ground с control_freq и БЕЗ
    video_band; антенна с video_band принадлежит видеотракту). Антенны
    добавляются в конец списка участников, якорь — первый участник
    с частотой (порядок: tx, rx, антенны); suggest_slot у
    антенны-нарушителя — её слот (antenna_air/antenna_ground).
    """
    errors: list[str] = []
    details: list[dict] = []
    warnings: list[str] = []
    slots = (rule.get("params") or {}).get("slots") or ()
    configured = (config or {}).get("slots") or {}
    with_freq: list[tuple[str, str, tuple[float, float]]] = []
    without_freq: list[str] = []
    for category in slots:
        if category not in configured:
            continue
        part = parts_by_slot.get(category)
        if part is None:
            continue
        name = part.get("name") or str(category)
        freq = (part.get("params") or {}).get("control_freq")
        if freq:
            with_freq.append((str(category), name, (float(freq[0]), float(freq[1]))))
        else:
            without_freq.append(name)
    # Хотфикс Д4: антенны тракта управления (control_freq, без video_band)
    # участвуют в проверке наравне с tx/rx (сид-правило НЕ трогаем —
    # участников добавляет код проверки). В конец списка — якорь
    # (with_freq[0]) остаётся у tx/rx, если у них есть частота.
    for category in ("antenna_air", "antenna_ground"):
        if category in slots or category not in configured:
            continue
        part = parts_by_slot.get(category)
        if part is None:
            continue
        params = part.get("params") or {}
        freq = params.get("control_freq")
        if not freq or params.get("video_band"):
            continue
        name = part.get("name") or str(category)
        with_freq.append((str(category), name, (float(freq[0]), float(freq[1]))))
    if len(with_freq) >= 2:
        anchor_category, anchor_name, anchor_freq = with_freq[0]
        for category, name, freq in with_freq[1:]:
            if freqs_intersect(anchor_freq, freq):
                continue
            message = (
                f"{name} ({_fmt_mhz_band(freq)}) не пересекается с "
                f"{anchor_name} ({_fmt_mhz_band(anchor_freq)})"
            )
            errors.append(message)
            details.append({"text": message, "suggest_slot": category})
    warnings.extend(
        f"Не удалось проверить частоту управления: {name}" for name in without_freq
    )
    return errors, warnings, details


def size_consistency(rule, config, parts_by_slot, ctx):
    """Соответствие размеров рамы/пропа/мотора размеру сборки.

    build_size берётся из ctx (None → warning «размер сборки не определён»
    и пропуск). Рама/проп: params["size"] != build_size → error; хотфикс
    Д2: рама без params["size"] → warning «размер рамы не определён» (проп
    без размера — по-прежнему молча). Мотор
    (Этап 10): ПРИОРИТЕТ у params["stator"] — допустимые размеры = ключи
    MOTOR_STATOR_BY_SIZE с этим статором (build_size вне них → error);
    params["size"] используется ТОЛЬКО при отсутствии статора (или статоре
    вне реестра) — серийный кейс R5: мотор «…для 9 дюймов» со статором
    3115 проходит на 10″ без ошибок. Ни статора, ни размера → warning
    «не удалось проверить размер мотора».
    """
    errors: list[str] = []
    warnings: list[str] = []
    build_size = (ctx or {}).get("build_size")
    if build_size is None:
        warnings.append("Размер сборки не определён — проверка размеров пропущена")
        return errors, warnings, _plain_details(errors)
    build_size = int(build_size)
    slots = (rule.get("params") or {}).get("slots") or ()
    configured = (config or {}).get("slots") or {}
    for category in slots:
        if category not in configured:
            continue
        part = parts_by_slot.get(category)
        if part is None:
            continue
        params = part.get("params") or {}
        name = part.get("name") or str(category)
        if params.get("hexa"):
            # «Гексакоптер» — ОТДЕЛЬНАЯ рама (не 10/13/15″): размерной
            # привязки к размеру борта нет — не проверяем и не предупреждаем.
            continue
        if category == "motor":
            stator = params.get("stator")
            allowed: list[int] = []
            if stator:
                stator_str = str(stator)
                allowed = sorted(
                    size_key
                    for size_key, stators in MOTOR_STATOR_BY_SIZE.items()
                    if stator_str in {str(s) for s in stators}
                )
            if allowed:
                if build_size not in allowed:
                    allowed_s = ", ".join(f"{s}″" for s in allowed)
                    errors.append(
                        f"{name}: статор {stator} не подходит для сборки {build_size}″ "
                        f"(допустимы: {allowed_s})"
                    )
                continue
            # статора нет (или он вне реестра) — fallback на params["size"]
            size = params.get("size")
            if size is None:
                warnings.append(f"Не удалось проверить размер мотора: {name}")
            elif int(size) != build_size:
                errors.append(
                    f"{name}: размер {_fmt_num(size)}″ не соответствует сборке {build_size}″"
                )
            continue
        # рама/проп/прочие — по params["size"] напрямую
        size = params.get("size")
        if size is None:
            if category == "frame":
                # Хотфикс Д2: рама без распознанного размера — warning, а не
                # тишина: молчание скрывало несовместимость (рама без размера
                # на борт известного размера проходила без вопросов)
                warnings.append(
                    f"{name}: размер рамы не определён — проверьте вручную"
                )
            continue   # проп/прочие без распознанного размера — проверять не по чему
        if int(size) != build_size:
            errors.append(
                f"{name}: размер {_fmt_num(size)}″ не соответствует сборке {build_size}″"
            )
    return errors, warnings, _plain_details(errors)


def esc_current_advisory(rule, config, parts_by_slot, ctx):
    """Ток ESC/стека не ниже MIN_ESC_CURRENT_BY_SIZE для размера сборки.

    severity из rule.params («warning» по умолчанию; «error» уводит
    сообщение в errors). Источник — первый из слотов fc_esc_stack/esc с
    распознанным params["current"]; ниже минимума → сообщение, иначе молча.
    """
    errors: list[str] = []
    warnings: list[str] = []
    build_size = (ctx or {}).get("build_size")
    if build_size is None:
        return errors, warnings, _plain_details(errors)
    min_current = MIN_ESC_CURRENT_BY_SIZE.get(int(build_size))
    if min_current is None:
        return errors, warnings, _plain_details(errors)
    severity = str((rule.get("params") or {}).get("severity") or "warning")
    configured = (config or {}).get("slots") or {}
    for category in ("fc_esc_stack", "esc"):
        if category not in configured:
            continue
        part = parts_by_slot.get(category)
        if part is None:
            continue
        current = (part.get("params") or {}).get("current")
        if current is None:
            continue
        current = float(current)
        if current < min_current:
            name = part.get("name") or str(category)
            message = (
                f"{name}: рекомендуется ток ≥ {min_current} А для {build_size}″ "
                f"(выбрано {_fmt_num(current)} А)"
            )
            (errors if severity == "error" else warnings).append(message)
        break   # первый слот с распознанным током — единственный источник
    return errors, warnings, _plain_details(errors)


def kv_advisory(rule, config, parts_by_slot, ctx):
    """KV мотора в рекомендованном диапазоне KV_RANGE_BY_SIZE для сборки.

    Вне диапазона → warning; мотора нет в конфиге / KV не распознан /
    для build_size нет нормы в реестре → молча.
    """
    errors: list[str] = []
    warnings: list[str] = []
    build_size = (ctx or {}).get("build_size")
    if build_size is None:
        return errors, warnings, _plain_details(errors)
    kv_range = KV_RANGE_BY_SIZE.get(int(build_size))
    if kv_range is None:
        return errors, warnings, _plain_details(errors)
    if "motor" not in ((config or {}).get("slots") or {}):
        return errors, warnings, _plain_details(errors)
    part = parts_by_slot.get("motor")
    if part is None:
        return errors, warnings, _plain_details(errors)
    kv = (part.get("params") or {}).get("kv")
    if kv is None:
        return errors, warnings, _plain_details(errors)
    lo, hi = kv_range
    if not (lo <= float(kv) <= hi):
        name = part.get("name") or "мотор"
        warnings.append(
            f"{name}: KV {_fmt_num(kv)} вне рекомендованного диапазона "
            f"{lo}–{hi} для сборки {build_size}″"
        )
    return errors, warnings, _plain_details(errors)


# Реестр проверок: rule_type → функция (rule, config, parts_by_slot, ctx).
CHECKS = {
    "video_band_match": video_band_match,
    "control_freq_match": control_freq_match,
    "size_consistency": size_consistency,
    "esc_current_advisory": esc_current_advisory,
    "kv_advisory": kv_advisory,
}


# ============================================================
# встроенные проверки (вне правил) и точка входа
# ============================================================
def _builtin_checks(
    config, parts_by_slot
) -> tuple[list[str], list[str], list[str]]:
    """Наличие/достаточность по слотам конфига (комплектность — в RESULT).

    Возвращает (not_found_errors, insufficient_errors, to_order_warnings).
    «Деталь не найдена» всегда раньше «недостаточно на складе»; внутри каждой
    группы — порядок перечисления слотов в конфиге. quantity приводится к
    float (из БД приходят float), сравнение без эпсилон-трюков. Позиции с
    params["to_order"] (Этап 10в) вместо insufficient дают warning.
    """
    slots = config.get("slots") or {}
    not_found: list[str] = []
    insufficient: list[str] = []
    to_order: list[str] = []
    for category, slot in slots.items():
        slot = slot or {}
        if not slot.get("key"):
            # Этап 11в: mto-слот (key=None, made_to_order — рама «под заказ»)
            # и пустые слоты в проверке наличия не участвуют — рама под заказ
            # не даёт «деталь не найдена» и не блокирует сборку (L4).
            continue
        part = parts_by_slot.get(category)
        if part is None:
            not_found.append(f"Деталь не найдена: {slot.get('key') or category}")
            continue
        quantity = part.get("quantity")
        quantity = 0.0 if quantity is None else float(quantity)
        raw_qty = slot.get("qty", 1)
        qty = 1 if raw_qty is None else int(raw_qty)
        name = part.get("name") or category
        if quantity < qty:
            # Этап 10в: позиция «под заказ» (to_order в params — например,
            # гекса-рамы, которых нет в выгрузке 1С) не блокирует сборку:
            # недостаток остатка → warning, а не error (L4).
            if (part.get("params") or {}).get("to_order"):
                to_order.append(f"{name}: под заказ (нет на складе)")
            else:
                insufficient.append(
                    f"Недостаточно на складе: {name} "
                    f"(нужно {qty}, доступно {_fmt_num(quantity)})"
                )
    return not_found, insufficient, to_order


def check_compatibility(config, parts_by_slot, rules) -> dict:
    """Полная проверка конфигурации → RESULT (контракт в docstring модуля).

    Порядок: правила (в порядке списка rules; is_active=False — молча,
    неизвестный rule_type — warning), затем встроенные проверки. ok =
    complete AND нет errors. error_details попарно к errors (Этап 11):
    details[i]["text"] == errors[i], suggest_slot — слот-адресат подсказки
    замены (None у встроенных проверок наличия/остатка). Детерминировано:
    одинаковый вход → одинаковый RESULT (включая порядок строк).

    Этап 10в: раздельные fc и esc в конфиге (вместе) закрывают слот
    fc_esc_stack в missing — форма сборки (стек против пары) проверяется
    на L2 (fc_esc_shape_errors), а не как незаполненный слот.
    """
    config = config or {}
    parts_by_slot = parts_by_slot or {}
    errors: list[str] = []
    error_details: list[dict] = []
    warnings: list[str] = []
    build_size = derive_build_size(config, parts_by_slot)
    ctx = {"build_size": build_size}
    for rule in rules or ():
        rule = rule or {}
        if rule.get("is_active") is False:
            continue
        check = CHECKS.get(rule.get("rule_type"))
        if check is None:
            warnings.append(
                f"Правило {rule.get('rule_type')} неизвестно, пропущено"
            )
            continue
        rule_errors, rule_warnings, rule_details = check(rule, config, parts_by_slot, ctx)
        errors.extend(rule_errors)
        warnings.extend(rule_warnings)
        error_details.extend(rule_details)
    slots = config.get("slots") or {}
    # Комплектность — required_slots (обязательные слоты BUILD_SLOTS;
    # условная обязательность antenna_air упразднена вместе со слотом —
    # хотфикс Этапа 10б).
    missing = [
        category
        for category in required_slots(config, parts_by_slot)
        if category not in slots
    ]
    # Этап 10в: раздельные FC и ESC вместе закрывают слот стека (13″ и
    # гекса 15″ собираются парой) — fc_esc_stack не «missing», если в
    # конфиге есть ОБЕ детали; форма сборки (стек против пары) — зона L2
    # (fc_esc_shape_errors). Одиночный fc или esc пару не закрывает.
    if "fc_esc_stack" in missing and slots.get("fc") and slots.get("esc"):
        missing.remove("fc_esc_stack")
    not_found, insufficient, to_order = _builtin_checks(config, parts_by_slot)
    errors.extend(not_found)
    error_details.extend(_plain_details(not_found))
    errors.extend(insufficient)
    error_details.extend(_plain_details(insufficient))
    warnings.extend(to_order)
    complete = not missing
    return {
        "ok": complete and not errors,
        "complete": complete,
        "missing": missing,
        "build_size": build_size,
        "errors": errors,
        "error_details": error_details,
        "warnings": warnings,
    }





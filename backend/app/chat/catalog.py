"""Verified project-methodology documents generated from the active model contract."""

from __future__ import annotations

from typing import Any

from app.modelcfg import ModelConfig


def builtin_documents(cfg: ModelConfig) -> list[dict[str, Any]]:
    docs: list[dict[str, Any]] = [
        {
            "title": "Назначение индекса и границы сравнения",
            "text": (
                "Атлас оценивает доступность территорий СКФО под профиль пользователя. "
                "Итоговый индекс лежит в диапазоне от 0 до 100. Сравнение и нормировка "
                "выполняются внутри кластера территории; рейтинги разных кластеров нельзя "
                "сопоставлять напрямую. Для точного балла, места или времени до объекта "
                "нужно запросить расчётные данные через инструмент чата."
            ),
        },
        {
            "title": "Как рассчитывается индекс",
            "text": (
                "Признаки нормируются внутри кластера. Выбросы ограничиваются квантилями "
                "из конфигурации кластера; затем применяется min-max нормировка, а для "
                "малых кластеров ранговая нормировка. Признаки, где меньшее значение лучше, "
                "инвертируются. Пропуски замещаются медианой кластера и помечаются. "
                "Если признак недоступен во всём кластере, его вес перераспределяется между "
                "доступными признаками соответствующего субиндекса. Итог учитывает веса профиля."
            ),
        },
        {
            "title": "Происхождение данных и деморежим",
            "text": (
                "Каталог признаков описывает методику и запланированные источники. "
                "Если feature_value.source равен synthetic-demo, баллы построены на "
                "синтетических демонстрационных данных. Источник конкретного значения нужно смотреть "
                "в данных индекса и загрузок."
            ),
        },
        {
            "title": "Сценарии что если",
            "text": (
                "Симулятор сценариев оценивает изменение индекса при изменении доступности "
                "объектов и транспортных условий. Это расчётная гипотеза, а не прогноз "
                "реального строительства или утверждённый транспортный план. Для "
                "численной дельты нужен инструмент run_scenario."
            ),
        },
    ]
    for code, sub in cfg.subindices.items():
        feature_names = "; ".join(f"{feature_code} — {cfg.features[feature_code].name}" for feature_code in cfg.features_of(code))
        role = "входит в итоговый индекс" if sub.in_total else "отображается отдельно и не входит в итоговый индекс"
        docs.append({"title": f"{code}: {sub.name}", "text": f"Субиндекс {code} «{sub.name}» {role}. Признаки: {feature_names}."})
    for code, feature in cfg.features.items():
        direction = "чем меньше, тем лучше" if feature.direction == "lower_better" else "чем больше, тем лучше"
        level = {"settlement": "населённый пункт", "municipality": "муниципалитет", "region": "субъект"}[feature.level]
        docs.append({
            "title": f"{code}: {feature.name}",
            "text": (
                f"Признак {code} «{feature.name}» относится к {feature.subindex} «{cfg.subindices[feature.subindex].name}». "
                f"Единица измерения: {feature.unit or 'не задана'}. Направление оценки: {direction}. "
                f"Естественный уровень данных: {level}. "
                f"Зависит от способа передвижения: {'да' if feature.time_based else 'нет'}. "
                f"Планируемый источник в конфигурации: {feature.source or 'не указан'}."
            ),
        })
    for code, cluster in cfg.clusters.items():
        docs.append({"title": f"Кластер {code}: {cluster.name}", "text": f"Кластер {code} «{cluster.name}». {cluster.description}. Сравнение делается с территориями этого же кластера."})
    for code, preset in cfg.presets.items():
        weights = ", ".join(f"{sub} — {weight:g}%" for sub, weight in preset.weights.items())
        docs.append({"title": f"Профиль {preset.name}", "text": f"Профиль «{preset.name}» ({code}). {preset.description}. Веса субиндексов: {weights}. Условия профиля: {preset.key_thresholds}."})
    return docs

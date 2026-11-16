# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Text saved in the legacy Windows code pages people still receive (#8040).

Each sample is a small CSV (header, then rows) long enough for encoding
detection to be meaningful; use it with ``pytest.mark.parametrize(**LEGACY_SAMPLES)``.
"""

import pytest

ARABIC_CSV = (
    "الشركة,المدينة,الإيرادات,ملاحظات\n"
    "شركة النور للتجارة,الرياض,1234,تم تجديد العقد في شهر مارس\n"
    "شركة الأمل للخدمات,جدة,5678,ينتظر العميل عرض السعر الجديد\n"
    "مؤسسة الفجر,الدمام,910,الاجتماع القادم يوم الخميس بعد الظهر\n"
)

JAPANESE_CSV = (
    "会議名,日付,担当者,メモ\n"
    "定例会議,木曜日,田中,次回の会議は木曜日の午後3時です。\n"
    "予算審議,金曜日,佐藤,来年度の予算案を確認してください。\n"
    "顧客訪問,月曜日,鈴木,東京本社で新製品の説明を行います。\n"
)

CHINESE_CSV = (
    "部门,城市,年度预算,备注\n"
    "销售部,北京,三百万元,下周一提交季度报告\n"
    "研发部,上海,五百万元,新产品计划在年底发布\n"
    "市场部,广州,二百万元,请确认展会的参展名单\n"
)

RUSSIAN_CSV = (
    "Отдел,Город,Бюджет,Примечание\n"
    "Продажи,Москва,300 рублей,Отчёт нужно сдать в понедельник\n"
    "Разработка,Казань,500 рублей,Новая версия выйдет в конце года\n"
    "Маркетинг,Самара,200 рублей,Проверьте список участников выставки\n"
)

LEGACY_SAMPLES = {
    "argnames": ("text", "encoding"),
    "argvalues": [
        pytest.param(ARABIC_CSV, "cp1256", id="arabic-windows-1256"),
        pytest.param(JAPANESE_CSV, "shift_jis", id="japanese-shift-jis"),
        pytest.param(CHINESE_CSV, "gbk", id="chinese-gbk"),
        pytest.param(RUSSIAN_CSV, "cp1251", id="russian-windows-1251"),
    ],
}

#: Bytes no text encoding fits: undefined in CP1252, unplaced by detection.
UNDECODABLE = bytes(range(128, 256)) * 8

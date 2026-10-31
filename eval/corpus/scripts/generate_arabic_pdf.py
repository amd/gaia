# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Build eval/corpus/documents/clinic_guide_ar_en.pdf, a fictional bilingual guide.

Chromium shapes the Arabic the way a real publishing tool does, so the PDF holds
the same glyph order and presentation forms that real Arabic PDFs hand to pypdf.

Usage: python eval/corpus/scripts/generate_arabic_pdf.py --chrome <path to chrome>
"""

import argparse
import subprocess
import tempfile
from pathlib import Path

OUTPUT = Path(__file__).resolve().parents[1] / "documents" / "clinic_guide_ar_en.pdf"

PAGES = (
    (
        "rtl",
        """
<h1>دليل المرضى: عيادة الواحة المجتمعية</h1>
<p>يشرح هذا الدليل كيفية الحصول على الرعاية في عيادة الواحة المجتمعية.</p>
<h2>مواعيد العمل</h2>
<p>تفتح العيادة من السبت إلى الخميس من الساعة 8:00 صباحاً حتى الساعة 8:00 مساءً،
وتغلق يوم الجمعة.</p>
<h2>خط الطوارئ</h2>
<p>يعمل خط التمريض على مدار الساعة على الرقم الداخلي 4417.</p>
""",
    ),
    (
        "rtl",
        """
<h1>الرسوم والمواعيد</h1>
<p>تبلغ رسوم الاستشارة العامة 150 ديناراً، وتبلغ رسوم استشارة الأخصائي 275 ديناراً.</p>
<h2>إلغاء المواعيد</h2>
<p>يجب إلغاء الموعد قبل 24 ساعة على الأقل. وتُفرض رسوم قدرها 40 ديناراً
على المواعيد التي تُلغى بعد ذلك.</p>
<h2>الفئات المعفاة</h2>
<p>يُعفى من رسوم الاستشارة العامة الأطفال دون سن ٦ سنوات.</p>
""",
    ),
    (
        "rtl",
        """
<h1>الصيدلية والسجلات الطبية</h1>
<p>تُصرف الوصفات الطبية المتكررة خلال 5 أيام عمل من تقديم الطلب.</p>
<h2>التطعيمات</h2>
<p>يتوفر لقاح التهاب الكبد ب (Hepatitis B) بثلاث جرعات، وتُعطى الجرعة الثانية
بعد شهر واحد من الأولى.</p>
<h2>السجلات الطبية</h2>
<p>تحتفظ العيادة بالسجلات الطبية لمدة 10 سنوات بعد آخر زيارة للمريض.</p>
""",
    ),
    (
        "ltr",
        """
<h1>Telehealth visits</h1>
<p>Video visits last up to 20 minutes and are available on weekdays only.
Patients need a confirmation code, which is sent by text message 2 hours
before the visit.</p>
<h2>Interpreters</h2>
<p>An interpreter is free of charge and must be requested at least 3 days
before the visit.</p>
""",
    ),
)

STYLE = """
@page { size: A4; margin: 24mm; }
body { font-family: Arial, sans-serif; font-size: 13pt; line-height: 1.7; }
section { break-after: page; }
section:last-child { break-after: auto; }
"""


def build_html() -> str:
    sections = "".join(
        f'<section dir="{direction}" lang="{"ar" if direction == "rtl" else "en"}">{body}</section>'
        for direction, body in PAGES
    )
    return (
        '<!doctype html><html><head><meta charset="utf-8">'
        f"<style>{STYLE}</style></head><body>{sections}</body></html>"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chrome", required=True, help="Chrome or Edge executable")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        html = Path(tmp) / "guide.html"
        html.write_text(build_html(), encoding="utf-8")
        subprocess.run(
            [
                args.chrome,
                "--headless",
                "--disable-gpu",
                "--no-pdf-header-footer",
                f"--print-to-pdf={OUTPUT}",
                html.as_uri(),
            ],
            check=True,
            timeout=60,
        )
    print(f"Wrote {OUTPUT}")


if __name__ == "__main__":
    main()

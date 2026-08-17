"""P7 benchmark: gzip compression on representative Arabic article text.

Not wired into the pipeline — this is a one-off measurement to back the
decision in docs/adr/0005-gzip-over-zstd.md with real numbers instead of
"it should be fine." Run manually:

    python scripts/bench_compression.py

The sample bodies below are representative (hand-written, realistic length
and structure), not yet scraped production data, because ingest_core hasn't
run against real infrastructure yet. Re-run this against a real corpus once
M1.5 Stage 4 has been live for a few days, and update benchmarks/results.md.
"""

from __future__ import annotations

import gzip
import time

from src.store.blob import compress_text

SAMPLE_BODIES = [
    # Politics
    """أعلن الرئيس الأمريكي جو بايدن، في تصريحات صحفية من البيت الأبيض، عن سياسة جديدة تجاه
    منطقة الشرق الأوسط تهدف إلى تعزيز الاستقرار الإقليمي وخفض التوترات المستمرة منذ أشهر.
    وأشار بايدن إلى أن الإدارة الأمريكية ستعمل بالتنسيق مع الحلفاء الإقليميين والدوليين من
    أجل التوصل إلى تسوية سياسية شاملة تأخذ بعين الاعتبار مصالح جميع الأطراف المعنية.
    وأضاف أن واشنطن ستواصل دعم الجهود الدبلوماسية التي تقودها الأمم المتحدة، مؤكدا أن
    الحل العسكري لن يكون كافيا لإنهاء الأزمة المستمرة. من جانبه، رحب وزير الخارجية بهذه
    الخطوة، معتبرا أنها تمثل تحولا مهما في الموقف الأمريكي من ملفات المنطقة الشائكة.
    وشدد المتحدث باسم الخارجية على أن المشاورات مع الأطراف الإقليمية ستستمر خلال الأسابيع
    المقبلة تمهيدا لعقد مؤتمر دولي حول القضية.""",
    # Economy
    """كشف تقرير اقتصادي حديث صادر عن صندوق النقد الدولي عن تراجع ملحوظ في معدلات النمو
    الاقتصادي بمنطقة الشرق الأوسط وشمال أفريقيا خلال الربع الأخير من العام الجاري، وذلك
    نتيجة لعدة عوامل من بينها ارتفاع أسعار الطاقة وتراجع حجم الاستثمارات الأجنبية المباشرة.
    وأوضح التقرير أن معدل التضخم في عدد من الدول العربية سجل ارتفاعا قياسيا، مما أثر بشكل
    مباشر على القدرة الشرائية للمواطنين وزاد من معدلات الفقر في بعض المناطق. وأوصى خبراء
    الصندوق الحكومات المعنية باتخاذ إجراءات إصلاحية عاجلة تشمل ترشيد الإنفاق العام وتنويع
    مصادر الدخل بعيدا عن الاعتماد على قطاع النفط والغاز، إلى جانب تحسين بيئة الأعمال
    لجذب مزيد من الاستثمارات الأجنبية في القطاعات غير النفطية.""",
    # Military / conflict
    """أفادت مصادر ميدانية بوقوع اشتباكات عنيفة بين القوات الحكومية ومسلحين في محيط المدينة،
    ترافقت مع سماع دوي انفجارات متتالية واستخدام أسلحة ثقيلة في عدد من الأحياء السكنية.
    وذكرت مصادر طبية أن الغارات الجوية التي استهدفت المنطقة أسفرت عن سقوط عدد من القتلى
    والجرحى بين المدنيين، فيما اضطرت عائلات كثيرة إلى النزوح باتجاه المناطق الآمنة هربا
    من القصف المستمر. ودعت منظمات إنسانية دولية إلى فتح ممرات آمنة لإجلاء المدنيين
    وإيصال المساعدات الغذائية والطبية العاجلة إلى السكان المحاصرين، محذرة من تدهور
    الوضع الإنساني في ظل استمرار العمليات العسكرية وانقطاع الخدمات الأساسية.""",
    # Protests
    """خرجت حشود كبيرة من المتظاهرين في العاصمة للمطالبة بإصلاحات سياسية واقتصادية عاجلة،
    وسط إجراءات أمنية مشددة من قبل قوات الشرطة التي انتشرت في محيط الساحات الرئيسية.
    ورفع المحتجون شعارات تندد بالسياسات الحكومية الأخيرة، مطالبين بمحاسبة المسؤولين عن
    تردي الأوضاع المعيشية وارتفاع معدلات البطالة بين الشباب. وشهدت بعض المناطق توترا
    محدودا بين المتظاهرين وقوات الأمن، فيما أكدت السلطات أنها ستتعامل بحزم مع أي محاولات
    للإخلال بالنظام العام، في وقت دعت فيه أحزاب المعارضة إلى مواصلة الحراك الشعبي حتى
    تحقيق مطالب المتظاهرين.""",
    # Humanitarian
    """حذرت منظمات الإغاثة الدولية من تفاقم الأزمة الإنسانية في المخيمات المكتظة باللاجئين،
    مشيرة إلى نقص حاد في المواد الغذائية والأدوية مع اقتراب فصل الشتاء. وأوضح مسؤولو
    الأمم المتحدة أن التمويل المخصص للعمليات الإنسانية في المنطقة لا يغطي سوى جزء يسير
    من الاحتياجات الفعلية، داعين المجتمع الدولي إلى زيادة المساهمات العاجلة لتفادي كارثة
    إنسانية واسعة النطاق. وأضافوا أن آلاف الأطفال باتوا معرضين لخطر سوء التغذية الحاد،
    فيما تعمل الفرق الطبية الميدانية على مدار الساعة لتقديم الرعاية الصحية الأساسية
    وسط ظروف بالغة الصعوبة ونقص حاد في الإمدادات الطبية.""",
]


def main() -> None:
    total_raw = 0
    total_gzip6 = 0
    total_gzip9 = 0
    total_time_gzip6 = 0.0

    print(f"{'body':>6} | {'raw bytes':>10} | {'gzip-6':>8} | {'ratio':>6} | {'gzip-9':>8} | {'ratio':>6} | {'ms (gz-6)':>10}")
    for i, body in enumerate(SAMPLE_BODIES, start=1):
        raw = body.encode("utf-8")

        start = time.perf_counter()
        gz6 = compress_text(body)
        elapsed_ms = (time.perf_counter() - start) * 1000

        gz9 = gzip.compress(raw, compresslevel=9)

        total_raw += len(raw)
        total_gzip6 += len(gz6)
        total_gzip9 += len(gz9)
        total_time_gzip6 += elapsed_ms

        print(
            f"{i:>6} | {len(raw):>10} | {len(gz6):>8} | "
            f"{len(raw) / len(gz6):>5.2f}x | {len(gz9):>8} | "
            f"{len(raw) / len(gz9):>5.2f}x | {elapsed_ms:>9.3f}"
        )

    print()
    print(f"total raw bytes:     {total_raw}")
    print(f"total gzip-6 bytes:  {total_gzip6}  ({total_raw / total_gzip6:.2f}x, {total_time_gzip6:.3f} ms total)")
    print(f"total gzip-9 bytes:  {total_gzip9}  ({total_raw / total_gzip9:.2f}x)")
    print(f"avg raw body size:   {total_raw / len(SAMPLE_BODIES):.0f} bytes")
    print(f"avg gzip-6 body:     {total_gzip6 / len(SAMPLE_BODIES):.0f} bytes")
    print(f"avg compress time:   {total_time_gzip6 / len(SAMPLE_BODIES):.3f} ms/doc")


if __name__ == "__main__":
    main()

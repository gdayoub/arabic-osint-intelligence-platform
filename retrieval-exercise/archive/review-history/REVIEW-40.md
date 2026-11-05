# Review 40 questions

George: fill in **Your label** and **Your reason** below each question. Save this file in VS Code.
This Markdown file is the human-editable review worksheet; review-40.json is the initial machine-readable draft.
After you finish, we will import your decisions into a new version; do not regenerate over your edits.

## How to label

Judge whether the displayed passage helps answer the exact English question.

- **relevant**: provides the requested answer, including an explicit statement that something is unknown, undisclosed, or not established when that answers the question.
- **partly_relevant**: provides useful evidence but leaves part of the requested answer unsupported.
- **irrelevant**: provides no useful evidence for the requested answer; shared keywords alone are not enough.
- **unsure**: translation, wording, scope, or evidence is ambiguous. Explain the issue instead of forcing a label.

A negative answer can be relevant. Missing an answer does not automatically mean the passage is unrelated.
Relevance means the article supplies evidence, not that its reported claims have been independently fact-checked.
Glosses are assistant drafts, not independently verified translations; quotations and attribution may be paraphrased.
If a gloss looks wrong, record the correction. Leave Arabic verification blank unless you actually checked it.
**Corpus answerability** is a different question: do not mark a query unanswerable in all 50 documents just because this one passage lacks its answer.
Use not_reviewed unless you checked the collection; if another passage answers it, record that document and span.

## What this packet is

40 unique draft questions, each with one candidate passage from a different document, drawn from the 50-document local sample.
This is an initial annotation pass, not exhaustive relevance judgments for retrieval metrics.
Later we must pool more candidates and check other valid answers before calculating Recall, nDCG, or MRR.
Questions were written after reading the sample; this can make them easier than natural searches.
The 50 documents stay together in evaluation-candidate quarantine. No training pairs are generated.
Story and identity group review, human labels, corpus answerability, and test-set freezing remain pending.

Snapshot: retrieval-fifty-v0.1. Offsets below are zero-based Unicode code points, end exclusive, against original text.

---

## Q01 — Which shipping strait did Pakistan's army chief discuss reopening in his talks with Iran?

**Source:** AsharqAlAwsat · document 3748 · published 2026-08-25T03:29:31.000Z

[Original local text](originals/3748.txt) · [Source URL](https://aawsat.com/%D8%B4%D8%A4%D9%88%D9%86-%D8%A5%D9%82%D9%84%D9%8A%D9%85%D9%8A%D8%A9/5310775-%D8%A8%D8%A7%D9%83%D8%B3%D8%AA%D8%A7%D9%86-%D9%82%D8%A7%D8%A6%D8%AF-%D8%A7%D9%84%D8%AC%D9%8A%D8%B4-%D8%A8%D8%AD%D8%AB-%D9%85%D8%B9-%D8%A5%D9%8A%D8%B1%D8%A7%D9%86-%D9%85%D9%86%D8%B9-%D8%A7%D9%84%D9%85%D8%B2%D9%8A%D8%AF-%D9%85%D9%86-%D8%A7%D9%84%D8%AA%D8%B5%D8%B9%D9%8A%D8%AF)

**Arabic passage** — code points [56, 319):

<div lang="ar" dir="rtl">

قال الجيش الباكستاني في بيان، ‌اليوم ‌(الثلاثاء)، ​إن ‌قائده ⁠عاصم ​منير، أجرى محادثات ⁠مع إيران ركزت على ⁠إجراءات ‌تشمل منع ‌المزيد ​من ‌تصعيد ‌الصراع وإعادة فتح مضيق هرمز. وأضاف ‌البيان أن منير ⁠ناقش السلام ⁠الإقليمي وسبل التوصل إلى تسوية للنزاعات عبر ​التفاوض.

</div>

**English gloss (draft):** Pakistan's army said on Tuesday that its chief, Asim Munir, held talks with Iran focused on measures including preventing further escalation of the conflict and reopening the Strait of Hormuz. The statement added that Munir discussed regional peace and ways to reach a negotiated settlement of disputes.

**Your label:** 

**Your reason:** 

**Arabic verified (yes/no/unsure):** 

**Gloss correction, if any:** 

**Corpus answerability (not_reviewed/answerable/unanswerable/unsure):** not_reviewed

**Other relevant document/span, if found:** 

---

## Q02 — What object did police find in the truck parked illegally near the US Capitol?

**Source:** AkhbarAlKhaleej · document 4481 · published 2026-08-26T23:00:00.000Z

[Original local text](originals/4481.txt) · [Source URL](https://akhbar-alkhaleej.com/news/article/1448444)

**Arabic passage** — code points [46, 261):

<div lang="ar" dir="rtl">

ألقت‭ ‬شرطة‭ ‬مبنى‭ ‬الكابيتول‭ ‬الأمريكي‭ ‬القبض‭ ‬على‭ ‬رجل‭ ‬من‭ ‬ولاية‭ ‬كاليفورنيا،‭ ‬بعدما‭ ‬عثرت‭ ‬على‭ ‬مقصلة‭ ‬في‭ ‬صندوق‭ ‬شاحنته‭ ‬المتوقفة‭ ‬بصورة‭ ‬غير‭ ‬قانونية‭ ‬قرب‭ ‬مجمع‭ ‬الكابيتول‭ ‬في‭ ‬واشنطن‭.

</div>

**English gloss (draft):** US Capitol Police arrested a man from California after finding a guillotine in the bed of his truck, which was parked illegally near the Capitol complex in Washington.

**Your label:** 

**Your reason:** 

**Arabic verified (yes/no/unsure):** 

**Gloss correction, if any:** 

**Corpus answerability (not_reviewed/answerable/unanswerable/unsure):** not_reviewed

**Other relevant document/span, if found:** 

---

## Q03 — Does the reported EP2 study establish that targeting this receptor is safe and effective in humans?

**Source:** AlRai · document 8790 · published 2026-09-09T06:01:00.000Z

[Original local text](originals/8790.txt) · [Source URL](https://www.alraimedia.com/article/1778779/أخيرة/أخبار-منوعة/سر-الشباب-دراسة-تكشف-عن-مفتاح-لإبطاء-الشيخوخة)

**Arabic passage** — code points [1348, 1500):

<div lang="ar" dir="rtl">

لكن الباحثين يؤكدون أن النتائج ما زالت في مراحلها المبكرة، ولا تعني وجود علاج للشيخوخة حاليًا، إذ لم تُثبت بعد فاعلية استهداف (EP2) لدى البشر أو سلامته.

</div>

**English gloss (draft):** The researchers stress that the findings are still at an early stage and do not mean there is currently a treatment for aging: the effectiveness and safety of targeting EP2 in humans have not yet been established.

**Your label:** 

**Your reason:** 

**Arabic verified (yes/no/unsure):** 

**Gloss correction, if any:** 

**Corpus answerability (not_reviewed/answerable/unanswerable/unsure):** not_reviewed

**Other relevant document/span, if found:** 

---

## Q04 — Who produced Filmed in Gaza, and which award did the documentary win?

**Source:** Wafa · document 3877 · published 2026-05-29T13:49:00.000Z

[Original local text](originals/3877.txt) · [Source URL](https://www.wafa.ps/news/2026/5/29/فيلم-وثائقي-عن-صحفيي-غزة-يفوز-بجائزة-إيمي-العالمية-148039)

**Arabic passage** — code points [54, 314):

<div lang="ar" dir="rtl">

غزة 29-5-2026 وفا- فاز الفيلم الوثائقي "صُوِّر في غزة (Filmed in Gaza) ، من إنتاج شبكة NBC News الأميركية، بجائزة "إيمي" العالمية للأخبار والأفلام الوثائقية، بعد توثيقه عمل الصحفيين الفلسطينيين داخل قطاع غزة خلال عدوان الاحتلال الإسرائيلي المتواصل على القطاع .

</div>

**English gloss (draft):** Gaza, 29 May 2026, WAFA: The documentary Filmed in Gaza, produced by the American network NBC News, won an international Emmy award for news and documentaries after documenting Palestinian journalists' work in Gaza during what the report describes as the continuing Israeli offensive.

**Your label:** 

**Your reason:** 

**Arabic verified (yes/no/unsure):** 

**Gloss correction, if any:** 

**Corpus answerability (not_reviewed/answerable/unanswerable/unsure):** not_reviewed

**Other relevant document/span, if found:** 

---

## Q05 — What exact total amount did the suspect collect through the alleged fake compensation committee?

**Source:** LibyaAlAhrar · document 4179 · published 2026-08-26T08:34:20.000Z

[Original local text](originals/4179.txt) · [Source URL](https://libyaalahrar.tv/2026/08/26/%d8%ad%d8%a8%d8%b3-%d9%85%d9%86%d8%aa%d8%ad%d9%84-%d8%b5%d9%81%d8%a9-%d8%b1%d8%a6%d9%8a%d8%b3-%d9%84%d8%ac%d9%86%d8%a9-%d8%aa%d8%b9%d9%88%d9%8a%d8%b6-%d8%a3%d8%b6%d8%b1%d8%a7%d8%b1-%d8%a7%d9%84%d8%a7)

**Arabic passage** — code points [846, 974):

<div lang="ar" dir="rtl">

كما وجّه المحقق إطار الخبرة المختص بحصر المعاملات التي أجراها المتهم، وتحديد قيمة المبالغ المالية التي جرى تحصيلها من المتضررين.

</div>

**English gloss (draft):** The investigator also directed the relevant experts to inventory the transactions carried out by the accused and determine the amount of money collected from those affected.

**Your label:** 

**Your reason:** 

**Arabic verified (yes/no/unsure):** 

**Gloss correction, if any:** 

**Corpus answerability (not_reviewed/answerable/unanswerable/unsure):** not_reviewed

**Other relevant document/span, if found:** 

---

## Q06 — Which two novels were compared in Rateb Sukkar's Damascus lecture about literary settings?

**Source:** SANA · document 13703 · published 2026-10-08T14:22:36.000Z

[Original local text](originals/13703.txt) · [Source URL](https://sana.sy/culture-and-arts/2600822)

**Arabic passage** — code points [86, 360):

<div lang="ar" dir="rtl">

أقيمت في مقر اتحاد الكتّاب العرب بدمشق، اليوم الخميس، محاضرة قدمها الأديب السوري الدكتور راتب سكر بمشاركة القاص عبد الله النفاخ، تناولت دراسة مقارنة لعنصر المكان بين الرواية العربية “ميرامار” للروائي المصري نجيب محفوظ، والرواية الفرنسية “الأب غوريو” للكاتب أونوريه دي بلزاك.

</div>

**English gloss (draft):** A lecture at the Arab Writers Union headquarters in Damascus on Thursday was delivered by Syrian writer Dr. Rateb Sukkar, with short-story writer Abdullah al-Naffakh participating. It compared setting in Miramar by Egyptian novelist Naguib Mahfouz and Father Goriot by Honore de Balzac.

**Your label:** 

**Your reason:** 

**Arabic verified (yes/no/unsure):** 

**Gloss correction, if any:** 

**Corpus answerability (not_reviewed/answerable/unanswerable/unsure):** not_reviewed

**Other relevant document/span, if found:** 

---

## Q07 — What increases in annually burned land did the reported study project under low versus high emissions?

**Source:** BBCArabic · document 633 · published 2026-08-21T00:00:00.000Z

[Original local text](originals/633.txt) · [Source URL](https://www.bbc.com/arabic/articles/c8xn0ryrpleo)

**Arabic passage** — code points [3523, 3691):

<div lang="ar" dir="rtl">

ويشير الكاتب إلى أن انخفاض الانبعاثات قد يؤدي إلى حرق مساحة من الأراضي تزيد بنحو 39 في المئة سنوياً، أمّا ارتفاع الانبعاثات فسيرفع النسبة إلى 192 في المئة، وفق الدراسة.

</div>

**English gloss (draft):** The writer says that low emissions could lead to about 39 percent more land burning annually, whereas high emissions would raise the increase to 192 percent, according to the study.

**Your label:** 

**Your reason:** 

**Arabic verified (yes/no/unsure):** 

**Gloss correction, if any:** 

**Corpus answerability (not_reviewed/answerable/unanswerable/unsure):** not_reviewed

**Other relevant document/span, if found:** 

---

## Q08 — Which Tunisian governorates were forecast to receive the heaviest rain on August 30, 2026, and what amounts were expected?

**Source:** AlChourouk · document 5504 · published 2026-08-30T12:03:42.000Z

[Original local text](originals/5504.txt) · [Source URL](https://www.alchourouk.com/article/%D8%A7%D9%84%D8%B1%D8%B5%D8%AF-%D8%A7%D9%84%D8%AC%D9%88%D9%8A-%D8%AA%D9%82%D9%84%D8%A8%D8%A7%D8%AA-%D8%AC%D9%88%D9%8A%D8%A9-%D9%85%D9%86%D8%AA%D8%B8%D8%B1%D8%A9-%D8%B8%D9%87%D8%B1-%D8%A7%D9%84%D9%8A%D9%88%D9%85-%D8%A8%D9%87%D8%B0%D9%87-%D8%A7%D9%84%D8%AC%D9%87%D8%A7%D8%AA)

**Arabic passage** — code points [55, 433):

<div lang="ar" dir="rtl">

أفاد المعهد الوطني للرصد الجوّي ، في نشرة متابعة ، بأن الوضع ظهر اليوم الأحد 30 أوت 2026 يتميّز بظهور خلايا رعدية مصحوبة بأمطار بالمناطق الغربية للشمال والوسط ثم تشمل تدريجيا الجهات الشرقية وتكون أحيانا غزيرة بولايات سليانة والكاف والقصرين حيث تتراوح أعلى الكميات بين 20 و40 مليمترا مع تساقط محلي للبرد وهبوب رياح قوية تتجاوز مؤقتا 70 كلم/س في شكل هبات أثناء ظهور السحب الرعدية.

</div>

**English gloss (draft):** The National Institute of Meteorology said that on Sunday afternoon, 30 August 2026, thunderstorms and rain would appear in western parts of the north and center, spreading east. Rain would sometimes be heavy in Siliana, Kef, and Kasserine, with the highest amounts between 20 and 40 millimeters, local hail, and gusts temporarily exceeding 70 km/h during thunderstorms.

**Your label:** 

**Your reason:** 

**Arabic verified (yes/no/unsure):** 

**Gloss correction, if any:** 

**Corpus answerability (not_reviewed/answerable/unanswerable/unsure):** not_reviewed

**Other relevant document/span, if found:** 

---

## Q09 — Where did the Al-Mulqi family's marriage-proposal delegation meet the Al-Bitar family?

**Source:** AmmonNews · document 13407 · published not recorded

[Original local text](originals/13407.txt) · [Source URL](https://www.ammonnews.net/article/1033858)

**Arabic passage** — code points [29, 332):

<div lang="ar" dir="rtl">

عمون - توجّهت مساء أمس الجمعة جاهة كريمة من آل الملقي إلى آل البيطار في جمعية الفيحاء – الشميساني، لطلب يد صاحبة الصون والعفاف، كريمة السيد عبد اللطيف سليم البيطار، للشاب طلحة الملقي، وسط أجواء من الفرح والمودة التي جمعت العائلتين الكريمتين، والدعاء بأن يبارك الله هذا البناء الجديد، ويحفظ الأردن وأهله.

</div>

**English gloss (draft):** Ammon: A delegation from the Al-Mulqi family went to the Al-Bitar family on Friday evening at Al-Fayhaa Association in Shmeisani to ask for the hand of Abdul Latif Salim al-Bitar's daughter for Talha al-Mulqi. The report describes a joyful, affectionate gathering and prayers for the couple and Jordan.

**Your label:** 

**Your reason:** 

**Arabic verified (yes/no/unsure):** 

**Gloss correction, if any:** 

**Corpus answerability (not_reviewed/answerable/unanswerable/unsure):** not_reviewed

**Other relevant document/span, if found:** 

---

## Q10 — What was the name of the hospital where Marta Kelly de Oliveira Silva died?

**Source:** AlKhaleej · document 1055 · published 2026-08-20T19:18:00.000Z

[Original local text](originals/1055.txt) · [Source URL](https://www.alkhaleej.ae/2026-08-20/منوعات/الصحة/مؤثرة-برازيلية-تخسر-حياتها-بجراحة-تجميلية)

**Arabic passage** — code points [256, 355):

<div lang="ar" dir="rtl">

وبعد تدهور حالتها الصحية، نُقلت إلى مستشفى في مدينة فولتا ريدوندا، حيث توفيت بوحدة العناية المركزة.

</div>

**English gloss (draft):** After her health deteriorated, she was taken to a hospital in the city of Volta Redonda, where she died in intensive care.

**Your label:** 

**Your reason:** 

**Arabic verified (yes/no/unsure):** 

**Gloss correction, if any:** 

**Corpus answerability (not_reviewed/answerable/unanswerable/unsure):** not_reviewed

**Other relevant document/span, if found:** 

---

## Q11 — What were Turkey's reported monthly and annual consumer inflation rates for August?

**Source:** Akhbarona · document 6910 · published 2026-09-03T11:27:00.000Z

[Original local text](originals/6910.txt) · [Source URL](https://www.akhbarona.com/divers/431862.html)

**Arabic passage** — code points [85, 223):

<div lang="ar" dir="rtl">

أظهرت بيانات معهد الإحصاء التركي ارتفاع أسعار المستهلكين في البلاد بنسبة 1.84% خلال غشت على أساس شهري، فيما بلغ معدل التضخم السنوي 31.51%.

</div>

**English gloss (draft):** Data from the Turkish Statistical Institute showed consumer prices rising by 1.84 percent in August compared with the previous month, with annual inflation at 31.51 percent.

**Your label:** 

**Your reason:** 

**Arabic verified (yes/no/unsure):** 

**Gloss correction, if any:** 

**Corpus answerability (not_reviewed/answerable/unanswerable/unsure):** not_reviewed

**Other relevant document/span, if found:** 

---

## Q12 — Which four Saudi cities did the coalition spokesman say were targeted, and how many civilians were injured?

**Source:** CNNArabic · document 8563 · published 2026-09-08T04:43:24.000Z

[Original local text](originals/8563.txt) · [Source URL](https://arabic.cnn.com/middle-east/article/2026/09/08/attacks-four-saudi-cities-support-legitimacy-houthis-confront-victory-all-firmness)

**Arabic passage** — code points [409, 757):

<div lang="ar" dir="rtl">

وأضاف المالكي، في بيان عبر منصة "إكس"، تويتر سابقًا، الثلاثاء، أن "اعتداءات الميليشيا الحوثية الإرهابية العبثية، والتي كان آخرها استهداف أعيان مدنية واقتصادية في مدن (أبها، خميس مشيط، جازان، نجران)، ونتج عنها إصابة (73) من المدنيين، بينهم نساء وأطفال، تعد انتهاكًا سافرًا لسيادة المملكة وتهديدًا مباشرًا لأمن وسلامة مواطنيها والمقيمين على أراضيها".

</div>

**English gloss (draft):** In a statement on X on Tuesday, al-Maliki said the Houthis' latest attacks targeted civilian and economic sites in Abha, Khamis Mushait, Jazan, and Najran, injuring 73 civilians, including women and children. He described the attacks as a violation of Saudi sovereignty and a direct threat to citizens and residents.

**Your label:** 

**Your reason:** 

**Arabic verified (yes/no/unsure):** 

**Gloss correction, if any:** 

**Corpus answerability (not_reviewed/answerable/unanswerable/unsure):** not_reviewed

**Other relevant document/span, if found:** 

---

## Q13 — How many CPAP devices did Al-Tadamon Charitable Society provide in its reported assistance project?

**Source:** AlWatanVoice · document 3891 · published 2023-08-15T19:34:59.000Z

[Original local text](originals/3891.txt) · [Source URL](https://www.alwatanvoice.com/arabic/news/2023/08/15/1532005.html)

**Arabic passage** — code points [311, 407):

<div lang="ar" dir="rtl">

وضمن المساعدات الانسانية الطبية تم توفير 5 أجهزة اوكسجين generator 5 لتر و 10لتر ، و3 أجهزة cpap

</div>

**English gloss (draft):** As part of the medical humanitarian assistance, five oxygen generators of 5-liter and 10-liter capacities and three CPAP devices were provided.

**Your label:** 

**Your reason:** 

**Arabic verified (yes/no/unsure):** 

**Gloss correction, if any:** 

**Corpus answerability (not_reviewed/answerable/unanswerable/unsure):** not_reviewed

**Other relevant document/span, if found:** 

---

## Q14 — What defense-budget shortfall did Zelensky report, and why did he dismiss the former defense minister?

**Source:** AlMasryAlYoum · document 2694 · published 2026-08-24T07:24:10.000Z

[Original local text](originals/2694.txt) · [Source URL](https://www.almasryalyoum.com/news/details/4344486)

**Arabic passage** — code points [890, 1094):

<div lang="ar" dir="rtl">

وكشف زيلينسكي عن عجز في ميزانية الدفاع يقدر بـ27 مليار دولار، إلى جانب تراجع إمدادات صواريخ «باتريوت»، موضحًا أن أوكرانيا تسلمت 675 صاروخًا في 2023 مقابل 364 في 2025، وتتوقع 264 صاروخًا خلال العام الجاري.

</div>

**English gloss (draft):** Zelensky disclosed a defense-budget shortfall estimated at 27 billion dollars and a decline in Patriot missile supplies. He said Ukraine received 675 missiles in 2023 versus 364 in 2025 and expected 264 during the current year.

**Your label:** 

**Your reason:** 

**Arabic verified (yes/no/unsure):** 

**Gloss correction, if any:** 

**Corpus answerability (not_reviewed/answerable/unanswerable/unsure):** not_reviewed

**Other relevant document/span, if found:** 

---

## Q15 — How did the Amazigh teachers' coordination group want the number of classes per teacher to change?

**Source:** Hespress · document 12415 · published 2026-10-04T15:00:56.000Z

[Original local text](originals/12415.txt) · [Source URL](https://www.hespress.com/%d8%a3%d8%b3%d8%a7%d8%aa%d8%b0%d8%a9-%d8%a7%d9%84%d8%a3%d9%85%d8%a7%d8%b2%d9%8a%d8%ba%d9%8a%d8%a9-%d9%8a%d8%ad%d8%aa%d8%ac%d9%88%d9%86-%d9%85%d9%86-%d8%a3%d8%ac%d9%84-%d8%a7%d9%84%d8%b9%d8%af%d8%a7-1817851.html)

**Arabic passage** — code points [676, 929):

<div lang="ar" dir="rtl">

وطالبت التنسيقية ذاتها بتقليص عدد الأقسام من 8 إلى 4 لكل أستاذ، مع الرفع من الغلاف الزمني المخصص للغة الأمازيغية، وتوفير الكتب المدرسية والموارد الديداكتيكية والعُدَد التربوية اللازمة لتدريسها، إلى جانب ضمان التكوين والتأطير والتفتيش للأساتذة المتخصصين.

</div>

**English gloss (draft):** The coordination group called for reducing the number of classes from eight to four per teacher, increasing the time allocated to Amazigh, supplying textbooks and teaching resources, and ensuring training, support, and inspection for specialist teachers.

**Your label:** 

**Your reason:** 

**Arabic verified (yes/no/unsure):** 

**Gloss correction, if any:** 

**Corpus answerability (not_reviewed/answerable/unanswerable/unsure):** not_reviewed

**Other relevant document/span, if found:** 

---

## Q16 — At which political party's conference did Putin make the quoted remarks about Ukrainian attacks?

**Source:** AnNahar · document 1481 · published 2026-08-22T15:55:00.000Z

[Original local text](originals/1481.txt) · [Source URL](https://www.annahar.com/international/340603/%D8%A8%D9%88%D8%AA%D9%8A%D9%86-%D8%A3%D9%88%D9%83%D8%B1%D8%A7%D9%86%D9%8A%D8%A7-%D9%81%D8%AA%D8%AD%D8%AA-%D8%B9%D9%84%D9%89-%D9%86%D9%81%D8%B3%D9%87%D8%A7-%D8%A3%D8%A8%D9%88%D8%A7%D8%A8-%D8%A7%D9%84%D8%AC%D8%AD%D9%8A%D9%85)

**Arabic passage** — code points [448, 588):

<div lang="ar" dir="rtl">

وقال بوتين، خلال مؤتمر حزب "روسيا الموحدة"، إنّ الهجمات التي تشنها كييف "تزداد وحشية وهمجية"، معتبراً أنّها تعكس "طبيعة القيادة الأوكرانية".

</div>

**English gloss (draft):** Speaking at a United Russia party conference, Putin said Kyiv's attacks were becoming more brutal and barbaric, which he claimed reflected the nature of the Ukrainian leadership.

**Your label:** 

**Your reason:** 

**Arabic verified (yes/no/unsure):** 

**Gloss correction, if any:** 

**Corpus answerability (not_reviewed/answerable/unanswerable/unsure):** not_reviewed

**Other relevant document/span, if found:** 

---

## Q17 — According to the UAE Justice Ministry statement, did the seven sanctioned companies have valid UAE business licenses?

**Source:** SudanTribune · document 3836 · published 2025-04-04T13:49:45.000Z

[Original local text](originals/3836.txt) · [Source URL](https://sudantribune.net/article/299363)

**Arabic passage** — code points [884, 1047):

<div lang="ar" dir="rtl">

وأضاف البيان الإماراتي إن “الوزارة تؤكد أن أيّاً من هذه الشركات السبع لا تملك ترخيصًا تجاريًا ساري المفعول في دولة الإمارات، ولا تمارس أيّ منها أعمالها في الدولة”.

</div>

**English gloss (draft):** The UAE statement added: 'The ministry confirms that none of these seven companies has a valid commercial license in the United Arab Emirates, and none conducts its business in the country.'

**Your label:** 

**Your reason:** 

**Arabic verified (yes/no/unsure):** 

**Gloss correction, if any:** 

**Corpus answerability (not_reviewed/answerable/unanswerable/unsure):** not_reviewed

**Other relevant document/span, if found:** 

---

## Q18 — Which two ports did Russia's defense ministry say contained the targeted military-equipment and fuel storage sites?

**Source:** Youm7 · document 905 · published 2026-08-22T08:59:44.000Z

[Original local text](originals/905.txt) · [Source URL](https://www.youm7.com/story/2026/8/22/روسيا-تستهدف-مرافق-البنى-التحتية-للطاقة-والنقل-الداعمة-للقوات-الأوكرانية/7520710)

**Arabic passage** — code points [685, 846):

<div lang="ar" dir="rtl">

وتابع البيان: "علاوة على ذلك، استُهدفت، في 22 أغسطس، مستودعات معدات عسكرية وخزانات وقود وزيوت تشحيم مخصصة للقوات المسلحة الأوكرانية في ميناءي تشيرنومورسك ويوجني"

</div>

**English gloss (draft):** The statement continued that on August 22, military-equipment depots and fuel and lubricant tanks designated for the Ukrainian armed forces were targeted at the ports of Chornomorsk and Yuzhny.

**Your label:** 

**Your reason:** 

**Arabic verified (yes/no/unsure):** 

**Gloss correction, if any:** 

**Corpus answerability (not_reviewed/answerable/unanswerable/unsure):** not_reviewed

**Other relevant document/span, if found:** 

---

## Q19 — Which authority did Libya's High Council of State say has exclusive jurisdiction to enforce punishments?

**Source:** AlJazeeraArabic · document 2569 · published 2026-08-24T03:32:05.000Z

[Original local text](originals/2569.txt) · [Source URL](https://www.aljazeera.net/news/2026/8/24/المجلس-الأعلى-للدولة-يرفض-أحكام)

**Arabic passage** — code points [68, 288):

<div lang="ar" dir="rtl">

أعرب المجلس الأعلى للدولة في ليبيا عن بالغ قلقه، بشأن تنفيذ أحكام بالإعدام بحق عدد من المواطنين الليبيين في شرق البلاد، على خلفية قضايا وُصفت بالإرهابية والجنائية، مشددا على أن إنفاذ العقوبات اختصاص حصري للسلطة القضائية.

</div>

**English gloss (draft):** Libya's High Council of State expressed deep concern about executions of Libyan citizens in the east of the country in cases described as terrorism-related and criminal. It stressed that enforcement of punishments is exclusively the judiciary's responsibility.

**Your label:** 

**Your reason:** 

**Arabic verified (yes/no/unsure):** 

**Gloss correction, if any:** 

**Corpus answerability (not_reviewed/answerable/unanswerable/unsure):** not_reviewed

**Other relevant document/span, if found:** 

---

## Q20 — Did Israeli police identify the officials they recommended prosecuting in the alleged document-falsification case?

**Source:** AsharqAlAwsat · document 14025 · published 2026-10-09T17:07:56.000Z

[Original local text](originals/14025.txt) · [Source URL](https://aawsat.com/%D8%B4%D8%A4%D9%88%D9%86-%D8%A5%D9%82%D9%84%D9%8A%D9%85%D9%8A%D8%A9/5327805-%D8%A7%D9%84%D8%B4%D8%B1%D8%B7%D8%A9-%D8%A7%D9%84%D8%A5%D8%B3%D8%B1%D8%A7%D8%A6%D9%8A%D9%84%D9%8A%D8%A9-%D8%AA%D8%A4%D9%83%D8%AF-%D9%85%D9%83%D8%AA%D8%A8-%D9%86%D8%AA%D9%86%D9%8A%D8%A7%D9%87%D9%88-%D8%B2%D9%8A%D9%91%D9%81-%D9%88%D8%AB%D8%A7%D8%A6%D9%82-%D9%88%D8%A8%D8%B1%D9%88%D8%AA%D9%88%D9%83%D9%88%D9%84%D8%A7%D8%AA)

**Arabic passage** — code points [287, 546):

<div lang="ar" dir="rtl">

وقالت الشرطة إنها حققت في الموضوع في حينه، بناء على عدة شكاوى وتوصيات، وقدمت تلخيصها إلى النيابة منذ سنتين مع توصية واضحة بمحاكمة المسؤولين. لكنها رفضت الكشف عن هوية هؤلاء المسؤولين، وهل نتنياهو نفسه متورط في القضية أو فقط مدير مكتبه، تساحي برفرمان، ومساعدوه.

</div>

**English gloss (draft):** Police said they investigated at the time following complaints and recommendations, and submitted their summary to prosecutors two years earlier with a clear recommendation to prosecute those responsible. They declined to identify those officials or disclose whether Netanyahu himself was implicated, or only office director Tzachi Braverman and his aides.

**Your label:** 

**Your reason:** 

**Arabic verified (yes/no/unsure):** 

**Gloss correction, if any:** 

**Corpus answerability (not_reviewed/answerable/unanswerable/unsure):** not_reviewed

**Other relevant document/span, if found:** 

---

## Q21 — From which club did Chelsea sign Emiliano Martinez, and how long was his reported contract?

**Source:** AlRai · document 5425 · published 2026-08-30T12:15:00.000Z

[Original local text](originals/5425.txt) · [Source URL](https://www.alraimedia.com/article/1777660/رياضة/رياضة-عالمية/تشلسي-يضم-مارتينيز-لمدة-3-سنوات)

**Arabic passage** — code points [35, 194):

<div lang="ar" dir="rtl">

أعلن نادي تشلسي المنافس ​في الدوري الإنجليزي الممتاز لكرة القدم اليوم، التعاقد مع حارس المرمى إميليانو مارتينيز قادما ​من أستون فيلا بموجب عقد لمدة ثلاث سنوات.

</div>

**English gloss (draft):** Chelsea, which competes in England's Premier League, announced the signing of goalkeeper Emiliano Martinez from Aston Villa on a three-year contract.

**Your label:** 

**Your reason:** 

**Arabic verified (yes/no/unsure):** 

**Gloss correction, if any:** 

**Corpus answerability (not_reviewed/answerable/unanswerable/unsure):** not_reviewed

**Other relevant document/span, if found:** 

---

## Q22 — Which Swedish diplomat met Palestinian National Council chairman Rawhi Fattouh?

**Source:** Wafa · document 3878 · published 2026-06-16T15:05:00.000Z

[Original local text](originals/3878.txt) · [Source URL](https://www.wafa.ps/news/2026/6/16/فتوح-يبحث-مع-القنصل-العام-السويدي-آخر-التطورات-السياسية-والميدانية-148993)

**Arabic passage** — code points [68, 243):

<div lang="ar" dir="rtl">

رام الله 16-6-2026 وفا- بحث رئيس المجلس الوطني الفلسطيني روحي فتوح، والقنصل العام السويدي لدى دولة فلسطين في القدس صوفي بيكر، اليوم الثلاثاء، آخر التطورات السياسية والميدانية.

</div>

**English gloss (draft):** Ramallah, 16 June 2026, WAFA: Palestinian National Council chairman Rawhi Fattouh and Sweden's consul general to the State of Palestine in Jerusalem, Sophie Becker, discussed the latest political and on-the-ground developments on Tuesday.

**Your label:** 

**Your reason:** 

**Arabic verified (yes/no/unsure):** 

**Gloss correction, if any:** 

**Corpus answerability (not_reviewed/answerable/unanswerable/unsure):** not_reviewed

**Other relevant document/span, if found:** 

---

## Q23 — Why did electricity cuts disrupt drinking-water supplies in Sorman?

**Source:** LibyaAlAhrar · document 1546 · published 2026-08-22T10:54:01.000Z

[Original local text](originals/1546.txt) · [Source URL](https://libyaalahrar.tv/2026/08/22/%d9%86%d9%82%d8%b5-%d8%a7%d9%84%d9%85%d9%8a%d8%a7%d9%87-%d9%81%d9%8a-%d8%b5%d8%b1%d9%85%d8%a7%d9%86-%d8%a7%d9%86%d9%82%d8%b7%d8%a7%d8%b9-%d8%a7%d9%84%d9%83%d9%87%d8%b1%d8%a8%d8%a7%d8%a1-%d9%8a%d8%ae)

**Arabic passage** — code points [56, 276):

<div lang="ar" dir="rtl">

أعربت بلدية صرمان عن بالغ قلقها واستيائها من تداعيات انقطاع التيار الكهربائي على مصادر مياه الشرب داخل البلدية، موضحة أن المدينة تعتمد بشكل رئيسي على الآبار الجوفية التي توقفت عدة آبار منها عن العمل بسبب انقطاع الكهرباء.

</div>

**English gloss (draft):** Sorman municipality expressed concern and dissatisfaction over power cuts affecting drinking-water sources. It explained that the city depends mainly on groundwater wells, several of which stopped operating because of the electricity outages.

**Your label:** 

**Your reason:** 

**Arabic verified (yes/no/unsure):** 

**Gloss correction, if any:** 

**Corpus answerability (not_reviewed/answerable/unanswerable/unsure):** not_reviewed

**Other relevant document/span, if found:** 

---

## Q24 — Where and at what time was Eman al-Rifai's lecture on agricultural technology scheduled for September 15, 2026?

**Source:** SANA · document 11093 · published 2026-09-15T07:38:19.000Z

[Original local text](originals/11093.txt) · [Source URL](https://sana.sy/culture-and-arts/2581135)

**Arabic passage** — code points [451, 594):

<div lang="ar" dir="rtl">

3 – محاضرة بعنوان “استثمار التكنولوجيا بالمجال الزراعي” تلقيها المهندسة إيمان الرفاعي، في المركز الثقافي العربي بالعدوي، الساعة الـ 5:00 عصراً.

</div>

**English gloss (draft):** 3. A lecture titled 'Using technology in agriculture,' delivered by engineer Eman al-Rifai at the Arab Cultural Center in al-Adawi, at 5:00 p.m.

**Your label:** 

**Your reason:** 

**Arabic verified (yes/no/unsure):** 

**Gloss correction, if any:** 

**Corpus answerability (not_reviewed/answerable/unanswerable/unsure):** not_reviewed

**Other relevant document/span, if found:** 

---

## Q25 — What was the exact record gold price per ounce in US dollars reported for 2026?

**Source:** BBCArabic · document 89 · published 2026-08-15T00:00:00.000Z

[Original local text](originals/89.txt) · [Source URL](https://www.bbc.com/arabic/articles/cx2r49eznvvo)

**Arabic passage** — code points [45, 465):

<div lang="ar" dir="rtl">

بلغ سعر الذهب مستويات قياسية غير مسبوقة في عام 2026. ساهم عدم الاستقرار العالمي والصراعات وانعدام الثقة بالدولار الأمريكي في هذا الارتفاع، كما دفع الدول إلى تكديس المعدن النفيس. شهد الذهب تقلبات حادة، حيث وصل إلى أعلى مستوياته على الإطلاق، قبل أن يعود إلى مستوياته الطبيعية. هذا الفيديو يشرح العوامل المؤثرة في أسعار الذهب، ولماذا يُعد الذهب مرغوباً فيه بشكل خاص في أوقات الأزمات، ولماذا تقوم الدول بتخزينه بكميات كبيرة،

</div>

**English gloss (draft):** Gold reached unprecedented record levels in 2026. Global instability, conflicts, and distrust of the US dollar contributed to the rise and encouraged countries to accumulate gold. Gold fluctuated sharply, reaching all-time highs before returning to normal levels. The video explains influences on gold prices, why gold is especially sought after during crises, and why countries store large quantities.

**Your label:** 

**Your reason:** 

**Arabic verified (yes/no/unsure):** 

**Gloss correction, if any:** 

**Corpus answerability (not_reviewed/answerable/unanswerable/unsure):** not_reviewed

**Other relevant document/span, if found:** 

---

## Q26 — What was the submission deadline for the Vision+ competition at the 2026 Carthage Film Festival?

**Source:** AlChourouk · document 8028 · published 2026-09-06T22:05:48.000Z

[Original local text](originals/8028.txt) · [Source URL](https://www.alchourouk.com/article/%D8%A3%D9%8A%D8%A7%D9%85-%D9%82%D8%B1%D8%B7%D8%A7%D8%AC-%D8%A7%D9%84%D8%B3%D9%8A%D9%86%D9%85%D8%A7%D8%A6%D9%8A%D8%A9-2026-%D9%81%D8%AA%D8%AD-%D8%A8%D8%A7%D8%A8-%D8%A7%D9%84%D8%AA%D8%B3%D8%AC%D9%8A%D9%84-%D9%84%D9%84%D9%85%D8%B3%D8%A7%D8%A8%D9%82%D8%A9-%D8%A7%D9%84%D8%AF%D9%88%D9%84%D9%8A%D8%A9-%D9%81%D9%8A%D8%B2%D9%8A%D9%88%D9%86)

**Arabic passage** — code points [445, 664):

<div lang="ar" dir="rtl">

وتبقى آجال التسجيل مفتوحة أمام صناع الأفلام إلى حدود 5 أكتوبر القادم لتقديم أعمالهم، ويجب ايداع ملفات الترشح عبر منصة التسجيل والتقديم لصناع الأفلام من جميع أنحاء العالم المتاحة على العنوان الالكتروني inscription.jcc.tn

</div>

**English gloss (draft):** Registration remains open to filmmakers until October 5 to submit their work. Applications must be filed through the global filmmaker registration and submission platform at inscription.jcc.tn.

**Your label:** 

**Your reason:** 

**Arabic verified (yes/no/unsure):** 

**Gloss correction, if any:** 

**Corpus answerability (not_reviewed/answerable/unanswerable/unsure):** not_reviewed

**Other relevant document/span, if found:** 

---

## Q27 — From what date did Saleh al-Daaja's appointment as a Ministry of Culture adviser take effect?

**Source:** AmmonNews · document 11952 · published not recorded

[Original local text](originals/11952.txt) · [Source URL](https://www.ammonnews.net/article/1031729)

**Arabic passage** — code points [38, 293):

<div lang="ar" dir="rtl">

عمون - صدرت في عدد الجريدة الرسمية اليوم الخميس، الإرادة الملكية السامية بالموافقة على قرار مجلس الوزراء المتضمن تعيين السيد صالح عودة غصبان الخصيلات الدعجة بوظيفة مستشار في ملاك وزارة الثقافة بالمجموعة الثانية من الفئة العليا اعتباراً من تاريخ ٢٠٢٦/٩/٢١.

</div>

**English gloss (draft):** Ammon: Thursday's Official Gazette published royal approval of the Cabinet decision appointing Saleh Odeh Ghasban al-Khusailat al-Daaja as an adviser on the Ministry of Culture's staff, in the second group of the senior category, effective 21 September 2026.

**Your label:** 

**Your reason:** 

**Arabic verified (yes/no/unsure):** 

**Gloss correction, if any:** 

**Corpus answerability (not_reviewed/answerable/unanswerable/unsure):** not_reviewed

**Other relevant document/span, if found:** 

---

## Q28 — What basic-pay increase and bonus did Hyundai's union say it had negotiated?

**Source:** AlKhaleej · document 3423 · published 2026-08-25T07:54:00.000Z

[Original local text](originals/3423.txt) · [Source URL](https://www.alkhaleej.ae/2026-08-25/اقتصاد/سيارات/اتفاق-مبدئي-بين-هيونداي-ونقابة-العمال-بعد-إضراب-نادر)

**Arabic passage** — code points [635, 781):

<div lang="ar" dir="rtl">

وأوضحت النقابة لوكالة فرانس برس في بيان «اتفقنا على زيادة الراتب الشهري الأساسي بمقدار 100 ألف وون (72 دولارا) ودفع مكافأة تعادل راتب أربعة أشهر».

</div>

**English gloss (draft):** The union told AFP in a statement: 'We agreed to increase monthly basic pay by 100,000 won (72 dollars) and pay a bonus equivalent to four months' salary.'

**Your label:** 

**Your reason:** 

**Arabic verified (yes/no/unsure):** 

**Gloss correction, if any:** 

**Corpus answerability (not_reviewed/answerable/unanswerable/unsure):** not_reviewed

**Other relevant document/span, if found:** 

---

## Q29 — In what city and year did Cheb Rizki recall receiving help from Mohamed El Ghaoui?

**Source:** Akhbarona · document 4230 · published 2026-08-26T09:08:00.000Z

[Original local text](originals/4230.txt) · [Source URL](https://www.akhbarona.com/culture/431402.html)

**Arabic passage** — code points [128, 404):

<div lang="ar" dir="rtl">

استحضر محمد رزقي، الفنان المغربي المعتزل الشهير بلقب "الشاب رزقي" موقفا شخصيا جمعه بالفنان الراحل محمد الغاوي سنة 2000 بمدينة فرانكفورت الألمانية، خلال أول مشاركة له في سهرة فنية خارج المغرب، مشيدا بالأخلاق التي كان يتحلى بها الراحل وبالطريقة التي تعامل بها معه في تلك الفترة.

</div>

**English gloss (draft):** Mohamed Rizki, the retired Moroccan singer known as Cheb Rizki, recalled a personal encounter with the late Mohamed El Ghaoui in Frankfurt, Germany, in 2000, during Rizki's first concert appearance outside Morocco. He praised El Ghaoui's character and treatment of him at that time.

**Your label:** 

**Your reason:** 

**Arabic verified (yes/no/unsure):** 

**Gloss correction, if any:** 

**Corpus answerability (not_reviewed/answerable/unanswerable/unsure):** not_reviewed

**Other relevant document/span, if found:** 

---

## Q30 — How did Egypt's non-oil private-sector PMI change between August and September in the report?

**Source:** CNNArabic · document 13023 · published 2026-10-06T01:49:35.000Z

[Original local text](originals/13023.txt) · [Source URL](https://arabic.cnn.com/middle-east/article/2026/10/06/egypt-pmi-declines-pressures-regional-turmoil-experts)

**Arabic passage** — code points [127, 366):

<div lang="ar" dir="rtl">

تراجع نشاط القطاع الخاص غير النفطي في مصر خلال سبتمبر / أيلول ، مع هبوط مؤشر مديري المشتريات إلى 47.2 نقطة، مقارنة بـ49.6 نقطة في أغسطس/ آب، في انعكاس لضعف الطلب وارتفاع تكاليف الإنتاج واستمرار حالة عدم اليقين المرتبطة بالتطورات الإقليمية.

</div>

**English gloss (draft):** Activity in Egypt's non-oil private sector declined during September, as the purchasing managers' index fell to 47.2 points from 49.6 in August, reflecting weak demand, higher production costs, and continuing uncertainty linked to regional developments.

**Your label:** 

**Your reason:** 

**Arabic verified (yes/no/unsure):** 

**Gloss correction, if any:** 

**Corpus answerability (not_reviewed/answerable/unanswerable/unsure):** not_reviewed

**Other relevant document/span, if found:** 

---

## Q31 — Where did Abdul Karim al-Zubaidi meet Italy's consul general Giuseppe Fedele?

**Source:** AlWatanVoice · document 3882 · published 2023-04-13T14:10:00.000Z

[Original local text](originals/3882.txt) · [Source URL](https://www.alwatanvoice.com/arabic/news/2023/04/13/1518054.html)

**Arabic passage** — code points [126, 426):

<div lang="ar" dir="rtl">

عقد الاتحاد الفلسطيني للهيئات المحلية ممثلًا برئيسه عبد الكريم الزبيدي اجتماعًا مع القنصل العام لإيطاليا في القدس جوزيبي فيديل في مقر بلدية سلفيت، بهدف بحث سبل تعزيز التعاون المشترك بين الهيئات المحلية الفلسطينية ونظيراتها الإيطالية من خلال المشاريع المشتركة التي ينفذها الاتحاد مع شركائه في إيطاليا.

</div>

**English gloss (draft):** The Palestinian Association of Local Authorities, represented by its chairman Abdul Karim al-Zubaidi, met Italy's consul general in Jerusalem, Giuseppe Fedele, at Salfit municipality headquarters. They discussed cooperation between Palestinian and Italian local authorities through joint projects with partners in Italy.

**Your label:** 

**Your reason:** 

**Arabic verified (yes/no/unsure):** 

**Gloss correction, if any:** 

**Corpus answerability (not_reviewed/answerable/unanswerable/unsure):** not_reviewed

**Other relevant document/span, if found:** 

---

## Q32 — Between which years did Ahmad Hassoun serve as Syria's grand mufti?

**Source:** AnNahar · document 2738 · published 2026-08-24T08:35:00.000Z

[Original local text](originals/2738.txt) · [Source URL](https://www.annahar.com/annahar-pedia/340915/%D9%85%D8%AD%D9%83%D9%85%D8%A9-%D8%A7%D9%84%D8%AC%D9%86%D8%A7%D9%8A%D8%A7%D8%AA-%D8%AF%D9%85%D8%B4%D9%82-%D8%A7%D9%84%D8%AD%D9%83%D9%85-%D8%A8%D8%A7%D9%84%D8%B3%D8%AC%D9%86-%D8%A7%D9%84%D9%85%D8%A4%D8%A8%D8%AF-%D8%B9%D9%84%D9%89-%D9%85%D9%81%D8%AA%D9%8A-%D8%B3%D9%88%D8%B1%D9%8A%D8%A7-%D8%A7%D9%84%D8%B3%D8%A7%D8%A8%D9%82-%D8%A3%D8%AD%D9%85%D8%AF-%D8%AD%D8%B3%D9%88%D9%86)

**Arabic passage** — code points [1061, 1202):

<div lang="ar" dir="rtl">

- أحمد بدر الدين حسون هو المفتي العام السابق لسوريا لأكثر من 16 عاماً، حيث تولّى منصبه منذ العام 2005 حتى إلغاء المنصب بمرسوم رئاسي عام 2021.

</div>

**English gloss (draft):** Ahmad Badr al-Din Hassoun was Syria's former grand mufti for more than 16 years, serving from 2005 until the office was abolished by presidential decree in 2021.

**Your label:** 

**Your reason:** 

**Arabic verified (yes/no/unsure):** 

**Gloss correction, if any:** 

**Corpus answerability (not_reviewed/answerable/unanswerable/unsure):** not_reviewed

**Other relevant document/span, if found:** 

---

## Q33 — Which Kenyan candidate pledged unrestricted visas for Africans traveling within the continent?

**Source:** SudanTribune · document 3834 · published 2024-11-08T20:32:12.000Z

[Original local text](originals/3834.txt) · [Source URL](https://sudantribune.net/article/293056)

**Arabic passage** — code points [78, 415):

<div lang="ar" dir="rtl">

أديس أبابا 8 نوفمبر 2024- أعلن مرشح لرئاسة مفوضية الاتحاد الإفريقي عن نيته تطبيق تأشيرات مفتوحة بلا قيود للأفارقة للتنقل داخل القارة، على غرار تجربة الاتحاد الأوروبي. ويتنافس المرشح الكيني رايلا أودينغا على منصب مفوض الاتحاد الإفريقي مع مرشح آخر من جيبوتي خلال قمة الاتحاد في فبراير المقبل، خلفا للمفوض الحالي موسى فكي الذي انتهت ولايته.

</div>

**English gloss (draft):** Addis Ababa, 8 November 2024: A candidate for African Union Commission chair announced an intention to introduce unrestricted visas for Africans traveling within the continent, modeled on the European Union. Kenyan candidate Raila Odinga was competing with a candidate from Djibouti at the following February's summit to succeed Moussa Faki, whose term the report says had ended.

**Your label:** 

**Your reason:** 

**Arabic verified (yes/no/unsure):** 

**Gloss correction, if any:** 

**Corpus answerability (not_reviewed/answerable/unanswerable/unsure):** not_reviewed

**Other relevant document/span, if found:** 

---

## Q34 — What cargo was the refrigerated truck carrying when it was robbed on the Cairo-Ismailia desert road?

**Source:** Youm7 · document 4554 · published 2026-08-27T14:55:09.000Z

[Original local text](originals/4554.txt) · [Source URL](https://www.youm7.com/story/2026/8/27/أحراز-واقعة-سرقة-سيارة-نقل-بصحراوى-الإسماعيلية-بنادق-وخرطوش-ومسروقات/7527790)

**Arabic passage** — code points [527, 898):

<div lang="ar" dir="rtl">

لم تتلقى الوزارة أية بلاغات فى هذا الشأن، وبالفحص تبين أنه بتاريخ 25 / الجارى وأثناء سير السيارة النقل الظاهرة بمقطع الفيديو (تابعة لإحدى شركات النقل المبرد) بطريق مصر الإسماعيلية الصحراوى بدائرة قسم شرطة الشروق بالقاهرة لتسليم بضائع "عبارة عن دواجن مجمدة" لإحدى الشركات بالعاشر من رمضان قامت سيارة ربع نقل بالسير خلفها وقيام مستقليها بسرقة بعض محتوياتها دون شعور السائق.

</div>

**English gloss (draft):** The ministry had received no reports about the incident. Its examination found that on the 25th of the current month the truck shown in the video, belonging to a refrigerated-transport company, was traveling on the Cairo-Ismailia desert road in the al-Shorouk police district to deliver frozen poultry to a company in 10th of Ramadan City. People in a pickup drove behind it and stole some contents without the driver's awareness.

**Your label:** 

**Your reason:** 

**Arabic verified (yes/no/unsure):** 

**Gloss correction, if any:** 

**Corpus answerability (not_reviewed/answerable/unanswerable/unsure):** not_reviewed

**Other relevant document/span, if found:** 

---

## Q35 — How many deaths in the preceding 24 hours did Wajih Dhakkar report during Tunisia's heatwave?

**Source:** AlJazeeraArabic · document 5024 · published 2026-08-29T05:57:52.000Z

[Original local text](originals/5024.txt) · [Source URL](https://www.aljazeera.net/news/2026/8/29/سجناء-وضحايا-الحر-وفيات-لم-تعلن-عنها)

**Arabic passage** — code points [842, 985):

<div lang="ar" dir="rtl">

وبحسب البيانات التي نشرها رئيس "المنظمة التونسية للأطباء الشبان" وجيه ذكار ليل الجمعة/السبت، فإنه تم تسجيل 13 حالة وفاة في 24 ساعة الأخيرة فقط.

</div>

**English gloss (draft):** According to information published by Wajih Dhakkar, head of the Tunisian Organization of Young Doctors, on Friday night into Saturday, 13 deaths were recorded in the previous 24 hours alone.

**Your label:** 

**Your reason:** 

**Arabic verified (yes/no/unsure):** 

**Gloss correction, if any:** 

**Corpus answerability (not_reviewed/answerable/unanswerable/unsure):** not_reviewed

**Other relevant document/span, if found:** 

---

## Q36 — What global growth rate did the IMF associate with AI investment, and exactly how many graduate jobs would be lost?

**Source:** AsharqAlAwsat · document 14773 · published 2026-10-12T04:36:23.000Z

[Original local text](originals/14773.txt) · [Source URL](https://aawsat.com/%D8%A7%D9%84%D8%A7%D9%82%D8%AA%D8%B5%D8%A7%D8%AF/5328535-%D8%B5%D9%86%D8%AF%D9%88%D9%82-%D8%A7%D9%84%D9%86%D9%82%D8%AF-%D8%A7%D9%84%D8%AF%D9%88%D9%84%D9%8A-%D8%A7%D9%84%D8%B0%D9%83%D8%A7%D8%A1-%D8%A7%D9%84%D8%A7%D8%B5%D8%B7%D9%86%D8%A7%D8%B9%D9%8A-%D9%8A%D8%AF%D8%B9%D9%85-%D8%A7%D9%84%D9%86%D9%85%D9%88-%D8%A7%D9%84%D8%B9%D8%A7%D9%84%D9%85%D9%8A-%D8%B9%D9%86%D8%AF-3-%D8%B1%D8%BA%D9%85-%D8%B5%D8%AF%D9%85%D8%A9-%D8%A7%D9%84%D8%B7%D8%A7%D9%82%D8%A9)

**Arabic passage** — code points [301, 565):

<div lang="ar" dir="rtl">

وفيما يقدّر صندوق النقد الدولي أن هذه الاستثمارات تسهم في إبقاء النمو العالمي عند نحو 3 في المائة، تضغط صدمة إمدادات الطاقة وارتفاع أعباء الديون على الاقتصادات، ولا سيما الدول النامية، لتتصدر هذه التحديات مناقشات الاجتماعات السنوية للصندوق والبنك الدولي في بانكوك.

</div>

**English gloss (draft):** The IMF estimates that these investments help keep global growth at around 3 percent. Meanwhile, the energy-supply shock and rising debt burdens pressure economies, especially developing countries, placing these challenges at the center of the IMF and World Bank annual meetings in Bangkok.

**Your label:** 

**Your reason:** 

**Arabic verified (yes/no/unsure):** 

**Gloss correction, if any:** 

**Corpus answerability (not_reviewed/answerable/unanswerable/unsure):** not_reviewed

**Other relevant document/span, if found:** 

---

## Q37 — What was the Asian spot price of liquefied natural gas after the reported attack on Larak Island?

**Source:** AlRai · document 5676 · published 2026-08-31T01:49:00.000Z

[Original local text](originals/5676.txt) · [Source URL](https://www.alraimedia.com/article/1777775/اقتصاد/النفط-يقفز-بأكثر-من-2-بعد-هجوم-أمريكي-على-جزيرة-لارك-الإيرانية)

**Arabic passage** — code points [355, 594):

<div lang="ar" dir="rtl">

وارتفعت العقود الآجلة ⁠لخام برنت 2.22 دولار أو 2.52 ​بالمئة لتصل إلى 90.32 دولار للبرميل ⁠بحلول ‌الساعة ​2202 بتوقيت جرينتش، بينما استقر سعر ‌خام غرب تكساس الوسيط ⁠الأمريكي عند ⁠85.41 دولار ⁠للبرميل ​بارتفاع قدره 2.01 دولار أو 2.41 بالمئة.

</div>

**English gloss (draft):** Brent crude futures rose 2.22 dollars, or 2.52 percent, to 90.32 dollars a barrel by 22:02 GMT, while US West Texas Intermediate crude stood at 85.41 dollars a barrel, up 2.01 dollars or 2.41 percent.

**Your label:** 

**Your reason:** 

**Arabic verified (yes/no/unsure):** 

**Gloss correction, if any:** 

**Corpus answerability (not_reviewed/answerable/unanswerable/unsure):** not_reviewed

**Other relevant document/span, if found:** 

---

## Q38 — How many nongovernmental hospitals in Gaza stopped operating because of fuel shortages, and what were their names?

**Source:** Wafa · document 11484 · published 2026-09-23T11:05:00.000Z

[Original local text](originals/11484.txt) · [Source URL](https://www.wafa.ps/news/2026/9/23/شبكة-المنظمات-الأهلية-في-قطاع-غزة-أكثر-من-75)

**Arabic passage** — code points [798, 1048):

<div lang="ar" dir="rtl">

وقال صالحة إن ثلاثة مستشفيات أهلية عاملة في قطاع غزة خرجت عن الخدمة بسبب نقص الوقود، مضيفا أن توفير الوقود للمرافق الصحية أصبح محدودا للغاية، وأن منظمة الصحة العالمية أبلغت المنظمات الأهلية بأنها لن تتمكن من تزويدها بالوقود اللازم لتشغيل المستشفيات .

</div>

**English gloss (draft):** Salha said that three operating nongovernmental hospitals in Gaza had gone out of service due to fuel shortages. Fuel provision for health facilities had become extremely limited, and WHO had told nongovernmental organizations that it would not be able to supply the fuel needed to run hospitals.

**Your label:** 

**Your reason:** 

**Arabic verified (yes/no/unsure):** 

**Gloss correction, if any:** 

**Corpus answerability (not_reviewed/answerable/unanswerable/unsure):** not_reviewed

**Other relevant document/span, if found:** 

---

## Q39 — Which named Nepalese village were people evacuated to during the reported flooding?

**Source:** LibyaAlAhrar · document 1338 · published 2026-06-19T17:32:30.000Z

[Original local text](originals/1338.txt) · [Source URL](https://libyaalahrar.tv/2026/06/19/%d8%ae%d8%a7%d8%b1%d8%ac%d9%8a%d8%a9-%d8%a7%d9%84%d9%88%d8%ad%d8%af%d8%a9-%d8%a7%d9%84%d9%88%d8%b7%d9%86%d9%8a%d8%a9-%d8%aa%d8%af%d9%8a%d9%86-%d8%a7%d9%84%d9%87%d8%ac%d9%88%d9%85-%d8%b9%d9%84%d9%89)

**Arabic passage** — code points [93, 293):

<div lang="ar" dir="rtl">

أدانت وزارة الخارجية بحكومة الوحدة الوطنية الهجوم الذي استهدف مطار “ديوري هاماني” الدولي بالعاصمة النيجرية نيامي، واصفة إياه بالاعتداء الذي يستهدف أمن واستقرار النيجر وسلامة مواطنيها ومنشآتها الحيوية.

</div>

**English gloss (draft):** The foreign ministry of Libya's Government of National Unity condemned the attack on Diori Hamani International Airport in Niger's capital, Niamey, describing it as an assault on Niger's security and stability and the safety of its citizens and vital facilities.

**Your label:** 

**Your reason:** 

**Arabic verified (yes/no/unsure):** 

**Gloss correction, if any:** 

**Corpus answerability (not_reviewed/answerable/unanswerable/unsure):** not_reviewed

**Other relevant document/span, if found:** 

---

## Q40 — Approximately how many manuscripts did Ahmad Pasha al-Jazzar's library in Acre contain?

**Source:** SANA · document 12379 · published 2026-10-04T10:37:56.000Z

[Original local text](originals/12379.txt) · [Source URL](https://sana.sy/culture-and-arts/2596899)

**Arabic passage** — code points [1119, 1268):

<div lang="ar" dir="rtl">

نبذة عن الكتاب: دراسة أكاديمية جماعية تبحث في تاريخ مكتبة أحمد باشا الجزّار التي أُنشئت في عكّا أواخر القرن الثامن عشر، وكانت تضم أكثر من 1800 مخطوط،

</div>

**English gloss (draft):** Book overview: A collective academic study of the history of Ahmad Pasha al-Jazzar's library, established in Acre in the late eighteenth century, which contained more than 1,800 manuscripts.

**Your label:** 

**Your reason:** 

**Arabic verified (yes/no/unsure):** 

**Gloss correction, if any:** 

**Corpus answerability (not_reviewed/answerable/unanswerable/unsure):** not_reviewed

**Other relevant document/span, if found:** 


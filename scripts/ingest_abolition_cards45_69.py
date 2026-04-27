"""Batch insert Abolition Aff/Neg cards 45-69 + 1 analytical (training session 2).

The analytical lives at P382 — the first one we've encountered in the corpus.
Argument: "Social Workers are key – that's Bergen – increasing funding to social
workers by 2% can be more effective then 20% of police funding – the CP can't
solve that"
answer_to: "Poverty Adv. CP" (from H2)

Cards 45-69 cover: 2AC Add-On (Police Abolition Stops State Violence), AT:
sections (No Cops Trade-Off, Social Workers Funded Now, Can't Replace Police,
Police Better than Social Workers, Abolition is Incomplete), the AT: Poverty
Adv. CP / AT: Immediate PIC / AT: Social Workers PIC / AT: Crime DA blocks,
and the start of AT: Sexual Assault.

Source dedup notes:
- Cards 48, 59, 60 all use Smith 11 (Kristen Smith PhD thesis) — collapse to one source.
- Card 62 uses Smith 11 with cutter typo `Kuchimanchic` (extra c) — kept as
  separate source row per user direction (faithful raw_cite-based dedup).
- Cards 65, 69 are Moody 17 (different page ranges from card 11) — separate sources.
- Card 55 is the same Marshall Project article as card 1 with swapped author
  order — separate source row.

New tags: kaba (theory-author tag for Mariame Kaba).
"""

from __future__ import annotations

import json

from debatabase.db import session_scope
from debatabase.ingest import (
    AnalyticalData,
    CardData,
    SourceData,
    get_or_create_source,
    insert_analytical,
    insert_card,
)

EXTRACT_PATH = "extracted/abolition_michigan.jsonl"
SOURCE_FILE = "Aff - Neg Abolition - Michigan7 2020 CCPTW.docx"


def spans_from_runs(runs, base: int = 0):
    parts: list[str] = []
    cur = base
    raw: list[dict] = []
    for r in runs:
        t = r.get("text", "")
        s, e = cur, cur + len(t)
        u = r.get("underline")
        if u and u != "none":
            raw.append({"start": s, "end": e, "kind": "underline"})
        if r.get("highlight") or r.get("shading"):
            raw.append({"start": s, "end": e, "kind": "highlight"})
        parts.append(t)
        cur = e
    return "".join(parts), raw


def merge(raw: list[dict]) -> list[dict]:
    m: dict[str, list[dict]] = {"underline": [], "highlight": []}
    for s in raw:
        b = m[s["kind"]]
        if b and b[-1]["end"] == s["start"]:
            b[-1]["end"] = s["end"]
        else:
            b.append({"start": s["start"], "end": s["end"]})
    return [{"kind": "underline", **s} for s in m["underline"]] + [
        {"kind": "highlight", **s} for s in m["highlight"]
    ]


def build_body(paragraphs, idx_range):
    text = ""
    raw: list[dict] = []
    for i in idx_range:
        bp = paragraphs[i]
        if not bp["text"].strip() and not bp["runs"]:
            continue
        if text and text[-1].isalnum() and bp["text"][:1].isalnum():
            text += " "
        seg, sr = spans_from_runs(bp["runs"], base=len(text))
        text += seg
        raw.extend(sr)
    return text, merge(raw)


def main() -> None:
    paragraphs = [json.loads(line) for line in open(EXTRACT_PATH)]

    BP = lambda *parts: ["Aff", "2AC"] + list(parts)

    # Smith 11 (Kristen Smith PhD thesis) — reused across cards 48, 59, 60
    smith_11 = SourceData(
        cite_short="Smith 11",
        author_last="Smith",
        author_full="Kristen Smith",
        qualifications=(
            "PhD thesis (in conformity with the requirements for the degree of "
            "Doctor of Philosophy), Department of Sociology and Equity Studies, "
            "Ontario Institute for Studies in Education, University of Toronto."
        ),
        publication="University of Toronto (PhD thesis)",
        title="Activist Social Workers in Neoliberal Times: Who are We Becoming Now?",
        published_date="2011-06",
        year=2011,
        url="https://tspace.library.utoronto.ca/bitstream/1807/29875/3/Smith_Kristin_M_201106_PhD_thesis.pdf",
        raw_cite=paragraphs[329]["text"],
    )

    cards = [
        # CARD 45 — Kaba/NYT on abolition as solvency for police violence
        dict(
            tag_idx=314, cite_idx=315, body_range=range(316, 318),
            block_path=BP("2AC Add-On – Police Abolition Stops State Violence"),
            source=SourceData(
                cite_short="Kaba 6-12",
                author_last="Kaba",
                author_full="Mariame Kaba",
                qualifications=(
                    "Director of Project NIA, a grass-roots group that works to "
                    "end youth incarceration; anti-criminalization organizer. "
                    "(@prisonculture)"
                ),
                publication="The New York Times",
                title="Yes, We Mean Literally Abolish the Police",
                published_date="2020-06-12 (accessed 2020-06-18)",
                year=2020,
                url="https://www.nytimes.com/2020/06/12/opinion/sunday/floyd-abolish-defund-police.html",
                raw_cite=paragraphs[315]["text"],
            ),
            tags=[("abolition","Abolition"), ("police-abolition","Police Abolition"),
                  ("kaba","Kaba")],
        ),
        # CARD 46 — Lassiter on education funding trades off with prison funding
        dict(
            tag_idx=319, cite_idx=320, body_range=range(321, 323),
            block_path=BP("AT: No Cops Trade-Off"),
            source=SourceData(
                cite_short="Lassiter 16",
                author_last="Lassiter",
                author_full="Linnea Lassiter",
                qualifications="State Policy Fellow for the Center on Budget and Policy Priorities.",
                publication="Evans School Review",
                title="When it Comes to Public School Funding, What Do Prisons Have to Do With it?",
                published_date="Spring 2016 (accessed 2020-06-30)",
                year=2016,
                url="https://depts.washington.edu/esreview/wordpress/wp-content/uploads/2016/06/Public-School-Funding-and-Prisons.pdf",
                raw_cite=paragraphs[320]["text"],
            ),
            tags=[("abolition","Abolition"), ("defund-police","Defund Police"),
                  ("divestment-reinvestment","Divestment / Reinvestment"),
                  ("education-impact","Education Impact")],
        ),
        # CARD 47 — Urban Institute on $115B police budget redirection
        dict(
            tag_idx=323, cite_idx=324, body_range=range(325, 327),
            block_path=BP("AT: No Cops Trade-Off"),
            source=SourceData(
                cite_short="Urban Institute 20",
                author_last="Urban Institute",
                author_full=None,
                qualifications="Nonprofit research organization.",
                publication="Urban Institute",
                title="Police and Corrections Expenditures",
                published_date="2020-02-04 (accessed 2020-06-18)",
                year=2020,
                url="https://www.urban.org/policy-centers/cross-center-initiatives/state-and-local-finance-initiative/state-and-local-backgrounders/police-and-corrections-expenditures",
                raw_cite=paragraphs[324]["text"],
            ),
            tags=[("abolition","Abolition"), ("defund-police","Defund Police"),
                  ("divestment-reinvestment","Divestment / Reinvestment")],
        ),
        # CARD 48 — Smith 11 on activist social workers (first instance)
        dict(
            tag_idx=328, cite_idx=329, body_range=range(330, 332),
            block_path=BP("AT: Social Workers Funded Now"),
            source=smith_11,
            tags=[("abolition","Abolition"), ("social-workers-solve","Social Workers Solve"),
                  ("divestment-reinvestment","Divestment / Reinvestment")],
        ),
        # CARD 49 — Vendel/PennLive on Harrisburg redirecting police budget
        dict(
            tag_idx=332, cite_idx=333, body_range=range(334, 339),
            block_path=BP("AT: Social Workers Funded Now"),
            source=SourceData(
                cite_short="Vendel 20",
                author_last="Vendel",
                author_full="Christian Vendel",
                qualifications=(
                    "Investigates public safety stories for PennLive/The "
                    "Patriot-News. Former Kansas City Star reporter."
                ),
                publication="PennLive / The Patriot-News",
                title=(
                    "Harrisburg may hire social workers, violence 'interrupters' "
                    "with money from police budget: mayor"
                ),
                published_date="2020-06",
                year=2020,
                url="https://www.pennlive.com/news/2020/06/harrisburg-may-hire-social-workers-violence-interrupters-with-money-from-police-budget-mayor.html",
                raw_cite=paragraphs[333]["text"],
            ),
            tags=[("abolition","Abolition"), ("divestment-reinvestment","Divestment / Reinvestment"),
                  ("education-impact","Education Impact")],
        ),
        # CARD 50 — Lopez on social workers being needed
        dict(
            tag_idx=339, cite_idx=340, body_range=range(341, 345),
            block_path=BP("AT: Social Workers Funded Now"),
            source=SourceData(
                cite_short="Lopez 15",
                author_last="Lopez",
                author_full="Marissa Lopez",
                qualifications=(
                    "Child advocate for a California non-profit organization; "
                    "MSW student at the University of Southern California."
                ),
                publication="The Hill",
                title="We need social workers now more than ever",
                published_date="2015-05-06 (accessed 2020-06-22)",
                year=2015,
                url="https://thehill.com/blogs/congress-blog/labor/241077-we-need-social-workers-now-more-than-ever",
                raw_cite=paragraphs[340]["text"],
            ),
            tags=[("abolition","Abolition"), ("social-workers-solve","Social Workers Solve")],
        ),
        # CARD 51 — Kwon/HuffPost on defunding police, funding social workers
        dict(
            tag_idx=346, cite_idx=347, body_range=range(348, 350),
            block_path=BP("AT: Can't Replace Police"),
            source=SourceData(
                cite_short="Kwon 20",
                author_last="Kwon",
                author_full="Sharon Kwon",
                qualifications=(
                    "Social worker specializing in immigrant and BIPOC "
                    "communities, families, adolescents, young adults, and "
                    "healthy relationships."
                ),
                publication="HuffPost",
                title="It's Time To Defund The Police And Start Funding Social Workers",
                published_date="2020-06-11 (accessed 2020-06-30)",
                year=2020,
                url="https://www.huffpost.com/entry/defund-police-social-workers_n_5ee12d80c5b6d1ad2bd82777",
                raw_cite=paragraphs[347]["text"],
            ),
            tags=[("abolition","Abolition"), ("social-workers-solve","Social Workers Solve"),
                  ("mental-health-response","Mental Health Response")],
        ),
        # CARD 52 — Vasilogambros/Pew Stateline on leaving police out
        dict(
            tag_idx=350, cite_idx=351, body_range=range(352, 364),
            block_path=BP("AT: Can't Replace Police"),
            source=SourceData(
                cite_short="Vasilogambros 20",
                author_last="Vasilogambros",
                author_full="Matt Vasilogambros",
                qualifications=(
                    "Writes about immigration and voting rights for Stateline. "
                    "Before joining Pew, was a writer and editor at The Atlantic, "
                    "covering national politics and demographics. Previously a "
                    "staff correspondent at National Journal; has written for "
                    "Outside."
                ),
                publication="Pew Stateline",
                title="'If the Police Aren't Needed, Let's Leave Them Out Completely'",
                published_date="2020-06-23",
                year=2020,
                url="https://www.pewtrusts.org/en/research-and-analysis/blogs/stateline/2020/06/23/if-the-police-arent-needed-lets-leave-them-out-completely",
                raw_cite=paragraphs[351]["text"],
            ),
            tags=[("abolition","Abolition"), ("defund-police","Defund Police"),
                  ("divestment-reinvestment","Divestment / Reinvestment"),
                  ("social-workers-solve","Social Workers Solve")],
        ),
        # CARD 53 — Redmond/FilterMag on police-as-social-workers being a non-solution
        dict(
            tag_idx=365, cite_idx=366, body_range=range(367, 369),
            block_path=BP("AT: Police Better than Social Workers"),
            source=SourceData(
                cite_short="Redmond 19",
                author_last="Redmond",
                author_full="Helen Redmond",
                qualifications=(
                    "Senior editor of Filter. Has written about nicotine, mental "
                    "health, and drug policy for publications including Al "
                    "Jazeera, AlterNet, Harper's, and The Influence."
                ),
                publication="Filter Magazine",
                title="Cops Morphing Into Social Workers Is Not a Solution",
                published_date="2019-04-23 (accessed 2020-06-30)",
                year=2019,
                url="https://filtermag.org/cops-morphing-into-social-workers-is-not-a-solution/",
                raw_cite=paragraphs[366]["text"],
            ),
            tags=[("abolition","Abolition"), ("social-workers-solve","Social Workers Solve"),
                  ("police-overreach","Police Overreach")],
        ),
        # CARD 54 — Patterson on police social work as a unique area
        dict(
            tag_idx=369, cite_idx=370, body_range=range(371, 374),
            block_path=BP("AT: Police Better than Social Workers"),
            source=SourceData(
                cite_short="Patterson 08",
                author_last="Patterson",
                author_full="George T. Patterson",
                qualifications=(
                    "PhD, ACSW, LCSW-R; Assistant Professor at Hunter College "
                    "School of Social Work."
                ),
                publication="NASW NYC",
                title=(
                    "Police Social Work: A Unique Area of Practice Arising from "
                    "Law Enforcement Functions"
                ),
                published_date="2008",
                year=2008,
                url="https://www.naswnyc.org/page/77/Police-Social-Work.htm",
                raw_cite=paragraphs[370]["text"],
            ),
            tags=[("abolition","Abolition"), ("social-workers-solve","Social Workers Solve")],
        ),
        # CARD 55 — Weichselbaum & Lewis (TMP) — same article as card 1, separate source row
        dict(
            tag_idx=375, cite_idx=376, body_range=range(377, 379),
            block_path=BP("AT: Abolition is Incomplete"),
            source=SourceData(
                cite_short="Weichselbaum & Lewis 6-9",
                author_last="Weichselbaum & Lewis",
                author_full="Simone Weichselbaum; Nicole Lewis",
                qualifications=(
                    "Both staff writers at The Marshall Project. Weichselbaum — "
                    "focuses on federal law enforcement and local policing; "
                    "co-chair of diversity & inclusion at TMP; 15+ years "
                    "reporting on urban criminal justice systems; previously NY "
                    "Daily News and Philadelphia Daily News; graduate degree in "
                    "criminology, University of Pennsylvania. Lewis — previously "
                    "wrote for Washington Post's \"The Fact Checker\" blog; "
                    "UMich and CUNY Graduate School of Journalism; 2016 "
                    "Education Writers Association National Award."
                ),
                publication="The Marshall Project",
                title=(
                    "Support For Defunding The Police Department Is Growing. "
                    "Here's Why It's Not A Silver Bullet."
                ),
                published_date="2020-06-09",
                year=2020,
                url="https://www.themarshallproject.org/2020/06/09/support-for-defunding-the-police-department-is-growing-here-s-why-it-s-not-a-silver-bullet",
                raw_cite=paragraphs[376]["text"],
            ),
            tags=[("abolition","Abolition"), ("defund-police","Defund Police"),
                  ("reformism-fails","Reformism Fails")],
        ),
        # CARD 56 — IFSW on social work + poverty eradication
        dict(
            tag_idx=384, cite_idx=385, body_range=range(386, 390),
            block_path=BP("AT: Poverty Adv. CP", "2AC – Social Workers Key"),
            source=SourceData(
                cite_short="IFSW 12",
                author_last="IFSW",
                author_full=None,
                qualifications=(
                    "International Federation of Social Workers — global body "
                    "for the profession. Strives for social justice, human "
                    "rights, and inclusive sustainable social development through "
                    "the promotion of social work best practice and engagement "
                    "in international cooperation."
                ),
                publication="ifsw.org",
                title="Poverty Eradication and the Role for Social Workers",
                published_date="2012",
                year=2012,
                url="https://www.ifsw.org/poverty-eradication-and-the-role-for-social-workers/",
                raw_cite=paragraphs[385]["text"],
            ),
            tags=[("abolition","Abolition"), ("social-workers-solve","Social Workers Solve"),
                  ("poverty-impact","Poverty Impact")],
        ),
        # CARD 57 — Suttie/Greater Good Berkeley on funding police
        dict(
            tag_idx=393, cite_idx=394, body_range=range(395, 397),
            block_path=BP("AT: Immediate PIC", "2AC – Abolition/Funding Key"),
            source=SourceData(
                cite_short="Suttie 6-24",
                author_last="Suttie",
                author_full="Jill Suttie",
                qualifications="Greater Good Berkeley Book Review Editor.",
                publication="Greater Good (UC Berkeley)",
                title="Is Funding Police the Best Way to Keep Everyone Safe?",
                published_date="2020-06-24 (accessed 2020-06-26)",
                year=2020,
                url="https://greatergood.berkeley.edu/article/item/is_funding_police_the_best_way_to_keep_everyone_safe",
                raw_cite=paragraphs[394]["text"],
            ),
            tags=[("abolition","Abolition"), ("defund-police","Defund Police"),
                  ("divestment-reinvestment","Divestment / Reinvestment")],
        ),
        # CARD 58 — Raven/Teen Vogue on 8 to Abolition
        dict(
            tag_idx=398, cite_idx=399, body_range=range(400, 402),
            block_path=BP("AT: Immediate PIC", "2AC - Immediate Reforms Bad"),
            source=SourceData(
                cite_short="Raven 6-25",
                author_last="Raven",
                author_full="Leila Raven",
                qualifications="Co-Founder of the 8 to Abolition Movement.",
                publication="Teen Vogue",
                title="8 to Abolition Is Advocating to Abolish Police to Keep Us All Safe",
                published_date="2020-06-25 (accessed 2020-06-26)",
                year=2020,
                url="https://www.teenvogue.com/story/8-to-abolition-abolish-police-keep-us-safe-op-ed",
                raw_cite=paragraphs[399]["text"],
            ),
            tags=[("abolition","Abolition"), ("police-abolition","Police Abolition"),
                  ("reformism-fails","Reformism Fails"), ("co-optation","Co-optation")],
        ),
        # CARD 59 — Smith 11 (dedup with card 48)
        dict(
            tag_idx=404, cite_idx=405, body_range=range(406, 416),
            block_path=BP("AT: Social Workers PIC", "AT: Props Up State Violence"),
            source=smith_11,
            tags=[("abolition","Abolition"), ("social-workers-solve","Social Workers Solve"),
                  ("community-control","Community Control")],
        ),
        # CARD 60 — Smith 11 (dedup with card 48)
        dict(
            tag_idx=416, cite_idx=417, body_range=range(418, 420),
            block_path=BP("AT: Social Workers PIC", "AT: Props Up State Violence"),
            source=smith_11,
            tags=[("abolition","Abolition"), ("social-workers-solve","Social Workers Solve"),
                  ("reformism-good","Reformism Good")],
        ),
        # CARD 61 — Horsell on Foucauldian perspective on homelessness/social work
        dict(
            tag_idx=420, cite_idx=421, body_range=range(422, 425),
            block_path=BP("AT: Social Workers PIC", "AT: Props Up State Violence"),
            source=SourceData(
                cite_short="Horsell 06",
                author_last="Horsell",
                author_full="Chris Horsell",
                qualifications=(
                    "BA(Hons), MEd, BSocAdmin; PhD student in Social "
                    "Administration and Social Work at Flinders University, SA, "
                    "Australia. Has been employed as a Social Worker with a "
                    "number of government and non-government homelessness "
                    "agencies."
                ),
                publication="(journal article — Taylor & Francis)",
                title=(
                    "Homelessness and Social Exclusion: A Foucauldian "
                    "Perspective for Social Workers"
                ),
                published_date="2006",
                year=2006,
                url="https://www.tandfonline.com/doi/full/10.1080/03124070600651911",
                raw_cite=paragraphs[421]["text"],
            ),
            tags=[("abolition","Abolition"), ("social-workers-solve","Social Workers Solve"),
                  ("anti-blackness","Anti-Blackness")],
        ),
        # CARD 62 — Smith 11 with cutter typo `Kuchimanchic` — separate source per user
        dict(
            tag_idx=426, cite_idx=427, body_range=range(428, 430),
            block_path=BP("AT: Social Workers PIC", "AT: CPS"),
            source=SourceData(
                cite_short="Smith 11",
                author_last="Smith",
                author_full="Kristen Smith",
                qualifications=(
                    "PhD thesis, Department of Sociology and Equity Studies, "
                    "Ontario Institute for Studies in Education, University of "
                    "Toronto."
                ),
                publication="University of Toronto (PhD thesis)",
                title="Activist Social Workers in Neoliberal Times: Who are We Becoming Now?",
                published_date="2011-06",
                year=2011,
                url="https://tspace.library.utoronto.ca/bitstream/1807/29875/3/Smith_Kristin_M_201106_PhD_thesis.pdf",
                raw_cite=paragraphs[427]["text"],  # contains the `Kuchimanchic` typo
            ),
            tags=[("abolition","Abolition"), ("child-welfare","Child Welfare / CPS"),
                  ("reformism-good","Reformism Good")],
        ),
        # CARD 63 — DePanfilis on CPS
        dict(
            tag_idx=430, cite_idx=431, body_range=range(432, 434),
            block_path=BP("AT: Social Workers PIC", "AT: CPS"),
            source=SourceData(
                cite_short="DePanfilis 18",
                author_last="DePanfilis",
                author_full="Diane DePanfilis",
                qualifications=(
                    "PhD, MSW; Professor at the Silberman School of Social Work, "
                    "Hunter College, City University of New York. Research "
                    "focuses on using prevention and implementation science to "
                    "design, implement, and evaluate interventions in public and "
                    "private child welfare systems."
                ),
                publication=None,
                title=None,
                published_date="2018",
                year=2018,
                url=None,
                raw_cite=paragraphs[431]["text"],
            ),
            tags=[("abolition","Abolition"), ("child-welfare","Child Welfare / CPS")],
        ),
        # CARD 64 — Bell on social workers + mass incarceration
        dict(
            tag_idx=435, cite_idx=436, body_range=range(437, 439),
            block_path=BP("AT: Social Workers PIC", "AT: Social Workers Counter Productive"),
            source=SourceData(
                cite_short="Bell 12",
                author_last="Bell",
                author_full="James Bell",
                qualifications=(
                    "Director of the W. Haywood Burns Institute. Since 2001, has "
                    "been spearheading a national movement to address racial and "
                    "ethnic disparities in the juvenile justice system."
                ),
                publication="The Sentencing Project",
                title=(
                    "Addicted No Longer: Breaking Away from Incarceration as a "
                    "Primary Instrument of Social Control"
                ),
                published_date="2012-03 (accessed 2020-06-26)",
                year=2012,
                url="https://www.sentencingproject.org/wp-content/uploads/2016/01/To-Build-a-Better-Criminal-Justice-System.pdf",
                raw_cite=paragraphs[436]["text"],
            ),
            tags=[("abolition","Abolition"), ("social-workers-solve","Social Workers Solve"),
                  ("anti-blackness","Anti-Blackness")],
        ),
        # CARD 65 — Moody 17 (different page range from card 11) — separate source
        dict(
            tag_idx=441, cite_idx=442, body_range=[443],
            block_path=BP("AT: Crime DA"),
            source=SourceData(
                cite_short="Moody 17",
                author_last="Moody",
                author_full="Amber Moody",
                qualifications="M.A. in Applied Ethics; thesis presented November 22, 2017.",
                publication=None,
                title=(
                    "Protection or Control? An Ethical Argument for Police "
                    "Abolition in the United States (pp. 81-82)"
                ),
                published_date="2017-11-22",
                year=2017,
                url=None,
                raw_cite=paragraphs[442]["text"],
            ),
            tags=[("abolition","Abolition"), ("police-overreach","Police Overreach")],
        ),
        # CARD 66 — Podur/InDepthNews on policing irrelevance
        dict(
            tag_idx=444, cite_idx=445, body_range=[446],
            block_path=BP("AT: Crime DA"),
            source=SourceData(
                cite_short="Podur 6-25",
                author_last="Podur",
                author_full="Justin Podur",
                qualifications=(
                    "Toronto-based writer; writing fellow at Globetrotter, a "
                    "project of the Independent Media Institute. Teaches at York "
                    "University in the Faculty of Environmental Studies. Author "
                    "of the novel Siegebreakers."
                ),
                publication="InDepthNews",
                title=(
                    "Policing Is Irrelevant for Public Safety—Here Are Some "
                    "Alternatives That Are Proven to Work"
                ),
                published_date="2020-06-25",
                year=2020,
                url="https://www.indepthnews.net/index.php/opinion/3645-policing-is-irrelevant-for-public-safety-here-are-some-alternatives-that-are-proven-to-work",
                raw_cite=paragraphs[445]["text"],
            ),
            tags=[("abolition","Abolition"), ("police-abolition","Police Abolition"),
                  ("police-overreach","Police Overreach")],
        ),
        # CARD 67 — Andrew/CNN on US without police (3 scenarios)
        dict(
            tag_idx=447, cite_idx=448, body_range=range(449, 451),
            block_path=BP("AT: Crime DA"),
            source=SourceData(
                cite_short="Andrew 6-17",
                author_last="Andrew",
                author_full="Scottie Andrew",
                qualifications=None,
                publication="CNN",
                title=(
                    "What the US would look like without police, as imagined in "
                    "3 scenarios"
                ),
                published_date="2020-06-17 (accessed 2020-06-18)",
                year=2020,
                url="https://www.cnn.com/2020/06/17/us/us-without-police-reform-defund-trnd/index.html",
                raw_cite=paragraphs[448]["text"],
            ),
            tags=[("abolition","Abolition"), ("police-abolition","Police Abolition")],
        ),
        # CARD 68 — Pauly/Mother Jones on world without cops
        dict(
            tag_idx=451, cite_idx=452, body_range=range(453, 457),
            block_path=BP("AT: Crime DA"),
            source=SourceData(
                cite_short="Pauly 6-2",
                author_last="Pauly",
                author_full="Madison Pauly",
                qualifications="Reporter at Mother Jones.",
                publication="Mother Jones",
                title="What a World Without Cops Would Look Like",
                published_date="2020-06-02",
                year=2020,
                url="https://www.motherjones.com/crime-justice/2020/06/police-abolition-george-floyd/",
                raw_cite=paragraphs[452]["text"],
            ),
            tags=[("abolition","Abolition"), ("police-abolition","Police Abolition")],
        ),
        # CARD 69 — Moody 17 (yet another page range) — separate source
        dict(
            tag_idx=459, cite_idx=460, body_range=[461],
            block_path=BP("AT: Crime DA", "AT: Sexual Assault"),
            source=SourceData(
                cite_short="Moody 17",
                author_last="Moody",
                author_full="Amber Moody",
                qualifications="M.A. in Applied Ethics; thesis presented November 22, 2017.",
                publication=None,
                title=(
                    "Protection or Control? An Ethical Argument for Police "
                    "Abolition in the United States (pp. 82-83)"
                ),
                published_date="2017-11-22",
                year=2017,
                url=None,
                raw_cite=paragraphs[460]["text"],
            ),
            tags=[("abolition","Abolition"), ("police-overreach","Police Overreach"),
                  ("domestic-violence","Domestic Violence")],
        ),
    ]

    with session_scope() as s:
        # Analytical first (P382)
        analytical_data = AnalyticalData(
            argument=paragraphs[382]["text"],
            answer_to="Poverty Adv. CP",
            argument_markup=[],
            source_file=SOURCE_FILE,
            block_path=["Aff", "2AC", "AT: Poverty Adv. CP", "2AC – Social Workers Key"],
        )
        a = insert_analytical(
            s,
            analytical_data,
            approved_tag_slugs=[
                ("abolition", "Abolition"),
                ("social-workers-solve", "Social Workers Solve"),
                ("poverty-impact", "Poverty Impact"),
            ],
        )
        print(f"  ANALYTICAL  id={a.id}")
        print(f"              argument={a.argument[:90]!r}...")
        print(f"              answer_to={a.answer_to!r}")
        print()

        for n, c in enumerate(cards, start=45):
            tag_text, tag_raw = spans_from_runs(paragraphs[c["tag_idx"]]["runs"])
            tag_markup = merge(tag_raw)
            body_text, body_markup = build_body(paragraphs, c["body_range"])

            src = get_or_create_source(s, c["source"])
            card = CardData(
                tag=tag_text,
                tag_markup=tag_markup,
                card_text=body_text,
                markup=body_markup,
                source_file=SOURCE_FILE,
                block_path=c["block_path"],
            )
            inserted = insert_card(s, src, card, c["tags"])
            nu = sum(1 for x in body_markup if x["kind"] == "underline")
            nh = sum(1 for x in body_markup if x["kind"] == "highlight")
            print(
                f"  card #{n:2d}  src={src.id:2d}  card.id={inserted.id:2d}  "
                f"body={len(body_text):6d}c  u={nu:2d}/h={nh:2d}  "
                f"tags={len(c['tags'])}"
            )


if __name__ == "__main__":
    main()

"""Batch insert Abolition Aff/Neg cards 20-44 (training session 2, approved by user).

All 25 cards live under Aff > 2AC > <various ---EXT. and 2AC Add-On sections>.
No analyticals encountered in this batch — analyticals schema work remains deferred.

Notable corrections to parsed fields (raw_cite preserved verbatim either way):
- Cards 38, 39: `Mgo & Mimoum` typo → corrected to `Ngo & Mimoun` in cite_short /
  author_last / author_full (real authors are Quyen Ngo and Raphael Mimoun);
  raw_cite still carries the typo.

Source dedup expectations:
- Cards 23 + 24 share `Smith 6-11` (Anna V. Smith, HighCountryNews, //Armaan).
- Card 32 references the same article but with different cite text and cutter
  (// JS) — will become a separate source row, intentionally faithful.
- Cards 38 + 39 share `Ngo & Mimoun 6/9/20` after correction.

New tags: education-impact, community-trust, housing-impact (all top-level).
"""

from __future__ import annotations

import json

from debatabase.db import session_scope
from debatabase.ingest import (
    CardData,
    SourceData,
    get_or_create_source,
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

    AFF_2AC = ["Aff", "2AC"]
    BP = lambda h3: AFF_2AC + [h3]

    cards = [
        # CARD 20 — Bell et al. on education-as-crime-reducer
        dict(
            tag_idx=171, cite_idx=172, body_range=range(173, 175),
            block_path=BP("---EXT. Education Decreases Crime"),
            source=SourceData(
                cite_short="Bell et al 18",
                author_last="Bell et al.",
                author_full="Brian Bell; Rui Costa; Stephen Machin",
                qualifications=(
                    "Bell — Professor of Economics at King's Business School. "
                    "Costa — Research Economist at the Centre for Economic "
                    "Performance. Machin — Professor of Economics at the Centre "
                    "for Economic Performance."
                ),
                publication="VoxEU.org",
                title="Why education reduces crime",
                published_date="2018-10-14 (accessed 2020-06-30)",
                year=2018,
                url="https://voxeu.org/article/why-education-reduces-crime",
                raw_cite=paragraphs[172]["text"],
            ),
            tags=[("abolition","Abolition"), ("education-impact","Education Impact"),
                  ("root-causes","Root Causes")],
        ),
        # CARD 21 — Miller on social workers solving root causes
        dict(
            tag_idx=176, cite_idx=177, body_range=range(178, 186),
            block_path=BP("---EXT Social Workers Solve Root Cause"),
            source=SourceData(
                cite_short="Miller 20",
                author_last="Miller",
                author_full=None,
                qualifications="NOW Reporter at USA TODAY.",
                publication="USA TODAY",
                title=(
                    "Defund police? Some cities have already started investing in "
                    "mental health"
                ),
                published_date="2020-06-22",
                year=2020,
                url="https://www.usatoday.com/story/news/nation/2020/06/22/defund-police-what-means-black-lives-matter/3218862001/",
                raw_cite=paragraphs[177]["text"],
            ),
            tags=[("abolition","Abolition"), ("defund-police","Defund Police"),
                  ("social-workers-solve","Social Workers Solve"),
                  ("mental-health-response","Mental Health Response"),
                  ("root-causes","Root Causes")],
        ),
        # CARD 22 — Cheng/Yale on community-trust as alternative to militarization
        dict(
            tag_idx=188, cite_idx=189, body_range=range(190, 193),
            block_path=BP("---EXT. Social Workers -> Community Trust"),
            source=SourceData(
                cite_short="Cheng 14",
                author_last="Cheng",
                author_full="Tony Cheng",
                qualifications=(
                    "Graduate student in Sociology at Yale and ISPS Graduate "
                    "Policy Fellow. Primary research focus: law enforcement "
                    "strategies in response to urban violence."
                ),
                publication="Yale ISPS",
                title="Do Alternatives to Police Militarization Exist?",
                published_date="2014-09-12 (accessed 2020-06-24)",
                year=2014,
                url="https://isps.yale.edu/news/blog/2014/09/do-alternatives-to-police-militarization-exist",
                raw_cite=paragraphs[189]["text"],
            ),
            tags=[("abolition","Abolition"), ("social-workers-solve","Social Workers Solve"),
                  ("community-trust","Community Trust")],
        ),
        # CARD 23 — Smith/HCN on community trust (first Smith cite — //Armaan)
        dict(
            tag_idx=193, cite_idx=194, body_range=range(195, 197),
            block_path=BP("---EXT. Social Workers -> Community Trust"),
            source=SourceData(
                cite_short="Smith 6-11",
                author_last="Smith",
                author_full="Anna V. Smith",
                qualifications="Writer/editor at High Country News.",
                publication="High Country News",
                title="There's already an alternative to calling the police",
                published_date="2020-06-11 (accessed 2020-06-22)",
                year=2020,
                url="https://www.hcn.org/issues/52.7/public-health-theres-already-an-alternative-to-calling-the-police",
                raw_cite=paragraphs[194]["text"],
            ),
            tags=[("abolition","Abolition"), ("social-workers-solve","Social Workers Solve"),
                  ("community-trust","Community Trust")],
        ),
        # CARD 24 — Smith/HCN on de-escalation (same source as 23 — dedup)
        dict(
            tag_idx=198, cite_idx=199, body_range=range(200, 202),
            block_path=BP("---EXT. De-Escalate Violence"),
            source=SourceData(
                cite_short="Smith 6-11",
                author_last="Smith",
                author_full="Anna V. Smith",
                qualifications="Writer/editor at High Country News.",
                publication="High Country News",
                title="There's already an alternative to calling the police",
                published_date="2020-06-11 (accessed 2020-06-22)",
                year=2020,
                url="https://www.hcn.org/issues/52.7/public-health-theres-already-an-alternative-to-calling-the-police",
                raw_cite=paragraphs[199]["text"],
            ),
            tags=[("abolition","Abolition"), ("social-workers-solve","Social Workers Solve"),
                  ("mental-health-response","Mental Health Response")],
        ),
        # CARD 25 — SafeLives/UK on social workers responding to domestic abuse
        dict(
            tag_idx=202, cite_idx=203, body_range=range(204, 213),
            block_path=BP("---EXT. De-Escalate Violence"),
            source=SourceData(
                cite_short="SafeLives 15",
                author_last="SafeLives",
                author_full=None,
                qualifications=(
                    "UK national charity supporting a strong multi-agency response "
                    "to domestic abuse. Provides practical tools, training, "
                    "guidance, quality assurance, policy and data insight to "
                    "support professionals and organisations working with "
                    "domestic abuse victims. (Formerly known as CAADA.)"
                ),
                publication="safelives.org.uk",
                title=(
                    "The role of social workers in responding effectively to "
                    "domestic abuse"
                ),
                published_date="2015",
                year=2015,
                url="https://safelives.org.uk/practice_blog/role-social-workers-responding-effectively-domestic-abuse",
                raw_cite=paragraphs[203]["text"],
            ),
            tags=[("abolition","Abolition"), ("social-workers-solve","Social Workers Solve"),
                  ("domestic-violence","Domestic Violence"),
                  ("mental-health-response","Mental Health Response")],
        ),
        # CARD 26 — MSWcareers on 6 steps for social workers vs poverty
        dict(
            tag_idx=214, cite_idx=215, body_range=range(216, 220),
            block_path=BP("---EXT. Solvency"),
            source=SourceData(
                cite_short="MSWcareers 19",
                author_last="MSWcareers",
                author_full=None,
                qualifications=(
                    "Career and education resource site for current and aspiring "
                    "social workers. Aggregates research and paths needed to "
                    "start, advance, and succeed in a social work career."
                ),
                publication="mswcareers.com",
                title="Fighting Against Poverty: The Social Worker's Battle",
                published_date="2019",
                year=2019,
                url="https://mswcareers.com/fighting-against-poverty-the-social-workers-battle/",
                raw_cite=paragraphs[215]["text"],
            ),
            tags=[("abolition","Abolition"), ("social-workers-solve","Social Workers Solve"),
                  ("poverty-impact","Poverty Impact"), ("root-causes","Root Causes")],
        ),
        # CARD 27 — DeParle/NYT on rising poverty + racial disparity
        dict(
            tag_idx=221, cite_idx=222, body_range=range(223, 229),
            block_path=BP("---EXT. Poverty High"),
            source=SourceData(
                cite_short="DeParle 20",
                author_last="DeParle",
                author_full="Jason DeParle",
                qualifications=(
                    "Senior writer at The New York Times; frequent contributor to "
                    "The New York Times Magazine. Previously a domestic "
                    "correspondent in Washington for The Times."
                ),
                publication="The New York Times",
                title="A Gloomy Prediction on How Much Poverty Could Rise",
                published_date="2020-04-16",
                year=2020,
                url="https://www.nytimes.com/2020/04/16/upshot/coronavirus-prediction-rise-poverty.html",
                raw_cite=paragraphs[222]["text"],
            ),
            tags=[("abolition","Abolition"), ("poverty-impact","Poverty Impact"),
                  ("anti-blackness","Anti-Blackness")],
        ),
        # CARD 28 — Buchheit on US poverty
        dict(
            tag_idx=229, cite_idx=230, body_range=range(231, 234),
            block_path=BP("---EXT. Poverty High"),
            source=SourceData(
                cite_short="Buchheit 18",
                author_last="Buchheit",
                author_full="Paul Buchheit",
                qualifications=(
                    "Advocate for social and economic justice; author of "
                    "numerous papers on economic inequality and cognitive science."
                ),
                publication="Common Dreams",
                title="The Evidence Pours In: Poverty Getting Much Worse in America",
                published_date="2018-11-05",
                year=2018,
                url="https://www.commondreams.org/views/2018/11/05/evidence-pours-poverty-getting-much-worse-america",
                raw_cite=paragraphs[230]["text"],
            ),
            tags=[("abolition","Abolition"), ("poverty-impact","Poverty Impact")],
        ),
        # CARD 29 — Childfund on poverty's effect on education
        dict(
            tag_idx=235, cite_idx=236, body_range=range(237, 241),
            block_path=BP("---EXT. Poverty !"),
            source=SourceData(
                cite_short="Childfund '13",
                author_last="Childfund",
                author_full=None,
                qualifications=(
                    "International NGO. Works with local partner organizations, "
                    "governments, corporations and individuals to help create the "
                    "safe environments children need to thrive."
                ),
                publication="childfund.org",
                title="The Effects of Poverty on Education in the United States",
                published_date="2013",
                year=2013,
                url="https://www.childfund.org/Content/NewsDetail/2147489206/",
                raw_cite=paragraphs[236]["text"],
            ),
            tags=[("abolition","Abolition"), ("poverty-impact","Poverty Impact"),
                  ("education-impact","Education Impact"),
                  ("child-welfare","Child Welfare / CPS")],
        ),
        # CARD 30 — Earley on mental illness as health (not police) issue
        dict(
            tag_idx=243, cite_idx=244, body_range=range(245, 247),
            block_path=BP("---EXT. Mental Health"),
            source=SourceData(
                cite_short="Earley 6/15",
                author_last="Earley",
                author_full="Pete Earley",
                qualifications=(
                    "Storyteller; author of 21 books including four New York Times "
                    "bestsellers (The Hot House; Crazy: A Father's Search Through "
                    "America's Mental Health Madness — 2007 Pulitzer Prize finalist)."
                ),
                publication="The Washington Post",
                title="Mental illness is a health issue, not a police issue",
                published_date="2020-06-15",
                year=2020,
                url="https://www.washingtonpost.com/opinions/2020/06/15/mental-illness-is-health-issue-not-police-issue/",
                raw_cite=paragraphs[244]["text"],
            ),
            tags=[("abolition","Abolition"), ("social-workers-solve","Social Workers Solve"),
                  ("mental-health-response","Mental Health Response")],
        ),
        # CARD 31 — Manfield on Dallas social workers + 911 calls
        dict(
            tag_idx=247, cite_idx=248, body_range=range(249, 253),
            block_path=BP("---EXT. Mental Health"),
            source=SourceData(
                cite_short="Manfield 19",
                author_last="Manfield",
                author_full=None,
                qualifications=(
                    "Editorial fellow at the Observer; former software developer; "
                    "recent graduate of Columbia Journalism School."
                ),
                publication="Dallas Observer",
                title=(
                    "Dallas Has Been Dispatching Social Workers to Some 911 Calls. "
                    "It's Working."
                ),
                published_date="2019",
                year=2019,
                url="https://www.dallasobserver.com/news/dallas-has-been-dispatching-social-workers-to-some-911-calls-its-working-11810019",
                raw_cite=paragraphs[248]["text"],
            ),
            tags=[("abolition","Abolition"), ("social-workers-solve","Social Workers Solve"),
                  ("mental-health-response","Mental Health Response")],
        ),
        # CARD 32 — Smith/HCN again, // JS cutter (separate source row, intentionally)
        dict(
            tag_idx=253, cite_idx=254, body_range=range(255, 259),
            block_path=BP("---EXT. Mental Health"),
            source=SourceData(
                cite_short="Smith 6-11",
                author_last="Smith",
                author_full="Anna V. Smith",
                qualifications=(
                    "Writer/editor at High Country News; assistant editor for "
                    "HCN's Indigenous affairs desk."
                ),
                publication="High Country News",
                title=(
                    "There's already an alternative to calling the police "
                    "(Experts in de-escalation)"
                ),
                published_date="2020-06-11 (accessed 2020-06-24)",
                year=2020,
                url="https://www.hcn.org/issues/52.7/public-health-theres-already-an-alternative-to-calling-the-police",
                raw_cite=paragraphs[254]["text"],
            ),
            tags=[("abolition","Abolition"), ("social-workers-solve","Social Workers Solve"),
                  ("mental-health-response","Mental Health Response")],
        ),
        # CARD 33 — CSWE policy on social work + mental health
        dict(
            tag_idx=259, cite_idx=260, body_range=range(261, 263),
            block_path=BP("---EXT. Mental Health"),
            source=SourceData(
                cite_short="CSWE 14",
                author_last="CSWE",
                author_full=None,
                qualifications=(
                    "Council on Social Work Education — national association of "
                    "social work education programs and individuals that ensures "
                    "and enhances the quality of social work education for "
                    "professional practice."
                ),
                publication="cswe.org",
                title=(
                    "The Role of Social Work in Mental and Behavioral Health Care "
                    "Principles for Public Policy"
                ),
                published_date="2014",
                year=2014,
                url="https://www.cswe.org/getattachment/Advocacy-Policy/RoleofSWinMentalandBehavorialHealthCare-January2015-FINAL.pdf.aspx",
                raw_cite=paragraphs[260]["text"],
            ),
            tags=[("abolition","Abolition"), ("social-workers-solve","Social Workers Solve"),
                  ("mental-health-response","Mental Health Response")],
        ),
        # CARD 34 — McHarris & McHarris on redirecting police funds
        dict(
            tag_idx=264, cite_idx=265, body_range=range(266, 268),
            block_path=BP("---EXT. Redirect Funding"),
            source=SourceData(
                cite_short="McHarris & McHarris 20",
                author_last="McHarris & McHarris",
                author_full="Philip V. McHarris; Thenjiwe Tameika McHarris",
                qualifications=(
                    "P. McHarris — joint PhD candidate in Sociology and African "
                    "American Studies at Yale University; research focuses on "
                    "race, punishment, and policing. T. McHarris — Co-Founder at "
                    "Blackbird; previously worked for human rights organizations "
                    "including Amnesty International."
                ),
                publication="The New York Times",
                title="No More Money for the Police",
                published_date="2020-05-30",
                year=2020,
                url="https://www.nytimes.com/2020/05/30/opinion/george-floyd-police-funding.html",
                raw_cite=paragraphs[265]["text"],
            ),
            tags=[("abolition","Abolition"), ("police-abolition","Police Abolition"),
                  ("defund-police","Defund Police"),
                  ("divestment-reinvestment","Divestment / Reinvestment")],
        ),
        # CARD 35 — McDowell & Fernandez on disempowering police
        dict(
            tag_idx=268, cite_idx=269, body_range=range(270, 273),
            block_path=BP("---EXT. Redirect Funding"),
            source=SourceData(
                cite_short="McDowell & Fernandez 18",
                author_last="McDowell & Fernandez",
                author_full="Meghan G. McDowell; Luis A. Fernandez",
                qualifications=(
                    "McDowell — Department of History, Politics, and Social "
                    "Justice, Winston-Salem State University. Fernandez — "
                    "Department of Criminology and Criminal Justice, Northern "
                    "Arizona University, Flagstaff."
                ),
                publication=None,
                title=(
                    "'Disband, Disempower, and Disarm': Amplifying the Theory and "
                    "Practice of Police Abolition (pp. 373–391)"
                ),
                published_date="2018-07-20",
                year=2018,
                url="https://rdcu.be/b485w",
                raw_cite=paragraphs[269]["text"],
            ),
            tags=[("abolition","Abolition"), ("police-abolition","Police Abolition"),
                  ("defund-police","Defund Police"),
                  ("divestment-reinvestment","Divestment / Reinvestment"),
                  ("incrementalism","Incrementalism")],
        ),
        # CARD 36 — Duran & Simon on police abolitionist discourse
        dict(
            tag_idx=275, cite_idx=276, body_range=range(277, 279),
            block_path=BP("---EXT. Gradualism Key"),
            source=SourceData(
                cite_short="Duran & Simon 19",
                author_last="Duran & Simon",
                author_full="Eduardo Bautista Duran; Jonathan Simon",
                qualifications=(
                    "Simon — Associate Dean of the Jurisprudence and Social "
                    "Policy Program at the UC Berkeley School of Law. Duran — "
                    "law student at UC Berkeley, Jurisprudence & Social Policy."
                ),
                publication=None,
                title=(
                    "Police Abolitionist Discourse? Why It Has Been Missing (and "
                    "Why It Matters) (pp. 85-103)"
                ),
                published_date="2019-04-07",
                year=2019,
                url="https://doi-org.proxy.lib.umich.edu/10.1017/9781108354721.005",
                raw_cite=paragraphs[276]["text"],
            ),
            tags=[("abolition","Abolition"), ("police-abolition","Police Abolition"),
                  ("incrementalism","Incrementalism")],
        ),
        # CARD 37 — Seigel on abolitionist police history
        dict(
            tag_idx=279, cite_idx=280, body_range=range(281, 283),
            block_path=BP("---EXT. Gradualism Key"),
            source=SourceData(
                cite_short="Seigel 17",
                author_last="Seigel",
                author_full="Micol Seigel",
                qualifications=(
                    "Professor of American Studies and History at Indiana "
                    "University, Bloomington."
                ),
                publication=None,
                title=(
                    "The dilemma of 'racial profiling': an abolitionist police "
                    "history (pp. 474-490)"
                ),
                published_date="2017-12-01",
                year=2017,
                url=None,
                raw_cite=paragraphs[280]["text"],
            ),
            tags=[("abolition","Abolition"), ("police-abolition","Police Abolition"),
                  ("incrementalism","Incrementalism"),
                  ("community-control","Community Control")],
        ),
        # CARD 38 — Ngo & Mimoun on abolition (CORRECTED parsed fields; raw_cite typo preserved)
        dict(
            tag_idx=283, cite_idx=284, body_range=[285],
            block_path=BP("---EXT. Gradualism Key"),
            source=SourceData(
                cite_short="Ngo & Mimoun 6/9/20",
                author_last="Ngo & Mimoun",
                author_full="Quyen Ngo; Raphael Mimoun",
                qualifications=(
                    "Ngo — actor, organizer, and facilitator. Mimoun — founder of "
                    "Horizontal, a human rights technology organization."
                ),
                publication="Newsweek",
                title=(
                    "Abolishing the Police is Not as Extreme as It Sounds. Here is "
                    "How it Would Work | Opinion"
                ),
                published_date="2020-06-09",
                year=2020,
                url="https://www.newsweek.com/defund-police-not-extreme-better-solutions-safer-1509713",
                raw_cite=paragraphs[284]["text"],
            ),
            tags=[("abolition","Abolition"), ("police-abolition","Police Abolition"),
                  ("incrementalism","Incrementalism")],
        ),
        # CARD 39 — same Ngo & Mimoun source as 38 (will dedup)
        dict(
            tag_idx=286, cite_idx=287, body_range=[288],
            block_path=BP("---EXT. Gradualism Key"),
            source=SourceData(
                cite_short="Ngo & Mimoun 6/9/20",
                author_last="Ngo & Mimoun",
                author_full="Quyen Ngo; Raphael Mimoun",
                qualifications=(
                    "Ngo — actor, organizer, and facilitator. Mimoun — founder of "
                    "Horizontal, a human rights technology organization."
                ),
                publication="Newsweek",
                title=(
                    "Abolishing the Police is Not as Extreme as It Sounds. Here is "
                    "How it Would Work | Opinion"
                ),
                published_date="2020-06-09",
                year=2020,
                url="https://www.newsweek.com/defund-police-not-extreme-better-solutions-safer-1509713",
                raw_cite=paragraphs[287]["text"],
            ),
            tags=[("abolition","Abolition"), ("police-abolition","Police Abolition"),
                  ("incrementalism","Incrementalism")],
        ),
        # CARD 40 — Gimbel & Muhammad on reform-as-first-step (Cardozo Law Rev)
        dict(
            tag_idx=289, cite_idx=290, body_range=range(291, 294),
            block_path=BP("---EXT. Gradualism Key"),
            source=SourceData(
                cite_short="Gimbel & Muhammad 19",
                author_last="Gimbel & Muhammad",
                author_full="Gimbel; Craig Muhammad",
                qualifications=(
                    "Muhammad — incarcerated for 36+ years; B.S. from Coppin "
                    "State (now Coppin State University); has spent considerable "
                    "time counseling young offenders, being a positive role model, "
                    "bringing young men out of gangs, establishing peace summits, "
                    "and encouraging members of street organizations to respect "
                    "their communities; co-founder of Project Emancipation Now; "
                    "President of the Lifers Organization at Jessup Correctional "
                    "Institution; Treasurer of Tubman House Baltimore. Gimbel — "
                    "Georgetown Law J.D. 2016."
                ),
                publication="Cardozo Law Review",
                title=(
                    "ARE POLICE OBSOLETE? BREAKING CYCLES OF VIOLENCE THROUGH "
                    "ABOLITION DEMOCRACY (p. 91)"
                ),
                published_date="2019-04",
                year=2019,
                url=None,
                raw_cite=paragraphs[290]["text"],
            ),
            tags=[("abolition","Abolition"), ("police-abolition","Police Abolition"),
                  ("incrementalism","Incrementalism"),
                  ("reformism-good","Reformism Good")],
        ),
        # CARD 41 — Coyle & Schept on penal abolition vs neoliberal racial capitalism
        dict(
            tag_idx=295, cite_idx=296, body_range=range(297, 299),
            block_path=BP("---EXT. Abolition Key"),
            source=SourceData(
                cite_short="Coyle & Schept 17",
                author_last="Coyle & Schept",
                author_full="Michael J. Coyle; Judah Schept",
                qualifications=(
                    "Coyle — Department of Criminal Justice and Political "
                    "Science, California State University. Schept — Professor at "
                    "Eastern Kentucky University."
                ),
                publication="Contemporary Justice Review 20(4)",
                title=(
                    "Penal abolition and the state: colonial, racial and gender "
                    "violences (pp. 399-403)"
                ),
                published_date="2017-11",
                year=2017,
                url="https://doi.org/10.1080/10282580.2017.1386065",
                raw_cite=paragraphs[296]["text"],
            ),
            tags=[("abolition","Abolition"), ("cap-k","Capitalism K"),
                  ("neoliberalism-bad","Neoliberalism Bad"),
                  ("anti-blackness","Anti-Blackness"),
                  ("slavery-origins","Slavery Origins of Policing")],
        ),
        # CARD 42 — Darling-Hammond on school funding
        dict(
            tag_idx=300, cite_idx=301, body_range=range(302, 304),
            block_path=BP("2AC Add-On – Education"),
            source=SourceData(
                cite_short="Darling-Hammond 19",
                author_last="Darling-Hammond",
                author_full="Linda Darling-Hammond",
                qualifications=(
                    "President of the Learning Policy Institute; professor "
                    "emeritus at Stanford University."
                ),
                publication="Forbes",
                title=(
                    "America's School Funding Struggle: How We're Robbing Our "
                    "Future By Under-Investing In Our Children"
                ),
                published_date="2019-08-05 (accessed 2020-06-30)",
                year=2019,
                url="https://www.forbes.com/sites/lindadarlinghammond/2019/08/05/americas-school-funding-struggle-how-were-robbing-our-future-by-under-investing-in-our-children/",
                raw_cite=paragraphs[301]["text"],
            ),
            tags=[("abolition","Abolition"), ("education-impact","Education Impact"),
                  ("divestment-reinvestment","Divestment / Reinvestment")],
        ),
        # CARD 43 — Rao & La Vigne on stable housing reducing crime
        dict(
            tag_idx=305, cite_idx=306, body_range=range(307, 309),
            block_path=BP("2AC Add-On – Housing"),
            source=SourceData(
                cite_short="Rao & Vigne 13",
                author_last="Rao & Vigne",
                author_full="Shebani Rao; Nancy La Vigne",
                qualifications=(
                    "Rao — Racial Equity Manager at the MacArthur Foundation "
                    "Safety and Justice Challenge. La Vigne — Vice President for "
                    "Justice Policy at the Urban Institute."
                ),
                publication="Urban Institute",
                title="Five ways to reduce crime",
                published_date="2013-05-07 (accessed 2020-06-30)",
                year=2013,
                url="https://www.urban.org/urban-wire/five-ways-reduce-crime",
                raw_cite=paragraphs[306]["text"],
            ),
            tags=[("abolition","Abolition"), ("housing-impact","Housing Impact"),
                  ("root-causes","Root Causes")],
        ),
        # CARD 44 — Couloute on housing insecurity and recidivism
        dict(
            tag_idx=309, cite_idx=310, body_range=range(311, 313),
            block_path=BP("2AC Add-On – Housing"),
            source=SourceData(
                cite_short="Couloute 18",
                author_last="Couloute",
                author_full="Lucius Couloute",
                qualifications=(
                    "PhD in Sociology from the University of Massachusetts "
                    "Amherst; examines the structural and cultural dynamics of "
                    "reentry systems."
                ),
                publication="Prison Policy Initiative",
                title=(
                    "Nowhere to Go: Homelessness among formerly incarcerated people"
                ),
                published_date="2018-08 (accessed 2020-06-30)",
                year=2018,
                url="https://www.prisonpolicy.org/reports/housing.html",
                raw_cite=paragraphs[310]["text"],
            ),
            tags=[("abolition","Abolition"), ("housing-impact","Housing Impact"),
                  ("root-causes","Root Causes")],
        ),
    ]

    with session_scope() as s:
        for n, c in enumerate(cards, start=20):
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

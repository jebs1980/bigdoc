"""
Bigdoc — Génération du rapport PDF personnalisé
Utilisé par la route /api/rapport/{session_id}
"""

import json
import os
from datetime import date

DEMO_PATH = os.path.join(os.path.dirname(__file__), "data", "demographics.json")

def load_demo():
    if os.path.exists(DEMO_PATH):
        with open(DEMO_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def normalize_spe(spe):
    if not spe: return None
    s = spe.lower()
    if "généraliste" in s or "médecine générale" in s or "generaliste" in s: return "Médecine générale"
    if "gynéco" in s or "gynecologue" in s:
        if "médical" in s or "médicale" in s: return "Gynécologie médicale"
        return "Gynécologie-obstétrique"
    if "cardio" in s: return "Cardiologie"
    if "pédiat" in s or "pediat" in s: return "Pédiatrie"
    if "psychiatr" in s: return "Psychiatrie"
    if "dermato" in s: return "Dermatologie"
    if "ophtalmo" in s: return "Ophtalmologie"
    if "orthop" in s: return "Orthopédie"
    if "gastro" in s: return "Gastro-entérologie"
    if "pneumo" in s: return "Pneumologie"
    return None

def get_dept(ville):
    if not ville: return None
    import re
    m = re.search(r'\((\d{5})\)', ville)
    if m:
        cp = m.group(1)
        if cp.startswith('75'): return '75'
        if cp.startswith('69'): return '69'
        if cp.startswith('13'): return '13'
        return cp[:2]
    m = re.search(r'\b(\d{5})\b', ville)
    if m: return m.group(1)[:2]
    return None

def generate_radar_svg(dims):
    """Génère un SVG radar pour le rapport PDF."""
    keys = ['administration','achats_materiel','informatique_teleconsult','comptabilite_finances','charge_mentale','financement_investissements','developpement_croissance']
    labels = ['Admin','Matériel','Info.','Compta','Charge','Financement','Développement']
    import math
    n = len(keys)
    cx, cy, r = 160, 155, 100
    scores = [(dims.get(k, {}).get('score', 0) or 0) / 20 for k in keys]

    def polygon(factor):
        pts = []
        for i in range(n):
            angle = (math.pi * 2 * i / n) - math.pi / 2
            x = cx + r * factor * math.cos(angle)
            y = cy + r * factor * math.sin(angle)
            pts.append(f"{x:.1f},{y:.1f}")
        return " ".join(pts)

    score_pts = []
    for i, val in enumerate(scores):
        angle = (math.pi * 2 * i / n) - math.pi / 2
        x = cx + r * val * math.cos(angle)
        y = cy + r * val * math.sin(angle)
        score_pts.append(f"{x:.1f},{y:.1f}")

    axes = ""
    for i in range(n):
        angle = (math.pi * 2 * i / n) - math.pi / 2
        x2 = cx + r * math.cos(angle)
        y2 = cy + r * math.sin(angle)
        axes += f'<line x1="{cx}" y1="{cy}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="#E8EBF0" stroke-width="1"/>'

    label_els = ""
    dots = ""
    for i, lbl in enumerate(labels):
        angle = (math.pi * 2 * i / n) - math.pi / 2
        lx = cx + (r + 22) * math.cos(angle)
        ly = cy + (r + 22) * math.sin(angle)
        sc = round(scores[i] * 20)
        col = '#C0392B' if scores[i] <= 0.35 else '#E67E22' if scores[i] <= 0.65 else '#1A7A5E'
        label_els += f'<text x="{lx:.1f}" y="{ly-4:.1f}" text-anchor="middle" font-family="Arial" font-size="7.5" fill="#0B2545" font-weight="bold">{lbl}</text>'
        label_els += f'<text x="{lx:.1f}" y="{ly+8:.1f}" text-anchor="middle" font-family="Arial" font-size="8" fill="{col}" font-weight="bold">{sc}/20</text>'
        px = cx + r * scores[i] * math.cos(angle)
        py = cy + r * scores[i] * math.sin(angle)
        dots += f'<circle cx="{px:.1f}" cy="{py:.1f}" r="3.5" fill="{col}" stroke="white" stroke-width="1.5"/>'

    return f"""<svg viewBox="0 0 320 310" width="280" height="245">
      <polygon points="{polygon(0.25)}" fill="none" stroke="#F0F2F5" stroke-width="1"/>
      <polygon points="{polygon(0.5)}"  fill="none" stroke="#E8EBF0" stroke-width="1"/>
      <polygon points="{polygon(0.75)}" fill="none" stroke="#E0E4E8" stroke-width="1"/>
      <polygon points="{polygon(1)}"    fill="none" stroke="#D0D5DE" stroke-width="1"/>
      {axes}
      <polygon points="{' '.join(score_pts)}" fill="rgba(26,122,94,0.12)" stroke="#1A7A5E" stroke-width="2" stroke-linejoin="round"/>
      {dots}
      {label_els}
    </svg>"""
    pct = (score / max_score) * 100
    if pct >= 70: return "#1A7A5E"
    if pct >= 40: return "#F4A261"
    return "#C0392B"

def generate_rapport_html(bilan, lead_info, specialite="", ville=""):
    demo = load_demo()
    spe_norm = normalize_spe(specialite)
    dept = get_dept(ville)
    dept_info = demo.get("departements", {}).get(dept, {}) if dept else {}
    benchmarks = demo.get("benchmarks_nationaux", {})
    niches = demo.get("niches_sous_dotees", {}).get(spe_norm, []) if spe_norm else []
    motifs_key = None
    if spe_norm == "Médecine générale": motifs_key = "medecine_generale"
    elif "Gynécologie médicale" in (spe_norm or ""): motifs_key = "gynecologie_medicale"
    elif spe_norm == "Psychiatrie": motifs_key = "psychiatrie"
    elif spe_norm == "Pédiatrie": motifs_key = "pediatrie"
    elif spe_norm == "Cardiologie": motifs_key = "cardiologie"
    motifs = demo.get("motifs_consultation", {}).get(motifs_key, {}) if motifs_key else {}

    score = bilan.get("score_global", 0)
    niveau = bilan.get("niveau", "")
    prenom = lead_info.get("prenom", "Docteur") if lead_info else "Docteur"
    dims = bilan.get("dimensions", {})
    actions = bilan.get("actions_prioritaires", [])
    reco = bilan.get("recommandation_principale", {})
    heures = bilan.get("heures_perdues_semaine", 0)
    euros = bilan.get("euros_evitables_an", 0)

    score_color = color_score(score, 100)

    # Densité locale
    densite_locale = dept_info.get("densites", {}).get(spe_norm) if spe_norm else None
    densite_nat = demo.get("densites_nationales", {}).get(spe_norm) if spe_norm else None
    revenu_moy = benchmarks.get("revenus_moyens_bnc", {}).get("par_specialite", {}).get(spe_norm) if spe_norm else None

    # Dimensions HTML
    dims_html = ""
    dim_labels = {
        "administration": "Administration & CPAM",
        "achats_materiel": "Matériel & Stocks",
        "informatique_teleconsult": "Informatique & Téléconsultation",
        "comptabilite_finances": "Comptabilité & Finances",
        "charge_mentale": "Charge mentale hors soins",
        "financement_investissements": "Financement & Investissements",
        "developpement_croissance": "Développement & Croissance",
    }
    for key, label in dim_labels.items():
        d = dims.get(key, {})
        sc = d.get("score", 10)
        comment = d.get("commentaire", "")
        pct = round((sc / 20) * 100)
        col = color_score(sc, 20)
        dims_html += f"""
        <div class="dim-item">
          <div class="dim-header">
            <span class="dim-label">{label}</span>
            <span class="dim-score" style="color:{col}">{sc}/20</span>
          </div>
          <div class="dim-bar-track">
            <div class="dim-bar-fill" style="width:{pct}%;background:{col}"></div>
          </div>
          <p class="dim-comment">{comment}</p>
        </div>"""

    # Motifs HTML
    motifs_html = ""
    top_motifs = motifs.get("top_motifs", [])
    if top_motifs:
        motifs_html = "<ul class='motifs-list'>"
        for m in top_motifs[:5]:
            pct = round(m['part'] * 100)
            motifs_html += f"""
            <li class="motif-item">
              <div class="motif-bar-row">
                <span class="motif-label">{m['motif']}</span>
                <span class="motif-pct">{pct}%</span>
              </div>
              <div class="motif-track">
                <div class="motif-fill" style="width:{pct}%"></div>
              </div>
            </li>"""
        motifs_html += "</ul>"

    # Niches HTML
    niches_html = ""
    if niches:
        niches_html = "<div class='niches-grid'>"
        for n in niches[:4]:
            niches_html += f"""
            <div class="niche-card">
              <div class="niche-title">{n['niche']}</div>
              <div class="niche-context">Contexte : {n['contexte']}</div>
              <div class="niche-opp">{n['opportunite']}</div>
            </div>"""
        niches_html += "</div>"

    # Actions HTML
    actions_html = ""
    for i, a in enumerate(actions[:3], 1):
        actions_html += f"""
        <div class="action-item">
          <div class="action-num">{i}</div>
          <div class="action-text">{a}</div>
        </div>"""

    # Contexte local
    context_html = ""
    if dept_info:
        dept_type_labels = {"sous_dote": "Zone sous-dotée ARS", "sur_dote": "Zone sur-dotée", "intermédiaire": "Zone intermédiaire"}
        dept_type = dept_info.get("type", "intermédiaire")
        context_html += f"<span class='ctx-badge badge-{dept_type}'>{dept_info.get('nom', '')} — {dept_type_labels.get(dept_type, '')}</span>"
    if densite_locale and densite_nat:
        ratio = densite_locale / densite_nat
        cmp = "inférieure à" if ratio < 0.9 else "supérieure à" if ratio > 1.1 else "proche de"
        context_html += f"<span class='ctx-badge'>Densité locale {densite_locale}/100k — {cmp} la moyenne nationale ({densite_nat}/100k)</span>"
    if revenu_moy:
        context_html += f"<span class='ctx-badge'>Revenu moyen BNC {spe_norm} : {revenu_moy:,}€/an (CARMF 2023)</span>"

    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8"/>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Cormorant+Garamond:ital,wght@0,400;0,600;0,700;1,400&family=DM+Sans:wght@300;400;500;600&display=swap');
  * {{ box-sizing:border-box; margin:0; padding:0; }}
  body {{ font-family:'DM Sans',Arial,sans-serif; color:#0B2545; background:white; font-size:9.5pt; line-height:1.5; }}
  @page {{ size:A4; margin:15mm 15mm 20mm; }}

  .header {{ background:#0B2545; padding:12mm 15mm 10mm; margin:-15mm -15mm 8mm; display:flex; justify-content:space-between; align-items:center; }}
  .logo-text {{ font-size:20pt; color:#FAF3E0; line-height:1; }}
  .logo-text b {{ font-weight:700; }}
  .logo-sub {{ font-size:6pt; color:rgba(250,243,224,.4); letter-spacing:2px; text-transform:uppercase; margin-top:2px; }}
  .header-right {{ text-align:right; color:rgba(250,243,224,.6); font-size:8pt; font-weight:300; }}

  .score-band {{ display:flex; align-items:center; gap:8mm; background:#FAF3E0; padding:6mm 0; margin-bottom:7mm; border-radius:8px; }}
  .score-circle {{ width:22mm; height:22mm; border-radius:50%; border:3px solid {score_color}; display:flex; flex-direction:column; align-items:center; justify-content:center; flex-shrink:0; margin-left:6mm; }}
  .score-num {{ font-family:'Cormorant Garamond',serif; font-size:22pt; font-weight:600; color:{score_color}; line-height:1; }}
  .score-lbl {{ font-size:6pt; color:#8A9BB0; letter-spacing:1px; }}
  .score-info h2 {{ font-family:'Cormorant Garamond',serif; font-size:14pt; color:#0B2545; margin-bottom:2mm; }}
  .score-info p {{ font-size:8.5pt; color:#3D4F66; line-height:1.5; }}
  .kpis {{ display:flex; gap:4mm; margin-top:3mm; }}
  .kpi {{ background:#0B2545; border-radius:5px; padding:2mm 4mm; text-align:center; }}
  .kpi-val {{ font-family:'Cormorant Garamond',serif; font-size:13pt; font-weight:600; color:#F4A261; line-height:1; }}
  .kpi-lbl {{ font-size:6pt; color:rgba(250,243,224,.6); }}

  .section-title {{ font-family:'Cormorant Garamond',serif; font-size:13pt; font-weight:600; color:#0B2545; margin:6mm 0 3mm; border-bottom:1px solid #E8EBF0; padding-bottom:1.5mm; }}
  .eyebrow {{ font-size:6pt; letter-spacing:1.5px; text-transform:uppercase; color:#1A7A5E; font-weight:600; margin-bottom:1mm; }}

  .dim-item {{ margin-bottom:3.5mm; }}
  .dim-header {{ display:flex; justify-content:space-between; margin-bottom:1mm; }}
  .dim-label {{ font-size:8.5pt; font-weight:500; }}
  .dim-score {{ font-size:8.5pt; font-weight:700; }}
  .dim-bar-track {{ height:4px; background:#EEF0F4; border-radius:2px; margin-bottom:1.5mm; }}
  .dim-bar-fill {{ height:100%; border-radius:2px; }}
  .dim-comment {{ font-size:7.5pt; color:#8A9BB0; line-height:1.4; }}

  .ctx-badges {{ display:flex; flex-wrap:wrap; gap:2mm; margin-bottom:4mm; }}
  .ctx-badge {{ font-size:7pt; padding:1mm 3mm; border-radius:3px; background:#EEF0F4; color:#3D4F66; }}
  .badge-sous_dote {{ background:rgba(192,57,43,.08); color:#C0392B; }}
  .badge-sur_dote {{ background:rgba(26,122,94,.08); color:#1A7A5E; }}

  .motifs-list {{ list-style:none; }}
  .motif-item {{ margin-bottom:2.5mm; }}
  .motif-bar-row {{ display:flex; justify-content:space-between; margin-bottom:0.8mm; font-size:8pt; }}
  .motif-label {{ color:#3D4F66; }}
  .motif-pct {{ font-weight:600; color:#0B2545; }}
  .motif-track {{ height:5px; background:#EEF0F4; border-radius:3px; }}
  .motif-fill {{ height:100%; background:#1A7A5E; border-radius:3px; }}

  .niches-grid {{ display:grid; grid-template-columns:1fr 1fr; gap:3mm; }}
  .niche-card {{ border:1px solid #E8EBF0; border-radius:5px; padding:3mm; }}
  .niche-title {{ font-size:8.5pt; font-weight:600; color:#0B2545; margin-bottom:1mm; }}
  .niche-context {{ font-size:7pt; color:#8A9BB0; margin-bottom:1mm; }}
  .niche-opp {{ font-size:7.5pt; color:#1A7A5E; line-height:1.4; }}

  .action-item {{ display:flex; gap:3mm; align-items:flex-start; margin-bottom:3mm; }}
  .action-num {{ width:6mm; height:6mm; border-radius:50%; background:#1A7A5E; color:white; font-size:8pt; font-weight:700; display:flex; align-items:center; justify-content:center; flex-shrink:0; margin-top:0.5mm; }}
  .action-text {{ font-size:8.5pt; color:#3D4F66; line-height:1.5; }}

  .reco-box {{ background:rgba(26,122,94,.06); border-left:3px solid #1A7A5E; padding:4mm; border-radius:0 5px 5px 0; margin-bottom:4mm; }}
  .reco-service {{ font-family:'Cormorant Garamond',serif; font-size:12pt; font-weight:600; color:#0B2545; }}
  .reco-tarif {{ font-size:9pt; color:#1A7A5E; font-weight:500; margin:1mm 0; }}
  .reco-text {{ font-size:8pt; color:#8A9BB0; line-height:1.5; }}

  .sources-band {{ margin-top:6mm; padding-top:3mm; border-top:1px solid #E8EBF0; }}
  .sources-title {{ font-size:7pt; color:#8A9BB0; text-transform:uppercase; letter-spacing:1px; margin-bottom:2mm; }}
  .sources-badges {{ display:flex; flex-wrap:wrap; gap:1.5mm; }}
  .source-badge {{ font-size:6.5pt; padding:0.8mm 2.5mm; border-radius:3px; background:#EEF0F4; color:#8A9BB0; }}

  .footer {{ position:fixed; bottom:0; left:0; right:0; background:#FAF3E0; padding:3mm 15mm; display:flex; justify-content:space-between; align-items:center; border-top:1px solid #E8EBF0; font-size:7pt; color:#8A9BB0; }}
  .page-break {{ page-break-before:always; }}
</style>
</head>
<body>

<!-- HEADER -->
<div class="header">
  <div>
    <div class="logo-text">big<b>doc</b></div>
    <div class="logo-sub">un service RMS</div>
  </div>
  <div class="header-right">
    Rapport de diagnostic personnalisé<br>
    {prenom} · {date.today().strftime('%d %B %Y')}<br>
    {specialite}{' · ' + ville if ville else ''}
  </div>
</div>

<!-- SCORE BAND -->
<div class="score-band">
  <div class="score-circle">
    <span class="score-num">{score}</span>
    <span class="score-lbl">/100</span>
  </div>
  <div class="score-info">
    <h2>{niveau}</h2>
    <p>{bilan.get('message_bilan', '')}</p>
    <div class="kpis">
      <div class="kpi">
        <div class="kpi-val">{heures}h</div>
        <div class="kpi-lbl">perdues/semaine</div>
      </div>
      <div class="kpi">
        <div class="kpi-val">{euros:,}€</div>
        <div class="kpi-lbl">récupérables/an</div>
      </div>
    </div>
  </div>
</div>

<!-- RADAR -->
<div style="text-align:center;margin-bottom:5mm">
  <div class="eyebrow" style="text-align:left">Profil radar</div>
  {generate_radar_svg(dims)}
</div>

<!-- CONTEXTE LOCAL -->
{f'<div class="eyebrow">Contexte local</div><div class="ctx-badges">{context_html}</div>' if context_html else ''}

<!-- DIMENSIONS -->
<div class="eyebrow">Analyse par dimension</div>
<div class="section-title">Bilan détaillé de votre cabinet</div>
{dims_html}

<!-- PAGE 2 -->
<div class="page-break"></div>

<!-- MOTIFS DE CONSULTATION -->
{f'''
<div class="eyebrow">Épidémiologie · {spe_norm}</div>
<div class="section-title">Motifs de consultation dans votre spécialité</div>
<p style="font-size:8pt;color:#8A9BB0;margin-bottom:4mm">Source : CPAM / Ameli Open Data 2023 · données nationales</p>
{motifs_html}
''' if motifs_html else ''}

<!-- NICHES -->
{f'''
<div class="eyebrow" style="margin-top:6mm">Opportunités de développement · {spe_norm}</div>
<div class="section-title">Niches sous-dotées dans votre spécialité</div>
<p style="font-size:8pt;color:#8A9BB0;margin-bottom:4mm">Sources : CNOM Atlas 2023 · IRDES · CPAM · enquêtes URPS</p>
{niches_html}
''' if niches_html else ''}

<!-- PAGE 3 -->
<div class="page-break"></div>

<!-- ACTIONS -->
<div class="eyebrow">Plan d'action</div>
<div class="section-title">3 priorités pour votre cabinet</div>
{actions_html}

<!-- RECOMMANDATION -->
{f'''
<div class="eyebrow" style="margin-top:5mm">Ordonnance Bigdoc</div>
<div class="reco-box">
  <div class="reco-service">{reco.get('service', '')}</div>
  <div class="reco-tarif">{reco.get('tarif', '')}</div>
  <div class="reco-text">{reco.get('justification', '')}</div>
</div>
''' if reco else ''}

<!-- SOURCES -->
<div class="sources-band">
  <div class="sources-title">Sources & méthodologie</div>
  <div class="sources-badges">
    {''.join(f'<span class="source-badge">{s}</span>' for s in ['CNOM Atlas 2023','CARMF 2023','DREES','CPAM / Ameli','IRDES 2023','URPS Médecins','HAS','INSEE 2023','RPPS ANS'])}
  </div>
  <p style="font-size:6.5pt;color:#8A9BB0;margin-top:2mm;line-height:1.4">
    Ce rapport croise vos réponses avec les données épidémiologiques nationales et locales, les benchmarks CARMF par spécialité
    et les motifs de consultation CPAM. Les estimations financières sont calculées sur la base des tarifs CCAM/NGAP en vigueur.
    Bigdoc est un service Real Med Services — bonjour@bigdoc.fr — bigdoc.fr
  </p>
</div>

<div class="footer">
  <span>bigdoc · un service Real Med Services</span>
  <span>Diagnostic du {date.today().strftime('%d/%m/%Y')} · Confidentiel</span>
</div>

</body>
</html>"""

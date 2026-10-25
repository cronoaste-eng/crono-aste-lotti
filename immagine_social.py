"""Crea l'immagine 4:5 (1080x1350) di un bene per Instagram e Facebook.

La foto della piattaforma e' piccola (420x315): viene messa in una cartolina con i dati del bene,
cosi' l'immagine e' sempre nel formato giusto e leggibile. Solo Pillow, nessun servizio esterno.
"""
import io
import re
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

W, H = 1080, 1350
NAVY = (0, 45, 89)
BIANCO = (255, 255, 255)
CHIARO = (207, 217, 230)
GRIGIO = (150, 170, 195)

CARATTERI = {
    "bold": ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
             "/Library/Fonts/Arial Bold.ttf"],
    "norm": ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/System/Library/Fonts/Supplemental/Arial.ttf",
             "/Library/Fonts/Arial.ttf"],
}


def font(tipo, size):
    for p in CARATTERI[tipo]:
        if Path(p).exists():
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def euro(n):
    p = f"{float(n):.2f}".split(".")
    return re.sub(r"\B(?=(\d{3})+(?!\d))", ".", p[0]) + "," + p[1]


def a_capo(draw, testo, f, larghezza, max_righe):
    parole, righe, riga = testo.split(), [], ""
    for p in parole:
        prova = (riga + " " + p).strip()
        if draw.textlength(prova, font=f) <= larghezza:
            riga = prova
        else:
            righe.append(riga)
            riga = p
    if riga:
        righe.append(riga)
    if righe and righe[-1].endswith('...'):
        righe[-1] = righe[-1][:-3].rstrip(' .,;:')
        riga_finale = True
    if len(righe) > max_righe:
        righe = righe[:max_righe]
        while righe[-1] and draw.textlength(righe[-1] + "...", font=f) > larghezza:
            righe[-1] = righe[-1][:-1]
        righe[-1] = righe[-1].rstrip(' .,;:') + "..."
    elif parole and testo.rstrip().endswith('...') and righe:
        righe[-1] = righe[-1] + "..."
    return righe


def scarica_foto(url):
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    im = Image.open(io.BytesIO(r.content))
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        fondo = Image.new("RGB", im.size, (255, 255, 255))
        fondo.paste(im, mask=im.split()[-1])
        return fondo
    return im.convert("RGB")


def crea(l, foto=None):
    """l = un bene di lotti.json; restituisce un'immagine PIL 1080x1350 (RGB)."""
    foto = foto or scarica_foto(l["foto"])
    # sfondo: la foto sfocata e scurita, poi un velo blu
    sfondo = ImageOps.fit(foto, (W, H), Image.LANCZOS).filter(ImageFilter.GaussianBlur(28))
    velo = Image.new("RGB", (W, H), NAVY)
    img = Image.blend(sfondo, velo, 0.82)
    d = ImageDraw.Draw(img)

    # testata
    d.text((60, 52), "CRONO ASTE", font=font("bold", 40), fill=BIANCO)
    d.text((60, 104), "Gestore della vendita telematica - PDG n. 80", font=font("norm", 24), fill=CHIARO)
    d.line((60, 150, W - 60, 150), fill=(60, 100, 150), width=2)

    # foto (ingrandita 2x, con angoli arrotondati e bordo bianco)
    fw = 840
    fh = int(foto.height * fw / foto.width)
    fh = min(fh, 600)
    f2 = ImageOps.fit(foto, (fw, fh), Image.LANCZOS)
    mask = Image.new("L", (fw, fh), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, fw, fh), radius=22, fill=255)
    x0, y0 = (W - fw) // 2, 185
    d.rounded_rectangle((x0 - 8, y0 - 8, x0 + fw + 8, y0 + fh + 8), radius=28, fill=BIANCO)
    img.paste(f2, (x0, y0), mask)

    y = y0 + fh + 52
    testa = "TRIB. %s - PROC. %s" % ((l.get("tribunale") or "-").upper(), l.get("procedura") or "-")
    if l.get("lotto"):
        testa += " - " + l["lotto"].upper()
    d.text((60, y), testa, font=font("bold", 30), fill=CHIARO)
    y += 52
    # dati in basso: calcolati prima, cosi' il titolo usa solo lo spazio che resta
    min_ = l.get("prezzo_minimo")
    prezzo = min_ if min_ is not None else l.get("prezzo")
    etichetta = "OFFERTA MINIMA" if min_ is not None else "PREZZO BASE"
    pie = H - 60
    dep = l.get("termine_carta") or l.get("termine_bonifico") or ""
    ini = (l.get("inizio") or "").split(" ")
    righe = []
    if dep:
        righe.append("Offerte entro %s" % dep)
    if ini[0]:
        righe.append("Asta il %s%s" % (ini[0], (" ore " + ini[1]) if len(ini) > 1 else ""))
    fy = pie - 150 - 14 - 38 * len(righe)
    titolo = (l.get("titolo") or "").strip()
    ft = font("bold", 40)
    max_righe = max(1, min(3, (fy - 12 - y) // 52))
    for riga in a_capo(d, titolo, ft, W - 120, max_righe):
        d.text((60, y), riga, font=ft, fill=BIANCO)
        y += 52
    if prezzo is not None:
        d.rounded_rectangle((60, pie - 150, W - 60, pie - 30), radius=20, fill=BIANCO)
        d.text((92, pie - 134), etichetta, font=font("norm", 26), fill=(90, 100, 115))
        d.text((92, pie - 100), "\u20ac " + euro(prezzo), font=font("bold", 62), fill=NAVY)
    for r in righe:
        d.text((60, fy), r, font=font("norm", 30), fill=BIANCO)
        fy += 38
    d.text((W - 60, pie + 8), "cronoaste.cloud", font=font("norm", 24), fill=CHIARO, anchor="ra")
    return img

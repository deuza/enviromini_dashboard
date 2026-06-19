#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
enviromini_dashboard.py
Dashboard web pour Pimoroni Enviro Mini - Raspberry Pi Zero W
stdlib Python + pimoroni-bme280 + ltr559 (+ enviroplus pour le bruit)

Usage : python3 enviromini_dashboard.py [port]
Acces : http://<ip_du_pi>:<port>   (defaut : 80)

Capteurs :
  BME280 (0x76)  -> temperature (compensee CPU), pression, humidite
  LTR-559 (0x23) -> lumiere (lux), proximite
  Micro MEMS     -> niveau sonore relatif (I2S, voir notes config)
"""

import json
import math
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

# -------------------------------------------------------------------
# Imports capteurs I2C - mode DEMO si libs/bus absents (test hors Pi)
# -------------------------------------------------------------------
try:
    try:
        from smbus2 import SMBus
    except ImportError:
        from smbus import SMBus
    from bme280 import BME280
    from ltr559 import LTR559

    _bus = SMBus(1)
    _bme280 = BME280(i2c_dev=_bus)   # adresse par defaut 0x76 (Enviro Mini)
    _ltr559 = LTR559()
    _DEMO = False
except (ImportError, OSError, FileNotFoundError):
    _DEMO = True
    _bme280 = None
    _ltr559 = None

# -------------------------------------------------------------------
# Micro / bruit : OPTIONNEL. Necessite l'I2S active (overlay
# adau7002-simple) + sounddevice. Si indisponible -> _noise = None
# et le dashboard affiche "N/A" sans planter.
# -------------------------------------------------------------------
_noise = None
if not _DEMO:
    try:
        from enviroplus.noise import Noise
        _noise = Noise()
    except Exception:
        _noise = None

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 80

# -------------------------------------------------------------------
# Reglages
# -------------------------------------------------------------------
QNH = 1013.25            # Pression de reference niveau mer (hPa) pour l'altitude

TEMP_COMPENSATION = True  # Le BME280 colle au CPU lit trop chaud -> compensation
TEMP_FACTOR = 2.25        # Facteur de compensation (exemple Pimoroni). A CALIBRER :
                          # plus petit -> compense plus fort vers le bas.

NOISE_RANGE = (20, 8000)  # Plage de frequences (Hz) pour la mesure de bruit
NOISE_SCALE = 3.722       # Amplitude correspondant a 100% sur la barre.
                          # Valeur EMPIRIQUE a ajuster selon ton environnement.

# -------------------------------------------------------------------
# Etat interne
# -------------------------------------------------------------------
_cpu_temps = []           # Fenetre glissante pour lisser la temp CPU


def _cpu_temp():
    """Temperature CPU via sysfs (None si indisponible)."""
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as fh:
            return float(fh.read().strip()) / 1000.0
    except (OSError, ValueError):
        return None


def _read_noise():
    """Amplitude sonore relative, ou None si micro indisponible."""
    if _noise is None:
        return None
    try:
        amp = _noise.get_amplitude_at_frequency_range(*NOISE_RANGE)
        return round(float(amp), 5)
    except Exception:
        return None


def read_sensors():
    """Retourne un dict JSON-serialisable avec toutes les mesures."""
    if _DEMO:
        ts = time.time()
        lux = max(0.0, 300.0 + math.sin(ts / 8.0) * 280.0)
        return {
            "temperature":  round(21.5 + math.sin(ts / 10.0), 2),
            "temp_raw":     round(28.0 + math.sin(ts / 10.0), 2),
            "compensated":  True,
            "pressure_hpa": round(1013.25 + math.cos(ts / 20.0) * 5.0, 2),
            "altitude_m":   round(45.0 + math.sin(ts / 15.0) * 2.0, 1),
            "humidity":     round(48.0 + math.sin(ts / 12.0) * 8.0, 1),
            "lux":          round(lux, 2),
            "proximity":    int(max(0, math.sin(ts / 4.0) * 1200)),
            "noise_amp":    round(0.03 + abs(math.sin(ts / 5.0)) * 0.04, 5),
            "noise_pct":    min(100.0, (0.03 + abs(math.sin(ts / 5.0)) * 0.04) / NOISE_SCALE * 100.0),
            "cpu_temp":     round(42.0 + math.sin(ts / 6.0) * 3.0, 1),
            "demo":         True,
        }

    raw_temp = _bme280.get_temperature()
    cpu = _cpu_temp()

    if TEMP_COMPENSATION and cpu is not None:
        _cpu_temps.append(cpu)
        if len(_cpu_temps) > 5:
            _cpu_temps.pop(0)
        avg_cpu = sum(_cpu_temps) / float(len(_cpu_temps))
        temp = raw_temp - ((avg_cpu - raw_temp) / TEMP_FACTOR)
        compensated = True
    else:
        temp = raw_temp
        compensated = False

    pressure = _bme280.get_pressure()
    noise_amp = _read_noise()
    noise_pct = (min(100.0, noise_amp / NOISE_SCALE * 100.0)
                 if noise_amp is not None else None)

    return {
        "temperature":  round(temp, 2),
        "temp_raw":     round(raw_temp, 2),
        "compensated":  compensated,
        "pressure_hpa": round(pressure, 2),
        "altitude_m":   round(44330.0 * (1.0 - (pressure / QNH) ** (1.0 / 5.255)), 1),
        "humidity":     round(_bme280.get_humidity(), 1),
        "lux":          round(_ltr559.get_lux(), 2),
        "proximity":    int(_ltr559.get_proximity()),
        "noise_amp":    noise_amp,
        "noise_pct":    noise_pct,
        "cpu_temp":     round(cpu, 1) if cpu is not None else None,
        "demo":         False,
    }


# -------------------------------------------------------------------
# HTML du dashboard (raw string -> {} JS ne posent pas de probleme)
# -------------------------------------------------------------------

_HTML = r"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Enviro Mini</title>
<style>
:root{
  --bg:#0d1117;--card:#161b22;--border:#30363d;
  --text:#e6edf3;--muted:#8b949e;
  --accent:#58a6ff;--green:#3fb950;
  --yellow:#d29922;--orange:#db6d28;--red:#f85149;--purple:#bc8cff;
}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--text);
     font-family:'Courier New',monospace;padding:1rem}
header{display:flex;align-items:center;gap:.75rem;
       margin-bottom:1.5rem;padding-bottom:.75rem;
       border-bottom:1px solid var(--border)}
h1{font-size:1.1rem;color:var(--accent)}
.badge{font-size:.7rem;padding:.1rem .5rem;border-radius:2rem;
       border:1px solid var(--border);color:var(--muted)}
.badge.live{border-color:var(--green);color:var(--green)}
.badge.demo{border-color:var(--yellow);color:var(--yellow)}
#ts{margin-left:auto;font-size:.7rem;color:var(--muted)}
/* Grille forcee sur 3 colonnes -> 6 cartes = 2 lignes */
.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:1rem}
@media (max-width:900px){.grid{grid-template-columns:repeat(2,1fr)}}
@media (max-width:600px){.grid{grid-template-columns:1fr}}
.card{background:var(--card);border:1px solid var(--border);
      border-radius:8px;padding:1rem}
.card h2{font-size:.7rem;text-transform:uppercase;
         color:var(--muted);letter-spacing:.1em;margin-bottom:.75rem}
.big{font-size:2rem;font-weight:bold;line-height:1}
.unit{font-size:.9rem;color:var(--muted);margin-left:.15rem}
.sub{font-size:.8rem;color:var(--muted);margin-top:.4rem}
.bar-bg{height:10px;background:var(--border);border-radius:5px;
        position:relative;overflow:hidden;margin-top:.6rem}
.bar-fill{position:absolute;left:0;height:100%;border-radius:5px;
          transition:width .4s,background .4s}
.tick{position:absolute;top:0;height:100%;width:1px;background:#0d1117}
.prox-dot{display:inline-block;width:10px;height:10px;border-radius:50%;
          background:var(--border);margin-right:.4rem;vertical-align:middle;
          transition:background .3s,box-shadow .3s}
.prox-dot.near{background:var(--green);box-shadow:0 0 8px var(--green)}
footer{margin-top:1.25rem;padding-top:.75rem;border-top:1px solid var(--border);
        font-size:.72rem;color:var(--muted);line-height:1.6}
footer code{color:var(--accent)}
</style>
</head>
<body>
<header>
  <h1>&#127807; Enviro Mini</h1>
  <span class="badge" id="badge">...</span>
  <span id="ts"></span>
</header>

<div class="grid">

  <!-- Temperature -->
  <div class="card">
    <h2>&#127777;&#65039; Temperature</h2>
    <div class="big"><span id="temp">--</span><span class="unit">&#176;C</span></div>
    <div class="sub" id="temp-feel"></div>
    <div class="sub" id="temp-note"></div>
  </div>

  <!-- Pression / Altitude -->
  <div class="card">
    <h2>&#128168; Pression</h2>
    <div class="big"><span id="pres">--</span><span class="unit">hPa</span></div>
    <div class="sub" id="alt"></div>
  </div>

  <!-- Humidite -->
  <div class="card">
    <h2>&#128167; Humidite relative</h2>
    <div class="big"><span id="hum">--</span><span class="unit">%</span></div>
    <div class="bar-bg">
      <div class="bar-fill" id="hum-bar"></div>
      <div class="tick" style="left:40%"></div>
      <div class="tick" style="left:60%"></div>
    </div>
    <div class="sub" id="hum-feel"></div>
  </div>

  <!-- Lumiere -->
  <div class="card">
    <h2>&#128161; Lumiere</h2>
    <div class="big"><span id="lux">--</span><span class="unit">lux</span></div>
    <div class="bar-bg"><div class="bar-fill" id="lux-bar"></div></div>
    <div class="sub" id="lux-feel"></div>
  </div>

  <!-- Proximite -->
  <div class="card">
    <h2>&#128400;&#65039; Proximite</h2>
    <div class="big"><span id="prox">--</span></div>
    <div class="bar-bg"><div class="bar-fill" id="prox-bar"></div></div>
    <div class="sub">
      <span class="prox-dot" id="prox-dot"></span><span id="prox-feel"></span>
    </div>
  </div>

  <!-- Bruit -->
  <div class="card">
    <h2>&#128266; Niveau sonore</h2>
    <div class="big"><span id="noise">--</span></div>
    <div class="bar-bg"><div class="bar-fill" id="noise-bar"></div></div>
    <div class="sub" id="noise-feel"></div>
  </div>

</div><!-- .grid -->

<footer>
  Pimoroni <strong>Enviro Mini</strong> &mdash;
  <code>0x76</code> BME280 (T/P/H) &middot;
  <code>0x23</code> LTR-559 (lux/prox) &middot;
  micro MEMS (I2S)<br>
  <span id="foot-state"></span>
</footer>

<script>
"use strict";

function update(){
  fetch('/data').then(function(r){
    if(!r.ok) return null;
    return r.json();
  }).then(function(d){
    if(!d) return;

    // Badge + horloge
    var badge=document.getElementById('badge');
    badge.textContent=d.demo?'DEMO':'LIVE';
    badge.className='badge '+(d.demo?'demo':'live');
    document.getElementById('ts').textContent=
      new Date().toLocaleTimeString('fr-FR');

    // Temperature
    document.getElementById('temp').textContent=d.temperature;
    var feel=d.temperature<5  ? 'Gel' :
             d.temperature<10 ? 'Froid' :
             d.temperature<18 ? 'Frais' :
             d.temperature<25 ? 'Confort' :
             d.temperature<30 ? 'Chaud' : 'Tres chaud';
    document.getElementById('temp-feel').textContent=feel;
    var note=document.getElementById('temp-note');
    if(d.compensated){
      note.textContent='compensee (brute '+d.temp_raw+' C)';
    } else if(d.cpu_temp!==null){
      note.textContent='brute - CPU a '+d.cpu_temp+' C';
    } else {
      note.textContent='';
    }

    // Pression
    document.getElementById('pres').textContent=d.pressure_hpa;
    document.getElementById('alt').textContent=
      'Alt. estimee : '+d.altitude_m+' m (QNH 1013)';

    // Humidite (0-100%)
    document.getElementById('hum').textContent=d.humidity;
    var hbar=document.getElementById('hum-bar');
    hbar.style.width=Math.min(100,Math.max(0,d.humidity))+'%';
    hbar.style.background=(d.humidity<30)?'var(--orange)':
                          (d.humidity>70)?'var(--accent)':'var(--green)';
    document.getElementById('hum-feel').textContent=
      (d.humidity<30)?'Air sec':
      (d.humidity<40)?'Plutot sec':
      (d.humidity<=60)?'Zone de confort':
      (d.humidity<=70)?'Plutot humide':'Air humide';

    // Lumiere : echelle log (0.01 -> 64000 lux)
    document.getElementById('lux').textContent=d.lux;
    var lbar=document.getElementById('lux-bar');
    lbar.style.width=Math.min(100,Math.max(0,
      Math.log10(d.lux+1)/Math.log10(64001)*100))+'%';
    lbar.style.background='var(--yellow)';
    document.getElementById('lux-feel').textContent=
      (d.lux<1)?'Nuit noire':
      (d.lux<10)?'Penombre':
      (d.lux<50)?'Tres faible':
      (d.lux<200)?'Interieur tamise':
      (d.lux<500)?'Interieur eclaire':
      (d.lux<2000)?'Tres lumineux':
      (d.lux<10000)?'Jour nuageux':'Plein soleil';

    // Proximite (0 -> ~2000)
    document.getElementById('prox').textContent=d.proximity;
    document.getElementById('prox-bar').style.width=
      Math.min(100,d.proximity/2000*100)+'%';
    document.getElementById('prox-bar').style.background='var(--accent)';
    var near=d.proximity>50;
    document.getElementById('prox-dot').className='prox-dot'+(near?' near':'');
    document.getElementById('prox-feel').textContent=
      near?'Objet detecte':'Rien a proximite';

    // Bruit (amplitude relative, non calibre en dB)
    var nbar=document.getElementById('noise-bar');
    if(d.noise_amp===null){
      document.getElementById('noise').textContent='N/A';
      nbar.style.width='0%';
      document.getElementById('noise-feel').textContent='micro non detecte';
    } else {
      document.getElementById('noise').textContent=d.noise_amp;
      nbar.style.width=Math.min(100,Math.max(0,d.noise_pct))+'%';
      nbar.style.background=(d.noise_pct<33)?'var(--green)':
                            (d.noise_pct<66)?'var(--yellow)':'var(--orange)';
      document.getElementById('noise-feel').textContent=
        ((d.noise_pct<33)?'Calme':
         (d.noise_pct<66)?'Modere':'Eleve')+' (relatif)';
    }

    // Footer
    document.getElementById('foot-state').textContent=
      (d.demo?'mode demo':
        ('CPU '+(d.cpu_temp!==null?d.cpu_temp+' C':'?')+
         ' - micro '+(d.noise_amp!==null?'actif':'inactif')));
  }).catch(function(){ /* silencieux */ });
}

update();
setInterval(update, 2000);
</script>
</body>
</html>"""


# -------------------------------------------------------------------
# Serveur HTTP
# -------------------------------------------------------------------

class _Handler(BaseHTTPRequestHandler):

    def _send_json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            body = _HTML.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/data":
            try:
                self._send_json(200, read_sensors())
            except Exception as exc:
                self._send_json(500, {"error": str(exc)})
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, fmt, *args):
        # Silencieux : retire le flood de logs HTTP dans le terminal
        pass


def main():
    mode = "DEMO" if _DEMO else "LIVE"
    try:
        server = HTTPServer(("0.0.0.0", PORT), _Handler)
    except PermissionError:
        sys.stderr.write(
            "[enviromini-dashboard] ERREUR: bind sur le port {} refuse "
            "(port < 1024 = privilegie).\n".format(PORT))
        sys.stderr.write(
            "[enviromini-dashboard] Solutions : service systemd avec "
            "CAP_NET_BIND_SERVICE, authbind, ou un port > 1024.\n")
        sys.exit(1)

    print("[enviromini-dashboard] mode={} port={}".format(mode, PORT))
    print("[enviromini-dashboard] http://<ip_du_pi>:{}".format(PORT))
    if _DEMO:
        print("[enviromini-dashboard] WARNING: capteurs absents, donnees simulees")
    elif _noise is None:
        print("[enviromini-dashboard] note: micro indisponible (bruit = N/A)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[enviromini-dashboard] Arret.")
        server.shutdown()


if __name__ == "__main__":
    main()

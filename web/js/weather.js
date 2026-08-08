const qs = (s, el=document) => el.querySelector(s);

function wxIcon(code){
  // Open-Meteo weather codes (simple mapping)
  const sun = "☀️", cloud="☁️", rain="🌧️", storm="⛈️", snow="❄️", fog="🌫️";
  if ([0].includes(code)) return sun;
  if ([1,2,3].includes(code)) return "🌤️";
  if ([45,48].includes(code)) return fog;
  if ([51,53,55,56,57].includes(code)) return "🌦️";
  if ([61,63,65,66,67].includes(code)) return rain;
  if ([71,73,75,77].includes(code)) return snow;
  if ([80,81,82].includes(code)) return "🌧️";
  if ([95,96,99].includes(code)) return storm;
  return cloud;
}
function wxText(code){
  if (code===0) return "Soleado";
  if ([1,2,3].includes(code)) return "Parcialmente nublado";
  if ([45,48].includes(code)) return "Neblina";
  if ([61,63,65,80,81,82].includes(code)) return "Lluvia";
  if ([71,73,75,77].includes(code)) return "Nieve";
  if ([95,96,99].includes(code)) return "Tormenta";
  return "Nublado";
}

async function reverseGeo(lat, lon){
  // Nominatim (sin key) — suficiente para “Mi ubicación”
  try{
    const url = `https://nominatim.openstreetmap.org/reverse?format=jsonv2&lat=${lat}&lon=${lon}`;
    const r = await fetch(url, { headers:{ "Accept":"application/json" } });
    if (!r.ok) throw 0;
    const j = await r.json();
    const a = j.address || {};
    return a.city || a.town || a.village || a.suburb || j.name || "Mi ubicación";
  }catch{
    return "Mi ubicación";
  }
}

async function getWx(lat, lon){
  const url =
    `https://api.open-meteo.com/v1/forecast?latitude=${lat}&longitude=${lon}` +
    `&current=temperature_2m,wind_speed_10m,weather_code` +
    `&hourly=temperature_2m,weather_code&forecast_days=2` +
    `&daily=temperature_2m_max,temperature_2m_min,weather_code&forecast_days=7` +
    `&timezone=auto`;
  const r = await fetch(url);
  if (!r.ok) throw new Error("wx fetch failed");
  return r.json();
}

function fmtHour(iso){
  const d = new Date(iso);
  return new Intl.DateTimeFormat("es-CL",{ hour:"numeric" }).format(d);
}

function dayLabel(i){
  const names = ["Hoy","Mar","Mié","Jue","Vie","Sáb","Dom","Lun"];
  return names[i] || "—";
}

async function init(){
  const pillIco = qs("#wxIco");
  const pillTemp = qs("#wxTemp");
  const pillCity = qs("#wxCity");

  const mCity = qs("#wxModalCity");
  const mTemp = qs("#wxModalTemp");
  const mIco  = qs("#wxModalIco");
  const mNow  = qs("#wxModalNow");
  const mSum  = qs("#wxSummary");
  const hours = qs("#wxHours");
  const days  = qs("#wxDays");

  if (!pillIco || !pillTemp || !pillCity || !mCity || !mTemp || !mIco || !mNow || !mSum || !hours || !days){
    return;
  }

  // fallback coords: Las Condes
  let lat = -33.408;
  let lon = -70.567;

  try{
    const pos = await new Promise((ok, bad) => {
      navigator.geolocation.getCurrentPosition(ok, bad, { enableHighAccuracy:false, timeout:6000 });
    });
    lat = pos.coords.latitude;
    lon = pos.coords.longitude;
  }catch{}

  const city = await reverseGeo(lat, lon);
  pillCity.textContent = city;
  mCity.textContent = city;

  try{
    const data = await getWx(lat, lon);

    const curT = Math.round(data.current.temperature_2m);
    const curW = Math.round(data.current.wind_speed_10m);
    const curC = data.current.weather_code;

    const ico = wxIcon(curC);
    const txt = wxText(curC);

    pillIco.textContent = ico;
    pillTemp.textContent = `${curT}°`;

    mTemp.textContent = `${curT}°`;
    mIco.textContent = ico;
    mNow.textContent = txt;
    mSum.textContent = `Rachas de viento hasta ${curW} km/h.`;

    // hours: toma próximos 6
    hours.innerHTML = "";
    const ht = data.hourly.time;
    const htemp = data.hourly.temperature_2m;
    const hcode = data.hourly.weather_code;

    const now = Date.now();
    let start = 0;
    for (let i=0;i<ht.length;i++){
      if (new Date(ht[i]).getTime() >= now){ start=i; break; }
    }
    const n = Math.min(6, ht.length-start);
    for (let i=0;i<n;i++){
      const idx = start+i;
      const el = document.createElement("div");
      el.className = "wx-hour";
      el.innerHTML = `
        <div class="t">${i===0 ? "Ahora" : fmtHour(ht[idx])}</div>
        <div class="i">${wxIcon(hcode[idx])}</div>
        <div class="v">${Math.round(htemp[idx])}°</div>
      `;
      hours.appendChild(el);
    }

    // days
    days.innerHTML = "";
    const dmax = data.daily.temperature_2m_max;
    const dmin = data.daily.temperature_2m_min;
    const dcode = data.daily.weather_code;

    const gMin = Math.min(...dmin);
    const gMax = Math.max(...dmax);
    const span = Math.max(1, gMax - gMin);

    for (let i=0;i<dmax.length;i++){
      const lo = Math.round(dmin[i]);
      const hi = Math.round(dmax[i]);
      const left = ((lo - gMin)/span)*100;
      const width = ((hi - lo)/span)*100;

      const row = document.createElement("div");
      row.className = "wx-day";
      row.innerHTML = `
        <div class="d">${dayLabel(i)}</div>
        <div class="i">${wxIcon(dcode[i])}</div>
        <div class="min">${lo}°</div>
        <div class="bar wx-bar"><span style="left:${left}%; width:${width}%;"></span></div>
        <div class="max">${hi}°</div>
      `;
      days.appendChild(row);
    }
  }catch(e){
    pillIco.textContent = "☁️";
    pillTemp.textContent = "—°";
    qs("#wxModalNow").textContent = "Clima no disponible";
    qs("#wxSummary").textContent = "Revisa internet / permisos de ubicación.";
  }
}

init();

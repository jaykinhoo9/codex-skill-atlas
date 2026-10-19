'use strict';

const SOURCE_COLORS_KEY = 'skill-atlas.source-colors.v1';
function normalizeHex(value) {
  const hex = String(value ?? '').trim().replace(/^#/, '');
  if (/^[\da-f]{3}$/i.test(hex)) return '#' + [...hex].map(c => c + c).join('').toLowerCase();
  return /^[\da-f]{6}$/i.test(hex) ? '#' + hex.toLowerCase() : null;
}
function parseSourceColors(raw, defaults) {
  let stored;
  try { stored = JSON.parse(raw); } catch { stored = null; }
  return Object.fromEntries(Object.entries(defaults).map(([key, fallback]) =>
    [key, normalizeHex(stored?.[key]) || fallback]));
}
function hsvToHex({h, s, v}) {
  h = ((h % 360) + 360) % 360 / 60;
  s = Math.max(0, Math.min(1, s)); v = Math.max(0, Math.min(1, v));
  const c = v * s, x = c * (1 - Math.abs(h % 2 - 1)), m = v - c;
  const rgb = [[c,x,0],[x,c,0],[0,c,x],[0,x,c],[x,0,c],[c,0,x]][Math.floor(h)];
  return '#' + rgb.map(n => Math.round((n + m) * 255).toString(16).padStart(2,'0')).join('');
}
function hexToHsv(hex, fallbackHue = 0) {
  const [r,g,b] = normalizeHex(hex).slice(1).match(/../g).map(n => parseInt(n,16)/255);
  const max = Math.max(r,g,b), min = Math.min(r,g,b), delta = max - min;
  let h = fallbackHue;
  if (delta) h = ((max === r ? (g-b)/delta : max === g ? (b-r)/delta+2 : (r-g)/delta+4) * 60 + 360) % 360;
  return {h, s:max ? delta/max : 0, v:max};
}

class SourcePalette {
  static load(defaults) {
    try { return parseSourceColors(localStorage.getItem(SOURCE_COLORS_KEY), defaults); }
    catch { return {...defaults}; }
  }

  constructor({defaults, colors, sources, onChange}) {
    Object.assign(this, {defaults, colors, sources, onChange, source:null, pointerId:null});
    this.panel = document.querySelector('#source-palette');
    this.plane = this.panel.querySelector('#color-plane');
    this.hue = this.panel.querySelector('#color-hue');
    this.hex = this.panel.querySelector('#color-hex');
    this.native = this.panel.querySelector('#color-native');
    this.error = this.panel.querySelector('#color-error');
    const presets = [
      ['湖蓝','#568aaf'],['薄荷','#49a580'],['麦金','#bc995e'],['雾紫','#9485b8'],['玫瑰','#bc7b88'],['苔绿','#7c9566'],
      ['靛蓝','#627ad3'],['青碧','#289c9b'],['琥珀','#dc9844'],['葡萄','#a36cc2'],['珊瑚','#d87866'],['石墨','#77818a']
    ];
    this.panel.querySelector('#color-presets').innerHTML = presets.map(([name,color]) =>
      `<button type="button" class="color-swatch" data-preset-color="${color}" style="--swatch:${color}" aria-label="${name} ${color}" aria-pressed="false" title="${name}"><svg viewBox="0 0 16 16" aria-hidden="true"><path d="m4 8 3 3 5-6"/></svg></button>`).join('');
    for (const [source,color] of Object.entries(colors)) this.paint(source,color);
    document.addEventListener('click', e => {
      const trigger = e.target.closest('[data-color-source]');
      if (trigger) { this.source === trigger.dataset.colorSource ? this.close(true) : this.open(trigger.dataset.colorSource); }
    });
    document.addEventListener('pointerdown', e => {
      if (this.source && !this.panel.contains(e.target) && !e.target.closest('[data-color-source]')) this.close();
    });
    document.addEventListener('focusin', e => {
      if (this.source && !this.panel.contains(e.target) && !e.target.closest('[data-color-source]')) this.close();
    });
    document.addEventListener('keydown', e => {
      if (this.source && e.key === 'Escape') { e.preventDefault(); e.stopImmediatePropagation(); this.close(true); }
    }, true);
    this.panel.addEventListener('click', e => {
      const swatch = e.target.closest('[data-preset-color]');
      if (swatch) this.apply(swatch.dataset.presetColor);
    });
    this.panel.querySelector('#color-close').onclick = () => this.close(true);
    this.panel.querySelector('#color-reset').onclick = () => this.apply(defaults[this.source]);
    this.hue.addEventListener('input', () => this.applyHSV({...this.hsv,h:Number(this.hue.value)}));
    this.native.addEventListener('input', () => this.apply(this.native.value));
    this.hex.addEventListener('change', () => this.applyHex());
    this.hex.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); this.applyHex(); } });
    this.plane.addEventListener('pointerdown', e => {
      if (e.button !== 0 || !e.isPrimary) return;
      e.preventDefault(); this.plane.focus({preventScroll:true});
      this.pointerId = e.pointerId; this.plane.setPointerCapture(e.pointerId); this.pick(e);
    });
    this.plane.addEventListener('pointermove', e => { if (e.pointerId === this.pointerId) this.pick(e); });
    this.plane.addEventListener('pointerup', e => {
      if (e.pointerId !== this.pointerId) return;
      this.pick(e); this.finishDrag(); this.save();
    });
    for (const event of ['pointercancel','lostpointercapture']) this.plane.addEventListener(event, () => { this.finishDrag(); this.save(); });
    this.plane.addEventListener('keydown', e => {
      if (!['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(e.key)) return;
      e.preventDefault(); const step = e.shiftKey ? .1 : .01;
      const {h,s,v} = this.hsv;
      this.applyHSV({h,s:Math.max(0,Math.min(1,s+(e.key==='ArrowRight'?step:e.key==='ArrowLeft'?-step:0))),
        v:Math.max(0,Math.min(1,v+(e.key==='ArrowUp'?step:e.key==='ArrowDown'?-step:0)))});
    });
    window.addEventListener('resize', () => this.position());
    document.addEventListener('scroll', e => { if (!this.panel.contains(e.target)) this.position(); }, true);
    window.addEventListener('blur', () => { this.finishDrag(); this.save(); });
    window.addEventListener('pagehide', () => this.save());
    window.addEventListener('storage', e => {
      if (e.key !== SOURCE_COLORS_KEY || this.pointerId !== null) return;
      const next = parseSourceColors(e.newValue,defaults);
      for (const [source,color] of Object.entries(next)) { colors[source]=color; this.paint(source,color); }
      if (this.source) { this.hsv=hexToHsv(colors[this.source]); this.render(); }
    });
  }

  paint(source,color) {
    document.documentElement.style.setProperty(`--source-${source}`,color);
    this.onChange(source,color);
  }
  open(source) {
    if (!Object.hasOwn(this.sources,source)) return;
    this.close(); this.source=source;
    this.hsv=hexToHsv(this.colors[source]);
    this.hex.value=this.colors[source].toUpperCase();
    this.panel.querySelector('#color-title').textContent=this.sources[source];
    this.panel.hidden=false; this.render(); this.position();
    document.querySelector(`[data-color-source="${source}"]`)?.setAttribute('aria-expanded','true');
    (this.panel.querySelector('[aria-pressed="true"]') || this.hex).focus({preventScroll:true});
  }
  close(restoreFocus=false) {
    if (!this.source) return;
    this.finishDrag(); this.save();
    const trigger=document.querySelector(`[data-color-source="${this.source}"]`);
    this.source=null; this.panel.hidden=true; trigger?.setAttribute('aria-expanded','false');
    if (restoreFocus) trigger?.focus({preventScroll:true});
  }
  position() {
    if (!this.source) return;
    const trigger=document.querySelector(`[data-color-source="${this.source}"]`);
    if (!trigger) return this.close();
    const r=trigger.getBoundingClientRect(), w=this.panel.offsetWidth, h=this.panel.offsetHeight;
    if (r.bottom<0 || r.top>innerHeight) return this.close();
    const x=r.right+14+w<=innerWidth-12?r.right+14:r.left-w-14;
    this.panel.style.left=Math.max(12,Math.min(innerWidth-w-12,x))+'px';
    this.panel.style.top=Math.max(12,Math.min(innerHeight-h-12,r.top-15))+'px';
  }
  apply(color,hsv=null) {
    color=normalizeHex(color); if (!this.source || !color) return;
    this.hsv=hsv || hexToHsv(color,this.hsv.h);
    this.colors[this.source]=color; this.paint(this.source,color); this.render();
    clearTimeout(this.saveTimer); this.dirty=true; this.saveTimer=setTimeout(()=>this.save(),150);
  }
  applyHSV(hsv) { this.apply(hsvToHex(hsv),hsv); }
  applyHex() {
    const color=normalizeHex(this.hex.value);
    if (!color) { this.hex.setAttribute('aria-invalid','true'); this.error.textContent='请输入 3 位或 6 位 HEX 色值。'; this.error.hidden=false; return; }
    this.apply(color); this.hex.value=color.toUpperCase();
  }
  pick(e) {
    const r=this.plane.getBoundingClientRect();
    this.applyHSV({h:this.hsv.h,s:Math.max(0,Math.min(1,(e.clientX-r.left)/r.width)),v:Math.max(0,Math.min(1,1-(e.clientY-r.top)/r.height))});
  }
  finishDrag() {
    const id=this.pointerId; this.pointerId=null;
    if (id!==null && this.plane.hasPointerCapture(id)) this.plane.releasePointerCapture(id);
  }
  render() {
    const color=this.colors[this.source], {h,s,v}=this.hsv;
    this.plane.style.backgroundColor=hsvToHex({h,s:1,v:1});
    this.plane.setAttribute('aria-label',`自定义色盘，饱和度 ${Math.round(s*100)}%，明度 ${Math.round(v*100)}%；左右方向键调整饱和度，上下方向键调整明度`);
    Object.assign(this.panel.querySelector('#color-thumb').style,{left:s*100+'%',top:(1-v)*100+'%',background:color});
    this.hue.value=Math.round(h); this.hue.setAttribute('aria-valuetext',Math.round(h)+' 度');
    this.native.value=color;
    if (document.activeElement!==this.hex) this.hex.value=color.toUpperCase();
    this.hex.removeAttribute('aria-invalid'); this.error.hidden=true;
    for (const swatch of this.panel.querySelectorAll('[data-preset-color]')) swatch.setAttribute('aria-pressed',String(swatch.dataset.presetColor===color));
    this.panel.querySelector('#color-reset').disabled=color===this.defaults[this.source];
  }
  save() {
    clearTimeout(this.saveTimer); if (!this.dirty) return;
    try { localStorage.setItem(SOURCE_COLORS_KEY,JSON.stringify(this.colors)); this.dirty=false; }
    catch { this.error.textContent='浏览器未允许保存，颜色仅在本次页面中生效。'; this.error.hidden=false; }
  }
}

if (typeof module !== 'undefined') module.exports={normalizeHex,parseSourceColors,hsvToHex,hexToHsv};

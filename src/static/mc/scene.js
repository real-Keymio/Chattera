// 表版 "共鸣" — the stage behind the interface, drawn as a real perspective scene on a 2D canvas (no library).
//
// The discussion is a small solid at the centre. Every participant is a body on its own tilted orbit around it.
//   thinking  → the body brightens, its orbit lights up, motes circle it
//   searching → probes leave the body for far points of the space and come back along the same line
//   writing   → a stream of particles arcs from the body into the centre, which takes on its hue
// Several participants working at once is several streams converging: the picture of a multi-model answer.
// The camera drifts with the pointer, dollies in when a chat is opened and swings when the pane layout changes.
// Dark theme draws with additive light; light theme draws glazed bodies with soft ground shadows instead of glow.
import { S, IDS, hueValue, runningTasks } from './state.js';

let canvas, ctx, W = 0, H = 0, raf = 0, last = 0, hues = {}, light = false, ink = '#fff', still = false, enabled = false;
const cam = { yaw: 0, pitch: .40, dist: 1000, yawTo: 0, pitchTo: .40, dolly: 0, spin: 0, px: .5, py: .5, cx: .5 };
const energy = {}, searching = {};
let pulse = 0, particles = [], dust = [];
const PHI = (1 + Math.sqrt(5)) / 2;
const ICO = [[-1, PHI, 0], [1, PHI, 0], [-1, -PHI, 0], [1, -PHI, 0], [0, -1, PHI], [0, 1, PHI], [0, -1, -PHI], [0, 1, -PHI], [PHI, 0, -1], [PHI, 0, 1], [-PHI, 0, -1], [-PHI, 0, 1]]
    .map(v => { const n = Math.hypot(...v); return v.map(x => x / n); });
const EDGES = [];
for (let a = 0; a < 12; a++) for (let b = a + 1; b < 12; b++) if (Math.abs(Math.hypot(ICO[a][0] - ICO[b][0], ICO[a][1] - ICO[b][1], ICO[a][2] - ICO[b][2]) - 1.0515) < .01) EDGES.push([a, b]);

const rnd = (a, b) => a + Math.random() * (b - a);
const rgba = (hex, alpha) => {
    const v = hex.replace('#', ''), n = parseInt(v.length === 3 ? v.replace(/./g, '$&$&') : v.slice(0, 6), 16);
    return `rgba(${n >> 16 & 255},${n >> 8 & 255},${n & 255},${Math.max(0, Math.min(1, alpha))})`;
};

/** World → screen. Returns [x, y, depth, scale]; depth grows away from the camera. */
function project(x, y, z) {
    const cy = Math.cos(cam.yaw), sy = Math.sin(cam.yaw), cp = Math.cos(cam.pitch), sp = Math.sin(cam.pitch);
    const x1 = x * cy - z * sy, z1 = x * sy + z * cy;
    const y2 = y * cp - z1 * sp, z2 = y * sp + z1 * cp + cam.dist + cam.dolly;
    const f = Math.min(W, H) * 1.15, k = f / Math.max(60, z2);
    return [W * cam.cx + x1 * k, H * .58 - y2 * k, z2, k];
}
const orbit = i => ({ radius: 250 + i * 74, tilt: (i % 2 ? -1 : 1) * (.05 + .035 * (i % 3)), node: i * 1.9, phase: i * 2.4, speed: (i % 2 ? -1 : 1) * (.05 - Math.min(i, 5) * .004) });
function onOrbit(o, angle) {
    const x = Math.cos(angle) * o.radius, z = Math.sin(angle) * o.radius;
    // tilt the orbital plane about an axis in the plane, turned by `node`
    const cn = Math.cos(o.node), sn = Math.sin(o.node), xr = x * cn + z * sn, zr = -x * sn + z * cn;
    const y = zr * Math.sin(o.tilt), zt = zr * Math.cos(o.tilt);
    return [xr * cn - zt * sn, y, xr * sn + zt * cn];
}
const GROUND = -150;

function readTheme() {
    const root = document.documentElement, style = getComputedStyle(root);
    light = root.dataset.xtheme === 'light';
    ink = style.getPropertyValue('--ink').trim() || (light ? '#141a2e' : '#e8ebf4');
    hues = Object.fromEntries(IDS.map(id => [id, hueValue(id) || '#8a8f98']));
    still = root.dataset.motion === 'off' || (root.dataset.motion !== 'full' && matchMedia('(prefers-reduced-motion: reduce)').matches);
}

function resize() {
    const scale = Math.min(devicePixelRatio || 1, 1.5);
    W = innerWidth; H = innerHeight;
    canvas.width = Math.round(W * scale); canvas.height = Math.round(H * scale);
    ctx.setTransform(scale, 0, 0, scale, 0, 0);
    const chat = document.getElementById('chat')?.getBoundingClientRect();
    cam.cx = chat && chat.width ? (chat.left + chat.width / 2) / W : .5;
}

function spawn(kind, id, from) {
    if (particles.length > 420) return;
    const p = { kind, id, t: 0, from };
    if (kind === 'stream') Object.assign(p, { life: rnd(1.1, 1.9), lift: rnd(40, 150), side: rnd(-70, 70), size: rnd(1, 2.4) });
    else if (kind === 'mote') Object.assign(p, { life: rnd(1.2, 2.2), r: rnd(16, 38), a: rnd(0, 6.28), b: rnd(-1, 1), w: rnd(2, 4.5), size: rnd(.8, 1.6) });
    else { const a = rnd(0, 6.28), e = rnd(-.25, .7), far = rnd(520, 880); Object.assign(p, { life: rnd(2.2, 3.4), to: [Math.cos(a) * Math.cos(e) * far, Math.sin(e) * far * .6 + 60, Math.sin(a) * Math.cos(e) * far], size: rnd(1.4, 2.4) }); }
    particles.push(p);
}

function frame(now) {
    raf = 0;
    if (!enabled || document.hidden) return;
    if (now - last < 31) { schedule(); return; }
    const dt = Math.min(.08, (now - last) / 1000 || .033); last = now;
    const t = now / 1000;
    const active = {}, look = {};
    for (const task of runningTasks(S.active)) { active[task.identity] = task.output_started_at ? 1 : .55; if (task.activity && !task.output_started_at) look[task.identity] = 1; }
    let any = 0;
    for (const id of IDS) {
        energy[id] = (energy[id] || 0) + ((active[id] || 0) - (energy[id] || 0)) * Math.min(1, dt * 2.4);
        searching[id] = (searching[id] || 0) + ((look[id] || 0) - (searching[id] || 0)) * Math.min(1, dt * 3);
        any = Math.max(any, energy[id]);
    }
    // camera
    cam.yawTo = (cam.px - .5) * .22; cam.pitchTo = .40 + (cam.py - .5) * .10;
    cam.spin *= Math.exp(-dt * 2.2); cam.dolly *= Math.exp(-dt * 2.6); pulse *= Math.exp(-dt * 1.8);
    cam.yaw += (cam.yawTo + cam.spin + (still ? 0 : Math.sin(t * .07) * .05) - cam.yaw) * Math.min(1, dt * 3);
    cam.pitch += (cam.pitchTo - cam.pitch) * Math.min(1, dt * 3);

    ctx.clearRect(0, 0, W, H);
    ctx.lineCap = 'round';
    const glow = light ? 'source-over' : 'lighter';
    const unit = Math.min(W, H) / 900;   // keeps the composition similar on any window

    // ground grid: the strongest depth cue; fades towards the horizon
    ctx.globalCompositeOperation = 'source-over';
    ctx.lineWidth = 1;
    for (let i = -10; i <= 10; i++) {
        for (const along of [0, 1]) {
            let prev = null;
            for (let j = -10; j <= 10; j++) {
                const a = i * 150, b = j * 150, point = along ? project(a, GROUND, b) : project(b, GROUND, a);
                if (prev && point[2] > 120 && prev[2] > 120) {
                    const fade = Math.max(0, 1 - Math.hypot(a, b) / 1600) * Math.min(1, 900 / point[2]);
                    if (fade > .02) { ctx.strokeStyle = rgba(ink, (light ? .12 : .06) * fade); ctx.beginPath(); ctx.moveTo(prev[0], prev[1]); ctx.lineTo(point[0], point[1]); ctx.stroke(); }
                }
                prev = point;
            }
        }
    }
    // far dust (dark) / motes of paper light (light)
    for (const d of dust) {
        const p = project(d[0], d[1], d[2]);
        if (p[2] < 150) continue;
        ctx.fillStyle = rgba(ink, (light ? .10 : .22) * d[3] * Math.min(1, 1100 / p[2]));
        ctx.beginPath(); ctx.arc(p[0], p[1], Math.max(.5, d[4] * p[3]), 0, 6.283); ctx.fill();
    }

    // orbits and bodies
    const bodies = IDS.map((id, i) => {
        const o = orbit(i), e = energy[id] || 0;
        const angle = o.phase + (still ? 0 : t * o.speed * (1 + e * 2.2));
        return { id, i, o, e, angle, pos: onOrbit(o, angle), hue: hues[id] || '#8a8f98' };
    });
    const ring = (body, front) => {
        const { o, e, hue } = body;
        let prev = null;
        for (let s = 0; s <= 72; s++) {
            const a = s / 72 * 6.2832, w = onOrbit(o, a), p = project(...w);
            const near = p[2] < cam.dist + cam.dolly;
            if (prev && near === front) {
                // the arc just behind the body is the brightest: a wake
                let wake = ((body.angle - a) * Math.sign(o.speed) % 6.2832 + 6.2832) % 6.2832;
                wake = Math.max(0, 1 - wake / 1.6);
                const alpha = (light ? .24 : .11) + e * .34 + wake * (.10 + e * .5);
                ctx.strokeStyle = rgba(e > .03 || wake > .05 ? hue : ink, alpha * Math.min(1, 1000 / p[2]) * (e > .03 || wake > .05 ? 1 : .55));
                ctx.lineWidth = (1 + e * 1.4 + wake * .8) * Math.max(.6, p[3]);
                ctx.beginPath(); ctx.moveTo(prev[0], prev[1]); ctx.lineTo(p[0], p[1]); ctx.stroke();
            }
            prev = p;
        }
    };
    for (const body of bodies) ring(body, false);

    // the centre: nested icosahedra, one shell per working participant, turning at different rates
    const shells = [{ hue: ink, e: light ? .5 : .34, r: 46, w: 1 }, ...bodies.filter(b => b.e > .03).map((b, n) => ({ hue: b.hue, e: b.e, r: 56 + n * 13 + pulse * 14, w: 1.5 }))];
    shells.forEach((shell, n) => {
        const ax = t * (still ? 0 : .21 + n * .07) + n, ay = t * (still ? 0 : .16 - n * .05) + n * 2;
        const pts = ICO.map(v => {
            let [x, y, z] = v;
            [x, z] = [x * Math.cos(ay) - z * Math.sin(ay), x * Math.sin(ay) + z * Math.cos(ay)];
            [y, z] = [y * Math.cos(ax) - z * Math.sin(ax), y * Math.sin(ax) + z * Math.cos(ax)];
            const r = shell.r * (1 + (n ? Math.sin(t * 2.2 + n) * .03 * shell.e : 0));
            return project(x * r, y * r + 6, z * r);
        });
        ctx.globalCompositeOperation = n ? glow : 'source-over';
        for (const [a, b] of EDGES) {
            const depth = (pts[a][2] + pts[b][2]) / 2, front = depth < cam.dist + cam.dolly;
            ctx.strokeStyle = rgba(shell.hue, shell.e * (front ? .9 : .3));
            ctx.lineWidth = shell.w * (front ? 1.25 : .8) * Math.max(.7, pts[a][3]);
            ctx.beginPath(); ctx.moveTo(pts[a][0], pts[a][1]); ctx.lineTo(pts[b][0], pts[b][1]); ctx.stroke();
        }
    });
    if (any > .03 || pulse > .02) {
        const c = project(0, 6, 0), top = bodies.reduce((a, b) => b.e > a.e ? b : a, bodies[0] || { e: 0, hue: ink });
        const radius = (90 + any * 90 + pulse * 120) * c[3];
        const g = ctx.createRadialGradient(c[0], c[1], 0, c[0], c[1], radius);
        g.addColorStop(0, rgba(top.hue, (light ? .22 : .30) * Math.max(any, pulse))); g.addColorStop(1, rgba(top.hue, 0));
        ctx.globalCompositeOperation = glow; ctx.fillStyle = g; ctx.beginPath(); ctx.arc(c[0], c[1], radius, 0, 6.283); ctx.fill();
    }
    for (const body of bodies) ring(body, true);

    // particles
    if (!still) for (const body of bodies) {
        if (body.e > .7 && Math.random() < dt * 34 * body.e) spawn('stream', body.id, body.pos);
        if (body.e > .08 && body.e < .8 && Math.random() < dt * 9) spawn('mote', body.id);
        if (searching[body.id] > .2 && Math.random() < dt * 5) spawn('probe', body.id, body.pos);
    }
    ctx.globalCompositeOperation = glow;
    const byId = Object.fromEntries(bodies.map(b => [b.id, b]));
    particles = particles.filter(p => {
        p.t += dt / p.life;
        const body = byId[p.id];
        if (p.t >= 1 || !body) return false;
        let w, alpha = Math.sin(Math.PI * Math.min(1, p.t)) ;
        if (p.kind === 'stream') {
            const u = p.t * p.t * (3 - 2 * p.t), s = 1 - u;   // ease; arc lifted over the plane
            w = [p.from[0] * s + p.side * 4 * u * s, p.from[1] * s + 6 * u + p.lift * 4 * u * s, p.from[2] * s];
        } else if (p.kind === 'mote') {
            const a = p.a + p.t * p.w * 3;
            w = [body.pos[0] + Math.cos(a) * p.r, body.pos[1] + Math.sin(a * 1.3) * p.r * .6 * p.b, body.pos[2] + Math.sin(a) * p.r];
        } else {
            const out = p.t < .5 ? 1 - (1 - p.t * 2) ** 3 : 1 - ((p.t - .5) * 2) ** 3;   // out, pause at the far point, back
            w = p.to.map((v, k) => body.pos[k] + (v - body.pos[k]) * out);
            const a = project(...body.pos), b = project(...w);
            ctx.strokeStyle = rgba(body.hue, .16 * alpha); ctx.lineWidth = 1; ctx.beginPath(); ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]); ctx.stroke();
        }
        const s = project(...w);
        if (s[2] < 100) return true;
        ctx.fillStyle = rgba(body.hue, (light ? .75 : .9) * alpha);
        ctx.beginPath(); ctx.arc(s[0], s[1], Math.max(.6, p.size * s[3] * (p.kind === 'probe' ? 1.6 : 1.3)), 0, 6.283); ctx.fill();
        return true;
    });

    // bodies, far to near
    for (const body of bodies.map(b => ({ b, p: project(...b.pos) })).sort((a, b) => b.p[2] - a.p[2])) {
        const { b, p } = body, r = (9 + b.e * 6) * p[3] * Math.max(1, unit * .9);
        if (light) {
            const sh = project(b.pos[0], GROUND, b.pos[2]);
            ctx.globalCompositeOperation = 'source-over';
            const g0 = ctx.createRadialGradient(sh[0], sh[1], 0, sh[0], sh[1], r * 2.6);
            g0.addColorStop(0, rgba(b.hue, .20)); g0.addColorStop(1, rgba(b.hue, 0));
            ctx.save(); ctx.translate(sh[0], sh[1]); ctx.scale(1, .34); ctx.translate(-sh[0], -sh[1]);
            ctx.fillStyle = g0; ctx.beginPath(); ctx.arc(sh[0], sh[1], r * 2.6, 0, 6.283); ctx.fill(); ctx.restore();
            const g = ctx.createRadialGradient(p[0] - r * .35, p[1] - r * .4, r * .1, p[0], p[1], r);
            g.addColorStop(0, '#ffffff'); g.addColorStop(.35, rgba(b.hue, .95)); g.addColorStop(1, rgba(b.hue, .78));
            ctx.fillStyle = g; ctx.beginPath(); ctx.arc(p[0], p[1], r, 0, 6.283); ctx.fill();
            if (b.e > .05) { ctx.strokeStyle = rgba(b.hue, .5 * b.e); ctx.lineWidth = 1.5; ctx.beginPath(); ctx.arc(p[0], p[1], r * (1.5 + .4 * Math.sin(t * 3 + b.i)), 0, 6.283); ctx.stroke(); }
        } else {
            ctx.globalCompositeOperation = 'lighter';
            const halo = r * (3.2 + b.e * 3.4);
            const g = ctx.createRadialGradient(p[0], p[1], 0, p[0], p[1], halo);
            g.addColorStop(0, rgba(b.hue, .55 + b.e * .4)); g.addColorStop(.25, rgba(b.hue, .16 + b.e * .2)); g.addColorStop(1, rgba(b.hue, 0));
            ctx.fillStyle = g; ctx.beginPath(); ctx.arc(p[0], p[1], halo, 0, 6.283); ctx.fill();
            ctx.fillStyle = rgba('#ffffff', .75 + b.e * .25); ctx.beginPath(); ctx.arc(p[0], p[1], r * .42, 0, 6.283); ctx.fill();
        }
    }
    ctx.globalCompositeOperation = 'source-over';
    const moving = any > .02 || pulse > .02 || Math.abs(cam.dolly) > 1 || Math.abs(cam.spin) > .002 || particles.length || Math.abs(cam.yawTo - cam.yaw) > .002;
    if (!still || moving) schedule();
}
function schedule() { if (!raf && enabled) raf = requestAnimationFrame(frame); }

/** A moment in the interface that the stage answers: 'room' (dolly in), 'layout' (swing), 'message' (the centre pulses). */
export function kick(kind, amount = 1) {
    if (still) { schedule(); return; }
    if (kind === 'room') { cam.dolly = 520; cam.spin = .5 * (Math.random() < .5 ? -1 : 1); }
    else if (kind === 'layout') cam.spin = .22 * amount;
    else if (kind === 'message') pulse = Math.min(1, pulse + .7);
    schedule();
}
export function setStage(on) {
    enabled = on;
    if (on) { readTheme(); resize(); schedule(); } else if (ctx) ctx.clearRect(0, 0, W, H);
}
export function refreshStage() { if (enabled) { readTheme(); resize(); schedule(); } }

export function initStage(element) {
    canvas = element; ctx = canvas.getContext('2d');
    dust = Array.from({ length: 110 }, () => [rnd(-1500, 1500), rnd(-120, 760), rnd(-1500, 1500), rnd(.3, 1), rnd(.6, 1.6)]);
    addEventListener('resize', refreshStage);
    addEventListener('pointermove', event => { cam.px = event.clientX / innerWidth; cam.py = event.clientY / innerHeight; schedule(); }, { passive: true });
    document.addEventListener('visibilitychange', schedule);
}

/**
 * Affichage Europe/Paris des horodatages UTC de l'API (CONTRAT : l'affichage
 * Europe/Paris est l'affaire du frontend). Aucun calcul de décalage en dur :
 * tout passe par Intl, ce qui gère les changements d'heure (journées de 23/25 h).
 */
export const PARIS = 'Europe/Paris';

const partsFmt = new Intl.DateTimeFormat('en-GB', {
  timeZone: PARIS, hourCycle: 'h23',
  year: 'numeric', month: '2-digit', day: '2-digit',
  hour: '2-digit', minute: '2-digit', second: '2-digit',
});

function parisParts(ms: number): Record<string, number> {
  const out: Record<string, number> = {};
  for (const p of partsFmt.formatToParts(new Date(ms))) {
    if (p.type !== 'literal') out[p.type] = Number(p.value);
  }
  return out;
}

/** Décalage Paris − UTC (ms) à l'instant donné. */
function parisOffset(ms: number): number {
  const p = parisParts(ms);
  const asUtc = Date.UTC(p['year'], p['month'] - 1, p['day'], p['hour'], p['minute'], p['second']);
  return asUtc - Math.floor(ms / 1000) * 1000;
}

/** Instant UTC (ms) de 00:00 heure de Paris pour la date « YYYY-MM-DD ». */
export function parisMidnight(date: string): number {
  const [y, m, d] = date.split('-').map(Number);
  const guess = Date.UTC(y, m - 1, d);
  let t = guess - parisOffset(guess);
  t = guess - parisOffset(t);
  return t;
}

/** Bornes [début, fin) en ms UTC de la journée de Paris « YYYY-MM-DD ». */
export function parisDayBounds(date: string): [number, number] {
  const [y, m, d] = date.split('-').map(Number);
  const next = new Date(Date.UTC(y, m - 1, d + 1)).toISOString().slice(0, 10);
  return [parisMidnight(date), parisMidnight(next)];
}

/** « YYYY-MM-DD » de l'instant donné, en heure de Paris (par défaut : maintenant). */
export function parisDate(ms: number = Date.now()): string {
  const p = parisParts(ms);
  return `${p['year']}-${String(p['month']).padStart(2, '0')}-${String(p['day']).padStart(2, '0')}`;
}

/** « YYYY-MM-DD » décalé de n jours (calendrier, sans fuseau). */
export function addDays(date: string, n: number): string {
  const [y, m, d] = date.split('-').map(Number);
  return new Date(Date.UTC(y, m - 1, d + n)).toISOString().slice(0, 10);
}

const timeFmt = new Intl.DateTimeFormat('fr-FR', { timeZone: PARIS, hour: '2-digit', minute: '2-digit' });
const dateTimeFmt = new Intl.DateTimeFormat('fr-FR', {
  timeZone: PARIS, weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit',
});
const longDateFmt = new Intl.DateTimeFormat('fr-FR', {
  timeZone: PARIS, weekday: 'long', day: 'numeric', month: 'long', year: 'numeric',
});

/** « 14:05 » (Paris). */
export function fmtTime(iso: string | number): string {
  return timeFmt.format(new Date(iso));
}

/** « sam. 4 oct., 14:05 » (Paris). */
export function fmtDateTime(iso: string | number): string {
  return dateTimeFmt.format(new Date(iso));
}

/** « samedi 4 octobre 2026 » pour une date « YYYY-MM-DD » de Paris. */
export function fmtLongDate(date: string): string {
  return longDateFmt.format(new Date(parisMidnight(date) + 12 * 3600_000));
}
